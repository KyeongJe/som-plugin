/**
 * Regressions from the adversarial audit of the 5% gate.
 *
 * The worst finding here was not a bypass but the opposite: the most honest
 * user was permanently locked out, and the escape the documentation offered
 * did not exist in code. That one is first.
 *
 *   node --test test/clarity-audit.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { scratch } from "./tmp.mjs";

import {
  GAP_CAP, ROTATE_TOLERANCE, ROUND_HARD_CAP,
  ambiguityOf, nextTarget,
} from "../lib/domain/clarity.mjs";
import {
  Interviews, clarityGate, emptyInterview,
} from "../lib/state/interview.mjs";

const proj = () => {
  process.env.CLAUDE_CONFIG_DIR = scratch("som-cfg-");
  return scratch("som-cl-");
};

/** An honest scorer: everything clear, two things genuinely unknown. */
const HONEST = {
  goal: { score: 1, justification: "확정", gap: "" },
  constraints: { score: 1, justification: "확정", gap: "보존 기간 미정" },
  criteria: { score: 1, justification: "확정", gap: "검증 방법 미정" },
};

function settledSetup(scores = HONEST, objective = "재고 리포트") {
  const project = proj();
  const store = new Interviews(project);
  let iv = emptyInterview({ objective, recipeId: "analyze", project });
  iv = store.confirmTopology(iv, [{ id: "rep", name: "리포트" }]);
  const r = store.addRound(iv, {
    componentId: "rep", dimension: "goal",
    question: "q", answer: "a", scores,
  });
  store.save(r.iv);
  return { project, store, objective, iv: r.iv };
}

test("two admitted unknowns floor the score above the threshold", () => {
  // The arithmetic that caused the lockout, stated as a test so it cannot be
  // rediscovered by a user instead: a capped dimension leaves weight x 0.11,
  // so two of them on greenfield floor at 6.6% and no further round can reach
  // 5%. This is the *expected* behaviour -- the bug was having no way out.
  const { ambiguity } = ambiguityOf(HONEST);
  assert.ok(Math.abs(ambiguity - 0.6 * (1 - GAP_CAP)) < 1e-9, `${ambiguity}`);
  assert.ok(ambiguity > 0.05, "this is the floor the escape has to clear");
});

test("running more rounds does not clear that floor", () => {
  const { project, store, objective } = settledSetup();
  let iv = store.load(store.findFor(objective).slug);
  for (const n of [5, 19, ROUND_HARD_CAP, 25]) {
    while (iv.rounds.length < n) {
      iv = store.addRound(iv, {
        componentId: "rep", dimension: "goal",
        question: "q", answer: "a", scores: HONEST,
      }).iv;
    }
    store.save(iv);
    assert.equal(clarityGate({ project, objective, recipeId: "analyze" }).ok, false,
                 `round ${n} must still refuse`);
  }
});

test("accepting an unknown is the escape, and it actually opens the gate", () => {
  // The docs told the user to answer 미확정 -- and that string read as an open
  // gap, so the word offered as the escape was the word that closed the gate.
  // An accepted unknown is a human decision recorded as data instead.
  // Three capped dimensions, so that accepting one is still not enough --
  // 0.40 + 0.30 = 0.70 of weight left capped is 7.7%, over the threshold.
  const { project, store, objective } = settledSetup({
    goal: { score: 1, justification: "확정", gap: "핵심 엔티티 미정" },
    constraints: { score: 1, justification: "확정", gap: "보존 기간 미정" },
    criteria: { score: 1, justification: "확정", gap: "검증 방법 미정" },
  });
  let iv = store.findFor(objective);

  iv = store.acceptUnknown(iv, "rep", "criteria", "검증은 수동으로");
  store.save(iv);
  assert.equal(clarityGate({ project, objective, recipeId: "analyze" }).ok, false,
               "one of three accepted is still over the threshold");

  iv = store.acceptUnknown(iv, "rep", "constraints", "정책 확정 후 별도로");
  iv = store.acceptUnknown(iv, "rep", "goal", "엔티티는 1차 산출물 보고 정하기로");
  store.save(iv);
  const g = clarityGate({ project, objective, recipeId: "analyze" });
  assert.equal(g.ok, true, g.problem);

  // The unknown is still on the record -- accepted, not erased.
  const comp = g.interview.topology.components.find((c) => c.id === "rep");
  assert.equal(comp.scores.constraints.gap, "보존 기간 미정");
  assert.equal(comp.scores.constraints.decided, true);
  assert.ok(comp.scores.constraints.decidedNote);
});

