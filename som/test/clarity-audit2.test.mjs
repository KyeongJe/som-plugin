/**
 * Regressions from the SECOND adversarial pass at the 5% gate.
 *
 * The first pass fixed the obvious bypasses; this pass found that the gate
 * never recomputed the number at all -- it read one cached field, so a
 * 99-byte record with `ambiguity: null` opened it. Everything here is a defect
 * proved by execution, with the mechanism in the comment.
 *
 *   node --test test/clarity-audit2.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { scratch } from "./tmp.mjs";

import {
  THRESHOLD_CEILING, asAmbiguity, pct, resolveThreshold,
} from "../lib/domain/clarity.mjs";
import {
  Interviews, clarityGate, emptyInterview,
} from "../lib/state/interview.mjs";

const proj = () => {
  process.env.CLAUDE_CONFIG_DIR = scratch("som-cfg2-");
  return scratch("som-cl2-");
};

/** Write a record straight to disk, under the slug the objective resolves to. */
function put(project, objective, extra) {
  const slug = emptyInterview({ objective, project }).slug;
  mkdirSync(join(project, ".som", "interview"), { recursive: true });
  writeFileSync(join(project, ".som", "interview", `${slug}.json`),
                JSON.stringify({
                  schema: 1, slug, objective, recipe: "prd", threshold: 0.05,
                  ...extra,
                }), "utf8");
  return objective;
}

const CLEAR = {
  goal: { score: 1, gap: "" },
  constraints: { score: 1, gap: "" },
  criteria: { score: 1, gap: "" },
};

// ------------------------------------------------------------- the big one
test("a non-numeric ambiguity reads as unknown, not as zero", () => {
  // Number(null), Number("") and Number(false) are all 0, so a record whose
  // ambiguity field was missing read as perfectly clear. The dimension path
  // already got this right; the record path had the polarity inverted.
  for (const bad of [null, "", false, undefined, NaN, -5, 1.5, "zero"]) {
    assert.equal(asAmbiguity(bad), 1, `asAmbiguity(${JSON.stringify(bad)})`);
  }
  assert.equal(asAmbiguity(0), 0);
  assert.equal(asAmbiguity(0.3), 0.3);
});

test("the gate recomputes the number instead of trusting the file", () => {
  const project = proj();
  for (const [label, extra] of [
    ["ambiguity null, no scores", {
      topology: { status: "confirmed" }, rounds: [{}], ambiguity: null,
    }],
    ["components [null]", {
      topology: { status: "confirmed", components: [null] },
      rounds: [{ n: 1 }], ambiguity: 0,
    }],
    ["forged ambiguity 0", {
      topology: {
        status: "confirmed",
        components: [{ id: "a", status: "active", scores: {} }],
      },
      rounds: [{ n: 1 }], ambiguity: 0,
    }],
    ["hand-set verdict start", {
      topology: {
        status: "confirmed",
        components: [{ id: "a", status: "active", scores: {} }],
      },
      rounds: [{ n: 1 }], ambiguity: 0, verdict: "start",
    }],
  ]) {
    const objective = put(project, label, extra);
    assert.equal(clarityGate({ project, objective, recipeId: "prd" }).ok, false,
                 `${label} must not open the gate`);
  }
});

test("the recomputed number is reported, and disagreement is flagged", () => {
  const project = proj();
  const objective = put(project, "위조 영점", {
    topology: {
      status: "confirmed",
      components: [{ id: "a", status: "active", scores: {} }],
    },
    rounds: [{ n: 1 }], ambiguity: 0,
  });
  const g = clarityGate({ project, objective, recipeId: "prd" });
  assert.equal(g.ambiguity, 1, "recomputed from the components");
  assert.equal(g.drifted, true, "the cached field disagreed and that is said");
});

// ------------------------------------------------------------- no throws
test("a missing or null rounds field refuses instead of throwing", () => {
  // It threw a raw TypeError out of the refusal path -- the one branch whose
  // whole job is to explain itself -- which broke Conduct.plan().
  const project = proj();
  for (const [objective, rounds] of [["라운드 없음", undefined], ["라운드 널", null]]) {
    put(project, objective, {
      recipe: "doc",
      topology: {
        status: "confirmed",
        components: [{ id: "a", status: "active", scores: CLEAR }],
      },
      rounds, ambiguity: 0.01,
    });
    const g = clarityGate({ project, objective, recipeId: "doc" });
    assert.equal(g.ok, false);
    assert.match(g.problem, /라운드/);
  }
});

test("a record from another schema version is refused, not misread", () => {
  const project = proj();
  const objective = put(project, "스키마 미래", {
    schema: 99,
    topology: {
      status: "confirmed",
      components: [{ id: "a", status: "active", scores: CLEAR }],
    },
    rounds: [{ n: 1 }], ambiguity: 0,
  });
  const g = clarityGate({ project, objective, recipeId: "prd" });
  assert.equal(g.ok, false);
  assert.match(g.problem, /schema/);
});

// ------------------------------------------------------------- recipe
test("an interview that does not know its recipe authorises nothing", () => {
  // The check required both sides truthy, and every record the documented flow
  // produced had recipe: null, so it authorised any recipe at all.
  const project = proj();
  const store = new Interviews(project);
  let iv = store.confirmTopology(
    emptyInterview({ objective: "레시피 없음", project }), [{ id: "a", name: "A" }]);
  iv = store.addRound(iv, {
    componentId: "a", dimension: "goal", question: "q", answer: "a", scores: CLEAR,
  }).iv;
  store.save(iv);
  for (const recipeId of [undefined, "doc", "prd"]) {
    const g = clarityGate({ project, objective: "레시피 없음", recipeId });
    assert.equal(g.ok, false, `recipeId ${String(recipeId)}`);
    assert.match(g.problem, /레시피가 적혀 있지 않습니다/);
  }
});

