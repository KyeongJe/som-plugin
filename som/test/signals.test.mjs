/**
 * Patterns the engine writes by itself, from what a run actually did.
 *
 * The learning loop had a broken first link. `conduct` injected patterns and
 * graded them, both automatically, and created none: `propose()` was reachable
 * only from the CLI. Someone who used the plugin heavily and never typed
 * `/som:learn` ended up with an empty library, no injected patterns, and no
 * promoted skills -- which reads as "nothing was worth saving" and was really
 * "nothing was ever offered". This module closes that link.
 *
 * Two properties matter and they pull against each other:
 *
 *   1. It has to produce something, or the loop stays broken.
 *   2. What it produces has to survive the same gate a person's lesson does,
 *      without the gate being loosened to let it through.
 *
 * So the load-bearing test here is the round trip: draft -> propose() -> stored.
 * A draft that is too vague to name a node or a file must be refused, and the
 * fix is a better draft, never a softer gate.
 *
 *   node --test test/signals.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { scratch } from "./tmp.mjs";
import { AUTO_CONFIDENCE, MAX_DRAFTS, signalsFrom } from "../lib/domain/signals.mjs";
import { validatePattern, newPattern, START_CONFIDENCE } from "../lib/domain/patterns.mjs";
import { PatternLibrary } from "../lib/state/patterns.mjs";

const NODES = [
  { key: "kpi-schema", role: "analyst", writes: ["docs/kpi/**"] },
  { key: "render", role: "builder", writes: ["src/render/**"] },
];

/** A run in which `kpi-schema` wrote somewhere it had not declared. */
function ranWithViolation(files = ["src/other/thing.mjs"]) {
  return {
    dispatches: {
      d1: { key: "kpi-schema", outcome: "succeeded", violations: files,
            filesModified: ["docs/kpi/a.md", ...files] },
      d2: { key: "render", outcome: "succeeded", violations: [] },
    },
    events: [],
  };
}

// ----------------------------------------------------------------- signals
test("an out-of-scope write becomes a draft naming both sides", () => {
  const [d, ...rest] = signalsFrom(ranWithViolation(), {
    nodes: NODES, recipe: "doc", runId: "run_1",
  });
  assert.equal(rest.length, 0, "위반 1건인데 초안이 더 나왔습니다");
  assert.match(d.title, /kpi-schema/);
  assert.match(d.action, /src\/other\/thing\.mjs/, "실제 경로가 빠졌습니다");
  assert.match(d.action, /docs\/kpi/, "선언했던 글롭이 빠졌습니다");
  assert.equal(d.source, "auto");
  assert.equal(d.confidence, AUTO_CONFIDENCE);
  assert.ok(d.evidence.some((e) => e.kind === "run" && e.ref === "run_1"));
  assert.ok(d.evidence.some((e) => e.kind === "file"));
});

test("a clean run teaches nothing", () => {
  // Success is the baseline. An engine that proposed a pattern per finished
  // run would fill the library with noise and drown the real findings.
  const clean = {
    dispatches: { d1: { key: "kpi-schema", outcome: "succeeded", violations: [] } },
    events: [],
  };
  assert.deepEqual(signalsFrom(clean, { nodes: NODES, runId: "run_1" }), []);
});

test("one retry is weather; two is a pattern", () => {
  // `attempts` is what the scheduler itself keeps, in state.tasks. A live run
  // showed that `state.events` -- which an earlier draft of this module read --
  // has never existed: events are appended to .som/events.ndjson and reach
  // this function through the `events` option.
  const ran = (attempts) => ({
    tasks: { t1: { key: "render", attempts } },
    dispatches: { d1: { key: "render", outcome: "succeeded", filesModified: ["src/render/a.mjs"] } },
  });
  assert.deepEqual(signalsFrom(ran(2), { nodes: NODES, runId: "run_1" }), []);

  const [d] = signalsFrom(ran(3), { nodes: NODES, recipe: "build", runId: "run_1" });
  assert.match(d.title, /render/);
  assert.match(d.action, /쪼갠다/);
  assert.match(d.action, /src\/render\/a\.mjs/);
});

test("a node that never passed is not yet a lesson", () => {
  // It is still broken. "Split this next time" is advice about something that
  // worked in the end; a task that failed outright needs a person, not a
  // pattern asserting how to size it.
  const stillFailing = {
    tasks: { t1: { key: "render", attempts: 3 } },
    dispatches: { d1: { key: "render", outcome: "failed", filesModified: [] } },
  };
  assert.deepEqual(signalsFrom(stillFailing, { nodes: NODES, runId: "run_1" }), []);
});

test("an escalation says the plan was missing an input", () => {
  // The real event carries a task id and no key -- exactly what a live run
  // produced -- so the task table is what turns it into a node name. Without
  // that the pattern read "task_143c6761ce82 는 사람에게 물어봐야 진행된다".
  const blocked = {
    tasks: { task_abc: { key: "kpi-schema" } },
    dispatches: { d1: { key: "kpi-schema", outcome: "succeeded", violations: [] } },
  };
  const [d] = signalsFrom(blocked, {
    nodes: NODES, recipe: "doc", runId: "run_1",
    events: [{ kind: "escalation", task: "task_abc" }],
  });
  assert.match(d.title, /kpi-schema/);
  assert.match(d.action, /인터뷰/);
  // It must not quote what the worker wrote. That is prose this module did not
  // measure, and repeating it would launder a model's words as an observation.
  assert.ok(!/summary/i.test(JSON.stringify(d)));
});

