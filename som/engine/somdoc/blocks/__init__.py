"""Block renderers: one IR block -> one HTML fragment.

Every renderer is a pure function of (block, ctx) and emits deterministic
markup. No clock, no randomness, no dict-order dependence -- the golden test
byte-compares the result.

`ctx` carries what a block needs from the document: the metrics table (for
{{m:key}} substitution) and a counter for stable element ids.
"""
from __future__ import annotations

import html
from typing import Any, Callable

from .. import ir as IR


class Ctx:
    """Render context. `n()` hands out stable, document-order element ids."""

    def __init__(self, metrics: dict | None = None, lang: str = "ko") -> None:
        self.metrics = metrics or {}
        self.lang = lang
        self._counters: dict[str, int] = {}

    def n(self, kind: str) -> str:
        self._counters[kind] = self._counters.get(kind, 0) + 1
        return f"{kind}{self._counters[kind]}"

    def text(self, s: Any) -> str:
        """Escape, and resolve metric references so prose numbers stay traceable."""
        if s is None:
            return ""
        return html.escape(IR.resolve_metrics(str(s), self.metrics), quote=False)

    def raw_text(self, s: Any) -> str:
        """Escape without metric resolution (labels, identifiers)."""
        return "" if s is None else html.escape(str(s), quote=False)

    def attr(self, s: Any) -> str:
        return "" if s is None else html.escape(str(s), quote=True)


REGISTRY: dict[str, Callable[[dict, Ctx], str]] = {}


def block(name: str):
    def deco(fn: Callable[[dict, Ctx], str]):
        REGISTRY[name] = fn
        return fn
    return deco


def render_block(b: dict, ctx: Ctx) -> str:
    fn = REGISTRY.get(b.get("type", ""))
    if fn is None:
        return (f'<div class="sds-callout" data-kind="crit"><div class="t">'
                f'Unrenderable block</div>type '
                f'<code>{ctx.raw_text(b.get("type"))}</code> has no renderer.</div>')
    return fn(b, ctx)


def _title(b: dict, ctx: Ctx) -> str:
    t = b.get("title")
    if not t:
        return ""
    tag = ('<span class="sds-derived-tag">derived</span>'
           if b.get("derived") else "")
    return f"<h3>{ctx.text(t)}{tag}</h3>"


# ---------------------------------------------------------------------------
# decision request -- the block the whole document exists to deliver
# ---------------------------------------------------------------------------
@block("decision_request")
def _decision(b: dict, ctx: Ctx) -> str:
    heading = b.get("title") or ("결정 요청" if ctx.lang == "ko" else "Decision Requests")
    parts = [f'<div class="sds-decision"><h3>{ctx.text(heading)}</h3>']
    for i, it in enumerate(b.get("items") or [], start=1):
        parts.append('<div class="sds-ask">')
        parts.append(
            f'<div class="sds-ask-line"><span class="sds-ask-no">{i}</span>'
            f'<span class="sds-ask-text">{ctx.text(it.get("ask"))}</span></div>'
        )
        parts.append(f'<div class="sds-ask-why">{ctx.text(it.get("rationale"))}</div>')
        meta = []
        if it.get("owner"):
            meta.append(f'요청 대상 {ctx.text(it["owner"])}')
        if it.get("due"):
            meta.append(f'필요 시점 {ctx.raw_text(it["due"])}')
        if it.get("options"):
            opts = " / ".join(ctx.text(o) for o in it["options"])
            meta.append(f'선택지 {opts}')
        if meta:
            parts.append(f'<div class="sds-ask-meta">{" · ".join(meta)}</div>')
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


# ---------------------------------------------------------------------------
@block("kpi_tiles")
def _tiles(b: dict, ctx: Ctx) -> str:
    out = [_title(b, ctx), '<div class="sds-tiles">']
    for it in b.get("items") or []:
        state = f' data-state="{ctx.attr(it["state"])}"' if it.get("state") else ""
        unit = f'<span class="u">{ctx.raw_text(it["unit"])}</span>' if it.get("unit") else ""
        note = f'<div class="sds-tile-note">{ctx.text(it["note"])}</div>' if it.get("note") else ""
        val = it.get("value")
        val_txt = f"{val:,}" if isinstance(val, int) else ctx.raw_text(val)
        out.append(
            f'<div class="sds-tile"{state}>'
            f'<div class="sds-tile-label">{ctx.raw_text(it.get("label"))}</div>'
            f'<div class="sds-tile-value">{val_txt}{unit}</div>'
            f"{note}</div>"
        )
    out.append("</div>")
    return "".join(out)


