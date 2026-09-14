/**
 * Snowflake bypass-guard tests.
 *
 * Two properties matter and they pull in opposite directions:
 *
 *   1. Nothing reaches Snowflake except the somsql entrypoint.
 *   2. A guard bug never blocks unrelated work.
 *
 * So the false-positive cases below are as load-bearing as the deny cases. A
 * guard that blocks `grep -r SELECT src/` would be turned off within a day, and
 * then property 1 is worth nothing.
 *
 *   node --test test/
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { judgeCommand, judgeFileWrite } from "../lib/guard/snowflake.mjs";

const SOM = dirname(dirname(fileURLToPath(import.meta.url)));

/**
 * What the hook actually told the host.
 *
 * The hooks used to emit `permissionDecision: "deny"`. They now emit
 * `additionalContext`, because a refusal broke the team's pre-existing scripts
 * -- work that predates this plugin and has nothing to do with it. So the two
 * outcomes on the wire are "nothing at all" and "a note attached to a command
 * that still runs", and this reads exactly that rather than a field that no
 * longer appears.
 */
function runHook(script, payload) {
  const out = execFileSync("node", [join(SOM, "scripts", script)], {
    input: typeof payload === "string" ? payload : JSON.stringify(payload),
    encoding: "utf8",
  });
  const o = JSON.parse(out || "{}").hookSpecificOutput ?? {};
  if (o.permissionDecision) return o.permissionDecision;   // if one ever comes back
  return o.additionalContext ? "warn" : "allow";
}

// ------------------------------------------------------------------ denies
test("a direct connector import is flagged", () => {
  const v = judgeCommand('python -c "import snowflake.connector"');
  assert.equal(v.decision, "warn");
  assert.match(v.reason, /SOMSQL-OUTSIDE/);
  // Spacing inside the block is aligned for reading, so match on the shape.
  assert.match(v.reason, /somsql run\s+--file/);
  assert.match(v.reason, /somsql write\s+--file/, "쓰기 경로도 안내해야 합니다");
});

test("snowsql and snow sql are flagged", () => {
  assert.equal(judgeCommand('snowsql -q "SELECT 1"').decision, "warn");
  assert.equal(judgeCommand('snow sql -q "SELECT 1"').decision, "warn");
});

test("credential file references are flagged", () => {
  assert.equal(judgeCommand("python x.py --config sf_login_info.json").decision, "warn");
  assert.equal(judgeCommand("SF_LOGIN_INFO=/tmp/a.json python x.py").decision, "warn");
  assert.equal(judgeCommand("cat ~/.snowflake/connections.toml").decision, "warn");
});

test("reusing the legacy helper is flagged", () => {
  assert.equal(judgeCommand("python -c 'from sf_utils import read_sql'").decision, "warn");
});

// ----------------------------------------------------------------- allows
test("the somsql entrypoint is the way through", () => {
  assert.equal(judgeCommand("python -m somsql run --file sql/01.sql").decision, "allow");
  assert.equal(judgeCommand("python3 -m somsql classify --file a.sql").decision, "allow");
  assert.equal(
    judgeCommand('PYTHONPATH="$SOM/engine" python -m somsql explain --sql "SELECT 1"').decision,
    "allow",
  );
});

test("unrelated commands are never touched", () => {
  for (const cmd of [
    "ls -la",
    "git status",
    "grep -r SELECT src/",
    "python -m somdoc build ir/x.somdoc.json",
    "rg 'INSERT INTO' --type sql",
    "cat sql/01_hold_order.sql",
    "python engine/tests/test_somsql.py",
  ]) {
    assert.equal(judgeCommand(cmd).decision, "allow", cmd);
  }
});

test("empty and malformed input allows", () => {
  assert.equal(judgeCommand("").decision, "allow");
  assert.equal(judgeCommand(null).decision, "allow");
  assert.equal(judgeCommand(undefined).decision, "allow");
});

// ------------------------------------------------------------ file writes
test("authoring a connector script is flagged", () => {
  const v = judgeFileWrite("etl.py",
    "import snowflake.connector\nc = snowflake.connector.connect()\nc.cursor().execute('SELECT 1')");
  assert.equal(v.decision, "warn");
  assert.match(v.reason, /from somsql\.conn import/);
});

test("a surface mention without an execution verb is not enough to deny", () => {
  // Naming the library in a comment or a requirements line is not a bypass.
  assert.equal(
    judgeFileWrite("requirements.txt", "snowflake-connector-python==3.18.0").decision,
    "allow",
  );
});

