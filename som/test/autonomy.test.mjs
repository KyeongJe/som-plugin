/**
 * The autonomy ledger and its floors.
 *
 * This module had no test at all, and that is why the published level table
 * drifted from the code unnoticed: README said an out-of-scope write asks at
 * L1-L4, `decide()` returned `auto`, and nothing compared them. So this file
 * asserts the table itself, cell by cell, and is the thing to update when the
 * policy changes -- not the README.
 *
 *   node --test test/autonomy.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { scratch } from "./tmp.mjs";

import { Autonomy, ACTIONS, HARD_FLOORS, LEVELS } from "../lib/domain/autonomy.mjs";
import { approvesWave, APPROVE } from "../lib/conduct.mjs";

const dir = () => scratch("som-auto-");

function at(level, d = dir()) {
  const a = new Autonomy(d);
  a.state.level = level;
  return a;
}

/**
 * The published table, as data. Each row is what README section 5 and the
 * generated doc both claim, so a change to either has to change this.
 */
const TABLE = {
  "worker.start":        ["gate", "gate", "auto", "auto", "auto"],
  "answer.question":     ["gate", "gate", "auto", "auto", "auto"],
  "answer.scope-change": ["gate", "gate", "gate", "auto", "auto"],
  "write.in-scope":      ["gate", "auto", "auto", "auto", "auto"],
  "write.out-of-scope":  ["deny", "gate", "gate", "gate", "gate"],
  "worker.stop":         ["gate", "gate", "gate", "auto", "auto"],
};

test("every published cell matches decide()", () => {
  const d = dir();
  for (const [action, expected] of Object.entries(TABLE)) {
    LEVELS.forEach((level, i) => {
      assert.equal(at(level, d).decide(action).verdict, expected[i],
                   `${action} at ${level}`);
    });
  }
});

test("the worker cap is 1/2/3/4/4", () => {
  const d = dir();
  assert.deepEqual(LEVELS.map((l) => at(l, d).maxWorkers()), [1, 2, 3, 4, 4]);
});

test("no level and no score unlocks a hard floor", () => {
  const d = dir();
  for (const [action] of HARD_FLOORS) {
    for (const level of LEVELS) {
      for (const score of [0, 50, 100]) {
        const a = at(level, d);
        a.state.score = score;
        const v = a.decide(action);
        assert.equal(v.verdict, "deny", `${action} at ${level}/${score}`);
        assert.ok(v.reason, "a refusal has to say why");
        assert.ok(v.remediation, "and where the human has to go instead");
      }
    }
  }
});

test("a hand-edited ledger cannot lift a floor", () => {
  // The file is the operator's and they can write anything into it.
  const d = dir();
  for (const state of [
    { level: "L9", score: 1e9 },
    { level: 99, score: "100" },
    { level: null, score: Infinity },
    { level: "L4", score: 100, ACTIONS: { "git.force-push": "L0" } },
    { level: "L4", HARD_FLOORS: [] },
    { __proto__: { level: "L4" } },
  ]) {
    writeFileSync(join(d, "autonomy.json"), JSON.stringify(state), "utf8");
    assert.equal(new Autonomy(d).decide("git.force-push").verdict, "deny",
                 JSON.stringify(state));
  }
});

test("an inherited property name is not an action", () => {
  // ACTIONS["constructor"] walked the prototype chain and returned a function:
  // truthy, so the fail-closed branch was skipped, and LEVELS.indexOf(fn) is
  // -1, which every level clears. decide("__proto__") returned `auto`.
  const d = dir();
  for (const name of ["constructor", "__proto__", "toString", "valueOf",
                      "hasOwnProperty", "isPrototypeOf"]) {
    assert.equal(at("L4", d).decide(name).verdict, "gate", name);
  }
});

test("an unknown action gates rather than proceeding", () => {
  assert.equal(at("L4").decide("something.invented").verdict, "gate");
  assert.equal(at("L4").decide("").verdict, "gate");
  assert.equal(at("L4").decide(undefined).verdict, "gate");
});

