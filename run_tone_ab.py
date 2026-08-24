"""톤 레지스터 A/B — 골든셋 19건을 ToneSpec **off/on**으로 돌려 나란히 비교한다.

    python run_tone_ab.py                 # 전체 19건
    python run_tone_ab.py essay_media     # 케이스 키만 골라서
    python run_tone_ab.py --out 리포트.md  # 저장 위치 지정
    python run_tone_ab.py --backend local # 라이브 백엔드로(수치가 의미를 갖는 경로)

무엇을 재는가:
  · 결정적으로 셀 수 있는 것(종결어미 규격 준수율·겸양·완충어·안부·이모지·금지어)은 표로.
  · 자연스러움·설득력처럼 셀 수 없는 것은 **본문을 나란히 붙여** 사람이 읽고 판단하게.

⚠️ mock 백엔드(기본)는 프롬프트와 무관하게 결정적 응답을 낸다. 그래서 mock에서는
본문이 off/on 동일하게 나오는 것이 **정상**이고, 이 실행이 검증하는 것은 하니스·
레지스터 확정·지표 계산이다. 톤 자체의 변화를 보려면 라이브 백엔드로 돌려야 한다
(run_evals.py와 같은 입장).

리포트는 기본적으로 gitignore 영역(`_until_work/`)에 쓴다 — 생성물에 과제 원문이
섞이므로 저장소에 커밋될 자리에 두지 않는다.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from until.config import Config
from until.evals.tone_cases import case_keys, tone_cases
from until.evals.tone_metrics import diff_row, measure_tone
from until.console import force_utf8

DEFAULT_OUT = Path("_until_work/tone_ab/report.md")
_FLAG = "UNTIL_TONE_REGISTER"


def _run_case(case, workdir: Path, cfg: Config, flag: str):
    """한 케이스를 주어진 플래그 상태로 1회 실행. 실패는 (None, 사유)."""
    import until.pipeline as pl
    from until.context import tone as tonemod

    src = case.write(workdir)
    previous = os.environ.get(_FLAG)
    os.environ[_FLAG] = flag
    # 페르소나는 실행자 개인 파일을 쓰지 않는다 — 비교 기준선이 사람마다 달라진다.
    tonemod.set_persona_path_override(workdir / "persona.json")
    try:
        return pl.run([str(src)], cfg), ""
    except Exception as exc:  # 한 케이스 실패가 리포트 전체를 막지 않는다
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        tonemod.set_persona_path_override(None)
        if previous is None:
            os.environ.pop(_FLAG, None)
        else:
            os.environ[_FLAG] = previous


def _table(rows, headers):
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
    return "\n".join(lines)


def _body_block(title: str, body: str, limit: int) -> str:
    text = (body or "").strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + f"\n…(이하 {len(body) - limit}자 생략)"
    return f"**{title}**\n\n```text\n{text or '(본문 없음)'}\n```"


def build_report(keys, cfg: Config, excerpt: int) -> str:
    from until.context.tone import REGISTER_PRESETS, resolve_tone_spec

    cases = tone_cases(keys)
    summary_rows, sections = [], []
    mismatches, failures, changed = [], [], 0

    for case in cases:
        with tempfile.TemporaryDirectory(prefix="tone_ab_") as d:
            # off/on은 각자 임시 폴더를 쓴다 — 같은 폴더를 쓰면 두 실행이 서로의
            # persona.json·입력 파일을 덮어써 비교가 오염된다.
            off_dir, on_dir = Path(d) / "off", Path(d) / "on"
            off_dir.mkdir(parents=True, exist_ok=True)
            on_dir.mkdir(parents=True, exist_ok=True)
            off, off_err = _run_case(case, off_dir, cfg, "0")
            on, on_err = _run_case(case, on_dir, cfg, "1")

        if off is None or on is None:
            reason = off_err or on_err or "unknown"
            failures.append((case.key, reason))
            summary_rows.append({"케이스": case.key, "레지스터": "—",
                                 "출처": "—", "기대": case.expect_register,
                                 "본문 변화": f"실행 실패 — {reason[:40]}"})
            continue

        register = on.tone_register or "(없음)"
        if register != case.expect_register:
            mismatches.append((case.key, case.expect_register, register))
        tone = resolve_tone_spec(register) if register in REGISTER_PRESETS else None

        off_body = (off.final_draft or off.draft).body
        on_body = (on.final_draft or on.draft).body
        same = off_body == on_body
        if not same:
            changed += 1

        m_off, m_on = measure_tone(off_body, tone), measure_tone(on_body, tone)
        summary_rows.append({
            "케이스": case.key,
            "레지스터": register + ("" if register == case.expect_register
                                    else f" ⚠(기대 {case.expect_register})"),
            "출처": on.tone_source or "—",
            "기대": case.expect_register,
            "본문 변화": "동일" if same else "다름",
        })

        detail = diff_row(m_off, m_on)
        rows = [dict({"구분": "off"}, **m_off.to_row()),
                dict({"구분": "on"}, **m_on.to_row()),
                dict({"구분": "Δ"}, **detail)]
        headers = ["구분", "글자", "문장", "관측 종결", "규격 준수", "겸양",
                   "완충어", "안부", "이모지", "금지어", "결정"]
        # Δ 행은 '관측 종결' 대신 전이 표기를 쓴다 — 헤더를 맞춰 준다.
        rows[2]["관측 종결"] = detail.get("종결", "")
        sections.append("\n\n".join([
            f"### {case.key} — {case.title}",
            f"- 확정 레지스터: `{register}` (출처 {on.tone_source or '—'}) "
            f"· 기대 `{case.expect_register}`"
            + ("" if register == case.expect_register else "  ⚠ **불일치**"),
            f"- 라우팅 전략: `{getattr(on.assignment_route, 'strategy', '—')}` "
            f"· 과제 유형: `{on.spec.get('task_type', '—')}`",
            _table(rows, headers),
            _body_block("ToneSpec off", off_body, excerpt),
            _body_block("ToneSpec on", on_body, excerpt),
            "<details><summary>주입된 톤 규격</summary>\n\n```text\n"
            + (on.tone_block or "(없음)") + "\n```\n</details>",
        ]))

    head = [
        "# 톤 레지스터 A/B 리포트",
        "",
        f"- 백엔드: `{cfg.backend}` · 케이스 {len(cases)}건 "
        f"· 본문이 달라진 케이스 {changed}건",
        f"- 레지스터 기대-실제 불일치 {len(mismatches)}건 · 실행 실패 {len(failures)}건",
    ]
    if cfg.backend == "mock":
        head.append(
            "- ⚠️ mock 백엔드는 프롬프트와 무관하게 결정적 응답을 낸다. "
            "**본문이 off/on 동일한 것이 정상**이며, 이 실행은 하니스·레지스터 확정·"
            "지표 계산을 검증한다. 톤 변화 자체는 라이브 백엔드로 측정할 것.")
    if mismatches:
        head.append("")
        head.append("## 레지스터 불일치 (톤 매핑 또는 라우팅을 봐야 한다)")
        head.append(_table(
            [{"케이스": k, "기대": e, "실제": a} for k, e, a in mismatches],
            ["케이스", "기대", "실제"]))
    if failures:
        head.append("")
        head.append("## 실행 실패")
        for key, reason in failures:
            head.append(f"- `{key}` — {reason}")
    head += ["", "## 요약", _table(
        summary_rows, ["케이스", "레지스터", "출처", "본문 변화"]), "", "## 케이스별 상세"]
    return "\n\n".join(["\n".join(head)] + sections) + "\n"


def main(argv=None) -> int:
    # Windows 기본 cp949 콘솔에서도 기호(—·⚠ 등) 때문에 실행이 죽지 않게 한다.
    force_utf8()
    ap = argparse.ArgumentParser(description="ToneSpec 적용 전후 side-by-side 리포트")
    ap.add_argument("keys", nargs="*", help=f"케이스 키(생략=전체). 목록: {', '.join(case_keys())}")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=f"리포트 저장 경로(기본 {DEFAULT_OUT})")
    ap.add_argument("--backend", default=os.getenv("UNTIL_BACKEND", "mock"),
                    help="mock(기본) | local | anthropic")
    ap.add_argument("--excerpt", type=int, default=1200, help="본문 발췌 글자 수(기본 1200)")
    args = ap.parse_args(argv)

    unknown = [k for k in args.keys if k not in case_keys()]
    if unknown:
        print(f"알 수 없는 케이스 키: {', '.join(unknown)}")
        print(f"사용 가능: {', '.join(case_keys())}")
        return 2

    cfg = Config(backend=args.backend)
    report = build_report(args.keys, cfg, args.excerpt)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    for line in report.splitlines():
        if line.startswith("- ") or line.startswith("# "):
            print(line)
        if line.startswith("## 케이스별 상세"):
            break
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
