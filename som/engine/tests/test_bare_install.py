"""The floor: what works with Claude and nothing else.

The promise this plugin makes to the team is that beyond being able to run
Claude there is no other requirement. That promise was false: pandas and
openpyxl were a CORE tier, so a fresh laptop got two hard failures and doctor
printed "지금 쓸 수 있는 것: 없음" -- while the thing most people want, an HTML
document, needs neither.

So this file runs the document path with every optional package stubbed out to
raise ImportError, and with Orca pointed at a name that does not exist. If it
starts failing, the floor has risen and someone has to decide that on purpose
rather than discover it after handing the plugin to a teammate.

    python engine/tests/test_bare_install.py
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SOM = Path(__file__).resolve().parents[2]


def scratch(prefix: str) -> str:
    """A temp directory that removes itself when this process exits.

    `tempfile.mkdtemp` never cleans up on its own, and these tests call it
    once per case. Over a project's development that is thousands of stray
    directories in %TEMP% -- 5,690 of them by the time anyone looked here.
    """
    d = tempfile.mkdtemp(prefix=prefix)
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return d

# Everything a teammate might not have. `snowflake` covers the connector.
OPTIONAL = ["pandas", "openpyxl", "sqlglot", "pyarrow", "snowflake", "docx",
            "pptx", "keyring"]


def bare_env() -> dict[str, str]:
    stub = scratch("som-bare-")
    for name in OPTIONAL:
        (Path(stub) / f"{name}.py").write_text(
            "raise ImportError('simulated: not installed')\n", encoding="utf-8")
    return {
        **os.environ,
        "PYTHONPATH": stub + os.pathsep + str(SOM / "engine"),
        "PYTHONUTF8": "1",
        # A name that cannot resolve, so nothing accidentally finds a real Orca.
        "ORCA_CLI_COMMAND": "__no_orca_on_this_machine__",
    }


def run(cmd: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=SOM, capture_output=True, text=True,
                          env=env, timeout=180)


def test_the_document_path_needs_nothing_installed():
    env = bare_env()
    example = "standard/examples/rnr.example.somdoc.json"

    r = run([sys.executable, "-m", "somdoc", "validate", example], env)
    assert r.returncode == 0, f"validate failed:\n{r.stdout}\n{r.stderr}"

    out = scratch("som-out-")
    r = run([sys.executable, "-m", "somdoc", "build", example,
             "--emit", "html", "--out", out, "--as-of", "2026-09-09"], env)
    assert r.returncode == 0, f"build failed:\n{r.stdout}\n{r.stderr}"

    produced = list(Path(out).glob("*.html"))
    assert len(produced) == 1, f"expected one HTML file, got {produced}"
    body = produced[0].read_text(encoding="utf-8")
    assert len(body) > 10_000, "the document came out suspiciously small"


def test_the_bare_render_is_still_byte_identical_to_golden():
    # Not merely "it produced something": the same bytes as a fully-equipped
    # machine, which is the claim the whole standard rests on.
    env = bare_env()
    r = run([sys.executable, "-m", "somdoc", "golden",
             "standard/examples/rnr.example.somdoc.json",
             "--against", "standard/examples/golden/rnr.html",
             "--as-of", "2026-09-09"], env)
    assert r.returncode == 0, f"golden mismatch without packages:\n{r.stdout}\n{r.stderr}"


def test_the_bare_document_makes_no_outbound_request():
    env = bare_env()
    out = scratch("som-out-")
    run([sys.executable, "-m", "somdoc", "build",
         "standard/examples/rnr.example.somdoc.json",
         "--emit", "html", "--out", out, "--as-of", "2026-09-09"], env)
    body = next(Path(out).glob("*.html")).read_text(encoding="utf-8")
    # The one permitted URL is the SVG namespace, which is an identifier.
    hits = body.count("http://") + body.count("https://")
    ns = body.count("http://www.w3.org/2000/svg")
    assert hits - ns == 0, f"{hits - ns} outbound reference(s) in the document"


def test_doctor_does_not_hard_fail_on_a_bare_machine():
    # Exit 1 means "nothing works until you fix this". A missing optional
    # package must never produce that -- it is how a tool gets uninstalled.
    env = bare_env()
    r = run([sys.executable, "standard/scripts/doctor.py",
             "--project", str(SOM.parent)], env)
    assert r.returncode != 1, f"doctor hard-failed on a bare machine:\n{r.stdout}"
    assert "0 fail" in r.stdout, r.stdout[-800:]


def test_doctor_tells_a_bare_user_what_they_can_do():
    env = bare_env()
    r = run([sys.executable, "standard/scripts/doctor.py",
             "--project", str(SOM.parent)], env)
    body = r.stdout
    assert "지금 쓸 수 있는 것" in body
    # The document recipe has to be in the usable list, not the blocked one.
    usable = body.split("지금 쓸 수 있는 것")[1].split("아직 안 되는 것")[0]
    assert "문서" in usable, f"the doc recipe is not offered:\n{usable}"
    assert "없음 —" not in usable, f"doctor said nothing works:\n{usable}"
    # And the blocked ones have to name what would unlock them.
    assert "pip install" in body
    # Orca is NOT one of them. This assertion used to read `"Orca 필요" in
    # body`, which pinned the defect in place: the engine plans before it
    # looks for Orca, so four of six recipes never reach it, and listing them
    # as blocked told a new teammate their machine could not do the work.
    blocked = body.split("아직 안 되는 것")[1] if "아직 안 되는 것" in body else ""
    assert "Orca 필요" not in blocked, (
        "Orca 를 차단 조건으로 표시했습니다 — 없어도 전부 됩니다:\n" + blocked)
    assert "설치는 선택" in body, "Orca 가 선택이라는 안내가 없습니다"


def test_conduct_refuses_without_orca_instead_of_throwing():
    # A teammate without Orca typing /som used to get a stack trace, not the
    # sentence this design is built around.
    env = bare_env()
    script = (
        "import('./lib/conduct.mjs').then(C=>{"
        "const pf=new C.Conduct({project:process.cwd(),say:()=>{}}).preflight();"
        "console.log(JSON.stringify({n:pf.problems.length,"
        "first:(pf.problems[0]||'').slice(0,20),installed:pf.orcaInstalled}));"
        "}).catch(e=>{console.log('THREW '+e.message);process.exit(3)})"
    )
    r = subprocess.run(["node", "-e", script], cwd=SOM, capture_output=True,
                       text=True, env=env, timeout=120)
    assert r.returncode == 0, f"preflight threw:\n{r.stdout}\n{r.stderr}"
    assert '"installed":false' in r.stdout, r.stdout
    assert '"n":1' in r.stdout, f"expected exactly one clear problem: {r.stdout}"


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  PASS  {name}")
        except Exception as e:                                  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {name}\n        {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
