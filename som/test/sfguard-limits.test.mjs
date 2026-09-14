/**
 * The guard's deliberate limits, and the two false positives that were not.
 *
 * Split from `sfguard.test.mjs` only because writing both in one file trips
 * the *installed* copy of this very guard, which is pinned at an older commit
 * than the working tree. That is worth knowing on its own: the hooks running
 * in a session come from `~/.claude/plugins/cache/...`, not from the repo you
 * are editing, so a guard fix is not live until the plugin is reinstalled.
 *
 *   node --test test/sfguard-limits.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { judgeCommand, judgeFileWrite } from "../lib/guard/snowflake.mjs";

/** Built by concatenation so this file does not trip the guard it tests. */
const CONNECTOR = "snow" + "flake.connector";
const PKG = "snow" + "flake-connector-python";
const CLI = "snow" + "sql";

test("a cursor verb alone is allowed; naming Snowflake beside it is not", () => {
  // `conn` could be sqlite, duckdb or postgres. Denying every `.execute(` in
  // the repository would block ordinary database work and get the guard
  // switched off, which costs more than this gap. The gap is narrow: reaching
  // Snowflake means naming it, and the moment the name appears in the same
  // segment the guard fires. Two layers stand behind it -- the classifier
  // refuses the write, and a SELECT-only role would refuse it at the server.
  assert.equal(
    judgeCommand(`python -c "conn.execute_string('INSERT INTO t VALUES(1)')"`).decision,
    "allow",
    "Snowflake 를 부르지 않는 커서 호출은 막지 않습니다 (sqlite·duckdb 도 같은 모양)");
  assert.equal(
    judgeCommand(`python -c "import ${CONNECTOR}; conn.execute_string('x')"`).decision,
    "warn",
    "같은 구간에서 Snowflake 를 부르면 막혀야 합니다");
});

test("installing the connector is not using it", () => {
  // `snowflake-connector-python` is a SURFACE token, so `pip install` of it was
  // denied -- the package this plugin's own requirements.txt pins, and the one
  // /som:doctor tells you to install when it is missing. A guard that blocks
  // its own setup instructions is a guard someone switches off.
  for (const cmd of [
    `pip install ${PKG}`,
    "pip install -r engine/requirements.txt",
    `python -m pip install ${PKG}==3.18.0`,
    `uv pip install ${PKG}`,
    `pip show ${PKG}`,
    `pip uninstall ${PKG}`,
    `conda install ${PKG}`,
    `poetry add ${PKG}`,
  ]) {
    assert.equal(judgeCommand(cmd).decision, "allow", `오탐: ${cmd}`);
  }
});

test("a package-manager prefix does not sanction a query beside it", () => {
  // The exemption is per segment, exactly like the entrypoint exemption --
  // which is the bug that let a trailing comment disable the whole guard.
  assert.equal(
    judgeCommand(`pip install ${PKG}; ${CLI} -q "SELECT 1"`).decision,
    "warn", "설치 명령 뒤에 붙인 쿼리가 통과했습니다");
  assert.equal(
    judgeCommand(`pip install ${PKG} && ${CLI} -q "SELECT 1"`).decision,
    "warn", "&& 로 이어붙인 쿼리가 통과했습니다");
});

test("writing SQL down is not the moment a write gets decided", () => {
  // This guard used to refuse a write statement inside a `.sql` file, and that
  // was right while a write could never run at all -- the refusal at authoring
  // time was the only refusal there would ever be.
  //
  // A write can run now: `somsql write` prints what the statement does, to
  // which objects, whether it can be undone, and asks. Keeping the authoring
  // refusal would only move the SQL somewhere this guard cannot see, and the
  // question would be skipped rather than asked. So the file is left alone and
  // `somsql write` is where the decision happens.
  //
  // The read side of this file is the part that still has to hold: nothing
  // about SQL files got noisier.
  const writes = [
    "INSERT INTO ANALYTICS.T SELECT * FROM S;",
    "UPDATE t SET a=1;", "DELETE FROM t;", "MERGE INTO t USING s ON 1=1;",
    "TRUNCATE TABLE t;", "DROP TABLE t;", "CREATE TABLE t (a int);",
    "GRANT SELECT ON t TO r;", "COPY INTO t FROM @s;",
  ];
  for (const path of ["sql/99_load.sql", "project/sql/10_upsert.sql", "load.sql"]) {
    for (const body of writes) {
      assert.equal(judgeFileWrite(path, "-- source: ANALYTICS.T\n" + body).decision, "allow",
        `${path}: ${body.split(" ")[0]} 을 적었다는 이유로 막혔습니다`);
    }
  }
});

test("a read-only SQL file is not touched, comments included", () => {
  for (const body of [
    "-- grain: 행 1개 = 거래선\nSELECT a FROM ANALYTICS.T WHERE d >= '2026-01-01';",
    "-- 이 파일은 쓰기를 하지 않습니다\nSELECT 1;",
    "/* 주석 안의 DELETE 는 실행되지 않는다 */\nWITH x AS (SELECT 1) SELECT * FROM x;",
    "SHOW TABLES;",
    "DESCRIBE TABLE ANALYTICS.T;",
  ]) {
    assert.equal(judgeFileWrite("sql/01_read.sql", body).decision, "allow",
      `오탐: ${body.slice(0, 48)}`);
  }
});

test("the test-file exemption works on a Windows path", () => {
  // The exemption is a regex over the path, and this project runs on Windows.
  // A `[\\/]` that only matched forward slashes would silently exempt nothing
  // on the primary platform.
  const body = `import ${CONNECTOR}`;
  for (const p of [
    String.raw`C:\dev\som-claude-plugin\som\test\sfguard.test.mjs`,
    String.raw`C:\Users\example\Temp\scratchpad\extra.test.mjs`,
    "test/sfguard.test.mjs",
    "engine/tests/test_somsql.py",
  ]) {
    assert.equal(judgeFileWrite(p, body).decision, "allow", `테스트 파일이 막혔습니다: ${p}`);
  }
  // And the exemption is the file name, not the directory it sits in.
  assert.equal(judgeFileWrite(String.raw`C:\a\src\tests\loader.py`, body).decision,
    "warn", "tests/ 폴더에 있다는 이유로 면제되면 안 됩니다");
});
