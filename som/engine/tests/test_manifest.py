"""Manifest regression tests.

These exist because the plugin once installed cleanly and then reported
"failed to load", which is a symptom with no visible cause. Bisecting the
installed cache found two structural rules that are not obvious from the
manifest alone. Each test below pins one of them.

    python engine/tests/test_manifest.py
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

SOM = Path(__file__).resolve().parents[2]
PLUGIN = SOM / ".claude-plugin" / "plugin.json"
MARKET = SOM.parent / ".claude-plugin" / "marketplace.json"
HOOKS = SOM / "hooks" / "hooks.json"
MONITORS = SOM / "monitors" / "monitors.json"

VALID_EVENTS = {
    "PreToolUse", "PostToolUse", "PostToolUseFailure", "PostToolBatch",
    "Notification", "UserPromptSubmit", "UserPromptExpansion",
    "SessionStart", "SessionEnd", "Stop", "StopFailure",
    "SubagentStart", "SubagentStop", "PreCompact", "PostCompact",
    "PermissionRequest", "PermissionDenied", "Setup", "TeammateIdle",
    "TaskCreated", "TaskCompleted", "ConfigChange",
}


def _json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def test_plugin_json_parses():
    d = _json(PLUGIN)
    for k in ("name", "version", "description"):
        assert d.get(k), f"plugin.json.{k} is required"


def test_plugin_json_does_not_declare_hooks_or_monitors():
    """The load-failure rule.

    Both surfaces are auto-discovered from hooks/hooks.json and
    monitors/monitors.json. Declaring either as a path string in plugin.json
    makes the plugin fail to load -- verified by bisection against the
    installed cache, where even {"hooks":{}} fails.
    """
    d = _json(PLUGIN)
    assert "hooks" not in d, (
        "plugin.json must NOT declare `hooks`; it is auto-discovered from "
        "hooks/hooks.json. Declaring it makes the plugin fail to load.")
    assert "monitors" not in d, (
        "plugin.json must NOT declare `monitors`; it is auto-discovered from "
        "monitors/monitors.json.")


def test_hooks_events_nest_under_a_hooks_key():
    d = _json(HOOKS)
    assert "hooks" in d and isinstance(d["hooks"], dict), (
        "hooks.json must nest its event map under a top-level \"hooks\" object; "
        "events at the root do not load.")
    stray = [k for k in d if k in VALID_EVENTS]
    assert not stray, f"event names must not sit at the root of hooks.json: {stray}"


def test_hook_events_are_known():
    for ev in _json(HOOKS)["hooks"]:
        assert ev in VALID_EVENTS, f"unknown hook event {ev!r}"


def test_hook_timeouts_are_seconds_not_milliseconds():
    """`timeout` is multiplied by 1000 by the host (wire field: timeout_s).

    bkit ships 5000, which is 83 minutes. Anything above 120 here is almost
    certainly a millisecond value pasted in by mistake.
    """
    for ev, groups in _json(HOOKS)["hooks"].items():
        for g in groups:
            for h in g.get("hooks", []):
                t = h.get("timeout")
                if t is None:
                    continue
                assert isinstance(t, int) and 1 <= t <= 120, (
                    f"{ev}: timeout={t} is not a plausible number of SECONDS. "
                    f"The host does timeout*1000.")


def test_once_sits_on_the_matcher_group():
    for ev, groups in _json(HOOKS)["hooks"].items():
        for g in groups:
            for h in g.get("hooks", []):
                assert "once" not in h, (
                    f"{ev}: `once` belongs on the matcher group, not on an "
                    f"individual hook entry.")


def test_hook_commands_use_the_plugin_root_variable():
    """Never an absolute path. A hardcoded path is what left ten dead wmux
    hooks firing a node process per tool call at a directory that no longer
    existed."""
    for ev, groups in _json(HOOKS)["hooks"].items():
        for g in groups:
            for h in g.get("hooks", []):
                cmd = h.get("command", "")
                if "session-start" in cmd or "scripts/" in cmd:
                    assert "${CLAUDE_PLUGIN_ROOT}" in cmd, (
                        f"{ev}: use ${{CLAUDE_PLUGIN_ROOT}}, not an absolute path")
                assert not cmd.startswith(("C:", "/c/", "/Users")), (
                    f"{ev}: absolute path in hook command")


def test_hook_scripts_exist_and_are_fail_open():
    """Every referenced script must exist and must not be able to block a tool
    call by crashing."""
    import re
    import subprocess
    for ev, groups in _json(HOOKS)["hooks"].items():
        for g in groups:
            for h in g.get("hooks", []):
                m = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\"']+)", h.get("command", ""))
                if not m:
                    continue
                p = SOM / m.group(1)
                assert p.exists(), f"{ev}: hook script missing: {m.group(1)}"
                # input="" is required, not cosmetic: the guards read fd 0, so
                # without it this blocks forever waiting on the parent's stdin.
                r = subprocess.run(["node", str(p)], capture_output=True,
                                   text=True, input="", timeout=30)
                assert r.returncode == 0, (
                    f"{ev}: {p.name} exited {r.returncode}; hooks must fail open")
                assert r.stdout.strip() in ("{}", "") or r.stdout.strip().startswith("{"), (
                    f"{ev}: {p.name} must print JSON on stdout, got {r.stdout[:80]!r}")


def test_monitors_is_a_list_of_named_entries():
    d = _json(MONITORS)
    assert isinstance(d, list) and d, "monitors.json must be a non-empty array"
    for e in d:
        for k in ("name", "command"):
            assert e.get(k), f"monitor entry needs {k}"
        when = e.get("when")
        assert when == "always" or str(when).startswith("on-skill-invoke:"), (
            f"monitor `when` must be \"always\" or \"on-skill-invoke:<skill>\", got {when!r}")
        assert "${user_config." not in e["command"], (
            "${user_config.*} is rejected inside monitor commands")


def test_every_path_a_skill_tells_you_to_open_actually_exists():
    """The gap this closes was real.

    `doc-standard/SKILL.md` told the reader to `cat
    standard/skeletons/rnr.skeleton.json` and referenced
    `standard/schema/somdoc.schema.json`. Neither file existed. A skill that
    instructs an agent to read a missing file fails in the least useful way
    possible: mid-task, on the user's document.

    Scans both inline-code references and fenced bash blocks. Resolution is
    tried plugin-relative and skill-relative, because `references/humanize.md`
    is legitimately relative to the skill directory.
    """
    import re
    docs = sorted(SOM.glob("skills/*/SKILL.md")) + sorted(SOM.glob("commands/*.md"))
    assert docs, "no skills or commands found"

    # Directories the plugin actually ships. Anything else in a path-shaped
    # string is prose, a project-local path, or an example.
    OWNED = ("standard/", "engine/", "references/", "lib/", "monitors/",
             "scripts/", "hooks/", "skills/", "commands/")
    missing: list[str] = []

    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        refs: set[str] = set()
        refs |= set(re.findall(r"`([A-Za-z0-9_./-]+\.[a-z]{2,5})`", text))
        for fence in re.findall(r"```(?:bash|sh)\n(.*?)```", text, re.S):
            refs |= set(re.findall(r"(?<![\w/${])((?:%s)[A-Za-z0-9_./-]*\.[a-z]{2,5})"
                                   % "|".join(re.escape(o) for o in OWNED), fence))
        for ref in sorted(refs):
            if not ref.startswith(OWNED):
                continue
            if "*" in ref or "<" in ref:
                continue
            if (SOM / ref).exists() or (doc.parent / ref).exists():
                continue
            missing.append(f"{doc.relative_to(SOM)} -> {ref}")

    assert not missing, (
        "a skill or command points at a file that does not exist:\n  "
        + "\n  ".join(missing))


def test_the_generated_schema_is_not_stale():
    """The schema is generated from ir.py. If the two disagree, the document
    telling an author what is allowed contradicts the code that enforces it."""
    import os
    import subprocess
    r = subprocess.run(
        [sys.executable, "standard/scripts/gen_schema.py", "--check"],
        cwd=SOM, capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(SOM / "engine"), "PYTHONUTF8": "1"})
    assert r.returncode == 0, r.stdout + r.stderr


def _doc_bash_blocks(paths) -> list[tuple[str, str]]:
    """(relative doc path, bash block) for every fenced bash block in `paths`."""
    out: list[tuple[str, str]] = []
    for doc in paths:
        text = doc.read_text(encoding="utf-8")
        rel = str(doc.relative_to(SOM)).replace("\\", "/")
        for block in re.findall(r"```(?:bash|sh)\n(.*?)```", text, re.S):
            out.append((rel, block))
    return out


def _substituted_docs() -> list[Path]:
    """Docs Claude Code loads itself, and therefore substitutes paths into."""
    return sorted(SOM.glob("skills/*/SKILL.md")) + sorted(SOM.glob("commands/*.md"))


def _read_only_docs() -> list[Path]:
    """Docs reached with the Read tool, where nothing is substituted."""
    return sorted(SOM.glob("skills/*/references/*.md"))


def test_loaded_docs_reference_the_plugin_the_way_the_host_substitutes_it():
    """`${CLAUDE_PLUGIN_ROOT}` is replaced with the real path when Claude Code
    loads a SKILL.md or a command -- measured, not assumed: loading
    `som:snowflake-safe` yields `export PYTHONPATH="C:/Users/.../som/0.1.0/engine"`.

    So a loaded doc needs no setup step at all. What it must not do is invent a
    shell variable of its own, because a placeholder like `SOM=<플러그인 경로>`
    is a step the reader can get wrong, and one that a literal-minded reader
    will paste verbatim -- giving `PYTHONPATH=/engine` and
    `No module named somdoc`.

    (An earlier version of this test banned `${CLAUDE_PLUGIN_ROOT}` inside
    single quotes, on the theory that bash would not expand it. Bash never sees
    it: the substitution happens before the model does. The rule was false and
    the snippets it condemned worked.)
    """
    bad: list[str] = []
    for rel, block in _doc_bash_blocks(_substituted_docs()):
        for line in block.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if re.match(r"^\s*(?:export\s+)?[A-Z_]+=<", line):
                bad.append(f"{rel}: 독자가 채워야 하는 자리표시자가 있습니다 -- "
                           f"${{CLAUDE_PLUGIN_ROOT}} 를 쓰면 채울 것이 없습니다"
                           f"\n    {stripped[:100]}")
    assert not bad, "\n".join(bad)


def test_every_shell_variable_a_doc_block_uses_is_one_it_or_the_host_sets():
    """A block that reads `$SOM` without assigning it is a broken instruction.

    `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PROJECT_DIR}` are supplied by the host
    in a loaded doc. Anything else has to be assigned in the same block, or the
    reader is being told to run a command with an empty path in it -- which on
    this machine silently becomes `/engine` and fails two steps later.
    """
    host_supplied = {"CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR", "PWD", "HOME",
                     "PATH", "TEMP", "TMPDIR", "PYTHONPATH", "PYTHONUTF8"}
    bad: list[str] = []
    for rel, block in _doc_bash_blocks(_substituted_docs() + _read_only_docs()):
        assigned = set(re.findall(r"^\s*(?:export\s+)?([A-Za-z_]\w*)=", block, re.M))
        for m in re.finditer(r"\$\{?([A-Za-z_]\w*)\}?", block):
            name = m.group(1)
            if name in host_supplied or name in assigned:
                continue
            # $1, $@ and friends are positional, not environment
            if name.isdigit():
                continue
            bad.append(f"{rel}: ${name} 를 쓰는데 같은 블록에서 대입하지 않고 "
                       f"호스트가 주지도 않습니다")
    assert not bad, "\n".join(sorted(set(bad)))


def test_reference_files_do_not_rely_on_substitution_that_does_not_happen():
    """references/*.md is opened with the Read tool, so nothing is substituted.

    `humanize.md` said "`$SOM` is `${CLAUDE_PLUGIN_ROOT}` for this plugin" and
    then used `$SOM` in three commands -- prose where an assignment was needed,
    which is the same defect in the one place the host cannot paper over.
    """
    bad: list[str] = []
    for doc in _read_only_docs():
        rel = str(doc.relative_to(SOM)).replace("\\", "/")
        text = doc.read_text(encoding="utf-8")
        if "${CLAUDE_PLUGIN_ROOT}" not in text:
            continue
        for block in re.findall(r"```(?:bash|sh)\n(.*?)```", text, re.S):
            if "${CLAUDE_PLUGIN_ROOT}" in block:
                bad.append(f"{rel}: 참조 파일의 실행 블록이 ${{CLAUDE_PLUGIN_ROOT}} 에 "
                           f"의존합니다 -- 이 파일은 Read 로 열리므로 치환되지 않습니다")
    assert not bad, "\n".join(bad)


def test_docs_drive_the_engine_through_the_cli_not_node_dash_e():
    """One entry point beats a hand-written import in every doc.

    Not because the old `node -e` blobs were broken -- they were not, once the
    host substituted the path -- but because each one hardcodes the module
    layout, cannot be smoke-tested, and breaks for anyone reading the file
    directly rather than through the skill.
    """
    bad = [f"{rel}: `node -e` 를 씁니다. bin/som.mjs 를 부르세요."
           for rel, block in _doc_bash_blocks(_substituted_docs())
           if re.search(r"\bnode\s+-e\b", block)]
    assert not bad, "\n".join(bad)


def test_every_cli_subcommand_the_docs_name_actually_exists():
    """Run each documented `som.mjs <cmd> <sub>` and reject "unknown command".

    Not a spelling check: the command is executed and the engine's own refusal
    is what fails the test. This is what catches a doc naming a subcommand
    nobody built -- `conduct plan` and `conduct run` were named in prose for
    months with no implementation behind them.
    """
    seen: set[tuple[str, str]] = set()
    for _rel, block in _doc_bash_blocks(_substituted_docs()):
        for m in re.finditer(r'som\.mjs"?\s+([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?', block):
            seen.add((m.group(1), m.group(2) or ""))
    assert seen, "문서에서 som.mjs 호출을 하나도 찾지 못했습니다 -- 정규식을 확인하세요"

    node = shutil.which("node")
    assert node, "node 를 찾을 수 없습니다. 이 테스트는 node 가 필요합니다."

    bad: list[str] = []
    for cmd, sub in sorted(seen):
        argv = [n for n in (cmd, sub) if n]
        r = subprocess.run([node, str(SOM / "bin" / "som.mjs"), *argv],
                           capture_output=True, text=True, cwd=str(SOM),
                           timeout=60, encoding="utf-8", errors="replace")
        blob = (r.stdout or "") + (r.stderr or "")
        if "알 수 없는" in blob:
            first = blob.strip().splitlines()[0] if blob.strip() else "(출력 없음)"
            bad.append(f"{' '.join(argv)}: CLI 가 모르는 명령입니다 -- {first}")
    assert not bad, "\n".join(bad)


def test_no_doc_advertises_a_flag_nothing_implements():
    """`argument-hint` is the first thing a person reads about a command.

    `learn` advertised `--from-run <runId>` in two places -- its SKILL.md and
    its command file -- and nothing in the engine had ever heard of it. A flag
    that exists only in a hint is a promise that fails the moment someone takes
    it up, and the failure looks like the plugin being broken.

    Flags the host supplies, and ones that belong to tools the docs merely
    quote, are exempt; everything else has to appear in the engine.
    """
    host = {"--help", "--version", "--json"}
    parts = []
    for d in ("lib", "bin", "engine/somdoc", "engine/somsql", "standard/scripts"):
        for f in (SOM / d).rglob("*"):
            if f.suffix in (".mjs", ".py"):
                parts.append(f.read_text(encoding="utf-8"))
    engine = "\n".join(parts)

    bad = []
    docs = sorted(SOM.glob("skills/*/SKILL.md")) + sorted(SOM.glob("commands/*.md"))
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        head = text.split("---", 2)[1]          # frontmatter only
        for flag in sorted(set(re.findall(r"(--[a-z][a-z0-9-]{2,})", head))):
            if flag in host or flag in engine:
                continue
            bad.append(f"{doc.relative_to(SOM)}: {flag} 를 구현한 코드가 없습니다")
    assert not bad, "\n".join(bad)


def test_marketplace_points_at_the_plugin_dir():
    d = _json(MARKET)
    assert d.get("name") == "som-marketplace"
    names = [p["name"] for p in d["plugins"]]
    assert "som" in names, names
    som = next(p for p in d["plugins"] if p["name"] == "som")
    assert (SOM.parent / som["source"]).resolve() == SOM.resolve(), (
        f"marketplace source {som['source']!r} does not resolve to the plugin dir")


# ======================================= what a public repository publishes
#
# This repository is public. Everything below is about one class of mistake:
# a file named ".sample" that is not a sample.
#
# The credential example under standard/secrets/ carried a real Snowflake
# account identifier, and the README beside it repeated it twice. No credential
# ever leaked -- the git history has never held a key, a password or a token --
# but the account identifier names the login endpoint, and published next to
# the schema map and the login-ID format it turns an attack from "find a
# target" into "guess one credential".
#
# Snowflake does not treat the account locator as a secret and the real defence
# is MFA plus a network policy. This is defence in depth, and cheap: an example
# file has no reason to hold a live value.

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".som", "_cache"}
TEXT_SUFFIXES = {".py", ".mjs", ".js", ".json", ".md", ".sample", ".toml",
                 ".yml", ".yaml", ".txt", ".sql", ".ps1", ".css", ".html"}


def _repo_text_files():
    """Every text file in the repository, minus this test and the caches."""
    for f in sorted(SOM.parent.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in f.parts):
            continue
        if f.name == Path(__file__).name:
            continue                       # this file names the patterns
        try:
            yield f, f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def _is_placeholder(value: str) -> bool:
    """A stand-in a reader is obviously meant to replace."""
    v = value.strip()
    if not v:
        return True
    if "<" in v or ">" in v:
        return True
    # An interpolation is code. `account="${X}"` in a test fixture is not an
    # account, and flagging it taught the reader to ignore this check.
    if "${" in v or v.startswith("$"):
        return True
    low = v.lower()
    return any(w in low for w in ("example", "your", "changeme",
                                 "placeholder", "xxxx", "sample"))


def test_no_live_account_identifier_is_published():
    """An account field has to hold a stand-in, everywhere.

    The shape ORG-ACCOUNT is exactly what goes in front of the Snowflake host
    name, so publishing one publishes the endpoint.
    """
    field = '"' + "account" + '"' + r'\s*:\s*"([^"]*)"'
    bad = []
    for f, text in _repo_text_files():
        for m in re.finditer(field, text):
            if not _is_placeholder(m.group(1)):
                rel = f.relative_to(SOM.parent).as_posix()
                bad.append(rel + ': account="' + m.group(1) + '"')
    assert not bad, (
        "공개 저장소에 실제 account identifier 가 있습니다:\n  "
        + "\n  ".join(bad)
        + "\n  <ORG>-<ACCOUNT> 같은 placeholder 로 바꾸세요.")


def test_no_concrete_connection_endpoint_is_published():
    """The same value in URL form, which is how it usually comes back."""
    host = r"([A-Za-z0-9_<>-]+)\." + "snowflakecomputing" + r"\.com"
    bad = []
    for f, text in _repo_text_files():
        for m in re.finditer(host, text):
            if not _is_placeholder(m.group(1)):
                rel = f.relative_to(SOM.parent).as_posix()
                bad.append(rel + ": " + m.group(0))
    assert not bad, (
        "접속 URL 에 실제 계정이 박혀 있습니다:\n  " + "\n  ".join(bad))


def test_the_credential_example_is_valid_json_and_entirely_placeholders():
    """It still has to be copy-and-edit usable, not merely scrubbed."""
    example = next((SOM / "standard" / "secrets").glob("*.sample"), None)
    assert example is not None, "자격증명 예시 파일이 없습니다"
    cfg = json.loads(example.read_text(encoding="utf-8"))
    for key in ("account", "user"):
        assert key in cfg, f"예시에 {key} 가 없습니다 — 복사해서 못 씁니다"
        assert _is_placeholder(str(cfg[key])),             f"예시의 {key} 가 실제 값입니다: {cfg[key]!r}"
    # externalbrowser is the interactive default and the reason the file holds
    # no secret at all. If that changes, the "파일에 비밀이 없다" claim beside
    # it stops being true.
    assert cfg.get("authenticator") == "externalbrowser", cfg


def test_no_private_key_material_anywhere_in_the_tree():
    """The thing that would actually be serious. Cheap to keep checking.

    The header alone is not the finding -- `test/patterns.test.mjs` quotes it
    inside a string to prove the secret scanner catches it, and failing on that
    would train someone to delete the check rather than the key. Real material
    is a header followed by a base64 body, so that is what this looks for.
    """
    begin = "-----BEGIN" + r"[A-Z ]*" + "PRIVATE KEY-----"
    body = begin + r"\s*[\r\n]+\s*[A-Za-z0-9+/=]{40}"
    bad = [f.relative_to(SOM.parent).as_posix()
           for f, text in _repo_text_files()
           if re.search(body, text)]
    assert not bad, "키 본문이 저장소에 있습니다: " + ", ".join(bad)


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