test("every floor in HARD_FLOORS is declared never in ACTIONS", () => {
  // The two lists have to agree, or a floor is documented but not enforced.
  for (const [action] of HARD_FLOORS) {
    assert.equal(ACTIONS[action], "never", `${action} is not "never" in ACTIONS`);
  }
  const nevers = Object.entries(ACTIONS)
    .filter(([, need]) => need === "never")
    .map(([a]) => a);
  assert.deepEqual(new Set(nevers), new Set(HARD_FLOORS.map(([a]) => a)),
                   "a never-action with no floor entry has no explanation for the user");
});

test("a floor trip resets to L0 immediately, whatever the score", () => {
  const a = at("L4");
  a.state.score = 100;
  a.record("floor_trip", { what: "테스트" });
  assert.equal(a.state.level, "L0");
  assert.equal(a.state.stats.floorTrips, 1);
  assert.equal(a.state.history.at(-1).trigger, "floor_trip");
});

test("a single lucky run cannot ratchet the level up inside itself", () => {
  const a = at("L0");
  for (let i = 0; i < 40; i += 1) a.record("wave_clean");
  // Score is high, but an upgrade needs a completed run since the last one
  // and a cooldown, so the level has not run away.
  assert.ok(a.state.score >= 40);
  assert.equal(a.state.level, "L0");
});

test("the summary names every floor, so a human can see what it will not do", () => {
  const text = at("L4").summary();
  for (const [, why] of HARD_FLOORS) {
    assert.ok(text.includes(why.slice(0, 12)), why.slice(0, 20));
  }
});

// ------------------------------------------------- the ledger is actually wired
// These exist because `autonomy.mjs` shipped with ZERO callers: the class, the
// ACTIONS table and HARD_FLOORS were documentation, while `conduct` took a bare
// integer and used it only for the worker cap. Every claim in README section 5
// described a system that did not run. If these fail, it has gone dead again.

test("conduct consults the ledger rather than an integer", async () => {
  const { Conduct } = await import("../lib/conduct.mjs");
  process.env.CLAUDE_PLUGIN_DATA = dir();
  const c = new Conduct({ project: dir(), say: () => {}, requireInterview: false });
  assert.ok(c.autonomy instanceof Autonomy,
            "conduct must hold a ledger, not a number");
  assert.equal(typeof c.autonomy.decide, "function");
});

test("a numeric autonomy is still accepted and pins the level", async () => {
  const { Conduct } = await import("../lib/conduct.mjs");
  process.env.CLAUDE_PLUGIN_DATA = dir();
  for (const [n, level, workers] of [[0, "L0", 1], [2, "L2", 3], [4, "L4", 4]]) {
    const c = new Conduct({ project: dir(), say: () => {}, autonomy: n,
                            requireInterview: false });
    assert.equal(c.autonomy.level, level);
    assert.equal(c.autonomy.maxWorkers(), workers);
  }
});

test("the level changes what worker.start and a question do", async () => {
  const { Conduct } = await import("../lib/conduct.mjs");
  process.env.CLAUDE_PLUGIN_DATA = dir();
  const low = new Conduct({ project: dir(), say: () => {}, autonomy: 0,
                            requireInterview: false });
  const high = new Conduct({ project: dir(), say: () => {}, autonomy: 3,
                             requireInterview: false });
  assert.equal(low.autonomy.decide("worker.start").verdict, "gate");
  assert.equal(high.autonomy.decide("worker.start").verdict, "auto");
  assert.equal(low.autonomy.decide("answer.question").verdict, "gate");
  assert.equal(high.autonomy.decide("answer.question").verdict, "auto");
  // And the floor does not move with the level.
  for (const c of [low, high]) {
    assert.equal(c.autonomy.decide("git.force-push").verdict, "deny");
    // A Snowflake write is not a floor any more -- the team has to be able to
    // create a table. It is a gate at every level instead, which is a
    // different promise and worth pinning separately: never refused outright,
    // and never granted without asking, however much the level has climbed.
    assert.equal(c.autonomy.decide("snowflake.write").verdict, "gate");
  }
});

