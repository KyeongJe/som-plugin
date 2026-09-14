"""Table renderer.

Behaviour inherited from a template that already survived real use. The three
things that look fussy and are not:

  1. Row grain prints above the table, always. Misreading what one row means is
     the most common way one of these documents gets misused.
  2. Filtering reads a per-row `data-f` JSON array built only from the columns
     the author nominated. Never all cell text. Searching "1100" must match the
     SALESORG column and not a PO number that happens to contain 1100.
  3. A table wider than 14 columns opens on its core columns with a toggle.
     Print always shows every column, because paper has no toggle.
"""
from __future__ import annotations

import json
from typing import Any

from . import Ctx, _title, block
from .. import ir as IR


def _cell(value: Any, col: dict, ctx: Ctx) -> str:
    align = col.get("align", "text")
    if align == "badge":
        state = "na"
        label = value
        if isinstance(value, dict):
            state = value.get("state", "na")
            label = value.get("label", state)
        elif isinstance(value, str) and value in ("ok", "warn", "crit", "info", "na"):
            state = value
        return (f'<td><span class="sds-badge" data-s="{ctx.attr(state)}">'
                f"{ctx.raw_text(label)}</span></td>")

    if align == "num":
        if isinstance(value, int):
            txt = f"{value:,}"
        elif isinstance(value, float):
            digits = col.get("digits", 2)
            txt = f"{value:,.{digits}f}"
        else:
            txt = ctx.raw_text(value)
        suffix = ctx.raw_text(col.get("suffix") or "")
        return f'<td class="num">{txt}{suffix}</td>'

    cls = ' class="wrap"' if align == "wrap" else ""
    return f"<td{cls}>{ctx.text(value)}</td>"


def _filter_payload(row: list, cols: list[dict], filter_idx: list[int]) -> str:
    """The only thing the client-side search ever looks at."""
    vals = []
    for i in filter_idx:
        if i >= len(row):
            continue
        v = row[i]
        if isinstance(v, dict):
            v = v.get("label", v.get("state", ""))
        vals.append("" if v is None else str(v))
    return json.dumps(vals, ensure_ascii=False, separators=(",", ":"))


@block("table")
def render_table(b: dict, ctx: Ctx) -> str:
    cols: list[dict] = b.get("columns") or []
    rows: list[list] = b.get("rows") or []
    tid = ctx.n("t")
    wide = len(cols) > IR.WIDE_TABLE_COLS
    # In a wide table the non-core columns start hidden.
    core_flags = [bool(c.get("core", True)) for c in cols] if wide else [True] * len(cols)

    # Which columns feed the search box. Authors nominate them; otherwise fall
    # back to columns that actually discriminate (many distinct values).
    named = b.get("filter_keys")
    if named:
        by_key = {c.get("key"): i for i, c in enumerate(cols)}
        filter_idx = [by_key[k] for k in named if k in by_key]
    else:
        filter_idx = [
            i for i in range(len(cols))
            if cols[i].get("align") != "num" and IR.column_is_searchable(rows, i)
        ]
    searchable = bool(filter_idx) and b.get("searchable", True) and len(rows) >= 8

    # --- head ---
    ths = []
    for i, c in enumerate(cols):
        cls = []
        if c.get("align") == "num":
            cls.append("num")
        if c.get("sticky"):
            cls.append("sticky-col")
        extra = "" if core_flags[i] else ' data-col-extra="1"'
        label_ko = ctx.raw_text(c.get("label"))
        label_en = c.get("label_en")
        label = (f'<span class="sds-ko">{label_ko}</span>'
                 f'<span class="sds-en">{ctx.raw_text(label_en)}</span>') if label_en else label_ko
        c_attr = f' class="{" ".join(cls)}"' if cls else ""
        ths.append(f"<th{c_attr}{extra}>{label}</th>")

    # --- body ---
    trs = []
    for r in rows:
        tds = []
        for i, c in enumerate(cols):
            td = _cell(r[i] if i < len(r) else None, c, ctx)
            bits = []
            if not core_flags[i]:
                bits.append('data-col-extra="1"')
            if c.get("sticky"):
                td = td.replace("<td", '<td class="sticky-col"', 1) if 'class="' not in td[:20] \
                    else td.replace('class="', 'class="sticky-col ', 1)
            if bits:
                td = td.replace("<td", "<td " + " ".join(bits), 1)
            tds.append(td)
        fattr = ""
        if filter_idx:
            fattr = f" data-f='{_filter_payload(r, cols, filter_idx)}'"
        trs.append(f"<tr{fattr}>{''.join(tds)}</tr>")

    # --- bar above the table ---
    bar = []
    if searchable:
        ph = "검색" if ctx.lang == "ko" else "Search"
        bar.append(f'<input class="sds-search" type="search" placeholder="{ph}" '
                   f'aria-label="{ph}">')
    if wide:
        lab_all = f"전체 {len(cols)}개 컬럼" if ctx.lang == "ko" else f"All {len(cols)} columns"
        n_core = sum(core_flags)
        lab_core = f"핵심 {n_core}개 컬럼" if ctx.lang == "ko" else f"Core {n_core} columns"
        bar.append(f'<button class="sds-btn" data-act="cols" aria-pressed="false" '
                   f'data-label-all="{ctx.attr(lab_core)}" '
                   f'data-label-core="{ctx.attr(lab_all)}">{ctx.raw_text(lab_all)}</button>')
    if rows:
        bar.append(f'<span class="sds-rowcount">{len(rows):,} rows</span>')
    barhtml = (f'<div class="sds-tablebar" data-for="{tid}">{"".join(bar)}</div>'
               if bar else "")

    grain = (f'<div class="sds-grain">'
             f'{"행 단위" if ctx.lang == "ko" else "Row grain"}: '
             f'{ctx.raw_text(b.get("row_grain"))}</div>')
    cap = f'<div class="sds-caption">{ctx.text(b["caption"])}</div>' if b.get("caption") else ""
    note = f'<div class="sds-caption">{ctx.text(b["note"])}</div>' if b.get("note") else ""

    return (
        f"{_title(b, ctx)}{grain}{cap}{barhtml}"
        f'<div class="sds-tablewrap" data-table-id="{tid}" data-cols="core">'
        f'<table class="sds-table">'
        f"<thead><tr>{''.join(ths)}</tr></thead>"
        f"<tbody>{''.join(trs)}</tbody>"
        f"</table></div>{note}"
    )
