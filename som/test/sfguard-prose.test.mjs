/**
 * You must be able to write about the guard.
 *
 * Explaining what this guard refuses means naming the tools it refuses, and
 * `git commit -m "..."` puts that text on the command line. So the guard
 * refused the commit message documenting it -- found by trying to make that
 * commit.
 *
 * It is the same mistake as denying `pip install` of the connector: judging a
 * command by a string it contains rather than by what the command does. A tool
 * that stores or searches prose cannot execute a query.
 *
 *   node --test test/sfguard-prose.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { judgeCommand } from "../lib/guard/snowflake.mjs";

const CLI = "snow" + "sql";
const HELPER = "sf_" + "utils";
const PKG = "snow" + "flake-connector-python";

test("a commit message may name what the guard refuses", () => {
  for (const cmd of [
    `git commit -m "guard: refuse ${CLI} outside the entrypoint"`,
    `git commit -m "drop ${HELPER} in favour of the sanctioned client"`,
    `git tag -a v2 -m "${CLI} bypass closed"`,
    `git log --grep "${CLI}"`,
    `gh pr create --title "guard" --body "closes the ${HELPER} bypass"`,
    `echo "${CLI} 은 쓰지 마세요"`,
  ]) {
    assert.equal(judgeCommand(cmd).decision, "allow", `오탐: ${cmd.slice(0, 70)}`);
  }
});

test("a prose tool does not sanction a query beside it", () => {
  // Per segment, like every other exemption. This is the shape of the bug that
  // once let a trailing comment switch the whole guard off.
  for (const cmd of [
    `git commit -m "note" ; ${CLI} -q "SELECT 1"`,
    `git commit -m "note" && ${CLI} -q "SELECT 1"`,
    `echo hi | ${CLI}`,
    `pip install ${PKG} ; ${CLI} -q "SELECT 1"`,
  ]) {
    assert.equal(judgeCommand(cmd).decision, "warn", `우회가 통과했습니다: ${cmd.slice(0, 70)}`);
  }
});

test("a file reader is never prose", () => {
  // Printing a credential file is exactly what this guard refuses, so `cat`
  // and its relatives are not in the exemption. Putting them there broke
  // "credential file references are flagged", which is how they came back out.
  const TOML = "~/." + "snowflake/connections.toml";
  const JSON_CFG = "sf_" + "login_info.json";
  assert.equal(judgeCommand(`cat ${TOML}`).decision, "warn");
  assert.equal(judgeCommand(`head -5 ${JSON_CFG}`).decision, "warn");
  assert.equal(judgeCommand(`cat q.sql | ${CLI}`).decision, "warn");
  // A plain read needs no exemption: it names no surface, so it always passed.
  assert.equal(judgeCommand("cat engine/requirements.txt").decision, "allow");
});

test("command substitution is execution, whatever the tool is called", () => {
  // Adding the prose exemption opened exactly this hole: `echo $(...)` and its
  // backtick spelling run the command and print the result.
  for (const cmd of [
    `echo $(${CLI} -q "SELECT 1")`,
    "echo `" + CLI + ' -q "SELECT 1"`',
    `git commit -m "$(${CLI} -q 'SELECT 1')"`,
  ]) {
    assert.equal(judgeCommand(cmd).decision, "warn",
      `치환이 산문으로 통과했습니다: ${cmd.slice(0, 60)}`);
  }
});
