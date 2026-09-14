"""
somdoc IR: the one document representation, and the rules it must satisfy.

The IR is the only thing an agent authors. Skeletons, themes, and emitters are
plugin files, so four output formats stay consistent and the golden test can
byte-compare a render.

Validation here is not a linter -- it is the BLUEPRINT/ATTEST gate expressed as
code. Each rule exists because its absence produced a real failure in a document
that went to a real reader:

  R1  every table declares its row grain      "행 1개 = 거래선 × 주"
  R2  every chart declares its takeaway       a chart without a conclusion is a puzzle
  R3  every {{m:key}} resolves in `metrics`   prose numbers stay traceable
  R4  document control is complete            an undated, unversioned doc cannot be superseded
  R5  a decision request exists, near the top what am I being asked to approve
  R6  table/chart shapes are rectangular      a ragged row is silent data loss
  R7  metrics carry a formula                 KPI ownership lives in that field
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SDS_VERSION = 1
ENGINE_VERSION = "0.1.0"

DOC_TYPES = ("rnr", "kpi", "charter", "report")
THEMES = ("paper", "console")
BLOCK_TYPES = (
    "decision_request", "kpi_tiles", "bullets", "narrative", "callout",
    "table", "matrix", "heatgrid", "chart", "diagram", "risks",
    "changelog", "appendix_source",
)
CHART_KINDS = ("bar", "hbar", "line", "dot-strip", "heat-grid")
BADGE_STATES = ("ok", "warn", "crit", "info", "na")
CALLOUT_KINDS = ("info", "warn", "crit", "good")
ALIGNS = ("text", "num", "wrap", "badge")

# A table wider than this opens on a core-column view with a toggle. 22 columns
# cannot be read across on a laptop.
WIDE_TABLE_COLS = 14
# Columns with more distinct values than this get a search box.
SEARCHABLE_MIN = 20
MAX_DECISIONS = 3
# A decision request buried on page 6 is not a decision request.
DECISION_WITHIN_SECTIONS = 3

METRIC_REF_RE = re.compile(r"\{\{m:([A-Za-z0-9_.\-]+)\}\}")
# Escape hatch: {{!m:key}} renders as the literal text {{m:key}} and is not a
# reference. A document that documents the placeholder syntax needs this, and
# without it R3 flags its own explanation as a dangling reference.
ESCAPED_REF_RE = re.compile(r"\{\{!([^}]*)\}\}")
_ANY_REF_RE = re.compile(r"\{\{([^}]*)\}\}")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class IRError(Exception):
    """Raised when the IR violates a rule that a reader would be harmed by."""


# ---------------------------------------------------------------------------
# load / canonicalise
# ---------------------------------------------------------------------------
def load(path: str | Path) -> dict:
    """Read an IR, and fail in a sentence rather than a stack trace.

    `somdoc` already routes `IRError` to a one-line message and exit 1, but
    `load` raised the raw OSError and JSONDecodeError straight through, so the
    three most ordinary mistakes -- a path typo, a trailing comma, pointing at
    a directory -- each produced twenty lines of CPython internals ending in
    `pathlib.py line 1044`. The person reading that has to work out that the
    file simply is not there.

    The typo case is the common one: `skills/doc-standard/SKILL.md` tells you
    to run `validate ir/som-rnr.somdoc.json`, and anyone who has not created it
    yet meets a traceback on their first command.
    """
    p = Path(path)
    if not p.exists():
        raise IRError(
            f"IR 파일이 없습니다: {p}\n"
            f"  현재 폴더: {Path.cwd()}\n"
            f"  스켈레톤에서 시작하려면: "
            f"cat <플러그인>/standard/skeletons/rnr.skeleton.json")
    if p.is_dir():
        raise IRError(f"IR 파일이 아니라 폴더입니다: {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise IRError(f"IR 파일을 읽지 못했습니다: {p}\n  {e.strerror}") from e
    try:
        ir = json.loads(text)
    except json.JSONDecodeError as e:
        line = text.splitlines()[e.lineno - 1] if 0 < e.lineno <= len(text.splitlines()) else ""
        raise IRError(
            f"IR 이 올바른 JSON 이 아닙니다: {p}\n"
            f"  {e.lineno}행 {e.colno}열: {e.msg}\n"
            f"  {line.strip()[:120]}") from e
    if not isinstance(ir, dict):
        raise IRError(
            f"IR 의 최상위는 객체여야 합니다: {p} 는 {type(ir).__name__} 입니다")
    return ir


def canonical_json(ir: dict) -> str:
    """Byte-stable serialisation. Sorted keys, no volatile fields.

    This is what the golden test and MANIFEST.json hash. Nothing time-derived
    may enter it -- `as_of` comes from the IR, never from the clock.
    """
    return json.dumps(ir, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def ir_sha256(ir: dict) -> str:
    return hashlib.sha256(canonical_json(ir).encode("utf-8")).hexdigest()


def dump(ir: dict, path: str | Path) -> None:
    Path(path).write_text(canonical_json(ir), encoding="utf-8")


# ---------------------------------------------------------------------------
# shape guards
# ---------------------------------------------------------------------------
def _objects(seq, at: str, what: str, add) -> list:
    """The entries of `seq` that are objects, reporting the ones that are not.

    A validator exists to turn a bad document into a list of sentences. Feeding
    it the shapes people actually write on a first attempt -- `"columns":
    ["이름", "역할"]`, `"items": ["첫째"]` -- made it raise AttributeError from
    inside a list comprehension instead: 14 of 21 wrong-shaped documents ended
    in a CPython traceback rather than a sentence naming the field.

    Reporting and skipping keeps every *other* problem in the document visible,
    which is the difference between one fix-and-rerun cycle and eight.
    """
    if not isinstance(seq, list):
        add(f"{at} must be a list of {what} objects")
        return []
    out = []
    for i, item in enumerate(seq):
        if isinstance(item, dict):
            out.append(item)
        else:
            got = "null" if item is None else type(item).__name__
            add(f"{at}[{i}] must be a {what} object, not {got}"
                + (f' (예: {{"label": {item!r}}})' if isinstance(item, str) else ""))
    return out


# ---------------------------------------------------------------------------
# walking helpers, shared with the emitters
# ---------------------------------------------------------------------------
def iter_blocks(ir: dict):
    """Yield (section_index, block_index, section, block).

    Non-dict sections and blocks are skipped: `validate` reports them by shape,
    and the emitters must not crash walking a document `validate` already
    refused.
    """
    sections = ir.get("sections")
    if not isinstance(sections, list):
        return
    for si, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        blocks = section.get("blocks")
        if not isinstance(blocks, list):
            continue
        for bi, block in enumerate(blocks):
            if isinstance(block, dict):
                yield si, bi, section, block


def iter_prose(ir: dict):
    """Yield (path, text) for every string a human reads.

    Used for metric-reference resolution. Deliberately broader than the
    humanize whitelist: a dangling {{m:...}} is a defect wherever it appears.
    """
    def walk(node: Any, path: str):
        if isinstance(node, str):
            yield path, node
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from walk(v, f"{path}[{i}]")
        elif isinstance(node, dict):
            for k, v in sorted(node.items()):
                yield from walk(v, f"{path}.{k}" if path else k)

    yield from walk(ir.get("sections") or [], "sections")


def metric_refs(ir: dict) -> dict[str, list[str]]:
    """key -> [paths that reference it]"""
    out: dict[str, list[str]] = {}
    for path, text in iter_prose(ir):
        for m in METRIC_REF_RE.finditer(text):
            out.setdefault(m.group(1), []).append(path)
    return out



def resolve_metrics(text: str, metrics: dict) -> str:
    """Substitute {{m:key}} with the metric's display value.

    Formatting is the metric's own business (`display`, else `value` + `unit`),
    so the same number never renders two ways in one document.
    """
    def sub(m: re.Match) -> str:
        key = m.group(1)
        spec = metrics.get(key)
        if spec is None:
            return m.group(0)
        if spec.get("display") is not None:
            return str(spec["display"])
        val = spec.get("value")
        if isinstance(val, float):
            txt = f"{val:,.2f}".rstrip("0").rstrip(".")
        elif isinstance(val, int):
            txt = f"{val:,}"
        else:
            txt = str(val)
        unit = spec.get("unit") or ""
        return f"{txt}{unit}"

    # Live references first; then unescape, so "{{!m:key}}" renders as the
    # literal "{{m:key}}" and is never substituted.
    out = METRIC_REF_RE.sub(sub, text)
    return ESCAPED_REF_RE.sub(lambda m: "{{" + m.group(1) + "}}", out)



def column_is_searchable(rows: list[list], idx: int) -> bool:
    seen = set()
    for r in rows:
        if idx < len(r):
            seen.add(str(r[idx]))
            if len(seen) > SEARCHABLE_MIN:
                return True
    return False


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
def validate(ir: dict, *, strict: bool = True) -> list[str]:
    """Return a list of problems. Empty list means the IR is renderable.

    `strict=False` downgrades the doc-type-specific structural rules (R5) so a
    partial draft can still be previewed mid-authoring.
    """
    p: list[str] = []
    add = p.append

    # ---- envelope ----
    if ir.get("sds_version") != SDS_VERSION:
        add(f"sds_version must be {SDS_VERSION}, got {ir.get('sds_version')!r}")
    dt = ir.get("doc_type")
    if dt not in DOC_TYPES:
        add(f"doc_type must be one of {DOC_TYPES}, got {dt!r}")
    th = ir.get("theme")
    if th not in THEMES:
        add(f"theme must be one of {THEMES}, got {th!r}")

    # ---- R4 document control ----
    meta = ir.get("docmeta") or {}
    for key in ("title", "version", "as_of"):
        if not str(meta.get(key) or "").strip():
            add(f"docmeta.{key} is required (R4: an undated, unversioned document cannot be superseded)")
    if meta.get("as_of") and not DATE_RE.match(str(meta["as_of"])):
        add(f"docmeta.as_of must be YYYY-MM-DD, got {meta['as_of']!r}")
    if meta.get("next_review") and not DATE_RE.match(str(meta["next_review"])):
        add(f"docmeta.next_review must be YYYY-MM-DD, got {meta['next_review']!r}")

    # ---- sections ----
    sections = ir.get("sections")
    if not isinstance(sections, list) or not sections:
        add("sections must be a non-empty list")
        sections = []
    seen_ids: set[str] = set()
    for si, s in enumerate(sections):
        if not isinstance(s, dict):
            got = "null" if s is None else type(s).__name__
            add(f"sections[{si}] must be an object, not {got}")
            continue
        sid = s.get("id")
        if not sid:
            add(f"sections[{si}].id is required")
        elif sid in seen_ids:
            add(f"sections[{si}].id {sid!r} is duplicated")
        else:
            seen_ids.add(sid)
        if not str(s.get("title") or "").strip():
            add(f"sections[{si}].title is required")
        if not isinstance(s.get("blocks"), list):
            add(f"sections[{si}].blocks must be a list")
        else:
            for bi, b in enumerate(s["blocks"]):
                if not isinstance(b, dict):
                    got = "null" if b is None else type(b).__name__
                    add(f"sections[{si}].blocks[{bi}] must be an object with a "
                        f"`type`, not {got}")

    # ---- blocks ----
    decision_at: list[int] = []
    for si, bi, _s, b in iter_blocks(ir):
        at = f"sections[{si}].blocks[{bi}]"
        bt = b.get("type")
        if bt not in BLOCK_TYPES:
            add(f"{at}.type {bt!r} is not a known block type")
            continue

        if bt == "table":
            # R1 -- row grain
            if not str(b.get("row_grain") or "").strip():
                add(f"{at}.row_grain is required (R1: state what one row is)")
            cols = b.get("columns") or []
            rows = b.get("rows") or []
            if not cols:
                add(f"{at}.columns is required")
            for ci, c in enumerate(_objects(cols, f"{at}.columns", "column", add)):
                if not str(c.get("label") or "").strip():
                    add(f"{at}.columns[{ci}].label is required")
                al = c.get("align", "text")
                if al not in ALIGNS:
                    add(f"{at}.columns[{ci}].align {al!r} must be one of {ALIGNS}")
            # R6 -- rectangular
            for ri, r in enumerate(rows):
                if not isinstance(r, list):
                    add(f"{at}.rows[{ri}] must be a list")
                elif len(r) != len(cols):
                    add(f"{at}.rows[{ri}] has {len(r)} cells, expected {len(cols)} (R6)")
            if len(cols) > WIDE_TABLE_COLS and not any(
                    isinstance(c, dict) and c.get("core") for c in cols):
                add(f"{at} has {len(cols)} columns (> {WIDE_TABLE_COLS}) so at least one "
                    f"column must be marked core:true for the default view")

        elif bt == "chart":
            # R2 -- takeaway
            if not str(b.get("takeaway") or "").strip():
                add(f"{at}.takeaway is required (R2: state the conclusion above the chart)")
            if b.get("kind") not in CHART_KINDS:
                add(f"{at}.kind {b.get('kind')!r} must be one of {CHART_KINDS}")
            cats = b.get("categories") or []
            series = b.get("series") or []
            if not cats:
                add(f"{at}.categories is required")
            if not series:
                add(f"{at}.series is required")
            if len(series) > 4:
                add(f"{at} has {len(series)} series; the standard allows 4 "
                    f"(split the chart rather than adding a fifth colour)")
            for qi, q in enumerate(_objects(series, f"{at}.series", "series", add)):
                vals = q.get("values") or []
                if len(vals) != len(cats):
                    add(f"{at}.series[{qi}].values has {len(vals)} points, "
                        f"expected {len(cats)} to match categories (R6)")
                if not str(q.get("name") or "").strip():
                    add(f"{at}.series[{qi}].name is required")

        elif bt == "decision_request":
            decision_at.append(si)
            items = b.get("items") or []
            if not items:
                add(f"{at}.items must not be empty")
            if len(items) > MAX_DECISIONS:
                add(f"{at} has {len(items)} asks; at most {MAX_DECISIONS} "
                    f"(more than three decisions is a status update, not a request)")
            for ii, it in enumerate(_objects(items, f"{at}.items", "ask", add)):
                if not str(it.get("ask") or "").strip():
                    add(f"{at}.items[{ii}].ask is required")
                if not str(it.get("rationale") or "").strip():
                    add(f"{at}.items[{ii}].rationale is required")

        elif bt == "kpi_tiles":
            for ii, it in enumerate(_objects(b.get("items") or [], f"{at}.items", "tile", add)):
                if not str(it.get("label") or "").strip():
                    add(f"{at}.items[{ii}].label is required")
                if it.get("value") is None:
                    add(f"{at}.items[{ii}].value is required")
                st = it.get("state")
                if st is not None and st not in BADGE_STATES:
                    add(f"{at}.items[{ii}].state {st!r} must be one of {BADGE_STATES}")

        elif bt == "callout":
            if b.get("kind") not in CALLOUT_KINDS:
                add(f"{at}.kind {b.get('kind')!r} must be one of {CALLOUT_KINDS}")
            if not str(b.get("body") or "").strip():
                add(f"{at}.body is required")

        elif bt in ("matrix", "heatgrid"):
            rows = b.get("rows") or []
            cols = b.get("columns") or []
            cells = b.get("cells") or []
            if not rows or not cols:
                add(f"{at} requires both rows and columns")
            if len(cells) != len(rows):
                add(f"{at}.cells has {len(cells)} rows, expected {len(rows)} (R6)")
            for ri, r in enumerate(cells):
                if not isinstance(r, list) or len(r) != len(cols):
                    n = len(r) if isinstance(r, list) else "?"
                    add(f"{at}.cells[{ri}] has {n} cells, expected {len(cols)} (R6)")

        elif bt == "risks":
            for ii, it in enumerate(_objects(b.get("items") or [], f"{at}.items", "risk", add)):
                for key in ("risk", "impact", "mitigation", "owner"):
                    if not str(it.get(key) or "").strip():
                        add(f"{at}.items[{ii}].{key} is required")

        elif bt == "bullets":
            if not (b.get("items") or []):
                add(f"{at}.items must not be empty")

        elif bt == "appendix_source":
            for ii, it in enumerate(_objects(b.get("items") or [], f"{at}.items", "source", add)):
                for key in ("name", "as_of"):
                    if not str(it.get(key) or "").strip():
                        add(f"{at}.items[{ii}].{key} is required "
                            f"(a source without an as-of date is not provenance)")

    # ---- R5 decision request present and near the top ----
    if strict and dt in ("rnr", "kpi", "charter"):
        if not decision_at:
            add("R5: a decision_request block is required. State what the reader "
                "is being asked to approve.")
        elif min(decision_at) >= DECISION_WITHIN_SECTIONS:
            add(f"R5: the decision_request sits in section {min(decision_at) + 1}; "
                f"it must appear within the first {DECISION_WITHIN_SECTIONS} sections")

    # ---- R3 metric references resolve; R7 metrics carry a formula ----
    metrics = ir.get("metrics") or {}
    if not isinstance(metrics, dict):
        add("metrics must be an object keyed by metric name, not a list")
        metrics = {}
    refs = metric_refs(ir)
    for key, paths in sorted(refs.items()):
        if key not in metrics:
            add(f"R3: {{{{m:{key}}}}} is referenced at {paths[0]} but is not "
                f"defined in metrics")
    for key, spec in sorted(metrics.items()):
        if not isinstance(spec, dict):
            add(f"metrics.{key} must be an object")
            continue
        if spec.get("value") is None and spec.get("display") is None:
            add(f"metrics.{key} needs a value or a display string")
        if not str(spec.get("formula") or "").strip():
            add(f"R7: metrics.{key}.formula is required. The formula is where "
                f"metric ownership actually lives.")

    # ---- malformed placeholders ----
    for path, text in iter_prose(ir):
        for m in _ANY_REF_RE.finditer(text):
            inner = m.group(1)
            if inner.startswith("!"):
                continue          # documented syntax, not a reference
            if not inner.startswith("m:"):
                add(f"{path} contains an unrecognised placeholder {m.group(0)!r}; "
                    f"only {{{{m:key}}}} is supported")

    return p


def assert_valid(ir: dict, *, strict: bool = True) -> None:
    problems = validate(ir, strict=strict)
    if problems:
        lines = "\n".join(f"  - {x}" for x in problems)
        raise IRError(f"{len(problems)} IR problem(s):\n{lines}")