test("a scorer cannot mark its own gaps as accepted", () => {
  // The escape was briefly a bypass: passing decided:true inside `scores` to
  // addRound() made the gap cap voluntary, which defeats the whole reason the
  // arithmetic is in code. Verified before the strip: three flagged dimensions
  // took ambiguity to 0% and opened the gate in one round.
  const project = proj();
  const store = new Interviews(project);
  let iv = store.confirmTopology(
    emptyInterview({ objective: "우회 시도", recipeId: "analyze", project }),
    [{ id: "a", name: "A" }]);
  store.save(iv);

  const r = store.addRound(iv, {
    componentId: "a", dimension: "goal", question: "q", answer: "a",
    scores: {
      goal: { score: 1, gap: "핵심 미정", decided: true },
      constraints: { score: 1, gap: "범위 미정", acceptedUnknown: true },
      criteria: { score: 1, gap: "검증 미정", decided: true },
    },
  });
  store.save(r.iv);
  assert.ok(r.iv.ambiguity > 0.05, `ambiguity is ${r.iv.ambiguity}`);
  assert.equal(clarityGate({ project, objective: "우회 시도", recipeId: "analyze" }).ok,
               false);
  const scored = r.iv.topology.components[0].scores;
  for (const dim of ["goal", "constraints", "criteria"]) {
    assert.notEqual(scored[dim].decided, true, `${dim} kept the flag`);
    assert.notEqual(scored[dim].acceptedUnknown, true, `${dim} kept the alias`);
  }

  // The human route still works, and a later re-score does not undecide it.
  let v = r.iv;
  for (const dim of ["goal", "constraints", "criteria"]) {
    v = store.acceptUnknown(v, "a", dim, "모르는 채로 가기로");
  }
  store.save(v);
  assert.equal(clarityGate({ project, objective: "우회 시도", recipeId: "analyze" }).ok,
               true);

  const after = store.addRound(v, {
    componentId: "a", dimension: "goal", question: "q2", answer: "a2",
    scores: { goal: { score: 1, gap: "핵심 미정" } },
  });
  assert.equal(after.iv.topology.components[0].scores.goal.decided, true,
               "a human decision is not undone by a later round");
});

test("`decided` has to be set explicitly, not inferred from the gap text", () => {
  // Whitelisting 미확정 would conflate "I have not worked it out" with "we have
  // decided to proceed without it". Only the second may release the gate.
  for (const gap of ["미확정", "TBD", "tbd", "unknown", "정해지지 않음", "?"]) {
    const { parts } = ambiguityOf({ ...HONEST, constraints: { score: 1, gap } });
    const c = parts.find((p) => p.dim === "constraints");
    assert.equal(c.capped, true, `gap ${JSON.stringify(gap)} must still cap`);
  }
  const decided = ambiguityOf({
    ...HONEST, constraints: { score: 1, gap: "미확정", decided: true },
  }).parts.find((p) => p.dim === "constraints");
  assert.equal(decided.capped, false);
  assert.equal(decided.decided, true);
});

test("deferring a component is reachable through the API", () => {
  // `clarity.mjs` always read the deferred flag; nothing could set it, so the
  // second honest exit was only available by hand-editing JSON.
  const { store, objective } = settledSetup();
  let iv = store.confirmTopology(store.findFor(objective), [
    { id: "rep", name: "리포트" },
    { id: "mail", name: "메일 발송" },
  ]);
  iv = store.deferComponent(iv, "mail", "이번 범위 아님");
  assert.equal(iv.topology.components.find((c) => c.id === "mail").status, "deferred");
  assert.equal(iv.topology.deferrals[0].reason, "이번 범위 아님");
});

