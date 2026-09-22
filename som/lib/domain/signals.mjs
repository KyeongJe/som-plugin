/**
 * Turning what a run actually did into pattern drafts, without inventing anything.
 *
 * The learning loop had a broken first link. `conduct` injected patterns and
 * graded them, both automatically, but nothing ever *created* one -- `propose()`
 * was reachable only from the CLI, so unless a person ran `/som:learn` the
 * library stayed empty forever and every downstream stage sat idle with nothing
 * to work on. Heavy use produced zero patterns and zero promoted skills, which
 * looked like "nothing was worth saving" and was really "nothing was ever
 * offered".
 *
 * This closes the link, and the hard part is doing it honestly. An engine
 * cannot decide what a run *taught*; that is judgement. What it can do is
 * report what it directly observed, in the shape a pattern takes. So every
 * draft below is a mechanical fact with the specifics filled in from state --
 * a node key, a real path, a real count -- and never a generalisation.
 *
 * Three signals qualify. Each had to clear the same bar: the engine measured
 * it, and the resulting sentence tells the next run something it can act on.
 *
 *   writes_violation   a node declared `writes` and touched something else.
 *                      The fix is a plan edit and the draft names both sides.
 *   repeated_attempts  a node needed more than one attempt to pass. The next
 *                      plan should size it differently, and the draft names it.
 *   escalation         a node blocked and a person unblocked it. What the
 *                      person had to supply is a slot the plan should carry.
 *
 * Deliberately absent: anything derived from a summary the model wrote, any
 * "this went well" observation (success is the baseline, not a lesson), and
 * leaked terminals (an engine defect, not something the next run can act on).
 *
 * Nothing here writes, and nothing here bypasses the gate. Every draft goes
 * through `propose()` and `validatePattern()` exactly as a hand-written one
 * does; most of the value of this module is that its drafts are specific
 * enough to survive that.
 */

/**
 * Where an auto-observed pattern starts, below a hand-curated one.
 *
 * A person proposing a lesson has already decided it is one. The engine has
 * only noticed something happen once. Starting lower means an auto draft has
 * to be right repeatedly before it carries weight in a brief -- and, since
 * promotion to a skill needs an average of 70, before it can ever become a
 * skill it must have won roughly six times.
 */
export const AUTO_CONFIDENCE = 25;

/** Cap per run. A run that trips fifty things has one problem, not fifty. */
export const MAX_DRAFTS = 4;

const clean = (s) => String(s ?? "").replace(/\s+/g, " ").trim();
const short = (s, n) => (clean(s).length <= n ? clean(s) : `${clean(s).slice(0, n - 1)}…`);

/** Trigger keywords that are specific enough to earn their place in a brief. */
function triggersFor(node, recipe) {
  const out = [];
  if (recipe) out.push(String(recipe));
  if (node?.key) out.push(String(node.key));
  if (node?.role) out.push(String(node.role));
  for (const g of node?.writes ?? []) {
    const base = String(g).split("/").filter((x) => x && !x.includes("*")).pop();
    if (base && base.length >= 3) out.push(base);
  }
  return [...new Set(out)].filter((t) => t.length >= 3).slice(0, 6);
}

/**
 * Pattern drafts for one finished run.
 *
 * `state` is the run record, `nodes` the plan. Returns drafts, not patterns --
 * the caller proposes them and the gate decides.
 */
