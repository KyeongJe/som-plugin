"""Word output, for the one thing HTML and xlsx cannot do: redlines.

The SDS ships HTML for reading and xlsx for the numbers. Neither survives the
way team governance documents actually get approved here -- somebody opens it
in Word, turns on track changes, and argues in the margin. "워드로 주세요" had
no answer, and the honest one was that this emitter did not exist.

What it is not: a second renderer with its own opinions. Every number, every
metric reference and every ordering decision has already been made by the IR,
and this walks the same structure the HTML emitter walks. If the two disagree
about content, this file is wrong.

Two deliberate differences from HTML, because a printed page is not a screen:

  - charts become their data. An inline SVG cannot go in a .docx without
    rasterising it, and a blurry picture of a bar chart is worse than the
    four numbers it was drawn from. The `takeaway` stays, in bold, above the
    table -- the rule that a conclusion never hides in prose holds here too.
  - the wide-table toggle has nowhere to go, so a wide table prints every
    column and says so. A reader who needs fewer columns has the xlsx.

Styling stays close to Word's defaults on purpose. A document that fights the
recipient's template is a document they have to fix before they can use it.
"""
from __future__ import annotations

from pathlib import Path

try:
    import docx
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor
except ImportError as e:                                 # pragma: no cover
    raise ImportError("python-docx") from e

from .. import ir as IR

# The SDS accent, used only where the HTML uses it: a rule under a heading and
# the decision-request block. Word documents that arrive in six colours get
# reformatted before they get read.
ACCENT = RGBColor(0xD8, 0x1E, 0x28)
MUTED = RGBColor(0x4A, 0x4A, 0x44)

BADGE = {"ok": "○", "warn": "△", "crit": "●", "info": "·", "na": "-"}


def _text(s, metrics) -> str:
    """Metric references resolve exactly as they do in HTML."""
    return IR.resolve_metrics(str(s if s is not None else ""), metrics or {})


def _para(doc, text="", *, bold=False, italic=False, size=None, color=None,
          space_after=6, align=None):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.bold = bold
    run.italic = italic
    if size:
        run.font.size = Pt(size)
    if color is not None:
        run.font.color.rgb = color
    p.paragraph_format.space_after = Pt(space_after)
    if align is not None:
        p.alignment = align
    return p


def _cell_text(v, metrics) -> str:
    """A badge cell is a dict in the IR and a glyph plus a word on paper."""
    if isinstance(v, dict):
        label = _text(v.get("label", ""), metrics)
        mark = BADGE.get(str(v.get("state", "")), "")
        return f"{mark} {label}".strip()
    return _text(v, metrics)


def _table(doc, columns, rows, metrics, *, grain=None, note=None, title=None,
           wide_note=True):
    if title:
        _para(doc, title, bold=True, size=12, space_after=2)
    # The grain caption is not decoration. It is the single most common cause
    # of an executive misreading a table, and the HTML emitter renders it
    # above every table for the same reason.
    if grain:
        _para(doc, f"행 단위: {grain}", italic=True, size=9, color=MUTED, space_after=4)
    if wide_note and len(columns) > IR.WIDE_TABLE_COLS:
        _para(doc, f"컬럼 {len(columns)}개 — 전체를 인쇄했습니다. "
                   f"핵심 컬럼만 보시려면 함께 드린 xlsx 를 쓰세요.",
              italic=True, size=9, color=MUTED, space_after=4)

    t = doc.add_table(rows=1, cols=len(columns))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, col in enumerate(columns):
        cell = t.rows[0].cells[i]
        cell.text = ""
        run = cell.paragraphs[0].add_run(_text(col.get("label", ""), metrics))
        run.bold = True
        run.font.size = Pt(9)

    for row in rows or []:
        cells = t.add_row().cells
        for i, col in enumerate(columns):
            v = row[i] if isinstance(row, list) and i < len(row) else (
                row.get(col.get("key")) if isinstance(row, dict) else "")
            cells[i].text = ""
            run = cells[i].paragraphs[0].add_run(_cell_text(v, metrics))
            run.font.size = Pt(9)
            if col.get("align") == "num":
                cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT

    if note:
        _para(doc, _text(note, metrics), italic=True, size=9, color=MUTED)
    else:
        _para(doc, "", space_after=4)
    return t


