"""
Guarded bridge between a *.somdoc.json IR and the `humanize-korean` skill.

Why this module exists
----------------------
`humanize-korean` rewrites Korean prose. It is an LLM pass, so it is not
deterministic. The somdoc renderer *is* deterministic and is byte-compared by
the golden test. Therefore humanizing happens during **IR authoring**, upstream
of the renderer: the IR that gets committed is already humanized, and
`python -m somdoc build` stays reproducible.

Two commands:

    extract  IR -> a single markdown payload of humanizable prose, plus a
             manifest recording every field's original text and invariants.
    apply    humanized markdown + manifest -> updated IR, with every field
             checked against its invariants. A field that fails any check is
             rolled back to the original and reported. Meaning preservation
             outranks naturalness, and that decision is made by code here, not
             by a prompt.

Only whitelisted prose fields are ever sent. Data, provenance, identifiers, and
normative wording (KPI definition sentences and formulas) are structurally
excluded -- see PROSE_FIELDS and DENY_PATH_TOKENS.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

if hasattr(sys.stdout, "reconfigure"):          # Korean output on Windows consoles
    sys.stdout.reconfigure(encoding="utf-8")

MARKER_OPEN = "<!-- SOM-F {id} -->"
MARKER_CLOSE = "<!-- /SOM-F {id} -->"
_MARKER_RE = re.compile(
    r"<!--\s*SOM-F\s+(?P<id>\d{4})\s*-->\s*\n(?P<body>.*?)\n?\s*<!--\s*/SOM-F\s+(?P=id)\s*-->",
    re.DOTALL,
)

# ---------------------------------------------------------------------------
# What may be rewritten. Block type -> field paths within that block.
# A path segment ending in "[]" means "every element of this list".
# ---------------------------------------------------------------------------
PROSE_FIELDS: dict[str, tuple[str, ...]] = {
    "section":          ("intro",),
    "bullets":          ("items[].text",),
    "narrative":        ("paragraphs[]",),
    "callout":          ("body",),
    "decision_request": ("items[].ask", "items[].rationale"),
    "chart":            ("takeaway",),
    "table":            ("caption", "note"),
    "matrix":           ("caption", "note"),
    "heatgrid":         ("caption", "note"),
    "risks":            ("items[].mitigation",),
}

# Any candidate whose JSON path contains one of these segments is never sent,
# even if a block type above would otherwise allow it. Defence in depth against
# a skeleton that later grows a same-named field somewhere sensitive.
DENY_PATH_TOKENS: frozenset[str] = frozenset({
    "docmeta", "appendix_source", "changelog", "kpi_tiles",
    "rows", "cells", "columns", "header", "headers", "data",
    "metrics", "source", "sources", "sql", "formula", "definition",
    "id", "key", "role", "owner", "title", "row_grain", "unit",
})

# Rewrite length vs original. The floor is deliberately loose: stripping Korean
# AI padding ("~하는 것을 목적으로 합니다" -> "~하고자 한다") legitimately cuts
# 30-45%, which is the whole point of the pass. Measurable content loss is caught
# by the digit / ref / term checks, not by length. The ceiling stays tight
# because invented padding is always suspicious.
MIN_RATIO, MAX_RATIO = 0.45, 1.60
MIN_CHARS = 12                          # shorter than this is not worth a pass

_NUM_RE = re.compile(r"\d[\d,   ]*(?:\.\d+)?")
_REF_RE = re.compile(r"\{\{[^}]*\}\}")
_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]+")
_SEP_RE = re.compile(r"[,   ]")


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def digits_of(text: str) -> Counter:
    """Multiset of numeric values, thousands separators normalised away."""
    out: Counter = Counter()
    for m in _NUM_RE.finditer(text):
        out[_SEP_RE.sub("", m.group(0)).rstrip(".")] += 1
    return out


def refs_of(text: str) -> Counter:
    """Multiset of metric placeholders such as {{m:gap_rate_1100}}."""
    return Counter(m.group(0) for m in _REF_RE.finditer(text))


def terms_of(text: str) -> Counter:
    """ASCII identifiers worth protecting: SALESORG, ANALYTICS, DIM_PRODUCT, KPI...

    Lowercase words of four characters or fewer are ordinary English and are not
    protected -- protecting them would block legitimate rewrites.
    """
    out: Counter = Counter()
    for m in _TERM_RE.finditer(text):
        t = m.group(0)
        if t.upper() == t or "_" in t or "." in t or len(t) > 4:
            out[t] += 1
    return out


# ---------------------------------------------------------------------------
# IR walking
# ---------------------------------------------------------------------------
def _resolve(spec: str, node: Any, base: str) -> Iterator[tuple[str, str]]:
    """Yield (json_path, text) for one PROSE_FIELDS spec against one node."""
    head, _, rest = spec.partition(".")
    if head.endswith("[]"):
        name = head[:-2]
        seq = node.get(name) if isinstance(node, dict) else None
        if not isinstance(seq, list):
            return
        for i, item in enumerate(seq):
            path = f"{base}.{name}[{i}]"
            if rest:
                yield from _resolve(rest, item, path)
            elif isinstance(item, str):
                yield path, item
        return
    if not isinstance(node, dict):
        return
    val = node.get(head)
    path = f"{base}.{head}"
    if rest:
        yield from _resolve(rest, val, path)
    elif isinstance(val, str):
        yield path, val


def _denied(path: str) -> str | None:
    for seg in re.split(r"[.\[\]]+", path):
        if seg and seg in DENY_PATH_TOKENS:
            return seg
    return None


def candidates(ir: dict) -> list[dict]:
    """Every humanizable field in document order, with its invariants."""
    found: list[dict] = []
    for si, section in enumerate(ir.get("sections") or []):
        for spec in PROSE_FIELDS.get("section", ()):
            for path, text in _resolve(spec, section, f"sections[{si}]"):
                found.append({"path": path, "text": text, "block": "section"})
        for bi, block in enumerate(section.get("blocks") or []):
            btype = block.get("type")
            base = f"sections[{si}].blocks[{bi}]"
            for spec in PROSE_FIELDS.get(btype, ()):
                for path, text in _resolve(spec, block, base):
                    found.append({"path": path, "text": text, "block": btype})

    out: list[dict] = []
    for c in found:
        if _denied(c["path"]):
            continue
        text = _nfc(c["text"]).strip()
        if len(text) < MIN_CHARS:
            continue
        c["text"] = text
        c["digits"] = digits_of(text)
        c["refs"] = refs_of(text)
        c["terms"] = terms_of(text)
        out.append(c)
    for i, c in enumerate(out, start=1):
        c["id"] = f"{i:04d}"
    return out


def set_at(ir: dict, path: str, value: str) -> None:
    """Assign into the IR along a path emitted by _resolve()."""
    steps = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]", path)
    node: Any = ir
    for idx, (name, num) in enumerate(steps):
        last = idx == len(steps) - 1
        if name:
            if last:
                node[name] = value
                return
            node = node[name]
        else:
            i = int(num)
            if last:
                node[i] = value
                return
            node = node[i]


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------
def cmd_extract(args: argparse.Namespace) -> int:
    ir = json.loads(Path(args.ir).read_text(encoding="utf-8"))
    cands = candidates(ir)
    if not cands:
        print("humanize-extract: no prose fields eligible; nothing to do.")
        return 4

    payload = "\n\n".join(
        f"{MARKER_OPEN.format(id=c['id'])}\n{c['text']}\n{MARKER_CLOSE.format(id=c['id'])}"
        for c in cands
    )
    out_md = Path(args.out)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(payload + "\n", encoding="utf-8")

    manifest = {
        "ir_path": str(Path(args.ir).resolve()),
        "payload_path": str(out_md.resolve()),
        "field_count": len(cands),
        "char_count": sum(len(c["text"]) for c in cands),
        "fields": [
            {
                "id": c["id"],
                "path": c["path"],
                "block": c.get("block"),
                "original": c["text"],
                "digits": dict(c["digits"]),
                "refs": dict(c["refs"]),
                "terms": dict(c["terms"]),
            }
            for c in cands
        ],
    }
    Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)
    Path(args.manifest).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"humanize-extract: {len(cands)} fields, {manifest['char_count']} chars\n"
        f"  payload  : {out_md}\n"
        f"  manifest : {args.manifest}\n"
        "Markers <!-- SOM-F NNNN --> delimit each field. Keep every marker line "
        "byte-identical; do not reorder, merge, split, or drop blocks."
    )
    return 0


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------
def _check(field: dict, new: str) -> list[str]:
    """Deterministic invariants. Meaning preservation outranks naturalness."""
    problems: list[str] = []
    old = field["original"]
    if not new.strip():
        return ["empty rewrite"]

    exp_d, got_d = Counter(field["digits"]), digits_of(new)
    if exp_d != got_d:
        lost = sorted((exp_d - got_d).elements())
        added = sorted((got_d - exp_d).elements())
        if lost:
            problems.append(f"numbers lost: {lost}")
        if added:
            problems.append(f"numbers invented: {added}")

    exp_r, got_r = Counter(field["refs"]), refs_of(new)
    if exp_r != got_r:
        problems.append(
            f"metric refs changed: lost={sorted((exp_r - got_r).elements())} "
            f"added={sorted((got_r - exp_r).elements())}"
        )

    exp_t, got_t = Counter(field["terms"]), terms_of(new)
    lost_t = sorted((exp_t - got_t).elements())
    if lost_t:
        problems.append(f"protected terms lost: {lost_t}")

    ratio = len(new) / max(len(old), 1)
    if not (MIN_RATIO <= ratio <= MAX_RATIO):
        problems.append(f"length ratio {ratio:.2f} outside [{MIN_RATIO}, {MAX_RATIO}]")
    return problems


def cmd_apply(args: argparse.Namespace) -> int:
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    ir_path = Path(args.ir or manifest["ir_path"])
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    rewritten = Path(args.humanized).read_text(encoding="utf-8")

    blocks = {
        m.group("id"): _nfc(m.group("body")).strip()
        for m in _MARKER_RE.finditer(rewritten)
    }
    by_id = {f["id"]: f for f in manifest["fields"]}

    applied: list[tuple[str, str, int, int]] = []
    unchanged: list[str] = []
    rolled_back: list[tuple[str, str, list[str]]] = []
    missing: list[tuple[str, str]] = []

    for fid, field in sorted(by_id.items()):
        new = blocks.get(fid)
        if new is None:
            missing.append((fid, field["path"]))
            continue
        if new == field["original"]:
            unchanged.append(fid)
            continue
        problems = _check(field, new)
        if problems:
            rolled_back.append((fid, field["path"], problems))
            continue
        set_at(ir, field["path"], new)
        applied.append((fid, field["path"], len(field["original"]), len(new)))

    stray = sorted(set(blocks) - set(by_id))
    total = len(by_id)
    base_chars = sum(len(f["original"]) for f in manifest["fields"])
    # Share of the prose that was rewritten, weighted by original length -- the
    # same shape as humanize-korean's own 30% / 50% change-rate gates, so the two
    # numbers are comparable.
    rewritten_base = sum(a[2] for a in applied)
    change_rate = rewritten_base / max(base_chars, 1)

    report = {
        "ir_path": str(ir_path),
        "fields_total": total,
        "applied": len(applied),
        "unchanged": len(unchanged),
        "rolled_back": len(rolled_back),
        "missing_markers": len(missing),
        "stray_markers": stray,
        "prose_change_rate": round(change_rate, 4),
        "applied_detail": [
            {"id": i, "path": p, "len_before": a, "len_after": b} for i, p, a, b in applied
        ],
        "rolled_back_detail": [
            {"id": i, "path": p, "problems": pr} for i, p, pr in rolled_back
        ],
        "missing_detail": [{"id": i, "path": p} for i, p in missing],
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if not args.dry_run and applied:
        ir_path.write_text(
            json.dumps(ir, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    verb = "would apply" if args.dry_run else "applied"
    print(
        f"humanize-apply: {verb} {len(applied)}/{total} fields "
        f"(unchanged {len(unchanged)}, rolled back {len(rolled_back)}, "
        f"missing {len(missing)}), prose change rate {change_rate:.1%}"
    )
    for fid, path, problems in rolled_back:
        print(f"  ROLLBACK {fid} {path}")
        for p in problems:
            print(f"           - {p}")
    for fid, path in missing:
        print(f"  MISSING  {fid} {path} (marker absent from humanized payload)")
    if stray:
        print(f"  STRAY    marker ids not in manifest: {stray}")
    print(f"  report   : {args.report}")

    # Exit 2 means a human must look. Anything rolled back or missing is a real
    # signal: either the humanizer drifted, or the markers were disturbed.
    if rolled_back or missing or stray:
        print(
            "\nSOMDOC-HUMANIZE-HOLD  some fields were not accepted. The IR keeps the "
            "original text for those. Review the report before rendering."
        )
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="somdoc-humanize",
        description="Guarded bridge between a somdoc IR and the humanize-korean skill.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="IR -> markdown payload + manifest")
    e.add_argument("--ir", required=True)
    e.add_argument("--out", required=True, help="markdown payload to hand to humanize-korean")
    e.add_argument("--manifest", required=True)
    e.set_defaults(fn=cmd_extract)

    a = sub.add_parser("apply", help="humanized markdown + manifest -> updated IR")
    a.add_argument("--manifest", required=True)
    a.add_argument("--humanized", required=True)
    a.add_argument("--ir", help="override the IR path recorded in the manifest")
    a.add_argument("--report", required=True)
    a.add_argument("--dry-run", action="store_true")
    a.set_defaults(fn=cmd_apply)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
