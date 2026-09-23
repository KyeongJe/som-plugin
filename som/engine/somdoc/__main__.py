"""somdoc CLI.

    python -m somdoc validate <ir.json>
    python -m somdoc build    <ir.json> --emit html[,xlsx] [--out DIR] [--as-of DATE]
    python -m somdoc hash     <ir.json>
    python -m somdoc golden   <ir.json> --against <file>   # byte comparison

`build` is deterministic: the same IR produces byte-identical output on any
machine with the pinned dependency versions. That property is what lets a
teammate re-render a committed IR and get the same file, and it is checked by
`golden`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import ir as IR

ENGINE_VERSION = "1.0.0"


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _humanize_report_path(a: argparse.Namespace) -> str | None:
    """Where the humanize report would be, if that optional pass ran."""
    guess = Path(".som") / "humanize" / Path(a.ir).stem / "report.json"
    return str(guess) if guess.exists() else None

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

EMITTERS = ("html", "xlsx", "docx", "pptx")
IMPLEMENTED = ("html", "xlsx", "docx")


def _slug(ir: dict) -> str:
    m = ir.get("docmeta") or {}
    base = str(m.get("slug") or m.get("title") or "document")
    keep = [c if (c.isalnum() or c in "-_") else "-" for c in base]
    s = "".join(keep)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-") or "document"


def cmd_validate(a: argparse.Namespace) -> int:
    ir = IR.load(a.ir)
    problems = IR.validate(ir, strict=not a.draft)
    if not problems:
        n_sections = len(ir.get("sections") or [])
        n_blocks = sum(1 for _ in IR.iter_blocks(ir))
        print(f"somdoc validate: OK  ({n_sections} sections, {n_blocks} blocks, "
              f"{len(ir.get('metrics') or {})} metrics)")
        print(f"  ir_sha256 {IR.ir_sha256(ir)}")
        return 0
    print(f"somdoc validate: {len(problems)} problem(s)")
    for x in problems:
        print(f"  - {x}")
    return 1


def cmd_hash(a: argparse.Namespace) -> int:
    print(IR.ir_sha256(IR.load(a.ir)))
    return 0


def cmd_build(a: argparse.Namespace) -> int:
    ir = IR.load(a.ir)
    if a.as_of:
        ir.setdefault("docmeta", {})["as_of"] = a.as_of

    problems = IR.validate(ir)
    if problems:
        print(f"somdoc build: refusing to render, {len(problems)} IR problem(s)")
        for x in problems:
            print(f"  - {x}")
        return 1

    wanted = [w.strip() for w in a.emit.split(",") if w.strip()]
    unknown = [w for w in wanted if w not in EMITTERS]
    if unknown:
        print(f"somdoc build: unknown emitter(s) {unknown}; known: {list(EMITTERS)}")
        return 2
    todo = [w for w in wanted if w in IMPLEMENTED]
    later = [w for w in wanted if w not in IMPLEMENTED]

    out_dir = Path(a.out or Path(a.ir).parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = a.name or f"{_slug(ir)}_{(ir.get('docmeta') or {}).get('as_of', '')}".rstrip("_")

    written: list[Path] = []
    if "html" in todo:
        from .emitters import html as html_emitter
        p = out_dir / f"{stem}.html"
        p.write_text(html_emitter.emit(ir, embed_fonts=a.embed_fonts),
                     encoding="utf-8", newline="\n")
        written.append(p)
    skipped_missing: list[tuple[str, str, str]] = []
    if "xlsx" in todo:
        # A missing package is a capability this machine does not have, not a
        # crash. `skills/doc-standard/SKILL.md` tells everyone to run
        # `--emit html,xlsx`, and on a machine without openpyxl -- which
        # `/som:doctor` calls perfectly fine for documents -- that raised an
        # ImportError traceback *after* writing the HTML, so the bundle was
        # left with no MANIFEST and no ir/ copy and the person got a stack.
        try:
            from .emitters import xlsx as xlsx_emitter
        except ImportError as e:
            # `e.name` is the module that failed, but it is None for a bare
            # `raise ImportError(...)`, so the package this emitter needs is
            # named here rather than guessed from the exception.
            skipped_missing.append(("xlsx", "openpyxl", str(e)))
        else:
            p = out_dir / f"{stem}.xlsx"
            xlsx_emitter.emit(ir, p)
            written.append(p)
    if "docx" in todo:
        # Same contract as xlsx: a missing package is a capability this machine
        # lacks, reported in the manifest, not a traceback after the HTML has
        # already been written.
        try:
            from .emitters import docx as docx_emitter
        except ImportError as e:
            skipped_missing.append(("docx", "python-docx", str(e)))
        else:
            p = out_dir / f"{stem}.docx"
            docx_emitter.emit(ir, p)
            written.append(p)

    # The bundle the skill documents is four things, and two of them were
    # never produced: MANIFEST.json and the reproducible IR copy. `/som:doc`
    # tells the agent to "report the bundle paths", so half of what it reported
    # did not exist. Writing them is what makes the byte-identical claim
    # checkable by whoever receives the folder.
    if written and not a.no_manifest:
        ir_copy_dir = out_dir / "ir"
        ir_copy_dir.mkdir(parents=True, exist_ok=True)
        ir_copy = ir_copy_dir / Path(a.ir).name
        ir_copy.write_text(
            json.dumps(ir, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")

        meta = ir.get("docmeta") or {}
        manifest = {
            "manifest_version": 1,
            "ir_sha256": IR.ir_sha256(ir),
            "ir": ir_copy.name,
            "as_of": meta.get("as_of"),
            "version": meta.get("version"),
            "doc_type": ir.get("doc_type"),
            "title": meta.get("title"),
            "engine": {"somdoc": ENGINE_VERSION, "python": sys.version.split()[0]},
            "emitted": [
                {"file": p.name, "bytes": p.stat().st_size,
                 "sha256": _sha256_file(p)}
                for p in written
            ],
            # Both kinds of absence, because the person receiving this folder
            # cannot tell a format that was never asked for from one that was
            # asked for and could not be produced.
            "skipped": [{"format": w, "reason": "emitter 미구현"} for w in later]
                       + [{"format": f, "reason": f"{pkg} 없음"}
                          for f, pkg, _ in skipped_missing],
            "sources": [
                {k: v for k, v in (s or {}).items() if k in
                 ("name", "path", "sheet", "rows", "as_of")}
                for sec in ir.get("sections", [])
                for blk in (sec.get("blocks") or [])
                if isinstance(blk, dict) and blk.get("type") == "appendix_source"
                for s in (blk.get("items") or [])
            ],
            "humanize_report": _humanize_report_path(a),
            "note": "이 폴더는 자기완결입니다. ir/ 의 IR 로 다시 렌더하면 "
                    "같은 바이트가 나옵니다 (--as-of 를 위 as_of 로 주세요).",
        }
        mp = out_dir / "MANIFEST.json"
        mp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8", newline="\n")
        written.append(ir_copy)
        written.append(mp)

    print(f"somdoc build: {len(written)} file(s), ir_sha256 {IR.ir_sha256(ir)[:12]}")
    for p in written:
        print(f"  {p}  ({p.stat().st_size:,} bytes)")
    for w in later:
        print(f"  건너뜀 {w}: 아직 emitter 가 없습니다 "
              f"(구현된 것: {', '.join(IMPLEMENTED)})")
    for fmt, package, detail in skipped_missing:
        print(f"  건너뜀 {fmt}: 이 컴퓨터에 {package} 가 없습니다 ({detail}).")
        print(f"           나머지는 그대로 나왔습니다 — {fmt} 도 필요하시면 "
              f"pip install {package}")

    # Asking only for formats that do not exist wrote nothing, and exiting 0
    # there let a caller report a document it never produced -- which is
    # exactly what `/som:doc` does, it reports the bundle paths.
    if later and not written:
        print("요청한 형식이 전부 미구현이라 아무것도 만들지 않았습니다.",
              file=sys.stderr)
        return 4
    if skipped_missing and not written:
        print("요청한 형식에 필요한 패키지가 없어 아무것도 만들지 않았습니다.",
              file=sys.stderr)
        return 5
    # Exit 2 is "ran, with warnings" across this plugin: something the caller
    # asked for is not in the bundle, and they need to know before they hand
    # the folder to somebody.
    return 2 if skipped_missing else 0


def cmd_golden(a: argparse.Namespace) -> int:
    """Byte comparison. This is the definition of 'identical output'."""
    from .emitters import html as html_emitter
    ir = IR.load(a.ir)
    if a.as_of:
        ir.setdefault("docmeta", {})["as_of"] = a.as_of
    got = html_emitter.emit(ir).encode("utf-8")
    ref_path = Path(a.against)
    if not ref_path.exists():
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_bytes(got)
        print(f"somdoc golden: reference did not exist, wrote {ref_path} "
              f"({len(got):,} bytes). Review it, then commit it.")
        return 0
    want = ref_path.read_bytes()
    if got == want:
        print(f"somdoc golden: byte-identical ({len(got):,} bytes)  {ref_path}")
        return 0

    print(f"somdoc golden: MISMATCH  got {len(got):,} bytes, expected {len(want):,}")
    gl = got.decode("utf-8", "replace").splitlines()
    wl = want.decode("utf-8", "replace").splitlines()
    shown = 0
    for i in range(max(len(gl), len(wl))):
        g = gl[i] if i < len(gl) else "<eof>"
        w = wl[i] if i < len(wl) else "<eof>"
        if g != w:
            print(f"  line {i + 1}:\n    expected: {w[:160]}\n    got     : {g[:160]}")
            shown += 1
            if shown >= 5:
                print("  ... (further differences suppressed)")
                break
    if a.update:
        ref_path.write_bytes(got)
        print(f"  --update: reference rewritten. Commit it only if the change is intended.")
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="somdoc", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="check the IR against the standard's rules")
    v.add_argument("ir")
    v.add_argument("--draft", action="store_true",
                   help="skip doc-type structural rules so a partial draft can be checked")
    v.set_defaults(fn=cmd_validate)

    h = sub.add_parser("hash", help="print the canonical IR sha256")
    h.add_argument("ir")
    h.set_defaults(fn=cmd_hash)

    b = sub.add_parser("build", help="render the IR")
    b.add_argument("ir")
    b.add_argument("--emit", default="html", help="comma separated: html,xlsx,docx (pptx 미구현)")
    b.add_argument("--no-manifest", action="store_true",
                   help="MANIFEST.json 과 ir/ 사본을 쓰지 않는다")
    b.add_argument("--out", help="output directory (default: alongside the IR)")
    b.add_argument("--name", help="output file stem (default: slug_as-of)")
    b.add_argument("--as-of", help="override docmeta.as_of")
    b.add_argument("--embed-fonts", action="store_true",
                   help="inline woff2 as base64 instead of using the local fallback stack")
    b.set_defaults(fn=cmd_build)

    g = sub.add_parser("golden", help="byte-compare an HTML render against a reference")
    g.add_argument("ir")
    g.add_argument("--against", required=True)
    g.add_argument("--as-of")
    g.add_argument("--update", action="store_true", help="rewrite the reference")
    g.set_defaults(fn=cmd_golden)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except IR.IRError as e:
        print(f"somdoc: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
