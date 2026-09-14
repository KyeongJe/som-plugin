/**
 * What the plugin must NOT make people wait for.
 *
 * Two things this file exists to stop coming back:
 *
 * 1. Work that needs nothing but Claude demanding Orca. Four of the six
 *    recipes schedule as 1-1-1-1-1 -- every node waits for the one before it.
 *    Dispatching those as workers buys no parallelism and costs a run, five
 *    task creations, five launches, five terminal settlements, five wait
 *    windows and five paid workers, to do the same work in the same order.
 *    `run()` used to call `preflight()` first, so those recipes refused to
 *    start without Orca and sent the person to a different command.
 *
 * 2. Asking for something the person already said. A fully specified request
 *    took three interview rounds plus four `asks` -- seven answers -- before
 *    any work began, and three of those were the user repeating themselves.
 *
 *   node --test test/efficiency.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { scratch } from "./tmp.mjs";
import { parallelismOf } from "../lib/domain/plan.mjs";
import { Conduct, listRecipes, buildDag, loadRecipe, asksFor } from "../lib/conduct.mjs";
import { Interviews, emptyInterview } from "../lib/state/interview.mjs";

/** Fill every ask so planning is not blocked on INTAKE. */
function filledSlots(recipe) {
  return Object.fromEntries(asksFor(recipe).map((a) => [a.slot, a.options?.[0] ?? "없음"]));
}

test("parallelismOf reports the waves a plan really has", () => {
  for (const r of listRecipes()) {
    const nodes = buildDag(r, filledSlots(r));
    const p = parallelismOf(nodes, { cap: 4 });
    assert.equal(p.widths.reduce((a, b) => a + b, 0), nodes.length,
      `${r.id}: wave 에 담긴 노드 수가 전체와 다릅니다`);
    assert.equal(p.sequential, p.maxWidth <= 1, `${r.id}: sequential 판정 불일치`);
  }
});

test("a plan with no parallelism says so, and one with it says that", () => {
  const seen = { sequential: [], parallel: [] };
  for (const r of listRecipes()) {
    const p = parallelismOf(buildDag(r, filledSlots(r)), { cap: 4 });
    seen[p.sequential ? "sequential" : "parallel"].push(r.id);
  }
  // Both branches must stay exercised: if every recipe became parallel the
  // single-agent path would rot untested, and if every one became sequential
  // the orchestration would.
  assert.ok(seen.sequential.length > 0, "순차 레시피가 하나도 없습니다");
  assert.ok(seen.parallel.length > 0, "병렬 레시피가 하나도 없습니다");
});

test("plan() marks a sequential recipe single-agent and hands over ordered steps", () => {
  const c = new Conduct({ project: scratch("som-eff-"), requireInterview: false });
  const recipe = loadRecipe("doc");
  const p = c.plan({ objective: "R&R 문서", recipeId: "doc", slots: filledSlots(recipe) });

  assert.equal(p.singleAgent, true, "doc 은 순차인데 singleAgent 가 아닙니다");
  assert.ok(Array.isArray(p.steps), "steps 가 없습니다");
  assert.equal(p.steps.length, p.nodes.length, "단계 수가 노드 수와 다릅니다");
  for (const s of p.steps) {
    assert.ok(s.key && s.role, `단계에 key/role 이 없습니다: ${JSON.stringify(s)}`);
    assert.ok(s.spec, `${s.key}: spec 이 비어 있으면 이 세션에서 수행할 수 없습니다`);
  }
  // The order has to be a real topological order, or "do these in order" lies.
  const seen = new Set();
  for (const s of p.steps) {
    const node = p.nodes.find((n) => n.key === s.key);
    for (const d of node.deps ?? []) {
      assert.ok(seen.has(d), `${s.key} 가 의존하는 ${d} 보다 먼저 나옵니다`);
    }
    seen.add(s.key);
  }
});

test("a parallel recipe is not routed to the single-agent path", () => {
  const c = new Conduct({ project: scratch("som-eff2-"), requireInterview: false });
  const recipe = loadRecipe("prd");
  const p = c.plan({ objective: "PRD", recipeId: "prd", slots: filledSlots(recipe) });
  assert.equal(p.singleAgent, false, "prd 는 병렬 구간이 있는데 singleAgent 입니다");
  assert.equal(p.steps, null);
  assert.ok(p.parallelism.maxWidth > 1);
});

