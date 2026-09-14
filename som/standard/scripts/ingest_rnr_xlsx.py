"""Turn an existing SOM R&R workbook into a somdoc IR.

This is a starter, not a standard. It reads the layout the 2026 workbook
actually uses:

  sheet 1              roster, one row per member, headers on row 1
  "전략 핵심 지점"      구분 | 내용 | 담당 팀/인물
  "... 상세" sheets    transposed: 구분 on column A, one column per member

Everything it can compute, it computes and marks `derived`. Everything that
requires judgement -- above all the decision request -- it leaves as a TODO for
the author to fill, because a document that invents its own ask is worthless.

    python ingest_rnr_xlsx.py --xlsx "<file>.xlsx" --out ir/som-rnr.somdoc.json \
        [--as-of 2026-09-09] [--version v1.0]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path

from openpyxl import load_workbook

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ALLOC_RE = re.compile(r"\d+")
PCT_IN_TASK_RE = re.compile(r"\((\d+)%\)")


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _rows(ws) -> list[list]:
    out = []
    for row in ws.iter_rows(values_only=True):
        vals = [_s(v) for v in row]
        while vals and not vals[-1]:
            vals.pop()
        if vals:
            out.append(vals)
    return out


# ---------------------------------------------------------------------------
def parse_allocation(text: str) -> tuple[list[int] | None, str]:
    """'30/25/20/15/10' -> ([30,25,20,15,10], 'numeric');
    '균형 배치' -> (None, 'unspecified').

    The distinction matters: ATTEST checks that a numeric allocation sums to
    100, and reports the unspecified ones rather than quietly passing them.
    """
    t = _s(text)
    if not t:
        return None, "missing"
    if "/" in t and ALLOC_RE.search(t):
        nums = [int(x) for x in ALLOC_RE.findall(t)]
        return nums, "numeric"
    return None, "unspecified"


def roster_table(ws) -> tuple[dict, list[dict]]:
    rows = _rows(ws)
    headers = rows[0]
    members = []
    for r in rows[1:]:
        rec = {headers[i]: (r[i] if i < len(r) else "") for i in range(len(headers))}
        if not _s(rec.get("팀원")):
            continue
        members.append(rec)

    # 13 real columns. Long free text wraps; identifiers stay on one line.
    align_for = {
        "순번": "num", "팀원": "text", "직책": "wrap", "Sub Team": "text",
        "핵심 역할": "wrap", "시간 비중": "text",
    }
    columns = []
    for h in headers:
        columns.append({
            "key": h,
            "label": h,
            "align": align_for.get(h, "wrap"),
            "core": h in ("순번", "팀원", "직책", "Sub Team", "핵심 역할", "시간 비중"),
            "sticky": h == "팀원",
        })

    data = []
    for m in members:
        row = []
        for h in headers:
            v = m.get(h, "")
            row.append(int(v) if h == "순번" and _s(v).isdigit() else v)
        data.append(row)

    block = {
        "type": "table",
        "title": "인원별 R&R 매트릭스",
        "row_grain": "행 1개 = 팀원 1명",
        "caption": "핵심 과업 뒤의 괄호 백분율은 해당 과업의 시간 비중이다. "
                   "시간 비중 열이 숫자가 아닌 인원은 배분이 아직 확정되지 않았다.",
        "columns": columns,
        "rows": data,
        "filter_keys": ["팀원", "직책", "Sub Team", "핵심 역할"],
    }
    return block, members


def strategy_table(ws) -> dict:
    rows = _rows(ws)
    headers = rows[0]
    return {
        "type": "table",
        "title": "2026 전략 핵심 지점",
        "row_grain": "행 1개 = 전략 항목 1건",
        "columns": [
            {"key": headers[0], "label": headers[0], "align": "text", "sticky": True},
            {"key": headers[1], "label": headers[1], "align": "wrap"},
            {"key": headers[2], "label": headers[2] if len(headers) > 2 else "담당", "align": "wrap"},
        ],
        "rows": [[r[0] if len(r) > 0 else "", r[1] if len(r) > 1 else "",
                  r[2] if len(r) > 2 else ""] for r in rows[1:]],
    }


def subteam_matrix(ws, title: str) -> dict:
    """The detail sheets are already transposed: 구분 down column A."""
    rows = _rows(ws)
    header = rows[0]
    people = [h for h in header[1:] if h]
    labels = [r[0] for r in rows[1:] if _s(r[0])]
    cells = []
    for r in rows[1:]:
        if not _s(r[0]):
            continue
        cells.append([(r[i + 1] if i + 1 < len(r) else "") for i in range(len(people))])
    return {
        "type": "matrix",
        "title": title,
        "corner": "구분",
        "columns": people,
        "rows": labels,
        "cells": cells,
        "caption": f"{len(people)}명 · {len(labels)}개 항목. 열이 인원, 행이 항목이다.",
    }


# ---------------------------------------------------------------------------
def derived_tiles(members: list[dict]) -> dict:
    subteams = Counter(_s(m.get("Sub Team")) for m in members if _s(m.get("Sub Team")))
    task_total = 0
    for m in members:
        for k in ("핵심 과업 1", "핵심 과업 2", "핵심 과업 3"):
            if _s(m.get(k)):
                task_total += 1
    unspecified = sum(
        1 for m in members if parse_allocation(m.get("시간 비중", ""))[1] != "numeric"
    )
    return {
        "type": "kpi_tiles",
        "title": "한눈에",
        "derived": True,
        "items": [
            {"label": "총 인원", "value": len(members), "unit": "명"},
            {"label": "Sub Team", "value": len(subteams), "unit": "개"},
            {"label": "정의된 핵심 과업", "value": task_total, "unit": "건"},
            {"label": "시간 비중 미확정", "value": unspecified, "unit": "명",
             "state": "warn" if unspecified else "ok",
             "note": "숫자 배분이 없는 인원" if unspecified else "전원 확정"},
        ],
    }


def subteam_table(members: list[dict]) -> dict:
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for m in members:
        groups.setdefault(_s(m.get("Sub Team")), []).append(m)
    rows = []
    for team, mem in groups.items():
        leads = [_s(x.get("팀원")) for x in mem
                 if re.search(r"Director|Senior Manager|Assistant Manager|Manager", _s(x.get("직책")))]
        rows.append([team, len(mem), ", ".join(_s(x.get("팀원")) for x in mem),
                     ", ".join(leads) or "-"])
    return {
        "type": "table",
        "title": "조직 스냅샷",
        "derived": True,
        "row_grain": "행 1개 = Sub Team 1개",
        "columns": [
            {"key": "team", "label": "Sub Team", "align": "text", "sticky": True},
            {"key": "n", "label": "인원", "align": "num", "suffix": "명"},
            {"key": "members", "label": "구성", "align": "wrap"},
            {"key": "lead", "label": "리드 직책 보유", "align": "wrap"},
        ],
        "rows": rows,
    }


def allocation_chart(members: list[dict]) -> dict:
    groups = Counter(_s(m.get("Sub Team")) for m in members if _s(m.get("Sub Team")))
    cats = list(groups.keys())
    vals = [groups[c] for c in cats]
    biggest = max(groups, key=lambda k: groups[k])
    return {
        "type": "chart",
        "kind": "hbar",
        "derived": True,
        "takeaway": f"인원의 {groups[biggest]}/{len(members)}이 {biggest} 한 팀에 있다.",
        "categories": cats,
        "series": [{"name": "인원", "values": vals}],
        "y_unit": "명",
        "label_width": 260,
        "src": "sheet:SOM Team 전체 현황#Sub Team",
    }


def coverage_heatgrid(members: list[dict]) -> dict:
    """Sub Team x 과업 슬롯 채움. 0 은 공백, 1인 의존은 과집중으로 표시."""
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for m in members:
        groups.setdefault(_s(m.get("Sub Team")), []).append(m)
    slots = ["핵심 과업 1", "핵심 과업 2", "핵심 과업 3"]
    rows, cells, flags = [], [], []
    for team, mem in groups.items():
        rows.append(team)
        line, fline = [], []
        for slot in slots:
            n = sum(1 for x in mem if _s(x.get(slot)))
            line.append(n)
            fline.append("gap" if n == 0 else ("conc" if n == 1 and len(mem) > 1 else None))
        cells.append(line)
        flags.append(fline)
    return {
        "type": "heatgrid",
        "title": "과업 슬롯 커버리지",
        "derived": True,
        "columns": slots,
        "rows": rows,
        "cells": cells,
        "flags": flags,
        "caption": "숫자는 해당 슬롯을 채운 인원 수다. 0은 담당 공백, "
                   "복수 인원 팀에서 1은 단일 인원 의존이다.",
    }


def risks_from_strategy(ws) -> dict | None:
    for r in _rows(ws)[1:]:
        if "리스크" in _s(r[0]):
            body = _s(r[1] if len(r) > 1 else "")
            owner = _s(r[2] if len(r) > 2 else "") or "미지정"
            items = []
            for part in re.split(r"[①②③④⑤]", body):
                part = part.strip(" ·,")
                if len(part) > 4:
                    items.append({
                        "risk": part,
                        "impact": "원본 워크북에 영향도가 기재되지 않았다.",
                        "mitigation": "TODO 완화 방안을 작성하라.",
                        "owner": owner,
                        "state": "warn",
                        "state_label": "미완",
                    })
            return {"type": "risks", "title": "리스크 · 전제", "items": items} if items else None
    return None


# ---------------------------------------------------------------------------
def build(xlsx: Path, as_of: str, version: str) -> dict:
    wb = load_workbook(xlsx, read_only=True, data_only=True)
    names = wb.sheetnames
    roster_ws = wb[names[0]]
    roster_block, members = roster_table(roster_ws)

    strat_name = next((n for n in names if "전략 핵심" in n), None)
    detail_names = [n for n in names if n.endswith("상세") or "전담" in n]

    sections = [
        {
            "id": "decision",
            "title": "결정 요청",
            "title_en": "Decision Requests",
            "intro": "이 문서로 승인받을 사항이다. 나머지 절은 근거다.",
            "blocks": [{
                "type": "decision_request",
                "items": [{
                    "ask": "TODO 승인받을 사항을 한 문장으로 쓰라. "
                           "예: 시간 비중이 미확정인 인원의 배분을 확정한다.",
                    "rationale": "TODO 근거를 쓰라. 파생 지표와 표를 인용하라.",
                    "owner": "TODO",
                }],
            }],
        },
        {
            "id": "overview",
            "title": "한눈에",
            "derived": True,
            "blocks": [derived_tiles(members), allocation_chart(members)],
        },
        {
            "id": "org",
            "title": "조직 스냅샷",
            "blocks": [subteam_table(members)],
        },
    ]
    if strat_name:
        sections.append({
            "id": "strategy",
            "title": "2026 전략 핵심 지점",
            "blocks": [strategy_table(wb[strat_name])],
        })
    sections.append({
        "id": "matrix",
        "title": "인원별 R&R 매트릭스",
        "intro": "원본 워크북의 전체 현황 시트를 그대로 옮긴 것이다.",
        "blocks": [roster_block],
    })
    for n in detail_names:
        sections.append({
            "id": "detail-" + re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-"),
            "title": n,
            "blocks": [subteam_matrix(wb[n], n)],
        })
    sections.append({
        "id": "coverage",
        "title": "롤 커버리지",
        "derived": True,
        "blocks": [coverage_heatgrid(members)],
    })
    if strat_name:
        rb = risks_from_strategy(wb[strat_name])
        if rb:
            sections.append({"id": "risks", "title": "리스크 · 전제", "blocks": [rb]})
    sections.append({
        "id": "sources",
        "title": "부록 — 원천 데이터",
        "blocks": [{
            "type": "appendix_source",
            "note": "이 문서의 모든 표는 아래 워크북에서 파생되었다.",
            "items": [{
                "name": xlsx.stem,
                "path": str(xlsx),
                "sheet": ", ".join(names),
                "rows": len(members),
                "as_of": as_of,
            }],
        }],
    })

    wb.close()
    return {
        "sds_version": 1,
        "doc_type": "rnr",
        "theme": "paper",
        "lang": "ko",
        "bilingual": False,
        "docmeta": {
            "title": "2026 SOM Team R&R",
            "subtitle": "역할과 책임 정의 · 15명 · 4 Sub Team",
            "slug": "SOM_RnR",
            "org": "SOM Unit",
            "version": version,
            "as_of": as_of,
            "author": "Kyeong Je Kim",
            "approver": "TODO",
            "classification": "Internal",
        },
        "metrics": {},
        "sections": sections,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--as-of", default="2026-09-09")
    ap.add_argument("--version", default="v1.0")
    a = ap.parse_args()

    ir = build(Path(a.xlsx), a.as_of, a.version)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(ir, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")

    n_blocks = sum(len(s.get("blocks") or []) for s in ir["sections"])
    print(f"ingest_rnr_xlsx: {len(ir['sections'])} sections, {n_blocks} blocks -> {out}")
    print("  TODO 가 남아 있다. 결정 요청과 리스크 완화 방안은 사람이 채워야 한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
