"""Emit standalone .svg files for the README.

GitHub renders committed SVGs in markdown but resolves no CSS variables, so the
embedded diagrams (which paint with var(--sds-*) so they can inherit a document
theme) come out invisible there. These files bake hex in.

Light and dark variants are written so the README can pair them in a <picture>
and stay readable in GitHub's dark mode.

    python docs/make_images.py [--out docs/img]

Numbers in the graphs are measured, not typed: test counts come from running
the suites, file sizes from the artifacts on disk.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SOM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOM / "docs"))

import svg as SVG  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def measure() -> dict:
    env = {**os.environ, "PYTHONPATH": str(SOM / "engine"), "PYTHONUTF8": "1"}

    def tests(script: str) -> int:
        r = subprocess.run([sys.executable, script], cwd=SOM,
                           capture_output=True, text=True, env=env)
        line = [x for x in r.stdout.splitlines() if "passed" in x]
        if not line:
            raise SystemExit(f"make_images: {script} 가 'N/M passed' 를 내지 않았습니다")
        got, total = (int(x) for x in line[-1].strip().split(" ")[0].split("/"))
        # A failing suite reports fewer passes, and the graph would bake that
        # number in as if it were the count. It happened: a transient failure
        # during an experiment put 282 into the SVG while the real total was
        # 283, and only check_counts.py noticed.
        if got != total:
            # test_diagrams.py reads the files this script writes, so gating on
            # it deadlocks: the images cannot be regenerated until the test
            # passes, and the test cannot pass until they are regenerated.
            # Everything else is upstream and safe to insist on.
            if script.endswith("test_diagrams.py"):
                print(f"  참고: {script} 가 {got}/{total} — 이 스크립트가 쓰는 "
                      f"파일을 검사하므로 계속 진행합니다. 생성 후 다시 도세요.")
            else:
                raise SystemExit(
                    f"make_images: {script} 가 {got}/{total} 입니다. "
                    "실패를 먼저 고치세요 — 그러지 않으면 그래프에 틀린 수가 박힙니다.")
        return got


    def node_tests(path: str) -> int:
        r = subprocess.run(["node", "--test", path], cwd=SOM,
                           capture_output=True, text=True)
        m = [x for x in r.stdout.splitlines() if x.startswith("# pass ")]
        f = [x for x in r.stdout.splitlines() if x.startswith("# fail ")]
        if not m:
            raise SystemExit(f"make_images: {path} 가 통과 수를 내지 않았습니다")
        if f and int(f[-1].split()[-1]) > 0:
            raise SystemExit(f"make_images: {path} 에 실패가 있습니다. "
                             "고친 뒤에 다시 도세요.")
        return int(m[-1].split()[-1])

    covered: set[str] = set()
    covered_py: set[str] = set()

    def py(rel: str) -> int:
        """A Python suite, recorded as covered so nothing is silently dropped.

        The node side already refuses to draw when a test file lands in no
        bucket. The Python side did not, and this list is hand-written, so
        `test_write_path.py` was measured by nothing -- the same undercount, on
        the half of the repo that had no guard against it.
        """
        covered_py.add(rel.rsplit("/", 1)[-1])
        return tests(rel)

    def node_group(*globs: str) -> int:
        """Sum every matching test file.

        Globbed rather than listed: the published total drifted twice because a
        new test file was not added to a hand-written list, and a number this
        repo calls "measured" has to come off the disk.
        """
        files = sorted({f for g in globs for f in (SOM / "test").glob(g)})
        covered.update(f.name for f in files)
        return sum(node_tests(f"test/{f.name}") for f in files)

    buckets = {
        "manifest": py("engine/tests/test_manifest.py"),
        "bare": py("engine/tests/test_bare_install.py") + py("engine/tests/test_bare_walkthrough.py"),
        "skeletons": py("engine/tests/test_skeletons.py"),
        "diagrams": py("engine/tests/test_diagrams.py"),
        "ir": py("engine/tests/test_ir.py"),
        "humanize": py("engine/tests/test_humanize_io.py"),
        "somsql": py("engine/tests/test_somsql.py") + py("engine/tests/test_write_path.py"),
        "guards": node_group("guard.test.mjs", "sfguard*.test.mjs"),
        "clarity": node_group("clarity*.test.mjs"),
        "learn": node_group("patterns*.test.mjs", "promote.test.mjs",
                            "signals.test.mjs"),
        "intake": node_group("intake.test.mjs"),
        # autonomy.test.mjs also parses the README table; floors.test.mjs is
        # the hard floors; cli.test.mjs is the error paths.
        "autonomy": node_group("autonomy.test.mjs", "floors.test.mjs"),
        # cli.test.mjs is the error paths; efficiency.test.mjs keeps the
        # Claude-only routing and the one-round interview from regressing.
        "cli": node_group("cli.test.mjs", "efficiency.test.mjs", "orca-adapter.test.mjs",
                          "release.test.mjs"),
    }
    # The globs are a hand-written list even though each one globs, so a new
    # test file lands in no bucket and the published total silently undercounts.
    # It has now happened three times. Refuse to draw a wrong number.
    missed = {f.name for f in (SOM / "test").glob("*.test.mjs")} - covered
    if missed:
        raise SystemExit(
            "make_images: 어느 버킷에도 안 들어간 테스트 파일: "
            + ", ".join(sorted(missed))
            + "\n  measure() 의 node_group 목록에 추가하세요.")
    missed_py = {f.name for f in (SOM / "engine" / "tests").glob("test_*.py")} - covered_py
    if missed_py:
        raise SystemExit(
            "make_images: 어느 버킷에도 안 들어간 Python 테스트 파일: "
            + ", ".join(sorted(missed_py))
            + "\n  measure() 의 buckets 에 py(...) 로 추가하세요.")
    return buckets


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(SOM / "docs" / "img"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    m = measure()
    args_for = {
        "graph-tests": dict(manifest=m["manifest"], ir=m["ir"], humanize=m["humanize"],
                            somsql=m["somsql"], guards=m["guards"],
                            clarity=m["clarity"], intake=m["intake"],
                            learn=m["learn"], autonomy=m["autonomy"],
                            bare=m["bare"], cli=m["cli"], skeletons=m["skeletons"] + m["diagrams"]),
    }

    written = []
    for name, fn in SVG.DIAGRAMS.items():
        for mode in ("light", "dark"):
            P = SVG.Palette.literal(mode)
            body = fn(P, **args_for.get(name, {}))
            p = out / f"{name}-{mode}.svg"
            p.write_text(body + "\n", encoding="utf-8", newline="\n")
            written.append(p)

    print(f"make_images: {len(written)} files -> {out}")
    for p in sorted(written):
        print(f"  {p.name:34} {p.stat().st_size:>7,} bytes")
    print("\n측정값 " + json.dumps(m, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
