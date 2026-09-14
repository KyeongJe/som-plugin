"""Tests for the IR rules and the golden render.

Each validation test names the rule it covers. These are not style checks --
every rule is here because its absence produced a document that misled a reader.

    python engine/tests/test_ir.py
    python -m pytest engine/tests -q
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
SOM = ENGINE.parent
sys.path.insert(0, str(ENGINE))

from somdoc import ir as IR                     # noqa: E402
from somdoc.emitters import html as HTML        # noqa: E402
from somdoc.emitters import xlsx as XLSX        # noqa: E402

EXAMPLE = SOM / "standard" / "examples" / "rnr.example.somdoc.json"
GOLDEN_HTML = SOM / "standard" / "examples" / "golden" / "rnr.html"
GOLDEN_XLSX_SHA = SOM / "standard" / "examples" / "golden" / "rnr.xlsx.sha256"

MINIMAL = {
    "sds_version": 1,
    "doc_type": "report",
    "theme": "console",
    "lang": "ko",
    "docmeta": {"title": "T", "version": "v1", "as_of": "2026-01-01"},
    "metrics": {},
    "sections": [{
        "id": "s1", "title": "S1",
        "blocks": [{"type": "bullets", "items": [{"text": "한 줄."}]}],
    }],
}


def _ir(**patch):
    d = copy.deepcopy(MINIMAL)
    d.update(patch)
    return d


def _problems(ir, **kw):
    return IR.validate(ir, **kw)


def _has(problems, needle):
    return any(needle in p for p in problems)


# --------------------------------------------------------------- baseline
def test_minimal_ir_is_valid():
    assert _problems(MINIMAL) == []


def test_docmeta_required_R4():
    ir = _ir()
    del ir["docmeta"]["as_of"]
    assert _has(_problems(ir), "docmeta.as_of is required")


def test_as_of_must_be_iso_date():
    ir = _ir()
    ir["docmeta"]["as_of"] = "2026/01/01"
    assert _has(_problems(ir), "must be YYYY-MM-DD")


def test_duplicate_section_id_is_rejected():
    ir = _ir()
    ir["sections"].append(copy.deepcopy(ir["sections"][0]))
    assert _has(_problems(ir), "is duplicated")


# ------------------------------------------------------------------- R1
def test_table_without_row_grain_is_rejected_R1():
    ir = _ir()
    ir["sections"][0]["blocks"] = [{
        "type": "table",
        "columns": [{"key": "a", "label": "A"}],
        "rows": [["x"]],
    }]
    assert _has(_problems(ir), "row_grain is required")


def test_table_with_row_grain_passes():
    ir = _ir()
    ir["sections"][0]["blocks"] = [{
        "type": "table", "row_grain": "행 1개 = 거래선 × 주",
        "columns": [{"key": "a", "label": "A"}], "rows": [["x"]],
    }]
    assert _problems(ir) == []


# ------------------------------------------------------------------- R2
def test_chart_without_takeaway_is_rejected_R2():
    ir = _ir()
    ir["sections"][0]["blocks"] = [{
        "type": "chart", "kind": "bar", "categories": ["a"],
        "series": [{"name": "s", "values": [1]}],
    }]
    assert _has(_problems(ir), "takeaway is required")


def test_chart_allows_at_most_four_series():
    ir = _ir()
    ir["sections"][0]["blocks"] = [{
        "type": "chart", "kind": "bar", "takeaway": "결론.", "categories": ["a"],
        "series": [{"name": f"s{i}", "values": [1]} for i in range(5)],
    }]
    assert _has(_problems(ir), "the standard allows 4")


# ------------------------------------------------------------------- R3
def test_dangling_metric_ref_is_rejected_R3():
    ir = _ir()
    ir["sections"][0]["blocks"] = [{
        "type": "bullets", "items": [{"text": "값은 {{m:nope}} 이다."}],
    }]
    assert _has(_problems(ir), "{{m:nope}}")


def test_metric_ref_resolves_when_defined():
    ir = _ir()
    ir["metrics"] = {"hit": {"value": 10.6, "unit": "%", "formula": "blocked / total"}}
    ir["sections"][0]["blocks"] = [{
        "type": "bullets", "items": [{"text": "비율은 {{m:hit}} 이다."}],
    }]
    assert _problems(ir) == []
    assert IR.resolve_metrics("비율은 {{m:hit}} 이다.", ir["metrics"]) == "비율은 10.6% 이다."


def test_unknown_placeholder_shape_is_rejected():
    ir = _ir()
    ir["sections"][0]["blocks"] = [{
        "type": "bullets", "items": [{"text": "값은 {{value}} 이다."}],
    }]
    assert _has(_problems(ir), "unrecognised placeholder")


# ------------------------------------------------------------------- R5
def test_rnr_requires_a_decision_request_R5():
    ir = _ir(doc_type="rnr", theme="paper")
    assert _has(_problems(ir), "decision_request block is required")
    # draft mode lets a partial document through
    assert not _has(_problems(ir, strict=False), "decision_request block is required")


def test_decision_request_must_be_near_the_top_R5():
    ask = {"type": "decision_request",
           "items": [{"ask": "승인해 달라.", "rationale": "근거."}]}
    ir = _ir(doc_type="rnr", theme="paper")
    ir["sections"] = [
        {"id": f"s{i}", "title": f"S{i}", "blocks": []} for i in range(4)
    ] + [{"id": "d", "title": "결정", "blocks": [ask]}]
    assert _has(_problems(ir), "within the first 3 sections")


def test_at_most_three_asks():
    ir = _ir(doc_type="rnr", theme="paper")
    ir["sections"][0]["blocks"] = [{
        "type": "decision_request",
        "items": [{"ask": f"a{i}", "rationale": "r"} for i in range(4)],
    }]
    assert _has(_problems(ir), "at most 3")


# ------------------------------------------------------------------- R6
def test_ragged_table_row_is_rejected_R6():
    ir = _ir()
    ir["sections"][0]["blocks"] = [{
        "type": "table", "row_grain": "행 1개 = x",
        "columns": [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}],
        "rows": [["x"]],
    }]
    assert _has(_problems(ir), "has 1 cells, expected 2")


def test_chart_series_length_must_match_categories_R6():
    ir = _ir()
    ir["sections"][0]["blocks"] = [{
        "type": "chart", "kind": "line", "takeaway": "결론.",
        "categories": ["a", "b", "c"],
        "series": [{"name": "s", "values": [1, 2]}],
    }]
    assert _has(_problems(ir), "expected 3 to match categories")


# ------------------------------------------------------------------- R7
def test_metric_without_formula_is_rejected_R7():
    ir = _ir()
    ir["metrics"] = {"x": {"value": 1}}
    assert _has(_problems(ir), "formula is required")


# ------------------------------------------------------- wide table guard
def test_wide_table_needs_core_columns():
    cols = [{"key": f"c{i}", "label": f"C{i}"} for i in range(16)]
    ir = _ir()
    ir["sections"][0]["blocks"] = [{
        "type": "table", "row_grain": "행 1개 = x",
        "columns": cols, "rows": [["v"] * 16],
    }]
    assert _has(_problems(ir), "core:true")


# ------------------------------------------------------------ determinism
def test_render_is_deterministic():
    ir = IR.load(EXAMPLE)
    assert HTML.emit(ir) == HTML.emit(ir)


def test_ir_hash_is_order_independent():
    ir = IR.load(EXAMPLE)
    shuffled = json.loads(json.dumps(ir))
    shuffled["docmeta"] = dict(reversed(list(shuffled["docmeta"].items())))
    assert IR.ir_sha256(ir) == IR.ir_sha256(shuffled)


def test_html_is_self_contained():
    import re
    out = HTML.emit(IR.load(EXAMPLE))
    body = re.sub(r"/\*.*?\*/", "", out, flags=re.S)      # drop CSS comments
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)     # drop HTML comments
    assert not re.search(r'(?:src|href)\s*=\s*["\']https?://', body), "outbound request"
    assert "@import" not in body
    assert "<link" not in body


def test_golden_html_is_byte_identical():
    assert GOLDEN_HTML.exists(), "golden reference missing; run somdoc golden once"
    got = HTML.emit(IR.load(EXAMPLE)).encode("utf-8")
    assert got == GOLDEN_HTML.read_bytes(), (
        "render drifted from the golden reference. If the change is intended, "
        "re-run `python -m somdoc golden ... --update` and commit the new reference."
    )


def test_golden_xlsx_content_digest():
    import tempfile
    assert GOLDEN_XLSX_SHA.exists(), "golden xlsx digest missing"
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.xlsx"
        XLSX.emit(IR.load(EXAMPLE), p)
        got = XLSX.content_digest(p)
    assert got == GOLDEN_XLSX_SHA.read_text(encoding="utf-8").strip()


# ----------------------------------------------------------- example doc
def test_example_validates_and_renders():
    ir = IR.load(EXAMPLE)
    assert IR.validate(ir, strict=False) == []
    out = HTML.emit(ir)
    assert "행 단위" in out, "row grain must be printed above tables"
    assert "sds-decision" in out, "decision request must render"


def test_cli_validate_exits_zero():
    r = subprocess.run(
        [sys.executable, "-m", "somdoc", "validate", str(EXAMPLE)],
        cwd=SOM, capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONPATH": str(ENGINE), "PYTHONUTF8": "1"},
    )
    assert r.returncode == 0, r.stdout + r.stderr


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
