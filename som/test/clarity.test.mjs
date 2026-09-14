/**
 * Ambiguity scoring and the 5% gate.
 *
 * The interview is only worth having if the number cannot be talked into
 * opening the gate, so most of these tests are attempts to do exactly that:
 * inflate a score while admitting a gap, loosen the threshold from settings,
 * hide a vague component behind a clear sibling, tie the threshold exactly,
 * or ask to skip.
 *
 *   node --test test/clarity.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { scratch } from "./tmp.mjs";

import {
  WEIGHTS, THRESHOLD_CEILING, GAP_CAP, ROUND_HARD_CAP,
  ambiguityOf, gate, nextTarget, ontologyDelta, overall, pct, resolveThreshold,
} from "../lib/domain/clarity.mjs";
import {
  Interviews, emptyInterview, clarityGate, report,
} from "../lib/state/interview.mjs";

const clear = { score: 1, justification: "확정", gap: "" };
const proj = () => scratch("som-clarity-");

test("weights sum to 1 for both project types", () => {
  for (const [type, w] of Object.entries(WEIGHTS)) {
    const sum = Object.values(w).reduce((a, b) => a + b, 0);
    assert.ok(Math.abs(sum - 1) < 1e-9, `${type} weights sum to ${sum}`);
  }
});

test("a fully clear request scores 0 and a blank one scores 1", () => {
  assert.equal(ambiguityOf(
    { goal: clear, constraints: clear, criteria: clear }).ambiguity, 0);
  assert.equal(ambiguityOf({}).ambiguity, 1);
});

test("an unscored dimension counts as unclear, not as absent", () => {
  // Scoring two of three dimensions perfectly must not read as 100% clarity.
  const { ambiguity } = ambiguityOf({ goal: clear, constraints: clear });
  assert.equal(Math.round(ambiguity * 100), 30);
});

test("brownfield adds the context dimension and it carries weight", () => {
  const scores = { goal: clear, constraints: clear, criteria: clear };
  const missingContext = ambiguityOf(scores, { brownfield: true }).ambiguity;
  assert.ok(Math.abs(missingContext - WEIGHTS.brownfield.context) < 1e-9,
            `expected ${WEIGHTS.brownfield.context}, got ${missingContext}`);
  assert.equal(ambiguityOf(scores, { brownfield: false }).ambiguity, 0);
});

test("a written gap caps the score, however high it was reported", () => {
  const { parts, ambiguity } = ambiguityOf({
    goal: { score: 1.0, gap: "보존 기간 미정" },
    constraints: clear,
    criteria: clear,
  });
  const goal = parts.find((p) => p.dim === "goal");
  assert.equal(goal.score, GAP_CAP);
  assert.equal(goal.reported, 1);
  assert.equal(goal.capped, true);
  assert.ok(ambiguity > 0, "an admitted gap cannot produce zero ambiguity");
});

test('"없음" and friends are not open gaps', () => {
  for (const g of ["", "  ", "없음", "clear", "N/A", "-"]) {
    const p = ambiguityOf({ goal: { score: 1, gap: g } }).parts
      .find((x) => x.dim === "goal");
    assert.equal(p.capped, false, `gap ${JSON.stringify(g)} should not cap`);
    assert.equal(p.score, 1);
  }
});

test("inflating every dimension to 1.0 while admitting gaps cannot pass", () => {
  const scores = {
    goal: { score: 1, gap: "핵심 엔티티 미정" },
    constraints: { score: 1, gap: "권한 범위 미정" },
    criteria: { score: 1, gap: "검증 방법 미정" },
  };
  const { ambiguity } = ambiguityOf(scores);
  assert.equal(gate({ ambiguity, threshold: 0.05, round: 3 }).verdict, "ask");
  assert.ok(Math.abs(ambiguity - (1 - GAP_CAP)) < 1e-9);
});

test("settings may tighten the threshold but never loosen it", () => {
  const tight = resolveThreshold({
    projectSettings: { som: { interview: { ambiguityThreshold: 0.02 } } },
  });
  assert.equal(tight.value, 0.02);
  assert.equal(tight.note, null);

  const loose = resolveThreshold({
    userSettings: { som: { interview: { ambiguityThreshold: 0.4 } } },
  });
  assert.equal(loose.value, THRESHOLD_CEILING);
  assert.equal(loose.requested, 0.4);
  assert.match(loose.note, /상한/, "a refused override must say so");

  const none = resolveThreshold({});
  assert.equal(none.value, THRESHOLD_CEILING);
  assert.equal(none.source, "기본값");
});

test("project settings beat user settings", () => {
  const r = resolveThreshold({
    projectSettings: { som: { interview: { ambiguityThreshold: 0.01 } } },
    userSettings: { som: { interview: { ambiguityThreshold: 0.04 } } },
  });
  assert.equal(r.value, 0.01);
  assert.match(r.source, /\.claude\/settings\.json/);
});

test("the threshold is strict: an exact tie does not start work", () => {
  assert.equal(gate({ ambiguity: 0.05, threshold: 0.05, round: 4 }).verdict, "ask");
  assert.equal(gate({ ambiguity: 0.0499, threshold: 0.05, round: 4 }).verdict, "start");
  // And it says the tie is a tie rather than "0% more to go".
  assert.match(gate({ ambiguity: 0.05, threshold: 0.05, round: 4 }).line, /동률/);
});

test('"just start" is refused, with the honest exit named', () => {
  const g = gate({ ambiguity: 0.4, threshold: 0.05, round: 5, userWantsToStop: true });
  assert.equal(g.verdict, "refuse");
  assert.match(g.why, /미확정/, "must point at marking unknowns as undecided");
});

test("the round cap refuses instead of proceeding", () => {
  const g = gate({ ambiguity: 0.3, threshold: 0.05, round: ROUND_HARD_CAP });
  assert.equal(g.verdict, "refuse");
  // The benchmark proceeds here. Diverging on purpose, so say why in the text.
  assert.match(g.why, /추측/);
});

test("a clear component cannot hide a vague sibling", () => {
  const components = [
    { id: "a", name: "검토 UI", status: "active",
      scores: { goal: clear, constraints: clear, criteria: clear } },
    { id: "b", name: "내보내기", status: "active", scores: {} },
  ];
  const o = overall(components);
  assert.equal(o.ambiguity, 1, "the worst component sets the number, not the mean");

  // Deferring the vague one is a decision the user makes, and it does count.
  components[1].status = "deferred";
  assert.equal(overall(components).ambiguity, 0);
});

test("targeting rotates instead of re-asking the last component", () => {
  const equallyWeak = [
    { id: "a", name: "A", status: "active", scores: { goal: { score: 0.5 } } },
    { id: "b", name: "B", status: "active", scores: { goal: { score: 0.5 } } },
  ];
  assert.equal(nextTarget(equallyWeak, { lastTargetId: "a" }).component.id, "b");
  assert.equal(nextTarget(equallyWeak, { lastTargetId: "b" }).component.id, "a");
  // A genuinely worse component still wins over rotation.
  equallyWeak[1].scores.goal.score = 0.1;
  assert.equal(nextTarget(equallyWeak, { lastTargetId: "b" }).component.id, "b");
});

test("targeting picks the weakest dimension, not just the weakest component", () => {
  const t = nextTarget([{
    id: "a", name: "A", status: "active",
    scores: { goal: clear, constraints: { score: 0.2 }, criteria: { score: 0.6 } },
  }]);
  assert.equal(t.dimension, "constraints");
});

test("a rename counts as convergence, a new noun does not", () => {
  const prev = [{ name: "거래선", type: "core domain", fields: ["코드", "명", "채널"] }];
  const renamed = [{ name: "계정", type: "core domain", fields: ["코드", "명", "채널"] }];
  const r = ontologyDelta(prev, renamed);
  assert.equal(r.ratio, 1);
  assert.deepEqual(r.changed, ["거래선 → 계정"]);
  assert.deepEqual(r.added, []);

  const churn = ontologyDelta(prev, [
    ...prev, { name: "매장", type: "core domain", fields: ["점포번호"] }]);
  assert.equal(churn.ratio, 0.5);
  assert.deepEqual(churn.added, ["매장"]);
});

test("the first round has no stability ratio rather than a divide by zero", () => {
  assert.equal(ontologyDelta([], [{ name: "A", type: "t", fields: [] }]).ratio, null);
  assert.equal(ontologyDelta([{ name: "A", type: "t", fields: [] }], []).ratio, null);
});

test("percentages keep a decimal only where the gate hinges on it", () => {
  assert.equal(pct(0.05), "5%");
  assert.equal(pct(0.0464), "4.64%");
  assert.equal(pct(0.0536), "5.36%");
  // The whole point of the extra digit: these two are on opposite sides of the
  // gate and used to print identically.
  assert.notEqual(pct(0.0501), pct(0.0499));
  assert.equal(pct(null), "알 수 없음");
  assert.equal(pct(0.371), "37%");
  assert.equal(pct(1), "100%");
});

// ---------------------------------------------------------------- the gate
test("no interview record means no run, and the message says the number", () => {
  const g = clarityGate({ project: proj(), objective: "뭔가 해줘" });
  assert.equal(g.ok, false);
  assert.match(g.problem, /100%/);
  assert.match(g.problem, /5%/);
});

test("a threshold stored in the record cannot outrank the ceiling", () => {
  // Found by adversarial testing: the ceiling was applied only where a record
  // was created, so `clarityGate` honoured whatever number was on disk. A
  // hand-edited file with threshold 0.9 opened the gate at 50% ambiguity.
  const project = proj();
  const store = new Interviews(project);
  const iv = emptyInterview({ objective: "심어둔 기록", recipeId: "doc", project });
  iv.threshold = 0.9;                        // looser than the ceiling
  iv.ambiguity = 0.5;
  iv.verdict = "start";                      // and it claims it may start
  iv.topology = {
    status: "confirmed", confirmedAt: "x", deferrals: [], lastTargetId: null,
    components: [{
      id: "a", name: "A", status: "active",
      // Scored well, so only the threshold is in question here.
      scores: { goal: { score: 1, gap: "" }, constraints: { score: 1, gap: "" },
                criteria: { score: 1, gap: "" } },
    }],
  };
  iv.rounds = [{ n: 1, question: "q", answer: "a", scores: {}, ambiguity: 0 }];

  // Written straight to disk, bypassing save()'s own clamp, to prove the read
  // path is guarded and not just the write path.
  const raw = JSON.parse(JSON.stringify(iv));
  mkdirSync(join(project, ".som", "interview"), { recursive: true });
  writeFileSync(join(project, ".som", "interview", `${raw.slug}.json`),
                JSON.stringify(raw), "utf8");

  const back = store.load(raw.slug);
  assert.equal(back.threshold, THRESHOLD_CEILING, "the read must re-clamp");
  assert.equal(back.thresholdClamped, true);
  assert.equal(back.thresholdStored, 0.9, "the refused value is reported, not hidden");

  // Ambiguity here is 0, so with a 0.9 threshold honoured it would start; the
  // point is that 0.9 is not honoured. Push ambiguity above the ceiling to
  // make the refusal about the threshold rather than about the score.
  raw.topology.components[0].scores.criteria = { score: 0.5, gap: "" };
  writeFileSync(join(project, ".som", "interview", `${raw.slug}.json`),
                JSON.stringify(raw), "utf8");
  const g = clarityGate({ project, objective: "심어둔 기록", recipeId: "doc" });
  assert.equal(g.ok, false, "a stored threshold must not open the gate");
  assert.match(g.problem, /5%/);
});

test("save() will not persist a threshold looser than the ceiling", () => {
  const project = proj();
  const store = new Interviews(project);
  const iv = emptyInterview({ objective: "저장 클램프", project });
  iv.threshold = 0.5;
  const saved = store.save(iv);
  assert.equal(saved.threshold, THRESHOLD_CEILING);
  assert.equal(store.load(iv.slug).threshold, THRESHOLD_CEILING);
});

test("an unconfirmed topology blocks even at low ambiguity", () => {
  const project = proj();
  const store = new Interviews(project);
  const iv = emptyInterview({ objective: "리포트", project });
  iv.ambiguity = 0.01;                       // pretend it scored well
  store.save(iv);
  const g = clarityGate({ project, objective: "리포트" });
  assert.equal(g.ok, false);
  assert.match(g.problem, /토폴로지|구성/);
});

test("a settled interview opens the gate and survives a reload", () => {
  const project = proj();
  const store = new Interviews(project);
  let iv = emptyInterview({ objective: "지난달 매출 리포트", recipeId: "analyze", project });
  iv = store.confirmTopology(iv, [{ id: "r", name: "리포트" }]);
  store.save(iv);

  const first = store.addRound(iv, {
    componentId: "r", dimension: "goal", question: "q", answer: "a",
    scores: { goal: { score: 0.6, gap: "기간 미정" }, constraints: { score: 0.5 },
              criteria: { score: 0.4 } },
  });
  store.save(first.iv);
  assert.equal(clarityGate({
    project, objective: "지난달 매출 리포트", recipeId: "analyze" }).ok, false);
  assert.match(report(first), /전체 모호도/);

  const second = store.addRound(first.iv, {
    componentId: "r", dimension: "criteria", question: "q2", answer: "a2",
    scores: { goal: clear, constraints: clear, criteria: { score: 0.99, gap: "" } },
  });
  store.save(second.iv);

  const g = clarityGate({
    project, objective: "지난달 매출 리포트", recipeId: "analyze" });
  assert.equal(g.ok, true, g.problem);
  assert.equal(g.interview.rounds.length, 2, "every round stays on the record");
  assert.ok(g.interview.settledAt, "settling is timestamped");

  // A fresh reader (post-restart, post-compaction) reaches the same verdict.
  assert.equal(clarityGate({
    project, objective: "지난달 매출 리포트", recipeId: "analyze" }).ok, true);
});

test("the record keeps the questions and answers, not just the score", () => {
  const project = proj();
  const store = new Interviews(project);
  let iv = emptyInterview({ objective: "감사 리포트", recipeId: "doc", project });
  iv = store.confirmTopology(iv, [{ id: "a", name: "A" }]);
  const r = store.addRound(iv, {
    componentId: "a", dimension: "goal",
    question: "행 1개가 무엇인가요?", answer: "거래선 × 주",
    scores: { goal: clear, constraints: clear, criteria: clear },
  });
  store.save(r.iv);
  const back = store.load(r.iv.slug);
  assert.equal(back.rounds[0].question, "행 1개가 무엇인가요?");
  assert.equal(back.rounds[0].answer, "거래선 × 주");
  assert.equal(back.thresholdSource, "기본값");
});

/**
 * A score that is not a finite number in range is not a score.
 *
 * `clamp01` used to be `Math.max(0, Math.min(1, Number(n)))`, and `Number()`
 * is generous: `"1"` became a full score and `Infinity` clamped to one, so
 * either opened the 5% gate on a request nobody had scored. `NaN` propagated
 * into a stored ambiguity of NaN, which is not even a valid record.
 *
 * A malformed score counts as unscored, which the weighting already treats as
 * fully ambiguous -- the gate closes rather than opens.
 */
