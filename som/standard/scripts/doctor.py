"""som environment check.

Exit 0 ready · 1 hard failure · 2 warnings only.

The secret scan is a hard failure on purpose. A private key inside a synced
tree is not a style problem, and OneDrive keeps previous versions, so deleting
the file is not by itself remediation.
"""
from __future__ import annotations

import argparse
import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SOM = Path(__file__).resolve().parents[2]

# Tiered by what each package actually unlocks, measured rather than assumed.
#
# There is no CORE tier any more. pandas and openpyxl used to be one, so a
# teammate on a fresh laptop got two FAILs and "지금 쓸 수 있는 것: 없음" --
# while the thing they most likely wanted, an HTML document, needs neither.
# Verified: rendering the example R&R with both packages stubbed to raise
# ImportError produces the same 69,962 bytes. The HTML path is standard
# library only.
#
# Nothing below is a hard failure. A missing package removes one capability
# and names it; it does not remove the plugin.
FEATURES = [
    ("openpyxl", "xlsx 출력", "엑셀 파일로도 내보내기"),
    ("pandas", "표 처리", "엑셀·CSV 를 읽어서 분석"),
]
SNOWFLAKE = [
    ("sqlglot", "SQL 분류 — 없으면 somsql 이 전부 거부한다"),
    ("snowflake.connector", "Snowflake 접속"),
    ("pyarrow", "parquet 캐시"),
]
NICE = [
    ("docx", "docx 출력 (예정)"),
    ("pptx", "pptx 출력 (예정)"),
    ("keyring", "자격증명 보관"),
]

SECRET_GLOBS = ("*.p8", "*.pem", "id_rsa", "id_rsa.*", "*.pfx", "*.key")
SECRET_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
BIG_FILE_MB = 50

rows: list[tuple[str, str, str]] = []   # (state, check, detail)


def ok(check: str, detail: str = "") -> None:
    rows.append(("OK", check, detail))


def warn(check: str, detail: str = "") -> None:
    rows.append(("WARN", check, detail))


def fail(check: str, detail: str = "") -> None:
    rows.append(("FAIL", check, detail))


SNOWFLAKE_READY = {"value": True}


def _have(mod: str):
    try:
        return importlib.import_module(mod)
    except Exception:
        return None


FEATURE_READY: dict[str, bool] = {}


def check_python() -> None:
    v = sys.version_info
    if v >= (3, 11):
        ok("Python >= 3.11", f"{v.major}.{v.minor}.{v.micro}")
    else:
        # Not a hard failure either: say what it costs and let the rest run.
        warn(f"Python {v.major}.{v.minor} — 3.11 이상 권장",
             "문서 엔진이 3.11 문법을 씁니다. 낮으면 렌더가 실패할 수 있습니다")

    for mod, what, unlocks in FEATURES:
        m = _have(mod)
        FEATURE_READY[mod] = bool(m)
        if m:
            ok(mod, f"{getattr(m, '__version__', '')} · {what}")
        else:
            warn(f"{mod} 없음", f"{unlocks} 가 안 됩니다 · pip install {mod}")

    missing_sf = []
    for mod, why in SNOWFLAKE:
        m = _have(mod)
        if m:
            ok(f"Snowflake {mod}", getattr(m, "__version__", ""))
        else:
            missing_sf.append(mod.split(".")[0])
            warn(f"Snowflake {mod} 없음", f"{why} — Snowflake 를 안 쓰면 무시해도 됩니다")
    if missing_sf:
        SNOWFLAKE_READY["value"] = False

    for mod, why in NICE:
        m = _have(mod)
        (ok if m else warn)(
            f"선택 {mod}" + ("" if m else " 없음"),
            getattr(m, "__version__", "") if m else why)

    if os.environ.get("PYTHONUTF8") == "1" or os.environ.get("PYTHONIOENCODING"):
        ok("UTF-8 강제", "PYTHONUTF8 / PYTHONIOENCODING 설정됨")
    else:
        warn("UTF-8 미강제",
             "한국어 출력에서 UnicodeEncodeError 가 난다 · PYTHONUTF8=1 을 설정하라")


