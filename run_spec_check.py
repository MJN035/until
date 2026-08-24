"""Canvas API 스펙 대조 체크 — until이 의존하는 엔드포인트가 공개 문서에 아직 있는지.

  python run_spec_check.py            # canvas.instructure.com 공개 문서와 대조
  UNTIL_SPEC_BASE=<url> …             # 다른 인스턴스 문서로 대조(예: 학교 호스팅)

Canvas는 인스턴스마다 /doc/api/<resource>.json (Swagger 1.2 형식)으로 API 문서를
서빙한다. 이 스크립트는 until 파이프라인이 실제 호출하는 (리소스, 메서드, 경로,
필수 파라미터)를 그 문서와 대조해, Instructure 쪽 제거·변경(지원중단)을 배포 전에
잡는다. 네트워크가 필요하므로 오프라인 테스트 러너(run_tests.py)와 분리 — CI에선
주 1회 스케줄 잡으로 돈다(.github/workflows/ci.yml의 api-spec).

판정 로직(missing_endpoints·evaluate_docs)은 순수 함수 — tests/test_spec_check.py가
오프라인 검증. fetch 실패 리소스는 '확인 불가(SKIP)'로 분리(exit 0) — 문서를 받았는데
엔드포인트가 사라진 경우만 exit 1.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from until.console import force_utf8

DEFAULT_BASE = "https://canvas.instructure.com/doc/api"

#: until이 의존하는 Canvas 엔드포인트 — (문서 리소스, 메서드, 문서 경로, 필수 파라미터).
#: 근거 코드: capture/sources/canvas_api.py (list_courses·list_assignments·
#: fetch_assignment·list_course_files·list_modules·list_my_submissions·
#: get_self_profile). 경로 표기는 Canvas 문서의 플레이스홀더 그대로 쓴다.
REQUIRED: list[tuple] = [
    ("courses", "GET", "/v1/courses", ()),
    ("assignments", "GET", "/v1/courses/{course_id}/assignments", ()),
    ("assignments", "GET", "/v1/courses/{course_id}/assignments/{id}", ()),
    ("submissions", "GET", "/v1/courses/{course_id}/students/submissions",
     ("student_ids",)),
    ("files", "GET", "/v1/courses/{course_id}/files", ()),
    ("modules", "GET", "/v1/courses/{course_id}/modules", ()),
    ("users", "GET", "/v1/users/{user_id}/profile", ()),
]


def _norm_param(name: str) -> str:
    """문서 파라미터명 정규화 — 배열 표기 'student_ids[]' ↔ 'student_ids' 동일시."""
    return (name or "").strip().rstrip("[]")


def missing_endpoints(docs_by_resource: dict, required: list[tuple]) -> list[str]:
    """문서 dict(리소스명 → Swagger JSON)와 대조해 어긋난 항목 메시지 목록을 돌려준다.

    결정적·네트워크 없음. 빈 목록 = 전부 확인됨.
    """
    problems: list[str] = []
    for resource, method, path, params in required:
        doc = docs_by_resource.get(resource)
        if not isinstance(doc, dict):
            problems.append(f"{resource}: 문서 자체를 읽지 못함")
            continue
        ops = []
        for api in doc.get("apis") or []:
            if not isinstance(api, dict) or api.get("path") != path:
                continue
            ops = [o for o in api.get("operations") or []
                   if isinstance(o, dict)
                   and (o.get("method") or o.get("httpMethod") or "").upper() == method]
            if ops:
                break
        if not ops:
            problems.append(f"{resource}: {method} {path} 가 문서에서 사라짐(지원중단?)")
            continue
        have = {_norm_param(p.get("name")) for p in ops[0].get("parameters") or []
                if isinstance(p, dict)}
        for want in params:
            if _norm_param(want) not in have:
                problems.append(
                    f"{resource}: {method} {path} 에 파라미터 '{want}' 가 없음")
    return problems


def evaluate_docs(docs_by_resource: dict, required: list[tuple]) -> tuple:
    """대조 결과를 (어긋난 항목, 확인 불가 리소스)로 분리 — 순수·결정적.

    문서를 아예 못 받은(None) 리소스는 '스펙 제거'가 아니라 '확인 불가(SKIP)'다.
    네트워크 일시 실패가 exit 1 빨간불(오탐)이 되지 않게 판정에서 뺀다 —
    주 1회 잡이라 다음 주기에 자연히 재확인된다.
    """
    skipped = sorted({r for r, *_ in required
                      if not isinstance(docs_by_resource.get(r), dict)})
    checked = [t for t in required if t[0] not in skipped]
    return missing_endpoints(docs_by_resource, checked), skipped


def fetch_docs(base: str, resources: list[str], attempts: int = 3,
               backoff: float = 1.5) -> dict:
    """리소스별 /doc/api/<r>.json 다운로드(일시 실패 대비 재시도·짧은 백오프).

    끝내 실패한 리소스는 None — evaluate_docs가 SKIP으로 분리한다."""
    out: dict = {}
    for r in resources:
        url = f"{base.rstrip('/')}/{r}.json"
        out[r] = None
        for i in range(attempts):
            try:
                with urllib.request.urlopen(url, timeout=30) as resp:
                    out[r] = json.loads(resp.read().decode("utf-8"))
                break
            except Exception as e:  # 리소스별 실패를 개별 보고(전체 중단 없음)
                print(f"[warn] {url} 조회 실패({i + 1}/{attempts}): {e}")
                if i < attempts - 1:
                    time.sleep(backoff * (i + 1))
    return out


def main() -> int:
    # Windows 기본 cp949 콘솔에서도 기호(—·⚠ 등) 때문에 실행이 죽지 않게 한다.
    force_utf8()
    base = (os.getenv("UNTIL_SPEC_BASE") or DEFAULT_BASE).strip()
    resources = sorted({r for r, *_ in REQUIRED})
    print(f"Canvas API 문서 대조: {base} (리소스 {len(resources)}개, "
          f"엔드포인트 {len(REQUIRED)}개)")
    problems, skipped = evaluate_docs(fetch_docs(base, resources), REQUIRED)
    for r in skipped:
        print(f"  ? {r}: 문서를 가져오지 못함 — 확인 불가(SKIP, 다음 주기에 재확인)")
    if problems:
        print("\n어긋난 항목:")
        for p in problems:
            print(f"  ✗ {p}")
        print("\n→ capture/sources/canvas_api.py 해당 경로를 점검하세요.")
        return 1
    if skipped:
        print(f"확인된 리소스는 전부 정상 — SKIP {len(skipped)}건은 네트워크 문제로 "
              "간주(오탐 방지, exit 0).")
    else:
        print("전부 확인됨 — until 의존 엔드포인트가 문서에 그대로 있습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