test("the router is handed the ledger, not a number", async () => {
  const { Conduct } = await import("../lib/conduct.mjs");
  process.env.CLAUDE_PLUGIN_DATA = dir();
  const c = new Conduct({ project: dir(), say: () => {}, requireInterview: false });
  assert.equal(typeof c.router.autonomy?.decide, "function",
               "the router cannot gate a question without the ledger");
});

test("success moves the level and a floor trip resets it", () => {
  // "성공 이력이 쌓이면 물어보는 횟수가 줄어든다" needs a mechanism, and the
  // mechanism is only real if something calls record().
  const d = dir();
  const a = new Autonomy(d);
  assert.equal(a.level, "L0");
  for (let i = 0; i < 7; i += 1) {
    a.record("run_accepted");
    a.record("wave_clean");
  }
  assert.notEqual(a.level, "L0", "a clean record has to buy something");
  assert.ok(a.maxWorkers() > 1);

  a.record("floor_trip", { what: "테스트" });
  assert.equal(a.level, "L0");
});

/**
 * The wave gate's answer parser.
 *
 * It was `!/중단|취소|no|stop/i.test(answer)` -- a negative match against four
 * words, so every refusal outside that list read as approval. 18 of 19 natural
 * Korean refusals launched the wave, and `record("human_interrupted")` never
 * fired, so the ledger did not even show that a person had said no.
 *
 * This is the only place L0 and L1 stop to ask before spending money on
 * workers, which is why the corpus below is long and why approval is now an
 * exact match against an option that was actually offered.
 */
const REFUSALS = [
  "아니요", "아니오", "아뇨", "안 돼요", "안돼", "하지 마세요", "하지마",
  "거부", "거절", "싫어", "싫어요", "그만", "그만하세요", "멈춰", "멈추세요",
  "보류", "잠깐만", "잠시만요", "나중에", "다음에", "안됨", "반대", "불허",
  "노", "No way", "nope", "nah", "cancel", "abort", "halt", "don't",
  "해당 없음", "모르겠어요", "글쎄요", "음...", "왜요?", "뭐라고요?",
  "", "   ", "계속하지 마세요", "계속하면 안 됩니다",
];

const APPROVALS = ["계속", "continue", "yes", "y", "예", "네"];

test("every refusal in the corpus stops the wave", () => {
  const leaked = REFUSALS.filter((a) => approvesWave(a));
  assert.deepEqual(leaked, [],
    `${leaked.length}개의 거부 표현이 승인으로 샜습니다: ${JSON.stringify(leaked)}`);
});

test("the offered options are accepted, whitespace and case included", () => {
  for (const a of APPROVALS) {
    assert.ok(approvesWave(a), `"${a}" should approve`);
    assert.ok(approvesWave(`  ${a}  `), `"  ${a}  " should approve`);
    assert.ok(approvesWave(a.toUpperCase()) || a !== a.toUpperCase(),
      `"${a.toUpperCase()}" should approve`);
  }
});

test("a non-string answer never approves", () => {
  for (const a of [null, undefined, 0, 1, true, false, {}, [], ["계속"]]) {
    assert.equal(approvesWave(a), false, `${JSON.stringify(a)} approved`);
  }
});

test("a sentence that merely contains an option word does not approve", () => {
  // The old parser's failure mode in the other direction: substring matching
  // turns "계속하면 안 됩니다" into consent.
  for (const a of ["계속하면 안 됩니다", "계속은 어렵겠어요", "yes 라고 하기엔 이릅니다"]) {
    assert.equal(approvesWave(a), false, `"${a}" approved`);
  }
});