def check_engine() -> None:
    for rel in ("engine/somdoc/ir.py", "engine/somdoc/emitters/html.py",
                "standard/themes/tokens.css", "standard/shell/doc.shell.html",
                "standard/examples/golden/rnr.html"):
        p = SOM / rel
        (ok if p.exists() else fail)(f"파일 {rel}",
                                     f"{p.stat().st_size:,} bytes" if p.exists() else "없음")

    env = {**os.environ, "PYTHONPATH": str(SOM / "engine"), "PYTHONUTF8": "1"}
    ex = SOM / "standard/examples/rnr.example.somdoc.json"
    if ex.exists():
        r = subprocess.run([sys.executable, "-m", "somdoc", "golden", str(ex),
                            "--against", str(SOM / "standard/examples/golden/rnr.html"),
                            "--as-of", "2026-09-09"],
                           cwd=SOM, capture_output=True, text=True, env=env)
        if r.returncode == 0:
            ok("골든 렌더 일치", "byte-identical")
        else:
            fail("골든 렌더 불일치",
                 "렌더러가 커밋된 레퍼런스와 다르다 · 의도한 변경이면 --update 후 커밋")


def check_registry(project: Path) -> None:
    """Does the cost guard know which objects are big here?

    The table list used to be compiled into the plugin, which published one
    company's schema map and fitted nobody else's account. It is a local file
    now -- and a local file that nobody wrote means the date-predicate rule
    silently never fires. Reported as a warning rather than left to be found
    the hard way, because "3/3 runnable" from a half-asleep guard reads exactly
    like "3/3 runnable" from a working one.
    """
    engine = SOM / "engine"
    if str(engine) not in sys.path:
        sys.path.insert(0, str(engine))
    try:
        from somsql import registry as R           # noqa: PLC0415
    except ImportError as e:                       # pragma: no cover
        warn("registry 를 읽지 못했습니다", str(e))
        return
    prof = R.SiteProfile(project)
    for problem in prof.problems:
        warn("registry 설정 오류", problem)
    if prof.empty:
        warn("대형 테이블 registry 비어 있음",
             f"날짜 조건 없는 전체 스캔 가드가 동작하지 않습니다 · "
             f".som/{R.PROFILE_NAME} 에 large_tables 를 적으세요 "
             f"(예시: standard/registry.json.sample) · "
             f"EXPLAIN 견적과 행 상한은 그대로 돕니다")
    else:
        ok("대형 테이블 registry",
           f"{len(prof.large_tables)}개 등록 · {prof.sources[0] if prof.sources else ''}")


def check_secrets(project: Path) -> None:
    hits: list[Path] = []
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in SECRET_SKIP_DIRS]
        rp = Path(root)
        for pat in SECRET_GLOBS:
            hits.extend(rp.glob(pat))
        if len(hits) > 40:
            break
    if hits:
        fail(f"private key 발견 {len(hits)}건",
             " · ".join(str(h.relative_to(project)) for h in hits[:4]))
        rows.append(("FAIL", "  → 조치",
                     "키를 rotate 하라. OneDrive 는 이전 버전을 보관하므로 파일 삭제만으로는 "
                     "부족하다 (휴지통 비우기 + 버전 정리 필요)"))
    else:
        ok("private key 없음", f"{', '.join(SECRET_GLOBS)} 스캔")


def check_big_files(project: Path) -> None:
    big: list[tuple[Path, int]] = []
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in SECRET_SKIP_DIRS]
        for f in files:
            p = Path(root) / f
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            if sz > BIG_FILE_MB * 1024 * 1024:
                big.append((p, sz))
        if len(big) > 20:
            break
    if big:
        warn(f"{BIG_FILE_MB}MB 초과 파일 {len(big)}건",
             "에이전트 컨텍스트에 절대 로드하지 말 것 · " +
             ", ".join(f"{p.name} {sz // (1024 * 1024)}MB" for p, sz in big[:3]))
    else:
        ok(f"{BIG_FILE_MB}MB 초과 파일 없음")


def check_sync(project: Path) -> None:
    s = str(project)
    if "OneDrive" in s or "SharePoint" in s:
        warn("동기 트리 안에서 실행 중",
             ".som/ 런 상태가 두 머신 간 충돌할 수 있다 · 플러그인 repo 는 동기 밖에 두라")
    else:
        ok("동기 트리 밖", s)

    # Walk up, the way git itself resolves a repository. Checking only the
    # current directory told anyone working in a subdirectory of their repo
    # -- which is most people -- that they had no version control.
    # `.git` is a directory in a normal clone and a file in a worktree or
    # submodule, so test for existence, not for a directory.
    root = next((d for d in [project, *project.parents] if (d / ".git").exists()),
                None)
    if root is not None:
        ok("git repo", "버전 관리됨" if root == project else f"버전 관리됨 · {root}")
    else:
        warn("git repo 아님", "산출물은 불변이라 안전하지만 IR 이력이 남지 않는다")