# --------------------------------------------------------------- blocks
def _block(doc, b, metrics):
    bt = b.get("type")

    if bt == "narrative":
        for para in b.get("paragraphs") or []:
            _para(doc, _text(para, metrics))

    elif bt == "bullets":
        if b.get("title"):
            _para(doc, b["title"], bold=True, size=12, space_after=2)
        for item in b.get("items") or []:
            txt = _text(item.get("text", item) if isinstance(item, dict) else item, metrics)
            p = doc.add_paragraph(style="List Bullet")
            run = p.add_run(txt)
            if isinstance(item, dict) and item.get("emph"):
                run.bold = True

    elif bt == "decision_request":
        # Always first in the document by IR rule, and the reason the document
        # was sent at all. It gets the accent and a rule, exactly as in HTML.
        _para(doc, b.get("title") or "결정 요청", bold=True, size=14,
              color=ACCENT, space_after=2)
        for item in b.get("items") or []:
            _para(doc, f"· {_text(item.get('ask', ''), metrics)}", bold=True, space_after=2)
            if item.get("rationale"):
                _para(doc, f"  {_text(item['rationale'], metrics)}", size=10,
                      color=MUTED, space_after=6)

    elif bt == "kpi_tiles":
        rows = [[t.get("label", ""), _text(t.get("value", ""), metrics), t.get("unit", "")]
                for t in (b.get("tiles") or [])]
        _table(doc, [{"label": "지표"}, {"label": "값", "align": "num"}, {"label": "단위"}],
               rows, metrics, title=b.get("title"), wide_note=False)

    elif bt == "callout":
        kind = {"info": "참고", "warn": "주의", "crit": "경고", "good": "확인"}.get(
            b.get("kind", "info"), "참고")
        _para(doc, f"[{kind}] {b.get('title') or ''}".strip(), bold=True, space_after=2)
        _para(doc, _text(b.get("body", ""), metrics), size=10, space_after=8)

    elif bt == "table":
        _table(doc, b.get("columns") or [], b.get("rows") or [], metrics,
               grain=b.get("row_grain"), note=b.get("note"), title=b.get("title"))

    elif bt in ("matrix", "heatgrid"):
        cols = [{"label": b.get("corner") or ""}] + [
            {"label": c} for c in (b.get("columns") or [])]
        rows = []
        for i, name in enumerate(b.get("rows") or []):
            cells = (b.get("cells") or [])[i] if i < len(b.get("cells") or []) else []
            rows.append([name] + [_cell_text(c, metrics) for c in cells])
        _table(doc, cols, rows, metrics, grain=b.get("row_grain"),
               note=b.get("caption") or b.get("note"), title=b.get("title"))

    elif bt == "chart":
        # The conclusion first, in bold, above the data -- the same rule the
        # renderer enforces on screen.
        if b.get("takeaway"):
            _para(doc, _text(b["takeaway"], metrics), bold=True, space_after=2)
        series = b.get("series") or []
        # `categories`, not `labels`. The IR has always called it that and the
        # first draft of this emitter read a key that does not exist, so every
        # chart fell through to the "[차트: …]" placeholder and the numbers
        # never appeared. test_docx caught it by validating its own fixture.
        cats = b.get("categories") or []
        if series and cats:
            cols = [{"label": b.get("x_label") or ""}] + [
                {"label": s.get("name") or f"계열{i + 1}", "align": "num"}
                for i, s in enumerate(series)]
            rows = [[c] + [(s.get("values") or [None] * len(cats))[i]
                           for s in series]
                    for i, c in enumerate(cats)]
            _table(doc, cols, rows, metrics, title=b.get("title"), wide_note=False)
        else:
            _para(doc, f"[차트: {b.get('title') or b.get('kind') or ''}]",
                  italic=True, color=MUTED)

    elif bt == "diagram":
        # An SVG cannot be embedded without rasterising, and a blurry picture
        # helps nobody. The caption carries what the picture said.
        _para(doc, f"[그림] {_text(b.get('caption') or b.get('title') or '', metrics)}",
              italic=True, color=MUTED)
        if b.get("takeaway"):
            _para(doc, _text(b["takeaway"], metrics), bold=True)

    elif bt == "risks":
        rows = [[r.get("id", ""), r.get("grade", ""), _text(r.get("risk", ""), metrics),
                 _text(r.get("mitigation", ""), metrics)]
                for r in (b.get("items") or [])]
        _table(doc, [{"label": "#"}, {"label": "등급"}, {"label": "리스크"},
                     {"label": "완화"}],
               rows, metrics, grain="행 1개 = 리스크 1건", title=b.get("title"))

    elif bt == "changelog":
        rows = [[e.get("version", ""), e.get("date", ""), _text(e.get("change", ""), metrics),
                 e.get("by", "")] for e in (b.get("entries") or [])]
        _table(doc, [{"label": "버전"}, {"label": "일자"}, {"label": "변경"},
                     {"label": "작성"}],
               rows, metrics, grain="행 1개 = 변경 1건", title=b.get("title"))

    elif bt == "appendix_source":
        rows = [[s.get("name", ""), s.get("path", ""), s.get("sheet", ""),
                 s.get("rows", ""), s.get("as_of", "")]
                for s in (b.get("items") or [])]
        _table(doc, [{"label": "원천"}, {"label": "경로"}, {"label": "시트"},
                     {"label": "행수", "align": "num"}, {"label": "기준일"}],
               rows, metrics, grain="행 1개 = 원천 1건", title=b.get("title"),
               note=b.get("note"))


def emit(ir: dict, path: str | Path) -> Path:
    """Render the IR to a .docx. Same structure, same numbers, same order."""
    IR.assert_valid(ir)
    metrics = ir.get("metrics") or {}
    meta = ir.get("docmeta") or {}
    doc = docx.Document()

    # Word's Normal style is the one the recipient's template will override,
    # so set it rather than styling every run.
    normal = doc.styles["Normal"]
    normal.font.size = Pt(10.5)

    _para(doc, _text(meta.get("title", ""), metrics), bold=True, size=20, space_after=2)
    sub = " · ".join(str(x) for x in (
        meta.get("version"), meta.get("as_of"), meta.get("owner")) if x)
    if sub:
        _para(doc, sub, size=10, color=MUTED, space_after=12)

    for section in ir.get("sections") or []:
        doc.add_heading(_text(section.get("title", ""), metrics), level=1)
        if section.get("intro"):
            _para(doc, _text(section["intro"], metrics))
        for b in section.get("blocks") or []:
            _block(doc, b, metrics)

    # The same provenance line the HTML foot carries. A Word file gets
    # forwarded, and the hash is how somebody checks they have the version
    # that was approved.
    _para(doc, "", space_after=12)
    _para(doc, f"somdoc {IR.ENGINE_VERSION} · ir_sha256 {IR.ir_sha256(ir)[:12]}",
          size=8, color=MUTED)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out)
    return out