const NOT_A_SCORE = [
  ["문자열 '1'", "1"], ["문자열 '0.9'", "0.9"], ["Infinity", Infinity],
  ["-Infinity", -Infinity], ["NaN", NaN], ["null", null], ["undefined", undefined],
  ["true", true], ["빈 문자열", ""], ["배열 [1]", [1]], ["객체", { valueOf: () => 1 }],
];

test("a score that is not a finite number counts as unscored, not as clear", () => {
  for (const [label, value] of NOT_A_SCORE) {
    const { ambiguity } = ambiguityOf({
      goal: { score: value, justification: "확정", gap: "" },
      constraints: clear,
      criteria: clear,
    });
    assert.ok(Number.isFinite(ambiguity),
      `${label}: 모호도가 유한한 수가 아닙니다 (${ambiguity})`);
    assert.ok(ambiguity >= WEIGHTS.greenfield.goal - 1e-9,
      `${label}: goal 이 채점되지 않은 것으로 취급되어야 하는데 모호도가 ` +
      `${(ambiguity * 100).toFixed(1)}% 입니다`);
  }
});

test("a malformed score never opens the gate", () => {
  for (const [label, value] of NOT_A_SCORE) {
    const a = ambiguityOf({
      goal: { score: value, justification: "확정", gap: "" },
      constraints: { score: value, justification: "확정", gap: "" },
      criteria: { score: value, justification: "확정", gap: "" },
    }).ambiguity;
    assert.ok(a >= THRESHOLD_CEILING,
      `${label}: 모호도 ${(a * 100).toFixed(1)}% 로 게이트가 열립니다`);
  }
});

test("a real number still scores normally", () => {
  // The hardening must not break the honest path.
  assert.equal(ambiguityOf({ goal: clear, constraints: clear, criteria: clear }).ambiguity, 0);
  const half = ambiguityOf({
    goal: { score: 0.5, justification: "절반", gap: "" },
    constraints: clear, criteria: clear,
  }).ambiguity;
  assert.ok(Math.abs(half - WEIGHTS.greenfield.goal * 0.5) < 1e-9,
    `0.5 점이 제대로 반영되지 않습니다: ${half}`);
});