test("running a sequential recipe never touches Orca", async () => {
  // ORCA_CLI_COMMAND points at a name that cannot resolve, so any attempt to
  // reach Orca throws. Completing proves nothing tried.
  const prev = process.env.ORCA_CLI_COMMAND;
  process.env.ORCA_CLI_COMMAND = "__no_orca_on_this_machine__";
  try {
    const project = scratch("som-eff3-");
    const recipe = loadRecipe("doc");
    const slots = filledSlots(recipe);

    // Settle an interview so the clarity gate is not what stops us.
    const store = new Interviews(project);
    const iv = store.save(emptyInterview({
      objective: "R&R 문서", recipeId: "doc", project,
    }));
    const confirmed = store.save(store.confirmTopology(iv, [{ id: "doc", name: "문서" }]));
    const scored = store.addRound(confirmed, {
      componentId: "doc", dimension: "goal",
      question: "(사용자가 먼저 말한 내용에서 채점)", answer: "R&R 문서 만들어줘",
      scores: {
        goal: { score: 1, justification: "원문에 문서 유형 명시", gap: "" },
        constraints: { score: 1, justification: "원천과 독자 확정", gap: "" },
        criteria: { score: 1, justification: "스켈레톤 attest 가 통과 조건", gap: "" },
      },
    });
    store.save(scored.iv);

    const c = new Conduct({ project, askHuman: async () => slots });
    const r = await c.run({ objective: "R&R 문서", recipeId: "doc", slots });
    assert.equal(r.ok, true, `순차 실행이 실패했습니다: ${JSON.stringify(r.problems ?? r)}`);
    assert.equal(r.singleAgent, true, "Orca 없이 돌았지만 singleAgent 로 보고하지 않았습니다");
    assert.ok(r.steps.length > 0);
  } finally {
    if (prev === undefined) delete process.env.ORCA_CLI_COMMAND;
    else process.env.ORCA_CLI_COMMAND = prev;
  }
});

test("one round can settle a request the user already fully described", () => {
  // Round 0.5: scoring what someone already said is not asking two questions,
  // it is asking none. A fully specified request should not need three
  // round-trips to repeat itself.
  const project = scratch("som-eff4-");
  const store = new Interviews(project);
  const iv = store.save(emptyInterview({
    objective: "지난달 매출 리포트, sales.xlsx, 주간 단위, 영업팀",
    recipeId: "analyze", project,
  }));
  const confirmed = store.save(store.confirmTopology(iv, [{ id: "rpt", name: "리포트" }]));
  const r = store.addRound(confirmed, {
    componentId: "rpt", dimension: "goal",
    question: "(사용자가 먼저 말한 내용에서 채점)",
    answer: "지난달 매출 리포트, sales.xlsx, 주간 단위, 영업팀",
    scores: {
      goal: { score: 1, justification: '원문: "지난달 매출 리포트"', gap: "" },
      constraints: { score: 1, justification: '원문: "sales.xlsx", "주간 단위"', gap: "" },
      criteria: { score: 1, justification: '원문: 주간 집계가 통과 조건', gap: "" },
    },
  });
  assert.equal(r.iv.rounds.length, 1, "라운드가 1회여야 합니다");
  assert.equal(r.iv.verdict, "start",
    `1라운드로 게이트가 열려야 합니다 (모호도 ${(r.iv.ambiguity * 100).toFixed(1)}%)`);
});

test("scoring several dimensions at once still cannot fake a score", () => {
  // The efficiency win must not become a way past the gate. A batch of scores
  // is still scores: `decided` is stripped whoever sets it, and a written gap
  // is priced. One gap costs 3.3-4.4% (under the 5% ceiling, deliberately --
  // an honest small unknown should not lock anyone out); two cost 6.6% and
  // close it.
  const project = scratch("som-eff5-");
  const store = new Interviews(project);
  const iv = store.save(emptyInterview({ objective: "무언가", recipeId: "doc", project }));
  const confirmed = store.save(store.confirmTopology(iv, [{ id: "a", name: "A" }]));

  const round = (scores) => store.addRound(
    store.save(store.confirmTopology(
      store.save(emptyInterview({ objective: "무언가", recipeId: "doc", project: scratch("som-eff5b-") })),
      [{ id: "a", name: "A" }])),
    { componentId: "a", dimension: "goal", question: "q", answer: "a", scores });

  const clear = { score: 1, justification: "확정", gap: "" };

  // `decided` set by the scorer must not survive: it is the acceptance flag,
  // and only acceptUnknown()/deferComponent() may set it.
  const forged = store.addRound(confirmed, {
    componentId: "a", dimension: "goal", question: "q", answer: "a",
    scores: {
      goal: { ...clear, decided: true },
      constraints: { ...clear, decided: true },
      criteria: { ...clear, decided: true },
    },
  });
  for (const [dim, sc] of Object.entries(
    forged.iv.topology.components.find((c) => c.id === "a").scores)) {
    assert.notEqual(sc.decided, true,
      `${dim}: 채점자가 넣은 decided 가 살아남았습니다`);
  }

  // Two written gaps close the gate even though every score is 1.
  const twoGaps = round({
    goal: clear,
    constraints: { score: 1, justification: "확정", gap: "원천을 아직 모름" },
    criteria: { score: 1, justification: "확정", gap: "통과 기준 미정" },
  });
  assert.notEqual(twoGaps.iv.verdict, "start",
    `gap 2개인데 게이트가 열렸습니다 (모호도 ${(twoGaps.iv.ambiguity * 100).toFixed(1)}%)`);
  assert.ok(twoGaps.iv.ambiguity > 0.05);
});