test("confirmTopology is what makes the gate's precondition reachable", () => {
  const project = proj();
  const store = new Interviews(project);
  const iv = emptyInterview({ objective: "무엇이든", project });
  assert.equal(iv.topology.status, "pending");
  assert.equal(store.confirmTopology(iv, [{ id: "a", name: "A" }]).topology.status,
               "confirmed");
});

// ------------------------------------------------------------- the gate keys
test("an interview settled for one recipe does not authorise another", () => {
  // clarityGate destructured recipeId and never read it, so a settled `doc`
  // interview opened the gate for a `prd` run whose questions it never asked.
  const { project, store, objective } = settledSetup();
  let iv = store.findFor(objective);
  iv = store.acceptUnknown(iv, "rep", "constraints", "x");
  iv = store.acceptUnknown(iv, "rep", "criteria", "y");
  store.save(iv);

  assert.equal(clarityGate({ project, objective, recipeId: "analyze" }).ok, true);
  const wrong = clarityGate({ project, objective, recipeId: "prd" });
  assert.equal(wrong.ok, false);
  assert.match(wrong.problem, /analyze/);
  assert.match(wrong.problem, /prd/);
});

test("two objectives differing past the readable slug do not collide", () => {
  // The slug truncated at 60 characters, so "...plan A which we will ship
  // first" and "...plan B which is the opposite decision" shared one record
  // and B's run started on A's interview.
  const head = "customer churn report for the executive team with monthly cohorts and ";
  const a = `${head}plan A which we will ship first`;
  const b = `${head}plan B which is the opposite decision`;

  const project = proj();
  const store = new Interviews(project);
  let iv = emptyInterview({ objective: a, recipeId: "analyze", project });
  iv = store.confirmTopology(iv, [{ id: "rep", name: "리포트" }]);
  const r = store.addRound(iv, {
    componentId: "rep", dimension: "goal", question: "q", answer: "a",
    scores: {
      goal: { score: 1, gap: "" }, constraints: { score: 1, gap: "" },
      criteria: { score: 1, gap: "" },
    },
  });
  store.save(r.iv);

  assert.equal(clarityGate({ project, objective: a, recipeId: "analyze" }).ok, true);
  assert.equal(clarityGate({ project, objective: b, recipeId: "analyze" }).ok, false,
               "B must not start on A's interview");
});

// ------------------------------------------------------------- targeting
test("rotation triggers on similarly weak, not only on an exact tie", () => {
  // With A at 36.0% and B at 34.5% the strict comparison returned A round
  // after round -- the depth-first overfitting the topology gate exists to
  // prevent.
  const components = [
    { id: "a", name: "A", status: "active",
      scores: { goal: { score: 0.9 }, constraints: { score: 0.9 }, criteria: { score: 0.1 } } },
    { id: "b", name: "B", status: "active",
      scores: { goal: { score: 0.9 }, constraints: { score: 0.95 }, criteria: { score: 0.12 } } },
  ];
  const gap = Math.abs(ambiguityOf(components[0].scores).ambiguity -
                       ambiguityOf(components[1].scores).ambiguity);
  assert.ok(gap > 0 && gap < ROTATE_TOLERANCE, `gap is ${gap}`);
  assert.equal(nextTarget(components, { lastTargetId: "a" }).component.id, "b");
  assert.equal(nextTarget(components, { lastTargetId: "b" }).component.id, "a");
});

test("a genuinely worse component still beats rotation", () => {
  const components = [
    { id: "a", name: "A", status: "active", scores: { goal: { score: 0.9 } } },
    { id: "b", name: "B", status: "active", scores: {} },
  ];
  assert.equal(nextTarget(components, { lastTargetId: "b" }).component.id, "b");
});