@block("bullets")
def _bullets(b: dict, ctx: Ctx) -> str:
    out = [_title(b, ctx), '<ul class="sds-bullets">']
    for it in b.get("items") or []:
        emph = ' data-emph="1"' if it.get("emph") else ""
        out.append(f'<li{emph}>{ctx.text(it.get("text"))}</li>')
    out.append("</ul>")
    return "".join(out)


@block("narrative")
def _narrative(b: dict, ctx: Ctx) -> str:
    out = [_title(b, ctx)]
    for para in b.get("paragraphs") or []:
        out.append(f"<p>{ctx.text(para)}</p>")
    return "".join(out)


@block("callout")
def _callout(b: dict, ctx: Ctx) -> str:
    t = f'<div class="t">{ctx.text(b["title"])}</div>' if b.get("title") else ""
    return (f'<div class="sds-callout" data-kind="{ctx.attr(b.get("kind", "info"))}">'
            f'{t}{ctx.text(b.get("body"))}</div>')


@block("risks")
def _risks(b: dict, ctx: Ctx) -> str:
    heads = (("리스크", "영향", "완화", "오너", "상태") if ctx.lang == "ko"
             else ("Risk", "Impact", "Mitigation", "Owner", "State"))
    rows = []
    for it in b.get("items") or []:
        st = it.get("state") or "info"
        rows.append(
            "<tr>"
            f'<td class="wrap">{ctx.text(it.get("risk"))}</td>'
            f'<td class="wrap">{ctx.text(it.get("impact"))}</td>'
            f'<td class="wrap">{ctx.text(it.get("mitigation"))}</td>'
            f"<td>{ctx.raw_text(it.get('owner'))}</td>"
            f'<td><span class="sds-badge" data-s="{ctx.attr(st)}">'
            f"{ctx.raw_text(it.get('state_label') or st)}</span></td>"
            "</tr>"
        )
    th = "".join(f"<th>{h}</th>" for h in heads)
    return (f'{_title(b, ctx)}<div class="sds-tablewrap"><table class="sds-table">'
            f"<thead><tr>{th}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>")


@block("changelog")
def _changelog(b: dict, ctx: Ctx) -> str:
    heads = (("시점", "변경", "사유", "승인") if ctx.lang == "ko"
             else ("When", "Change", "Why", "Approved by"))
    rows = "".join(
        "<tr>"
        f"<td>{ctx.raw_text(it.get('when'))}</td>"
        f'<td class="wrap">{ctx.text(it.get("what"))}</td>'
        f'<td class="wrap">{ctx.text(it.get("why"))}</td>'
        f"<td>{ctx.raw_text(it.get('who'))}</td>"
        "</tr>"
        for it in b.get("items") or []
    )
    th = "".join(f"<th>{h}</th>" for h in heads)
    return (f'{_title(b, ctx)}<div class="sds-tablewrap"><table class="sds-table">'
            f"<thead><tr>{th}</tr></thead><tbody>{rows}</tbody></table></div>")


@block("appendix_source")
def _sources(b: dict, ctx: Ctx) -> str:
    heads = (("원천", "경로", "시트", "행수", "기준일") if ctx.lang == "ko"
             else ("Source", "Path", "Sheet", "Rows", "As of"))
    rows = []
    for it in b.get("items") or []:
        n = it.get("rows")
        rows.append(
            "<tr>"
            f"<td>{ctx.raw_text(it.get('name'))}</td>"
            f'<td class="wrap"><code>{ctx.raw_text(it.get("path"))}</code></td>'
            f"<td>{ctx.raw_text(it.get('sheet'))}</td>"
            f'<td class="num">{f"{n:,}" if isinstance(n, int) else ctx.raw_text(n)}</td>'
            f"<td>{ctx.raw_text(it.get('as_of'))}</td>"
            "</tr>"
        )
    num_heads = {"행수", "Rows"}
    th = "".join(
        ('<th class="num">' if h in num_heads else "<th>") + h + "</th>"
        for h in heads
    )
    note = (f'<div class="sds-caption">{ctx.text(b["note"])}</div>' if b.get("note") else "")
    return (f'{_title(b, ctx)}{note}<div class="sds-tablewrap"><table class="sds-table">'
            f"<thead><tr>{th}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>")


