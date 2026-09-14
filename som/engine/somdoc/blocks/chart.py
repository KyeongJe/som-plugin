"""Chart renderer: hand-built inline SVG, no library, no CDN.

Five kinds only, on purpose: bar, hbar, line, dot-strip, heat-grid. There is no
pie, no donut, and no stacked area -- each of those makes a comparison harder
than the bar chart it would replace.

House rules the renderer enforces rather than suggests:
  - the takeaway prints ABOVE the chart, in bold (ir.py rejects a chart without one)
  - bar and hbar baselines start at zero, always
  - four series maximum; with four or fewer, series are labelled directly and
    the legend is dropped
  - `data-sds-src` on the figure records where the numbers came from
"""
from __future__ import annotations

from typing import Any

from . import Ctx, _title, block

W = 880          # viewBox width; CSS scales it to the container
PAD_L, PAD_R = 56, 18
PAD_T, PAD_B = 14, 34


def _fmt(v: Any, digits: int = 1) -> str:
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        s = f"{v:,.{digits}f}"
        return s.rstrip("0").rstrip(".") if "." in s else s
    return str(v)


def _nice_ceiling(v: float) -> float:
    """A round upper bound so gridline labels are readable."""
    if v <= 0:
        return 1.0
    import math
    mag = 10 ** math.floor(math.log10(v))
    for step in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if v <= step * mag:
            return step * mag
    return 10 * mag


def _ticks(vmax: float, n: int = 4) -> list[float]:
    return [vmax * i / n for i in range(n + 1)]


def _legend(series: list[dict], ctx: Ctx) -> str:
    if len(series) <= 1:
        return ""
    items = "".join(
        f'<span><i style="background:var(--sds-series-{i + 1})"></i>'
        f"{ctx.raw_text(s.get('name'))}</span>"
        for i, s in enumerate(series)
    )
    return f'<div class="sds-legend">{items}</div>'


# ---------------------------------------------------------------------------
def _bar(b: dict, ctx: Ctx) -> str:
    cats = b.get("categories") or []
    series = b.get("series") or []
    unit = b.get("y_unit") or ""
    H = int(b.get("height") or 300)
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B

    flat = [v for s in series for v in (s.get("values") or []) if isinstance(v, (int, float))]
    vmax = _nice_ceiling(max(flat) if flat else 1)

    g = len(cats) or 1
    group_w = plot_w / g
    n = max(len(series), 1)
    bar_w = min(46.0, (group_w * 0.68) / n)
    inner = bar_w * n

    out = [f'<svg viewBox="0 0 {W} {H}" role="img" preserveAspectRatio="none">']

    out.append('<g class="grid">')
    for t in _ticks(vmax):
        y = PAD_T + plot_h - (t / vmax) * plot_h
        out.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}"/>')
    out.append("</g>")
    out.append('<g class="axis">')
    for t in _ticks(vmax):
        y = PAD_T + plot_h - (t / vmax) * plot_h
        out.append(f'<text x="{PAD_L - 8}" y="{y + 3.5:.1f}" text-anchor="end">{_fmt(t)}</text>')
    out.append(f'<line x1="{PAD_L}" y1="{PAD_T + plot_h}" x2="{W - PAD_R}" y2="{PAD_T + plot_h}"/>')
    out.append("</g>")

    for si, s in enumerate(series):
        vals = s.get("values") or []
        for ci in range(len(cats)):
            v = vals[ci] if ci < len(vals) else None
            if not isinstance(v, (int, float)):
                continue
            h = (v / vmax) * plot_h
            x = PAD_L + ci * group_w + (group_w - inner) / 2 + si * bar_w
            y = PAD_T + plot_h - h
            out.append(f'<rect class="s{si + 1}" x="{x:.1f}" y="{y:.1f}" '
                       f'width="{bar_w - 2:.1f}" height="{max(h, 0):.1f}" rx="2"/>')
            if len(series) == 1:
                out.append(f'<text class="lbl-value" x="{x + (bar_w - 2) / 2:.1f}" '
                           f'y="{y - 5:.1f}" text-anchor="middle">{_fmt(v)}{unit}</text>')

    out.append('<g class="axis">')
    for ci, c in enumerate(cats):
        x = PAD_L + ci * group_w + group_w / 2
        out.append(f'<text x="{x:.1f}" y="{PAD_T + plot_h + 18}" '
                   f'text-anchor="middle">{ctx.raw_text(c)}</text>')
    out.append("</g></svg>")
    return "".join(out) + _legend(series, ctx)