test("docs and tests may describe the surface", () => {
  const body = "import snowflake.connector\nc.cursor().execute(1)";
  assert.equal(judgeFileWrite("docs/how.md", body).decision, "allow");
  assert.equal(judgeFileWrite("engine/tests/test_x.py", body).decision, "allow");
  assert.equal(judgeFileWrite("engine/somsql/conn.py", body).decision, "allow");
});

test("unrelated files are never touched", () => {
  assert.equal(judgeFileWrite("a.py", "import pandas as pd\nprint(1)").decision, "allow");
  assert.equal(judgeFileWrite("a.sql", "SELECT * FROM t").decision, "allow");
});

// ------------------------------------------------------- hooks end to end
test("the bash hook flags a bypass and allows everything else", () => {
  assert.equal(
    runHook("pre-bash-guard.mjs", { tool_input: { command: 'python -c "import snowflake.connector"' } }),
    "warn",
  );
  assert.equal(runHook("pre-bash-guard.mjs", { tool_input: { command: "ls -la" } }), "allow");
});

test("the write hook covers Write, Edit and NotebookEdit payload shapes", () => {
  const body = "snowflake.connector.connect()\nx.execute(1)";
  assert.equal(runHook("pre-write-guard.mjs", { tool_input: { file_path: "a.py", content: body } }), "warn");
  assert.equal(runHook("pre-write-guard.mjs", { tool_input: { file_path: "a.py", new_string: body } }), "warn");
  assert.equal(runHook("pre-write-guard.mjs", { tool_input: { notebook_path: "a.ipynb", new_source: body } }), "warn");
});

test("hooks fail open on garbage input", () => {
  for (const payload of ["not json at all", "{}", "", "[]", '{"tool_input":null}']) {
    assert.equal(runHook("pre-bash-guard.mjs", payload), "allow", JSON.stringify(payload));
    assert.equal(runHook("pre-write-guard.mjs", payload), "allow", JSON.stringify(payload));
  }
});

test("every hook script exits 0 with no stdin at all", () => {
  // Derived from hooks.json rather than listed here. The hardcoded list went
  // stale the moment session-start.mjs and post-compact.mjs were merged into
  // resume-card.mjs, and it would have said nothing about a hook registered
  // against a script that does not exist -- which is the failure that matters,
  // because the host reports it as the plugin being broken.
  const cfg = JSON.parse(readFileSync(join(SOM, "hooks", "hooks.json"), "utf8"));
  const scripts = new Set();
  for (const groups of Object.values(cfg.hooks ?? {})) {
    for (const g of groups) {
      for (const h of g.hooks ?? []) {
        const m = String(h.command ?? "").match(/scripts\/([\w.-]+\.mjs)/);
        assert.ok(m, `훅 명령에서 스크립트를 읽지 못했습니다: ${h.command}`);
        scripts.add(m[1]);
      }
    }
  }
  assert.ok(scripts.size >= 3, `hooks.json 에서 스크립트를 ${scripts.size}개만 찾았습니다`);

  for (const s of scripts) {
    const p = join(SOM, "scripts", s);
    assert.ok(existsSync(p), `hooks.json 이 없는 스크립트를 가리킵니다: scripts/${s}`);
    const out = execFileSync("node", [p], { input: "", encoding: "utf8" });
    assert.doesNotThrow(() => JSON.parse(out || "{}"), `${s} must print JSON`);
  }
});

test("no orphan script sits in scripts/ unregistered", () => {
  // The other direction: a stub left behind after its hook was removed still
  // reads as live code to whoever opens the folder.
  const cfg = JSON.parse(readFileSync(join(SOM, "hooks", "hooks.json"), "utf8"));
  const registered = new Set();
  for (const groups of Object.values(cfg.hooks ?? {})) {
    for (const g of groups) {
      for (const h of g.hooks ?? []) {
        const m = String(h.command ?? "").match(/scripts\/([\w.-]+\.mjs)/);
        if (m) registered.add(m[1]);
      }
    }
  }
  const onDisk = readdirSync(join(SOM, "scripts")).filter((f) => f.endsWith(".mjs"));
  const orphans = onDisk.filter((f) => !registered.has(f));
  assert.deepEqual(orphans, [],
    "어떤 훅도 부르지 않는 스크립트: " + orphans.join(", "));
});