# ---------------------------------------------------------------------------
@block("matrix")
def _matrix(b: dict, ctx: Ctx) -> str:
    cols = b.get("columns") or []
    rows = b.get("rows") or []
    cells = b.get("cells") or []
    corner = ctx.raw_text(b.get("corner") or "")
    th = "".join(f"<th>{ctx.raw_text(c)}</th>" for c in cols)
    body = []
    for ri, rname in enumerate(rows):
        tds = "".join(
            f'<td class="wrap">{ctx.text(v)}</td>'
            for v in (cells[ri] if ri < len(cells) else [])
        )
        body.append(f'<tr><th class="sticky-col">{ctx.raw_text(rname)}</th>{tds}</tr>')
    cap = f'<div class="sds-caption">{ctx.text(b["caption"])}</div>' if b.get("caption") else ""
    return (f'{_title(b, ctx)}{cap}<div class="sds-tablewrap"><table class="sds-table">'
            f'<thead><tr><th class="sticky-col">{corner}</th>{th}</tr></thead>'
            f"<tbody>{''.join(body)}</tbody></table></div>")


@block("heatgrid")
def _heatgrid(b: dict, ctx: Ctx) -> str:
    cols = b.get("columns") or []
    rows = b.get("rows") or []
    cells = b.get("cells") or []
    flags = b.get("flags") or []
    scale_max = b.get("scale_max")
    if not scale_max:
        flat = [v for r in cells for v in r if isinstance(v, (int, float))]
        scale_max = max(flat) if flat else 1

    def bucket(v: Any) -> int:
        if not isinstance(v, (int, float)) or v == 0:
            return 0
        frac = v / scale_max if scale_max else 0
        return 1 if frac <= .25 else 2 if frac <= .5 else 3 if frac <= .75 else 4

    th = "".join(f"<th>{ctx.raw_text(c)}</th>" for c in cols)
    body = []
    for ri, rname in enumerate(rows):
        tds = []
        row_cells = cells[ri] if ri < len(cells) else []
        row_flags = flags[ri] if ri < len(flags) else []
        for ci, v in enumerate(row_cells):
            fl = row_flags[ci] if ci < len(row_flags) else None
            fa = f' data-flag="{ctx.attr(fl)}"' if fl else ""
            disp = "" if (v in (0, None)) else (f"{v:,}" if isinstance(v, int) else ctx.raw_text(v))
            tds.append(f'<td data-h="{bucket(v)}"{fa}>{disp}</td>')
        body.append(f"<tr><th>{ctx.raw_text(rname)}</th>{''.join(tds)}</tr>")

    key = ""
    if any(any(r) for r in flags):
        key = ('<div class="sds-flagkey">'
               '<span>■ 빨강 외곽선 — 담당 공백(0명)</span>'
               '<span>■ 주황 외곽선 — 과집중(1인 의존)</span></div>')
    cap = f'<div class="sds-caption">{ctx.text(b["caption"])}</div>' if b.get("caption") else ""
    return (f'{_title(b, ctx)}{cap}<div style="overflow:auto">'
            f'<table class="sds-heat"><thead><tr><th></th>{th}</tr></thead>'
            f"<tbody>{''.join(body)}</tbody></table></div>{key}")


@block("diagram")
def _diagram(b: dict, ctx: Ctx) -> str:
    """Inline SVG produced upstream by the archify skill.

    The SVG is embedded, not linked, so the document stays self-contained.
    """
    take = (f'<div class="sds-takeaway">{ctx.text(b["takeaway"])}</div>'
            if b.get("takeaway") else "")
    svg = b.get("svg") or ""
    if not svg:
        return (f'{_title(b, ctx)}{take}<div class="sds-callout" data-kind="warn">'
                f'<div class="t">Diagram missing</div>'
                f'inline <code>svg</code> was not supplied for this block.</div>')
    return f'{_title(b, ctx)}{take}<div class="sds-chart">{svg}</div>'


# Import for side effects: these modules register themselves.
from . import table as _table_mod  # noqa: E402,F401
from . import chart as _chart_mod  # noqa: E402,F401