def check_orca() -> None:
    exe = os.environ.get("ORCA_CLI_COMMAND") or "orca"
    path = shutil.which(exe)
    if not path:
        warn("orca CLI 없음",
             "단일 에이전트 순차 모드로 전부 동작합니다 (설계된 degradation). "
             "동시 실행을 원하시면: https://github.com/stablyai/orca/releases")
        return
    if path.lower().endswith((".cmd", ".bat")):
        sib = Path(path).with_suffix(".exe")
        if sib.exists():
            ok("orca 실행 파일", f"{sib.name} 사용 (.cmd 는 orchestration send 를 거부한다)")
        else:
            fail("orca 가 .cmd 로만 해석됨",
                 "orchestration send/reply 가 거부된다 · .exe 를 PATH 에 두라")
    else:
        ok("orca 실행 파일", path)

    r = subprocess.run([exe, "orchestration", "run-list", "--json"],
                       capture_output=True, text=True)
    if r.returncode == 0 and '"ok": true' in r.stdout:
        ok("orchestration 기능 활성", "Settings > Experimental 통과")
    else:
        warn("orchestration 비활성 또는 런타임 미가동",
             "Settings > Experimental > Orchestration 을 켜야 다중 에이전트가 돈다")


SAY = {
    "doc":     '"R&R 문서 만들어줘"',
    "prd":     '"PRD 필요해"',
    "watch":   '"밤새 확인해줘"',
    "analyze": '"이 엑셀 분석해줘"',
    "build":   '"스크립트 만들어줘"',
    "data":    '"Snowflake 에서 뽑아줘"',
}