// ------------------------------------------------------------- topology
test("confirmTopology closes the door addRound guards", () => {
  // Blocking `decided` in addRound alone left it plantable one call earlier,
  // and the strip there preserves whatever it already finds on the record.
  const project = proj();
  const store = new Interviews(project);
  let iv = store.confirmTopology(
    emptyInterview({ objective: "심기 시도", recipeId: "analyze", project }),
    [{
      id: "c1", name: "only",
      scores: {
        goal: { score: 1, gap: "모름", decided: true },
        constraints: { score: 1, gap: "모름", decided: true },
        criteria: { score: 1, gap: "모름", decided: true },
      },
    }]);
  for (const dim of ["goal", "constraints", "criteria"]) {
    assert.notEqual(iv.topology.components[0].scores[dim]?.decided, true, dim);
  }
  iv = store.addRound(iv, {
    componentId: "c1", dimension: "goal", question: "q", answer: "a",
    scores: {
      goal: { score: 1, gap: "모름" }, constraints: { score: 1, gap: "모름" },
      criteria: { score: 1, gap: "모름" },
    },
  }).iv;
  store.save(iv);
  assert.ok(iv.ambiguity > 0.05, `ambiguity is ${iv.ambiguity}`);
  assert.equal(clarityGate({
    project, objective: "심기 시도", recipeId: "analyze" }).ok, false);
});

test("a topology needs one active component and no duplicate ids", () => {
  // Zero components skipped the component path entirely, so one perfect
  // self-score opened the gate in a single round. Duplicate ids scored the
  // first while targeting the second, so the number never moved and the run
  // ground to the round cap.
  const project = proj();
  const store = new Interviews(project);
  const iv = emptyInterview({ objective: "구성 검사", project });
  assert.throws(() => store.confirmTopology(iv, []), /비어 있습니다/);
  assert.throws(() => store.confirmTopology(iv, [{ id: "c1" }, { id: "c1" }]), /중복/);
  assert.throws(
    () => store.confirmTopology(iv, [{ id: "c1", status: "deferred" }]), /활성/);
});

test("scoring a component that was never confirmed is refused", () => {
  const project = proj();
  const store = new Interviews(project);
  const iv = store.confirmTopology(
    emptyInterview({ objective: "없는 구성", project }), [{ id: "a", name: "A" }]);
  assert.throws(() => store.addRound(iv, {
    componentId: "nope", dimension: "goal", question: "q", answer: "a",
    scores: { goal: { score: 1 } },
  }), /없습니다/);
});

test("the round log records the scores as applied, not as submitted", () => {
  const project = proj();
  const store = new Interviews(project);
  const iv = store.confirmTopology(
    emptyInterview({ objective: "감사 추적", project }), [{ id: "a", name: "A" }]);
  const r = store.addRound(iv, {
    componentId: "a", dimension: "goal", question: "q", answer: "a",
    scores: { goal: { score: 1, gap: "모름", decided: true } },
  });
  assert.notEqual(r.iv.rounds[0].scores.goal.decided, true,
                  "the log showed an acceptance that had been stripped");
});

// ------------------------------------------------------------- settings
test("the strictest of project, user and the ceiling wins", () => {
  // Precedence was project ?? user, so a project file at 0.05 discarded a user
  // setting of 0.001 -- raising the bar, which the framing says cannot happen.
  assert.equal(resolveThreshold({
    projectSettings: { som: { interview: { ambiguityThreshold: 0.05 } } },
    userSettings: { som: { interview: { ambiguityThreshold: 0.001 } } },
  }).value, 0.001);
});

test("a malformed threshold says so instead of silently defaulting", () => {
  // Someone writing "0.01" means stricter; dropping it silently left them on
  // the looser default with no indication either way.
  const bad = resolveThreshold({
    projectSettings: { som: { interview: { ambiguityThreshold: 1.5 } } },
  });
  assert.equal(bad.value, THRESHOLD_CEILING);
  assert.match(bad.note, /읽을 수 없어/);
  assert.equal(resolveThreshold({
    projectSettings: { som: { interview: { ambiguityThreshold: "0.01" } } },
  }).value, 0.01);
});

// ------------------------------------------------------------- formatting
test("the slug survives trailing whitespace and Unicode form", () => {
  // A trailing newline off $ARGUMENTS produced a different digest, so plan()
  // reported "인터뷰 기록이 없습니다" for an interview that had just finished.
  const project = proj();
  const of = (o) => emptyInterview({ objective: o, project }).slug;
  assert.equal(of("리포트 만들기"), of("리포트 만들기\n"));
  assert.equal(of("리포트 만들기"), of("리포트 만들기".normalize("NFD")));
});

test("a pass and a refusal never print the same percentage", () => {
  // One decimal rendered 5.01% and 4.99% both as "5.0%", so a refusal looked
  // arbitrary next to a pass.
  assert.notEqual(pct(0.0501), pct(0.0499));
  assert.equal(pct(0.05), "5%");
  assert.equal(pct(null), "알 수 없음");
  assert.equal(pct(NaN), "알 수 없음");
});