def _hbar(b: dict, ctx: Ctx) -> str:
    cats = b.get("categories") or []
    series = b.get("series") or []
    unit = b.get("y_unit") or ""
    vals = (series[0].get("values") if series else []) or []
    row_h = 30
    label_w = int(b.get("label_width") or 190)
    H = PAD_T + len(cats) * row_h + 16
    plot_w = W - label_w - 92

    flat = [v for v in vals if isinstance(v, (int, float))]
    vmax = _nice_ceiling(max(flat) if flat else 1)

    out = [f'<svg viewBox="0 0 {W} {H}" role="img" preserveAspectRatio="none">']
    for ci, c in enumerate(cats):
        y = PAD_T + ci * row_h
        v = vals[ci] if ci < len(vals) else 0
        wid = (v / vmax) * plot_w if isinstance(v, (int, float)) else 0
        out.append(f'<text x="{label_w - 10}" y="{y + row_h / 2 + 4:.1f}" '
                   f'text-anchor="end">{ctx.raw_text(c)}</text>')
        out.append(f'<rect x="{label_w}" y="{y + 6}" width="{plot_w}" '
                   f'height="{row_h - 12}" rx="2" fill="var(--sds-sunk)"/>')
        out.append(f'<rect class="s1" x="{label_w}" y="{y + 6}" width="{max(wid, 0):.1f}" '
                   f'height="{row_h - 12}" rx="2"/>')
        out.append(f'<text class="lbl-value" x="{label_w + wid + 8:.1f}" '
                   f'y="{y + row_h / 2 + 4:.1f}">{_fmt(v)}{unit}</text>')
    out.append("</svg>")
    return "".join(out)


def _line(b: dict, ctx: Ctx) -> str:
    cats = b.get("categories") or []
    series = b.get("series") or []
    unit = b.get("y_unit") or ""
    H = int(b.get("height") or 300)
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B

    flat = [v for s in series for v in (s.get("values") or []) if isinstance(v, (int, float))]
    vmax = _nice_ceiling(max(flat) if flat else 1)
    step = plot_w / max(len(cats) - 1, 1)

    out = [f'<svg viewBox="0 0 {W} {H}" role="img" preserveAspectRatio="none">']
    out.append('<g class="grid">')
    for t in _ticks(vmax):
        y = PAD_T + plot_h - (t / vmax) * plot_h
        out.append(f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}"/>')
    out.append("</g><g class=\"axis\">")
    for t in _ticks(vmax):
        y = PAD_T + plot_h - (t / vmax) * plot_h
        out.append(f'<text x="{PAD_L - 8}" y="{y + 3.5:.1f}" text-anchor="end">{_fmt(t)}</text>')
    out.append(f'<line x1="{PAD_L}" y1="{PAD_T + plot_h}" x2="{W - PAD_R}" y2="{PAD_T + plot_h}"/>')
    out.append("</g>")

    for si, s in enumerate(series):
        vals = s.get("values") or []
        pts = []
        for ci in range(len(cats)):
            v = vals[ci] if ci < len(vals) else None
            if not isinstance(v, (int, float)):
                continue
            x = PAD_L + ci * step
            y = PAD_T + plot_h - (v / vmax) * plot_h
            pts.append((x, y, v))
        if not pts:
            continue
        d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y, _) in enumerate(pts))
        out.append(f'<path class="stroke-s{si + 1}" d="{d}" fill="none" stroke-width="2.2"/>')
        for x, y, _v in pts:
            out.append(f'<circle class="s{si + 1}" cx="{x:.1f}" cy="{y:.1f}" r="3.2"/>')
        # Direct label at the last point instead of a legend.
        lx, ly, lv = pts[-1]
        out.append(f'<text class="lbl-value" x="{lx + 7:.1f}" y="{ly + 3.5:.1f}">'
                   f"{ctx.raw_text(s.get('name'))} {_fmt(lv)}{unit}</text>")

    out.append('<g class="axis">')
    for ci, c in enumerate(cats):
        out.append(f'<text x="{PAD_L + ci * step:.1f}" y="{PAD_T + plot_h + 18}" '
                   f'text-anchor="middle">{ctx.raw_text(c)}</text>')
    out.append("</g></svg>")
    return "".join(out)