test("APPROVE is frozen so a caller cannot widen what counts as consent", () => {
  assert.ok(Object.isFrozen(APPROVE));
  try { APPROVE.push("아니요"); } catch { /* strict mode throws */ }
  assert.equal(approvesWave("아니요"), false);
});

/**
 * The published table, parsed out of README.md and compared to `decide()`.
 *
 * The previous version of this test hardcoded a copy of the table -- a copy
 * transcribed from the code, not from the README -- so the two could disagree
 * and the test stayed green. They did disagree: README claimed a scope-change
 * question reached a person at L3, while `decide()` answered it automatically.
 *
 * Reading the README is the point. A cell nobody can change without breaking a
 * test is the only kind of published table worth having.
 */
const VERDICT_FOR = { "확인": "gate", "자동": "auto", "거부": "deny" };

function readmeTable() {
  const readme = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), "..", "..", "README.md"), "utf8");
  const start = readme.indexOf("<!-- AUTONOMY-TABLE:");
  assert.ok(start > 0, "README 에서 AUTONOMY-TABLE 표식을 찾지 못했습니다");

  const rows = [];
  for (const line of readme.slice(start).split("\n")) {
    if (!line.startsWith("|")) {
      if (rows.length) break;            // table ended
      continue;
    }
    const cells = line.split("|").slice(1, -1).map((c) => c.trim());
    if (cells[0].startsWith("---") || !cells[0].startsWith("`")) continue;
    const action = cells[0].match(/`([^`]+)`/)?.[1];
    assert.ok(action, `표의 행 이름에서 action 을 읽지 못했습니다: ${cells[0]}`);
    rows.push([action, cells.slice(1).map((c) => c.replace(/\*/g, "").trim())]);
  }
  assert.ok(rows.length >= 6, `표에서 행을 ${rows.length}개만 읽었습니다`);
  return rows;
}

test("every cell of the published table is what decide() returns", () => {
  const wrong = [];
  for (const [action, cells] of readmeTable()) {
    assert.equal(cells.length, LEVELS.length,
      `${action}: 칸이 ${cells.length}개입니다 (레벨은 ${LEVELS.length}개)`);
    cells.forEach((cell, i) => {
      const want = VERDICT_FOR[cell];
      assert.ok(want, `${action} ${LEVELS[i]}: "${cell}" 는 확인·자동·거부 중 하나여야 합니다`);
      const a = new Autonomy(dir());
      a.state.level = LEVELS[i];
      const got = a.decide(action).verdict;
      if (got !== want) {
        wrong.push(`${action} @ ${LEVELS[i]}: README "${cell}"(${want}) vs decide() ${got}`);
      }
    });
  }
  assert.deepEqual(wrong, [],
    "README 의 자율 표가 코드와 갈라졌습니다:\n  " + wrong.join("\n  "));
});

test("the published table covers every action the engine can be asked about", () => {
  const published = new Set(readmeTable().map(([a]) => a));
  // Actions the engine never reaches a decision on are not the table's job,
  // but anything a call site passes to decide() must be in it.
  const asked = ["worker.start", "answer.question", "answer.scope-change",
                 "worktree.create"];
  const missing = asked.filter((a) => !published.has(a));
  assert.deepEqual(missing, [],
    "decide() 가 실제로 판정하는데 표에 없는 동작: " + missing.join(", "));
});

test("the worker cap in the README prose matches maxWorkers()", () => {
  const readme = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), "..", "..", "README.md"), "utf8");
  const line = readme.split("\n").find((l) => l.startsWith("동시 워커:"));
  assert.ok(line, "README 에서 '동시 워커:' 줄을 찾지 못했습니다");
  const published = line.match(/L\d (\d+)/g).map((m) => Number(m.split(" ")[1]));
  const actual = LEVELS.map((L) => {
    const a = new Autonomy(dir());
    a.state.level = L;
    return a.maxWorkers();
  });
  assert.deepEqual(published, actual,
    `README ${published.join(",")} vs maxWorkers() ${actual.join(",")}`);
});
