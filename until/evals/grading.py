"""사람 중심 eval 채점 시트 생성과 제출 가능 비율 집계."""
from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from .metrics import CaseScore


def write_grading_sheet(rows: Iterable[CaseScore], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    cards = []
    manifest = []
    for i, row in enumerate(rows):
        rid = f"{row.key}__{row.variant}"
        failed = bool(row.notes and not row.generated_body)
        if failed:
            cards.append(f"""<article class="failed" data-id="{html.escape(rid)}">
<h2>{html.escape(row.title or row.key)} <small>({html.escape(row.variant)})</small></h2>
<p><strong>실행 실패 — 품질 채점에서 제외</strong></p>
<pre>{html.escape(' '.join(row.notes))}</pre></article>""")
            continue
        manifest.append({"id": rid, "assignment_type": row.assignment_type})
        cards.append(f"""<article data-id="{html.escape(rid)}" data-type="{html.escape(row.assignment_type)}">
<h2>{html.escape(row.title)} <small>({html.escape(row.variant)})</small></h2>
<details><summary>과제 지문 보기</summary><pre>{html.escape(row.assignment_text)}</pre></details>
<h3>생성 본문</h3><pre>{html.escape(row.generated_body)}</pre>
<fieldset><legend>제출 가능 수준인가?</legend>
{_radio(rid, 'yes', '예', i)} {_radio(rid, 'partial', '부분', i)} {_radio(rid, 'no', '아니오', i)}
</fieldset><label>메모 <textarea rows="3"></textarea></label></article>""")
    manifest_json = json.dumps(manifest, ensure_ascii=False).replace("<", "\\u003c")
    page = (_PAGE.replace("{{CARDS}}", "\n".join(cards))
            .replace("{{MANIFEST}}", manifest_json))
    path = output_dir / "grading.html"
    path.write_text(page, encoding="utf-8")
    return path


def _radio(rid: str, value: str, label: str, index: int) -> str:
    return (f'<label><input type="radio" name="g{index}" value="{value}" '
            f'data-record="{html.escape(rid)}"> {label}</label>')


def load_grades(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("grades") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError("채점 JSON에는 grades 배열이 필요합니다.")
    expected = payload.get("manifest") if isinstance(payload, dict) else None
    if not isinstance(expected, list):
        raise ValueError("채점 JSON에는 manifest 배열이 필요합니다.")
    valid = {"yes", "partial", "no"}
    if any(not isinstance(r, dict) or r.get("grade") not in valid
           or not r.get("assignment_type") or not r.get("id") for r in records):
        raise ValueError("각 채점에는 id, assignment_type과 yes/partial/no grade가 필요합니다.")
    expected_map = {}
    for item in expected:
        if (not isinstance(item, dict) or not item.get("id")
                or not item.get("assignment_type") or item["id"] in expected_map):
            raise ValueError("manifest 항목은 고유한 id와 assignment_type이 필요합니다.")
        expected_map[str(item["id"])] = str(item["assignment_type"])
    record_ids = [str(r["id"]) for r in records]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("같은 채점 id가 중복되었습니다.")
    if set(record_ids) != set(expected_map):
        raise ValueError("모든 생성물을 빠짐없이 한 번씩 채점해야 합니다.")
    if any(str(r["assignment_type"]) != expected_map[str(r["id"])] for r in records):
        raise ValueError("채점 유형이 manifest와 일치하지 않습니다.")
    return records


def aggregate_grades(records: Iterable[dict]) -> dict[str, dict[str, float | int]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for record in records:
        buckets[str(record["assignment_type"])].append(str(record["grade"]))
    return {kind: {"yes": grades.count("yes"), "partial": grades.count("partial"),
                   "no": grades.count("no"), "total": len(grades),
                   "submission_ready_rate": grades.count("yes") / len(grades)}
            for kind, grades in sorted(buckets.items())}


def render_grade_table(summary: dict[str, dict[str, float | int]]) -> str:
    lines = ["유형                  제출 가능   부분   아니오   제출 가능 비율",
             "-" * 66]
    for kind, values in summary.items():
        lines.append(f"{kind:<21} {values['yes']:>8} {values['partial']:>6} "
                     f"{values['no']:>8} {values['submission_ready_rate'] * 100:>14.1f}%")
    return "\n".join(lines)


_PAGE = """<!doctype html><html lang="ko"><meta charset="utf-8">
<title>Until 사람 채점 시트</title><style>body{font:16px system-ui;max-width:960px;margin:2rem auto;padding:0 1rem}article{border:1px solid #ccc;border-radius:12px;padding:1rem;margin:1rem 0}pre{white-space:pre-wrap;background:#f6f6f6;padding:1rem}label{margin-right:1rem}textarea{display:block;width:100%;box-sizing:border-box}button{padding:.7rem 1rem}</style>
<h1>Until 사람 채점 시트</h1><p>각 생성물을 읽고 제출 가능 수준을 선택하세요.</p>
{{CARDS}}<button id="export">채점 결과 JSON 내보내기</button>
<script>const manifest={{MANIFEST}};document.querySelector('#export').onclick=()=>{const cards=[...document.querySelectorAll('article[data-type]')];const missing=cards.filter(a=>!a.querySelector('input:checked'));if(missing.length){alert(`아직 채점하지 않은 결과가 ${missing.length}개 있습니다.`);missing[0].scrollIntoView();return}const grades=cards.map(a=>{const g=a.querySelector('input:checked');return{id:a.dataset.id,assignment_type:a.dataset.type,grade:g.value,note:a.querySelector('textarea').value}});const b=new Blob([JSON.stringify({manifest,grades},null,2)],{type:'application/json'});const x=document.createElement('a');x.href=URL.createObjectURL(b);x.download='until-grades.json';x.click();URL.revokeObjectURL(x.href)};</script></html>"""
