"""Verify that every published test count is what the suites actually report.

    python docs/check_counts.py

Deliberately not named `test_*.py`: it runs every suite, and a suite that runs
every suite would include itself.

Why this exists. The README prints a total and a per-suite breakdown, and the
README's graph prints a total of its own from a separate pipeline. All three
drifted. The graph is generated from live measurements, but its buckets were a
hand-written list of globs, so a new test file landed in no bucket and the
published total quietly undercounted -- three times, each time under a comment
explaining that this had already happened.

A number this repository calls "measured" has to come off the disk. Run this
after adding a test file, and again before publishing a count.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SOM = Path(__file__).resolve().parent.parent
README = SOM.parent / "README.md"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def run_counts() -> dict[str, int]:
    env = {**os.environ, "PYTHONPATH": str(SOM / "engine"), "PYTHONUTF8": "1"}
    counts: dict[str, int] = {}
    for script in sorted((SOM / "engine" / "tests").glob("test_*.py")):
        r = subprocess.run([sys.executable, str(script)], cwd=str(SOM),
                           capture_output=True, text=True, env=env, timeout=900,
                           encoding="utf-8", errors="replace")
        line = [x for x in (r.stdout or "").splitlines() if "passed" in x]
        counts[script.name] = int(line[-1].strip().split("/")[0]) if line else 0
        if not line:
            print(f"  경고: {script.name} 이 'N/M passed' 를 출력하지 않았습니다")

    node = shutil.which("node")
    if not node:
        raise SystemExit("node 를 찾을 수 없습니다.")
    files = sorted((SOM / "test").glob("*.test.mjs"))
    r = subprocess.run([node, "--test", *[f"test/{f.name}" for f in files]],
                       cwd=str(SOM), capture_output=True, text=True, timeout=900,
                       encoding="utf-8", errors="replace")
    passed = [x for x in (r.stdout or "").splitlines() if x.startswith("# pass ")]
    failed = [x for x in (r.stdout or "").splitlines() if x.startswith("# fail ")]
    counts["node"] = int(passed[-1].split()[-1]) if passed else 0
    if failed and int(failed[-1].split()[-1]) > 0:
        raise SystemExit(f"node 테스트가 {failed[-1].split()[-1]}건 실패합니다. "
                         "개수를 세기 전에 그것부터 고치세요.")
    return counts


def main() -> int:
    counts = run_counts()
    total = sum(counts.values())
    readme = README.read_text(encoding="utf-8")
    svg = (SOM / "docs" / "img" / "graph-tests-light.svg").read_text(encoding="utf-8")

    problems: list[str] = []

    m = re.search(r"합계 (\d+)", readme)
    if not m:
        problems.append("README 에서 '합계 N' 을 찾지 못했습니다")
    elif int(m.group(1)) != total:
        problems.append(f"README 합계 {m.group(1)} vs 실제 {total}")

    m = re.search(r"node --test test/\*\.test\.mjs\s+#\s*(\d+)", readme)
    if not m:
        problems.append("README 에서 node 테스트 수를 찾지 못했습니다")
    elif int(m.group(1)) != counts["node"]:
        problems.append(f"README node {m.group(1)} vs 실제 {counts['node']}")

    n_files = len(list((SOM / "test").glob("*.test.mjs")))
    m = re.search(r"\((\d+)개 파일\)", readme)
    if m and int(m.group(1)) != n_files:
        problems.append(f"README 파일 수 {m.group(1)} vs 실제 {n_files}")

    for name, n in counts.items():
        if name == "node":
            continue
        m = re.search(rf"{re.escape(name)}\s+#\s*(\d+)", readme)
        if not m:
            problems.append(f"README 에 {name} 의 개수가 없습니다")
        elif int(m.group(1)) != n:
            problems.append(f"README {name} {m.group(1)} vs 실제 {n}")

    for label, text in (("그래프 SVG", svg), ("README alt", readme)):
        m = re.search(r"테스트 (\d+)건 전부 통과", text)
        if not m:
            problems.append(f"{label} 에서 총계 문구를 찾지 못했습니다")
        elif int(m.group(1)) != total:
            problems.append(f"{label} {m.group(1)} vs 실제 {total}"
                            " — `python docs/make_images.py` 를 다시 도세요")

    print(f"실측 {total}건 " + json.dumps(counts, ensure_ascii=False))
    if problems:
        print(f"\n{len(problems)}건 불일치:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("발행된 숫자가 전부 실측과 일치합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