def _sequential_recipes() -> dict[str, bool]:
    """Which recipes have no parallel stretch, asked of the scheduler itself.

    This used to be `if r["id"] != "doc": needs.append("orca")`, which was true
    when the engine checked for Orca before it planned anything. It plans first
    now, and four of the six schedule with every wave one node wide -- those
    never reach Orca at all.

    The cost of getting it wrong lands on the first command a new teammate
    runs: `/som:doctor` told them five of the six things did not work on their
    machine, when five of the six did.

    Falls back to "sequential" if node is missing: claiming a recipe is
    blocked is the more harmful error, because it stops someone trying.
    """
    import json                                          # noqa: PLC0415
    import shutil                                        # noqa: PLC0415
    import subprocess                                    # noqa: PLC0415

    node = shutil.which("node")
    if not node:
        return {}
    probe = "\n".join([
        "const root = process.argv[2];",
        "const C = await import(`file://${root}/lib/conduct.mjs`);",
        "const P = await import(`file://${root}/lib/domain/plan.mjs`);",
        "const out = {};",
        "for (const r of C.listRecipes()) {",
        '  const slots = Object.fromEntries((r.asks ?? []).map((a) => [a.slot, "x"]));',
        "  out[r.id] = P.parallelismOf(C.buildDag(r, slots), { cap: 4 }).sequential;",
        "}",
        "process.stdout.write(JSON.stringify(out));",
    ])
    probe_path = SOM / ".doctor-probe.mjs"
    try:
        probe_path.write_text(probe, encoding="utf-8")
        r = subprocess.run([node, str(probe_path), str(SOM).replace("\\", "/")],
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        return json.loads(r.stdout) if r.returncode == 0 else {}
    except Exception:
        return {}
    finally:
        try:
            probe_path.unlink()
        except OSError:
            pass


def _recipe_rows() -> list[tuple[str, str, list[str]]]:
    """Read the recipes rather than hardcoding the list.

    A doctor that says five when the folder holds six is the kind of small lie
    that makes someone stop trusting the rest of the output.

    Each row carries what that recipe needs beyond Claude itself, so the
    summary can say which ones work on THIS machine rather than which exist.
    """
    import json                                          # noqa: PLC0415
    sequential = _sequential_recipes()
    out = []
    rdir = SOM / "skills/conduct/recipes"
    for p in sorted(rdir.glob("*.json")):
        if p.stem.startswith("_"):
            continue
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        needs = list(r.get("requires") or [])
        # Orca is a requirement only where it buys something. A plan whose
        # waves are all one node wide is run in this session, in order.
        if not sequential.get(r["id"], True):
            needs.append("orca")
        if r["id"] == "analyze":
            needs.append("pandas")
        out.append((SAY.get(r["id"], f'"{r["id"]}"'), r.get("title", r["id"]), needs))
    out.sort(key=lambda x: (len(x[2]), x[0]))     # fewest requirements first
    return out


def _orca_ready() -> bool:
    """Orca present AND orchestration switched on. Either alone is not enough."""
    exe = os.environ.get("ORCA_CLI_COMMAND") or "orca"
    if not shutil.which(exe):
        return False
    try:
        r = subprocess.run([exe, "orchestration", "run-list", "--json"],
                           capture_output=True, text=True, timeout=20)
    except Exception:
        return False
    return r.returncode == 0 and '"ok": true' in r.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=os.getcwd())
    a = ap.parse_args()
    project = Path(a.project).resolve()

    print(f"som doctor · project {project}")
    print(f"           · plugin  {SOM}\n")

    check_python()
    check_engine()
    check_secrets(project)
    check_registry(project)
    check_big_files(project)
    check_sync(project)
    check_orca()

    width = max(len(c) for _, c, _ in rows) + 2
    for state, check, detail in rows:
        mark = {"OK": " ok ", "WARN": "warn", "FAIL": "FAIL"}[state]
        print(f"  [{mark}] {check.ljust(width)}{detail}")

    fails = sum(1 for s, _, _ in rows if s == "FAIL")
    warns = sum(1 for s, _, _ in rows if s == "WARN")
    print(f"\n{len(rows) - fails - warns} ok · {warns} warn · {fails} fail")

    # What this person can do right now, on THIS machine. A count of warnings
    # tells them nothing; this tells them what to type, and what one line of
    # install would add.
    have = {
        "snowflake": SNOWFLAKE_READY["value"],
        "orca": _orca_ready(),
        "pandas": FEATURE_READY.get("pandas", False),
    }
    label = {"snowflake": "Snowflake 패키지", "orca": "Orca", "pandas": "pandas"}

    # Orca is never a blocker. A recipe with a parallel stretch runs without it
    # -- one wave slower -- so listing those under "아직 안 되는 것" told a new
    # teammate that four of six things were unavailable when five were. That is
    # the first screen anyone sees, and the discouragement is the damage.
    ready, blocked = [], []
    for say, what, needs in _recipe_rows():
        missing = [n for n in needs if n != "orca" and not have.get(n, True)]
        faster = "orca" in needs and not have["orca"]
        (blocked if missing else ready).append((say, what, missing, faster))

    print("\n지금 쓸 수 있는 것")
    if fails:
        print("  없음 — 아래 FAIL 을 먼저 해결하세요")
    elif not ready:
        print("  (없음)")
    else:
        for say, what, _missing, faster in ready:
            tail = "   (Orca 가 있으면 한 단계 빨라집니다)" if faster else ""
            print(f"  {say.ljust(24)}{what}{tail}")
        print("\n  슬래시 명령을 외울 필요는 없습니다. 하고 싶은 일을 말하면 됩니다.")

    if blocked and not fails:
        print("\n아직 안 되는 것 — 하나씩 풀면 됩니다")
        for say, what, missing, _faster in blocked:
            need = ", ".join(label[m] for m in missing)
            print(f"  {say.ljust(24)}{what}  ← {need} 필요")
        if not have["pandas"]:
            print("    pip install pandas openpyxl        (엑셀·CSV 를 다루실 때)")
        if not have["snowflake"]:
            print("    pip install sqlglot snowflake-connector-python pyarrow"
                  "   (Snowflake 를 쓰실 때)")

    if not have["orca"] and any(f for _, _, _, f in ready + blocked):
        print("\n  Orca 는 여러 작업을 동시에 돌리기 위한 앱입니다. 없어도 전부 되고,")
        print("  위에 표시된 것만 한 단계 빨라집니다. 설치는 선택입니다.")

    if fails:
        print("\n해결 후 다시 실행하세요.")
        return 1
    return 2 if warns else 0


if __name__ == "__main__":
    raise SystemExit(main())
