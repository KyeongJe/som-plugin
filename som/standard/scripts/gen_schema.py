"""Generate standard/schema/somdoc.schema.json from engine/somdoc/ir.py.

Generated, not hand-written, on purpose: the validator in ir.py is the
authority. A hand-maintained schema drifts from it silently, and then the
document that tells an author what is allowed disagrees with the code that
enforces it.

    PYTHONPATH=engine python standard/scripts/gen_schema.py
    PYTHONPATH=engine python standard/scripts/gen_schema.py --check   # CI
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SOM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOM / "engine"))

from somdoc import ir as IR  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

OUT = SOM / "standard" / "schema" / "somdoc.schema.json"

BLOCKS = {
    "decision_request": {
        "required": ["items"], "item_required": ["ask", "rationale"],
        "item_optional": ["owner", "due", "options"]},
    "kpi_tiles": {
        "required": ["items"], "item_required": ["label", "value"],
        "item_optional": ["unit", "note", "state"]},
    "bullets": {
        "required": ["items"], "item_required": ["text"], "item_optional": ["emph"]},
    "narrative": {"required": ["paragraphs"]},
    "callout": {"required": ["kind", "body"], "optional": ["title"]},
    "table": {
        "required": ["row_grain", "columns", "rows"],
        "optional": ["title", "caption", "note", "filter_keys", "searchable"],
        "column_required": ["label"],
        "column_optional": ["key", "align", "core", "sticky", "digits",
                            "suffix", "label_en"]},
    "matrix": {
        "required": ["rows", "columns", "cells"],
        "optional": ["title", "corner", "caption", "note"]},
    "heatgrid": {
        "required": ["rows", "columns", "cells"],
        "optional": ["title", "flags", "scale_max", "caption", "note"],
        "note": 'flags: null | "gap" (담당 공백) | "conc" (단일 인원 의존)'},
    "chart": {
        "required": ["kind", "takeaway", "categories", "series"],
        "optional": ["title", "y_unit", "height", "label_width", "src"]},
    "diagram": {
        "required": ["svg"], "optional": ["title", "takeaway"],
        "note": "inline SVG, embedded not linked, so the document stays self-contained"},
    "risks": {
        "required": ["items"],
        "item_required": ["risk", "impact", "mitigation", "owner"],
        "item_optional": ["state", "state_label"]},
    "changelog": {
        "required": ["items"], "item_required": ["when", "what", "why", "who"]},
    "appendix_source": {
        "required": ["items"], "item_required": ["name", "as_of"],
        "item_optional": ["path", "sheet", "rows"],
        "note": "a source without an as_of date is not provenance"},
}


def build() -> dict:
    missing = set(IR.BLOCK_TYPES) - set(BLOCKS)
    if missing:
        raise SystemExit(
            f"gen_schema: ir.py declares block types this generator does not "
            f"describe: {sorted(missing)}. Add them here, then regenerate.")

    blocks = {k: dict(v) for k, v in BLOCKS.items()}
    blocks["decision_request"]["note"] = (
        f"at most {IR.MAX_DECISIONS} items, within the first "
        f"{IR.DECISION_WITHIN_SECTIONS} sections (R5)")
    blocks["kpi_tiles"]["note"] = "state: " + "|".join(IR.BADGE_STATES)
    blocks["callout"]["note"] = "kind: " + "|".join(IR.CALLOUT_KINDS)
    blocks["table"]["note"] = (
        f"align: {'|'.join(IR.ALIGNS)}. row_grain is required (R1). "
        f"> {IR.WIDE_TABLE_COLS} columns needs at least one core:true. "
        f"rows must be rectangular (R6)")
    blocks["chart"]["note"] = (
        f"kind: {'|'.join(IR.CHART_KINDS)}. takeaway is required and renders "
        f"above the chart in bold (R2). At most 4 series.")

    return {
        "$comment": "Generated from engine/somdoc/ir.py by "
                    "standard/scripts/gen_schema.py. Do not hand-edit: the "
                    "validator is the authority and this file would drift.",
        "sds_version": IR.SDS_VERSION,
        "engine_version": IR.ENGINE_VERSION,
        "envelope": {
            "required": ["sds_version", "doc_type", "theme", "docmeta", "sections"],
            "optional": ["lang", "bilingual", "metrics"],
            "doc_type": list(IR.DOC_TYPES),
            "theme": list(IR.THEMES)},
        "docmeta": {
            "required": ["title", "version", "as_of"],
            "optional": ["subtitle", "slug", "org", "author", "approver",
                         "next_review", "classification"],
            "note": "as_of and next_review are YYYY-MM-DD. R4."},
        "section": {
            "required": ["id", "title", "blocks"],
            "optional": ["title_en", "intro", "derived"],
            "note": "ids must be unique"},
        "metrics": {
            "entry_required": ["formula"],
            "entry_one_of": ["value", "display"],
            "entry_optional": ["unit", "source_sql", "source_rows", "computed_at"],
            "note": "R7: formula is required. Reference a metric from prose as "
                    "{{m:key}}; write {{!m:key}} to render the literal syntax."},
        "blocks": blocks,
        "rules": {
            "R1": "every table declares row_grain",
            "R2": "every chart declares takeaway",
            "R3": "every {{m:key}} resolves in metrics",
            "R4": "docmeta has title, version, as_of",
            "R5": f"a decision_request exists within the first "
                  f"{IR.DECISION_WITHIN_SECTIONS} sections, at most "
                  f"{IR.MAX_DECISIONS} asks (rnr/kpi/charter)",
            "R6": "table and chart shapes are rectangular",
            "R7": "every metric carries a formula"},
        "constants": {
            "WIDE_TABLE_COLS": IR.WIDE_TABLE_COLS,
            "SEARCHABLE_MIN": IR.SEARCHABLE_MIN,
            "MAX_DECISIONS": IR.MAX_DECISIONS,
            "DECISION_WITHIN_SECTIONS": IR.DECISION_WITHIN_SECTIONS},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="fail if the committed schema is stale")
    a = ap.parse_args()

    text = json.dumps(build(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if a.check:
        if not OUT.exists():
            print(f"gen_schema: {OUT} is missing. Run without --check.")
            return 1
        if OUT.read_text(encoding="utf-8") != text:
            print("gen_schema: the committed schema is stale. "
                  "Run `python standard/scripts/gen_schema.py` and commit it.")
            return 1
        print("gen_schema: committed schema matches ir.py")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"gen_schema: wrote {OUT}  ({OUT.stat().st_size:,} bytes, "
          f"{len(BLOCKS)} block types)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