export function signalsFrom(state = {}, { nodes = [], recipe = "", runId = "" } = {}) {
  const byKey = new Map(nodes.map((n) => [n.key, n]));
  const dispatches = Object.values(state?.dispatches ?? {});
  const events = state?.events ?? [];
  const drafts = [];

  const evidence = (extra = []) => {
    const e = [];
    if (runId) e.push({ kind: "run", ref: String(runId), note: "" });
    return [...e, ...extra];
  };

  // -- 1. wrote outside what it declared -----------------------------------
  //
  // The plan's `writes` globs are how the scheduler keeps two workers off one
  // file. A node that reaches past them is not misbehaviour so much as a plan
  // that was wrong, and the correction belongs in the next plan.
  for (const d of dispatches) {
    const v = (d.violations ?? []).filter(Boolean);
    if (!v.length) continue;
    const node = byKey.get(d.key);
    const declared = (node?.writes ?? []).join(", ") || "(선언 없음)";
    const hit = v.slice(0, 3);
    drafts.push({
      title: short(`${d.key} 는 선언한 범위 밖 파일을 고친다`, 110),
      trigger: short(`${recipe || "이 레시피"} 에서 ${d.key} 노드를 계획할 때`, 280),
      action: short(
        `${d.key} 의 writes 에 ${hit.join(", ")} 를 포함한다. ` +
        `이번 런에서 선언은 ${declared} 였는데 실제로는 ${v.length}개 파일을 더 고쳤다.`, 1100),
      why: short(
        "선언 밖 쓰기는 스케줄러가 충돌을 못 막는다는 뜻이다 — " +
        "같은 파일을 건드리는 두 노드가 같은 wave 에 들어갈 수 있다.", 560),
      triggers: triggersFor(node, recipe),
      tags: ["plan", "writes"],
      evidence: evidence(hit.map((f) => ({ kind: "file", ref: String(f), note: "" }))),
    });
  }

  // -- 2. needed more than one attempt -------------------------------------
  //
  // `repairPlan` already says at the third attempt that the task is too big.
  // Recording it turns that from advice inside one run into something the
  // next plan sees before it starts.
  const attemptsBy = new Map();
  for (const e of events) {
    if (e?.kind !== "retry" && e?.kind !== "worker_failed") continue;
    const k = e.key ?? e.task;
    if (k) attemptsBy.set(k, (attemptsBy.get(k) ?? 0) + 1);
  }
  for (const [key, n] of attemptsBy) {
    if (n < 2) continue;                       // once is weather, twice is a pattern
    const node = byKey.get(key);
    const settled = dispatches.find((d) => d.key === key);
    if (settled?.outcome !== "succeeded") continue;   // still broken: not a lesson yet
    const files = (settled.filesModified ?? []).slice(0, 3);
    drafts.push({
      title: short(`${key} 는 한 번에 끝나지 않는다`, 110),
      trigger: short(`${recipe || "이 레시피"} 에서 ${key} 를 계획할 때`, 280),
      action: short(
        `${key} 를 더 작은 노드로 쪼갠다. 이번 런에서 ${n + 1}회차에야 통과했고, ` +
        (files.length ? `건드린 파일은 ${files.join(", ")} 였다.`
                      : "파일 목록은 기록되지 않았다."), 1100),
      why: short("재시도는 무료가 아니다 — 워커 기동·정산·대기를 매번 다시 치른다.", 560),
      triggers: triggersFor(node ?? { key }, recipe),
      tags: ["plan", "sizing"],
      evidence: evidence(files.map((f) => ({ kind: "file", ref: String(f), note: "" }))),
    });
  }

  // -- 3. blocked until a person supplied something ------------------------
  //
  // An escalation that a human resolved means the plan was missing an input.
  // The draft says which node and that the input belongs in the plan; it does
  // not repeat what the worker wrote, because that is prose this module did
  // not measure.
  const blocked = new Set();
  for (const e of events) {
    if (e?.kind === "escalation" && (e.key ?? e.task)) blocked.add(e.key ?? e.task);
  }
  for (const key of blocked) {
    const node = byKey.get(key);
    drafts.push({
      title: short(`${key} 는 사람에게 물어봐야 진행된다`, 110),
      trigger: short(`${recipe || "이 레시피"} 에서 ${key} 를 계획할 때`, 280),
      action: short(
        `${key} 가 필요로 한 입력을 인터뷰 단계에서 미리 받는다. ` +
        `이번 런에서는 이 노드가 막혀서 사람이 풀어줄 때까지 기다렸다.`, 1100),
      why: short("워커가 막혀 있는 동안 그 터미널은 잡혀 있고 wave 는 끝나지 않는다.", 560),
      triggers: triggersFor(node ?? { key }, recipe),
      tags: ["plan", "intake"],
      evidence: evidence(),
    });
  }

  // Newest signal types first is not meaningful; the cap is. A run that trips
  // many things has one underlying problem, and forty drafts would bury it.
  return drafts.slice(0, MAX_DRAFTS).map((d) => ({
    ...d, source: "auto", confidence: AUTO_CONFIDENCE,
  }));
}