test("a run that trips many things still yields a handful", () => {
  const messy = { dispatches: {}, events: [] };
  for (let i = 0; i < 20; i += 1) {
    messy.dispatches[`d${i}`] = {
      key: `n${i}`, outcome: "succeeded", violations: [`src/x${i}.mjs`],
    };
  }
  const out = signalsFrom(messy, { nodes: [], runId: "run_1" });
  assert.equal(out.length, MAX_DRAFTS, `${out.length}건이 나왔습니다`);
});

test("an auto draft starts below a hand-written one", () => {
  // A person proposing a lesson has already decided it is one. The engine has
  // only seen something happen once, so it has to be right repeatedly before
  // it carries weight -- and promotion to a skill needs an average of 70.
  const [d] = signalsFrom(ranWithViolation(), { nodes: NODES, runId: "run_1" });
  assert.ok(d.confidence < START_CONFIDENCE, `${d.confidence} vs ${START_CONFIDENCE}`);
});

test("missing or malformed state does not throw", () => {
  for (const bad of [undefined, {}, { dispatches: null, tasks: null },
                     { dispatches: { d: {} }, tasks: { t: {} } }]) {
    assert.doesNotThrow(() => signalsFrom(bad, {
      nodes: NODES, runId: "r", events: [{}, null, { kind: "escalation" }],
    }));
  }
  assert.doesNotThrow(() => signalsFrom({}, { nodes: NODES, events: "not an array" }));
});

// ------------------------------------------- the part that actually matters
test("every draft survives the gate that judges a human's lesson", () => {
  // The whole feature is worthless if the drafts are refused, and dangerous if
  // the gate is loosened to admit them. This is the test that keeps both from
  // happening quietly.
  const state = {
    tasks: { t1: { key: "kpi-schema" }, t2: { key: "render", attempts: 3 } },
    dispatches: {
      d1: { key: "kpi-schema", outcome: "succeeded", violations: ["src/other/thing.mjs"],
            filesModified: ["docs/kpi/a.md", "src/other/thing.mjs"] },
      d2: { key: "render", outcome: "succeeded", filesModified: ["src/render/a.mjs"] },
    },
  };
  const drafts = signalsFrom(state, {
    nodes: NODES, recipe: "doc", runId: "run_1",
    events: [{ kind: "escalation", task: "t1" }],
  });
  assert.ok(drafts.length >= 3, `초안이 ${drafts.length}건뿐입니다`);

  for (const d of drafts) {
    const problems = validatePattern(newPattern(d), { evidenceExists: () => true });
    assert.deepEqual(problems, [],
      `게이트가 거부한 자동 초안:\n  ${d.title}\n  ${problems.join("\n  ")}`);
  }
});

test("the round trip actually stores something", () => {
  const dir = scratch("som-signals-");
  mkdirSync(join(dir, ".som"), { recursive: true });
  // `evidenceExists` checks run ids against the state file and paths on disk,
  // so both have to be real for this to prove anything.
  writeFileSync(join(dir, ".som", "state.json"),
                JSON.stringify({ runId: "run_1" }), "utf8");
  mkdirSync(join(dir, "src", "other"), { recursive: true });
  writeFileSync(join(dir, "src", "other", "thing.mjs"), "//\n", "utf8");

  const lib = new PatternLibrary(dir);
  const drafts = signalsFrom(ranWithViolation(), {
    nodes: NODES, recipe: "doc", runId: "run_1",
  });
  const results = drafts.map((d) => lib.propose(d, { scope: "project" }));
  const stored = results.filter((r) => r.ok);
  assert.equal(stored.length, drafts.length,
    "거부됨:\n  " + results.filter((r) => !r.ok)
      .map((r) => r.problems.join("; ")).join("\n  "));
  assert.equal(lib.all().length, drafts.length);
  assert.equal(lib.all()[0].confidence, AUTO_CONFIDENCE);
});

test("the same problem twice merges instead of duplicating", () => {
  // A recurring failure is more worth recording, not two rows. Confidence
  // rises because the content hash matches.
  const dir = scratch("som-signals-");
  mkdirSync(join(dir, ".som"), { recursive: true });
  writeFileSync(join(dir, ".som", "state.json"),
                JSON.stringify({ runId: "run_1" }), "utf8");
  mkdirSync(join(dir, "src", "other"), { recursive: true });
  writeFileSync(join(dir, "src", "other", "thing.mjs"), "//\n", "utf8");

  const lib = new PatternLibrary(dir);
  const d = signalsFrom(ranWithViolation(), { nodes: NODES, recipe: "doc", runId: "run_1" })[0];
  const first = lib.propose(d, { scope: "project" });
  const second = lib.propose(d, { scope: "project" });
  assert.ok(first.ok && second.ok);
  assert.equal(lib.all().length, 1, "같은 관측이 두 줄이 됐습니다");
  assert.ok(second.pattern.confidence > first.pattern.confidence);
});
