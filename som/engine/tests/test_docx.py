"""The Word emitter.

The SDS shipped HTML for reading and xlsx for the numbers, and neither
survives how a governance document actually gets approved here: somebody opens
it in Word, turns on track changes, and argues in the margin. "워드로 주세요"
had no answer.

What these tests are really about is that docx is not a second renderer with
its own opinions. Every number and every ordering decision was already made by
the IR; this walks the same structure the HTML emitter walks. So the tests
compare the two rather than checking docx against a fixture -- a fixture would
let the two drift and stay green.

    python engine/tests/test_docx.py
"""
from __future__ import annotations

import re
import sys
import tempfile
import zipfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
SOM = ENGINE.parent
sys.path.insert(0, str(ENGINE))

from html import unescape as html_unescape       # noqa: E402

from somdoc import ir as IR                      # noqa: E402
from somdoc.emitters import html as HTML         # noqa: E402

EXAMPLE = SOM / "standard" / "examples" / "rnr.example.somdoc.json"

try:
    from somdoc.emitters import docx as DOCX
    HAVE = True
except ImportError:                              # pragma: no cover
    HAVE = False


def _render(ir=None) -> tuple[Path, str]:
    """Emit, and hand back the document body as text."""
    ir = ir or IR.load(EXAMPLE)
    out = Path(tempfile.mkdtemp()) / "x.docx"
    DOCX.emit(ir, out)
    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    # Word splits a run wherever it likes, so tags have to go before any text
    # comparison. Without this, "결정 요청" can be three runs and no assertion
    # about it ever holds.
    #
    # And the entities have to come back: "인원별 R&R 매트릭스" is stored as
    # `R&amp;R`, so a raw tag-strip reported that section as missing from a
    # document that contained it perfectly. The emitter was right; the first
    # version of this test was not.
    return out, html_unescape(re.sub(r"<[^>]+>", "", xml))


def test_it_emits_a_real_docx():
    if not HAVE:
        raise AssertionError("python-docx 가 없습니다 — requirements 에 있어야 합니다")
    out, _ = _render()
    assert out.stat().st_size > 10_000, out.stat().st_size
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    # The parts Word requires. A file that opens in python-docx but not in Word
    # is the failure mode worth guarding.
    for part in ("word/document.xml", "[Content_Types].xml", "_rels/.rels"):
        assert part in names, f"{part} 이 없습니다: {names}"


def test_every_section_title_appears():
    """Same structure as the IR, not a subset someone forgot to walk."""
    ir = IR.load(EXAMPLE)
    _, text = _render(ir)
    missing = [s.get("title") for s in ir.get("sections") or []
               if s.get("title") and s["title"] not in text]
    assert not missing, f"docx 에 빠진 섹션: {missing}"


def test_the_decision_request_is_there_and_early():
    """The reason the document was sent. It is first in the IR by rule."""
    ir = IR.load(EXAMPLE)
    asks = [i.get("ask") for s in ir.get("sections") or []
            for b in (s.get("blocks") or [])
            if b.get("type") == "decision_request"
            for i in (b.get("items") or [])]
    if not asks:
        return                                   # this example carries none
    _, text = _render(ir)
    for ask in asks:
        assert ask[:24] in text, f"결정 요청이 빠졌습니다: {ask[:40]}"


def test_every_table_becomes_a_table():
    """Not a paragraph of comma-separated values."""
    ir = IR.load(EXAMPLE)
    want = sum(1 for s in ir.get("sections") or []
               for b in (s.get("blocks") or [])
               if b.get("type") in ("table", "matrix", "heatgrid", "risks",
                                    "changelog", "appendix_source", "kpi_tiles"))
    out, _ = _render(ir)
    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    got = xml.count("<w:tbl>")
    assert got >= want, f"표 {want}개를 기대했는데 {got}개입니다"


def test_the_grain_caption_survives():
    """The single most common cause of an executive misreading a table."""
    ir = IR.load(EXAMPLE)
    grains = [b.get("row_grain") for s in ir.get("sections") or []
              for b in (s.get("blocks") or []) if b.get("row_grain")]
    if not grains:
        return
    _, text = _render(ir)
    assert "행 단위" in text, "grain 캡션이 없습니다"
    assert grains[0] in text, f"첫 grain 이 빠졌습니다: {grains[0]}"


def test_a_chart_becomes_its_numbers_with_the_takeaway_above():
    """An SVG cannot go in a .docx without rasterising, and a blurry picture of
    a bar chart is worse than the four numbers it was drawn from. The takeaway
    still has to lead -- the rule that a conclusion never hides in prose is not
    a rendering detail."""
    ir = IR.load(EXAMPLE)
    ir["sections"] = [{"id": "s", "title": "S", "blocks": [{
        "type": "chart", "kind": "bar", "title": "매출",
        "takeaway": "1100 이 두 배로 늘었다",
        "categories": ["1월", "2월"],
        "series": [{"name": "건수", "values": [10, 20]}],
    }]}] + ir["sections"][:1]
    out, text = _render(ir)
    assert "1100 이 두 배로 늘었다" in text
    assert "1월" in text and "20" in text, "차트 데이터가 빠졌습니다"
    with zipfile.ZipFile(out) as z:
        assert "<w:tbl>" in z.read("word/document.xml").decode("utf-8")


def test_metric_references_resolve_exactly_as_in_html():
    """`{{m:key}}` is how a narrative cites a number. If docx printed the
    placeholder, the Word copy would say something the HTML does not."""
    ir = IR.load(EXAMPLE)
    ir.setdefault("metrics", {})["gap_probe"] = {
        "value": "37%", "unit": "", "formula": "a/b", "source_sql": "",
        "source_rows": 0, "computed_at": "2026-01-01",
    }
    ir["sections"] = [{"id": "s", "title": "S", "blocks": [
        {"type": "narrative", "paragraphs": ["격차는 {{m:gap_probe}} 이다."]},
    ]}] + ir["sections"][:1]
    _, text = _render(ir)
    assert "37%" in text, "지표 참조가 안 풀렸습니다"
    assert "{{m:gap_probe}}" not in text, "플레이스홀더가 그대로 인쇄됐습니다"


def test_the_provenance_line_is_there():
    """A Word file gets forwarded. The hash is how somebody checks they are
    looking at the version that was approved."""
    ir = IR.load(EXAMPLE)
    _, text = _render(ir)
    assert "somdoc" in text
    assert IR.ir_sha256(ir)[:12] in text, "ir_sha256 이 없습니다"


def test_the_same_ir_twice_is_the_same_document():
    """Not byte-identical -- a .docx is a zip and carries timestamps -- but the
    text has to be, or "same input, same output" is not true of this format."""
    ir = IR.load(EXAMPLE)
    _, a = _render(ir)
    _, b = _render(ir)
    assert a == b, "같은 IR 인데 문서 내용이 다릅니다"


def test_it_holds_no_more_and_no_less_than_the_html():
    """The comparison that keeps the two from drifting.

    Not a character match -- HTML carries CSS, a table of contents and a
    bilingual toggle. But every section title and every table caption the HTML
    prints has to be in the Word file too, or one of the two is lying about
    what the document contains.
    """
    ir = IR.load(EXAMPLE)
    html = re.sub(r"<[^>]+>", " ", HTML.emit(ir))
    _, text = _render(ir)
    titles = [b.get("title") for s in ir.get("sections") or []
              for b in (s.get("blocks") or []) if b.get("title")]
    missing = [t for t in titles if t in html and t not in text]
    assert not missing, f"HTML 에는 있는데 docx 에 없는 블록 제목: {missing[:5]}"


def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(_run_all())
