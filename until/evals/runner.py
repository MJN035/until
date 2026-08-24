"""eval 러너 — 골든셋을 until 파이프라인과 raw LLM(비교군)에 돌려 지표 표 출력.

- 생성은 설정된 백엔드(UNTIL_BACKEND)로, 채점은 전부 결정적(metrics.py).
- raw 비교군: 같은 입력을 파이프라인 없이 한 번에 던진 출력(존재 증명용 격차).
- mock 백엔드에서도 하니스는 완주한다(수치는 라이브 키로 측정해야 의미).
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import List, Optional

from ..capture.ingest import ingest_file
from ..config import Config
from ..llm.base import build_client
from .goldens import GoldenCase, golden_cases
from .metrics import CaseScore, form_slot_count, score_output

_RAW_PROMPT = (
    "다음 대학 과제를 수행해 제출물 본문을 작성하라. 양식이 있으면 그 구조를 "
    "따르고, 분량 요건을 지켜라.\n\n[과제]\n{assignment}\n\n[참고]\n{note}")


class _Counting:
    """LLM 호출 수 계측 래퍼(토큰 프록시). shared를 주면 래퍼들 사이에 누적."""

    def __init__(self, inner, shared: Optional[dict] = None):
        self.inner = inner
        self.calls = 0
        self._shared = shared

    def complete(self, system, user, **kw):
        self.calls += 1
        if self._shared is not None:
            self._shared["n"] += 1
        return self.inner.complete(system, user, **kw)


def _run_until(case: GoldenCase, files: List[str], cfg: Config,
               form_text: str, source_text: str, workdir: Path,
               variant: str = "legacy") -> CaseScore:
    import until.pipeline as pl
    from .. import profile as prof
    prof.set_profile_path_override(workdir / f"profile-{variant}.json")
    try:
        prof.save_profile(case.profile)
        # pipeline.run()은 build_client를 여러 번 부른다(메인 llm + 요건추출
        # req_llm). 래퍼를 갈아끼우면 앞 클라이언트의 초안·reask 호출이 집계에서
        # 빠지므로, 공유 카운터로 모든 클라이언트에 걸쳐 누적한다.
        counter = {"n": 0}
        orig = pl.build_client

        def patched(backend, model=None):
            return _Counting(orig(backend, model), shared=counter)

        pl.build_client = patched
        try:
            res = pl.run(files, cfg)
        finally:
            pl.build_client = orig
        body = res.draft.body if res.draft else ""
        s = score_output(case.key, variant, body,
                         per_item_range=case.per_item_range,
                         n_items_expected=case.n_items_expected,
                         whole_min=case.whole_min, form_text=form_text,
                         profile=case.profile, source_text=source_text,
                         elements=getattr(res, "content_elements", None))
        s.reasks = res.guard.reasks if res.guard else 0
        s.llm_calls = counter["n"]
        s.generated_body = body
        # 원본 주입 성공률 — 채운 자리 / 채울 수 있는 자리.
        if case.has_form:
            from ..report import write_filled_form
            got = write_filled_form(res, workdir / "filled.hwpx")
            slots = form_slot_count(form_text)
            if got and slots:
                s.injection = min(1.0, got[1].total / slots)
                s.injected = got[1].describe()
            else:
                s.injection = 0.0
        return s
    finally:
        prof.set_profile_path_override(None)


def _run_raw(case: GoldenCase, cfg: Config, form_text: str,
             assignment_text: str, note_text: str) -> CaseScore:
    llm = _Counting(build_client(cfg.backend, cfg.model))
    text = llm.complete(
        "당신은 대학생의 과제를 대신 작성하는 AI다.",
        _RAW_PROMPT.format(assignment=assignment_text[:6000], note=note_text[:3000]),
        tag="raw-baseline").text
    from ..understanding.requirements import extract_content_elements
    elems = extract_content_elements(
        {"requirements": [assignment_text, note_text]}, None, llm=None)
    s = score_output(case.key, "raw", text,
                     per_item_range=case.per_item_range,
                     n_items_expected=case.n_items_expected,
                     whole_min=case.whole_min, form_text=form_text,
                     profile=case.profile,
                     source_text=assignment_text + note_text,
                     elements=elems)
    s.llm_calls = llm.calls
    s.generated_body = text
    s.injection = None  # 파이프라인이 없으니 원본 주입 자체가 불가
    return s


def _fmt_pct(v: Optional[float]) -> str:
    return "-" if v is None else f"{v * 100:4.0f}%"


def _fmt_bool(v) -> str:
    return "-" if v is None else ("O" if v else "X")


def run_all(cfg: Optional[Config] = None, keys: Optional[List[str]] = None):
    cfg = cfg or Config()
    rows: List[CaseScore] = []
    cases = [c for c in golden_cases() if not keys or c.key in keys]
    t0 = time.time()
    for case in cases:
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            files = case.build(d)
            form_text = ""
            note_text = ""
            assignment_text = ""
            for f in files:
                text = ingest_file(f, backend="basic").text
                if f.endswith((".hwpx", ".docx")):
                    form_text = text
                    assignment_text = assignment_text or text
                else:
                    if not assignment_text:
                        assignment_text = text
                    else:
                        note_text += text + "\n"
            source_text = assignment_text + "\n" + note_text
            # 두 생성 경로(legacy 통짜 / unit 단위별)를 같은 입력으로 비교(10단계).
            for variant in ("legacy", "unit"):
                vcfg = Config(**{**cfg.__dict__})
                vcfg.pipeline_mode = variant
                try:
                    rows.append(_run_until(case, files, vcfg, form_text,
                                           source_text, d, variant=variant))
                except Exception as e:  # 케이스 하나가 전체 eval을 막지 않게
                    s = CaseScore(case.key, variant)
                    s.notes.append(f"실행 실패: {e}")
                    rows.append(s)
            try:
                rows.append(_run_raw(case, cfg, form_text,
                                     assignment_text, note_text))
            except Exception as e:
                s = CaseScore(case.key, "raw")
                s.notes.append(f"실행 실패: {e}")
                rows.append(s)
            for row in rows[-3:]:
                if row.key == case.key:
                    row.assignment_type = case.assignment_type
                    row.title = case.title
                    row.assignment_text = assignment_text + "\n" + note_text
    return rows, time.time() - t0, cases


def render_table(rows: List[CaseScore], elapsed: float, cases) -> str:
    by_key = {c.key: c for c in cases}
    out = []
    out.append(f"{'케이스':<16} {'경로':<7} {'항목분량':>8} {'양식':>5} {'주입':>5} "
               f"{'구체성':>6} {'공허':>4} {'커버':>5} {'무근거':>6} {'환각':>4} "
               f"{'reask':>5} {'호출':>4}  비고")
    out.append("-" * 104)
    for r in rows:
        note = " ".join(r.notes)[:26]
        out.append(
            f"{r.key:<16} {r.variant:<7} {_fmt_pct(r.item_compliance):>8} "
            f"{_fmt_pct(r.form_fidelity):>5} {_fmt_pct(r.injection):>5} "
            f"{_fmt_pct(r.specificity):>6} {r.n_empty:>4} "
            f"{_fmt_pct(r.coverage):>5} {r.ungrounded:>6} "
            f"{r.hallucinated_cells:>4} {r.reasks:>5} {r.llm_calls:>4}  {note}")
    # 요약(경로별 평균) — legacy/unit vs raw 격차가 존재 증명(10단계 비교).
    for variant in ("legacy", "unit", "raw"):
        vs = [r for r in rows if r.variant == variant]
        if not vs:
            continue
        def avg(field, _vs=vs):
            vals = [getattr(r, field) for r in _vs if getattr(r, field) is not None]
            return sum(vals) / len(vals) if vals else None
        out.append("-" * 104)
        out.append(
            f"{'평균(' + variant + ')':<16} {'':<7} {_fmt_pct(avg('item_compliance')):>8} "
            f"{_fmt_pct(avg('form_fidelity')):>5} {_fmt_pct(avg('injection')):>5} "
            f"{_fmt_pct(avg('specificity')):>6} {sum(r.n_empty for r in vs):>4} "
            f"{_fmt_pct(avg('coverage')):>5} {sum(r.ungrounded for r in vs):>6} "
            f"{sum(r.hallucinated_cells for r in vs):>4} "
            f"{sum(r.reasks for r in vs):>5} {sum(r.llm_calls for r in vs):>4}")
    out.append(f"\n케이스 {len(by_key)}개 × 3경로(legacy/unit/raw) · {elapsed:.1f}s · "
               "채점은 전부 결정적(LLM 판정 0)")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import sys
    from .grading import (aggregate_grades, load_grades, render_grade_table,
                          write_grading_sheet)

    parser = argparse.ArgumentParser(description="Until 품질 eval 하네스")
    parser.add_argument("keys", nargs="*", help="실행할 골든 케이스 키")
    parser.add_argument("--grade-out", type=Path,
                        help="사람 채점용 grading.html 출력 디렉터리")
    parser.add_argument("--grade-in", type=Path,
                        help="내보낸 채점 JSON의 유형별 제출 가능 비율 집계")
    args = parser.parse_args(list(argv if argv is not None else sys.argv[1:]))
    if args.grade_in:
        print(render_grade_table(aggregate_grades(load_grades(args.grade_in))))
        return 0
    keys = args.keys or None
    if keys:
        known = {case.key for case in golden_cases()}
        unknown = sorted(set(keys) - known)
        if unknown:
            parser.error("알 수 없는 케이스: " + ", ".join(unknown)
                         + ". 사용 가능: " + ", ".join(sorted(known)))
    cfg = Config()
    print(f"[evals] backend={cfg.backend} model={cfg.model}"
          + (" — mock은 하니스 검증용(실수치는 라이브 키로)" if cfg.backend == "mock" else ""))
    rows, elapsed, cases = run_all(cfg, keys)
    print(render_table(rows, elapsed, cases))
    if args.grade_out:
        path = write_grading_sheet(rows, args.grade_out)
        print(f"\n[evals] 사람 채점 시트: {path}")
    return 0
