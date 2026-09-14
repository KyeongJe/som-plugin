/**
 * The hard floors, checked against the code rather than against the prose.
 *
 * The floors used to be seven strings and a comment that said "Frozen". Of the
 * seven, six were never passed to `decide()` by anything, `Object.freeze` was
 * absent so seven assignments flipped them all to `auto`, and the published
 * table promised 거부 at every level for rows the engine had no opinion about.
 *
 * These tests assert the three things that make a floor real: it cannot be
 * reassigned, it is enforced somewhere nameable, and that somewhere still
 * exists.
 *
 *   node --test test/floors.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { ACTIONS, HARD_FLOORS, ENFORCED_AT, Autonomy, LEVELS }
  from "../lib/domain/autonomy.mjs";

const SOM = join(dirname(fileURLToPath(import.meta.url)), "..");

/** Every .mjs under lib/, as one searchable blob per file. */
function libFiles() {
  const out = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) walk(p);
      else if (name.endsWith(".mjs")) out.push([p, readFileSync(p, "utf8")]);
    }
  };
  walk(join(SOM, "lib"));
  return out;
}

/** Source with comments removed, so a floor's own doc comment is not evidence. */
function code(text) {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/.*$/gm, "$1 ");
}

test("ACTIONS and HARD_FLOORS are actually frozen, not just called frozen", () => {
  assert.ok(Object.isFrozen(ACTIONS), "ACTIONS is not frozen");
  assert.ok(Object.isFrozen(HARD_FLOORS), "HARD_FLOORS is not frozen");
  for (const row of HARD_FLOORS) {
    assert.ok(Object.isFrozen(row), `HARD_FLOORS row ${row[0]} is not frozen`);
  }
});

test("assigning over a floor does not open it", () => {
  const a = new Autonomy(join(SOM, "test", "__nonexistent__"));
  a.state.level = "L4";
  try { ACTIONS["worktree.create"] = "L0"; } catch { /* strict mode throws */ }
  try { HARD_FLOORS.length = 0; } catch { /* strict mode throws */ }
  assert.equal(a.decide("worktree.create").verdict, "deny");
  // Six, not seven: snowflake.write left the list when writes became possible.
  // The number is asserted so emptying the array still fails this test, and so
  // that removing a floor is a deliberate edit rather than a silent one.
  assert.equal(HARD_FLOORS.length, 6, "the floor list was emptied");
});

test("every hard floor declares where it is enforced", () => {
  const floors = HARD_FLOORS.map(([a]) => a).sort();
  const declared = Object.keys(ENFORCED_AT).sort();
  assert.deepEqual(declared, floors,
    "HARD_FLOORS and ENFORCED_AT disagree -- a floor with no declared " +
    "enforcement point is a promise with nothing behind it");
});

test("a floor enforced by decide() has a real call site in lib/", () => {
  const files = libFiles();
  for (const [action, e] of Object.entries(ENFORCED_AT)) {
    if (e.how !== "decide") continue;
    const hits = files.filter(([p, t]) =>
      code(t).includes(`decide("${action}")`) &&
      !p.endsWith("autonomy.mjs"));
    assert.ok(hits.length > 0,
      `${action}: ENFORCED_AT says "decide" but nothing calls decide("${action}")`);
    for (const where of e.where) {
      assert.ok(hits.some(([p]) => p.replace(/\\/g, "/").endsWith(where)),
        `${action}: declared at ${where}, but the call is not there`);
    }
  }
});

test("a floor enforced by a guard names a file that exists and mentions it", () => {
  for (const [action, e] of Object.entries(ENFORCED_AT)) {
    if (e.how !== "guard") continue;
    assert.ok(e.where.length > 0, `${action}: "guard" with no file named`);
    for (const rel of e.where) {
      let text;
      try { text = readFileSync(join(SOM, rel), "utf8"); }
      catch { assert.fail(`${action}: guard file ${rel} does not exist`); }
      const key = action.split(".").pop();
      assert.ok(/snowflake|somsql/i.test(text) || text.includes(key),
        `${action}: ${rel} exists but does not look like its guard`);
    }
  }
});

test("a floor enforced by absence stays absent", () => {
  const files = libFiles();
  const bad = [];
  for (const [action, e] of Object.entries(ENFORCED_AT)) {
    if (e.how !== "absent") continue;
    for (const needle of e.forbid ?? []) {
      for (const [p, t] of files) {
        if (p.endsWith("autonomy.mjs")) continue;      // the floor list itself
        if (code(t).includes(needle)) {
          bad.push(`${action}: "${needle}" now appears in ` +
                   `${p.replace(/\\/g, "/").split("/som/")[1] ?? p}. ` +
                   "The engine gained a capability its floor assumed it did " +
                   "not have -- give it a decide() gate and change ENFORCED_AT.");
        }
      }
    }
  }
  assert.deepEqual(bad, [], bad.join("\n"));
});

test("no level, however high, turns a floor into auto", () => {
  for (const level of LEVELS) {
    const a = new Autonomy(join(SOM, "test", "__nonexistent__"));
    a.state.level = level;
    a.state.score = 100;
    for (const [action] of HARD_FLOORS) {
      assert.equal(a.decide(action).verdict, "deny",
        `${action} was not denied at ${level}`);
    }
  }
});

test("the published floor list is the code's floor list", () => {
  // README section 5 prints the floors as a table. It printed seven for a
  // while after the code went to six, and nothing noticed: the table was
  // prose, and prose does not fail a build. So it is parsed here, the same way
  // the autonomy table already is.
  const readme = readFileSync(join(SOM, "..", "README.md"), "utf8");
  const start = readme.indexOf("<!-- HARD-FLOORS:");
  const end = readme.indexOf("<!-- /HARD-FLOORS -->", start);
  assert.ok(start > 0 && end > start,
    "README 에서 HARD-FLOORS 표식을 찾지 못했습니다");

  const published = [];
  for (const line of readme.slice(start, end).split("\n")) {
    const m = line.match(/^\|\s*`([\w.-]+)`\s*\|/);
    if (m) published.push(m[1]);
  }
  assert.deepEqual(published.sort(), HARD_FLOORS.map(([a]) => a).sort(),
    "README 의 하드플로어 목록이 코드와 갈라졌습니다");

  // And the one that left the list is described as what it actually became.
  // "removed" and "now permitted" are very different claims.
  assert.ok(/snowflake.*쓰기는 이 목록에 없습니다/s.test(readme),
    "README 가 snowflake.write 이 왜 플로어가 아닌지 설명하지 않습니다");
});