def _dot_strip(b: dict, ctx: Ctx) -> str:
    """Current vs target on one axis per category. Good for maturity levels."""
    cats = b.get("categories") or []
    series = b.get("series") or []
    row_h = 34
    label_w = int(b.get("label_width") or 190)
    H = PAD_T + len(cats) * row_h + 22
    plot_w = W - label_w - 60
    flat = [v for s in series for v in (s.get("values") or []) if isinstance(v, (int, float))]
    vmax = _nice_ceiling(max(flat) if flat else 1)

    out = [f'<svg viewBox="0 0 {W} {H}" role="img" preserveAspectRatio="none">']
    for ci, c in enumerate(cats):
        y = PAD_T + ci * row_h + row_h / 2
        out.append(f'<text x="{label_w - 10}" y="{y + 4:.1f}" text-anchor="end">'
                   f"{ctx.raw_text(c)}</text>")
        out.append(f'<line x1="{label_w}" y1="{y:.1f}" x2="{label_w + plot_w}" y2="{y:.1f}" '
                   f'stroke="var(--sds-rule)" stroke-width="1"/>')
        xs = []
        for si, s in enumerate(series):
            vals = s.get("values") or []
            v = vals[ci] if ci < len(vals) else None
            if not isinstance(v, (int, float)):
                continue
            x = label_w + (v / vmax) * plot_w
            xs.append(x)
            out.append(f'<circle class="s{si + 1}" cx="{x:.1f}" cy="{y:.1f}" r="6"/>')
        if len(xs) == 2:
            out.append(f'<line x1="{min(xs):.1f}" y1="{y:.1f}" x2="{max(xs):.1f}" y2="{y:.1f}" '
                       f'stroke="var(--sds-rule-firm)" stroke-width="2"/>')
    out.append('<g class="axis">')
    for t in _ticks(vmax):
        x = label_w + (t / vmax) * plot_w
        out.append(f'<text x="{x:.1f}" y="{H - 6}" text-anchor="middle">{_fmt(t)}</text>')
    out.append("</g></svg>")
    return "".join(out) + _legend(series, ctx)


RENDERERS = {"bar": _bar, "hbar": _hbar, "line": _line, "dot-strip": _dot_strip}


@block("chart")
def render_chart(b: dict, ctx: Ctx) -> str:
    kind = b.get("kind")
    if kind == "heat-grid":
        # Same visual as the heatgrid block; reuse it so there is one code path.
        from . import REGISTRY
        return REGISTRY["heatgrid"](b, ctx)

    fn = RENDERERS.get(kind)
    if fn is None:
        return (f'<div class="sds-callout" data-kind="crit"><div class="t">'
                f'Unsupported chart</div>kind <code>{ctx.raw_text(kind)}</code> '
                f"is not in the standard.</div>")

    src = f' data-sds-src="{ctx.attr(b["src"])}"' if b.get("src") else ""
    return (
        f'{_title(b, ctx)}'
        f'<div class="sds-takeaway">{ctx.text(b.get("takeaway"))}</div>'
        f'<figure class="sds-chart" style="margin:0"{src}>{fn(b, ctx)}</figure>'
    )
