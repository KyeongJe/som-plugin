/**
 * Roles, wave scheduling, and worker placement.
 *
 * Orca states plainly that it does not schedule workers or infer conflicts.
 * This file is the part it leaves to us.
 */

/**
 * Role -> model and effort. One table, referenced by alias everywhere else, so
 * a model change is one edit rather than a search across agent files.
 */
export const ROLES = {
  planner:    { model: "opus",   effort: "high",   timeoutMs: 1_800_000 },
  architect:  { model: "opus",   effort: "high",   timeoutMs: 1_800_000 },
  analyst:    { model: "opus",   effort: "high",   timeoutMs: 2_400_000 },
  designer:   { model: "opus",   effort: "high",   timeoutMs: 2_400_000 },
  builder:    { model: "sonnet", effort: "medium", timeoutMs: 2_700_000 },
  writer:     { model: "opus",   effort: "medium", timeoutMs: 1_800_000 },
  renderer:   { model: "sonnet", effort: "low",    timeoutMs: 600_000 },
  verifier:   { model: "opus",   effort: "high",   timeoutMs: 1_200_000 },
  watcher:    { model: "sonnet", effort: "medium", timeoutMs: 2_700_000 },
  scribe:     { model: "sonnet", effort: "low",    timeoutMs: 600_000 },
};

/** A repair attempt escalates rather than repeating the same settings. */
const ESCALATE_MODEL = { sonnet: "opus", opus: "opus" };
const ESCALATE_EFFORT = { low: "medium", medium: "high", high: "high" };

export function launchFor(role, { attempt = 1 } = {}) {
  const base = ROLES[role] ?? ROLES.builder;
  if (attempt <= 1) return { ...base };
  return {
    model: ESCALATE_MODEL[base.model] ?? base.model,
    effort: ESCALATE_EFFORT[base.effort] ?? base.effort,
    timeoutMs: base.timeoutMs,
  };
}

// ---------------------------------------------------------------- glob match
/** Minimal glob: `**` any depth, `*` one segment, `?` one char. */
export function globToRegExp(glob) {
  let re = "";
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i];
    if (c === "*") {
      if (glob[i + 1] === "*") {
        re += ".*";
        i++;
        if (glob[i + 1] === "/") i++;
      } else {
        re += "[^/]*";
      }
    } else if (c === "?") re += "[^/]";
    else re += c.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  }
  return new RegExp(`^${re}$`, "i");
}

const norm = (p) => String(p).replace(/\\/g, "/").replace(/^\.\//, "");

/**
 * Do two write scopes overlap?
 *
 * Conservative on purpose: an undeclared or empty scope is treated as
 * "touches everything", because the failure it prevents -- two workers editing
 * one file in one checkout -- is silent and expensive, while the cost of being
 * wrong is one extra wave.
 */
export function writesConflict(a = [], b = []) {
  if (!a.length || !b.length) return true;
  const ra = a.map((g) => ({ g: norm(g), re: globToRegExp(norm(g)) }));
  const rb = b.map((g) => ({ g: norm(g), re: globToRegExp(norm(g)) }));
  for (const x of ra) {
    for (const y of rb) {
      if (x.g === y.g) return true;
      if (x.re.test(y.g) || y.re.test(x.g)) return true;
      // Prefix overlap: docs/** vs docs/kpi/report.html
      const xs = x.g.replace(/\*+$/, "");
      const ys = y.g.replace(/\*+$/, "");
      if (xs && ys && (xs.startsWith(ys) || ys.startsWith(xs))) return true;
    }
  }
  return false;
}

// -------------------------------------------------------------------- DAG

/**
 * Longest dependency chain, measured in HOPS (edges), not nodes.
 *
 * Orca's guidance is to avoid chains deeper than 3-4 steps, and a step is a
 * hop: a five-node pipeline like intake -> ir -> humanize -> render -> attest
 * is four hops. Counting nodes instead would reject genuinely sequential
 * pipelines where each stage really does need the previous one's output, which
 * is a modelling error in the guard rather than in the plan.
 */
export const MAX_DEPTH = 4;

export function validateDag(nodes) {
  const problems = [];
  const byKey = new Map();
  for (const n of nodes) {
    if (!n.key) problems.push("a node has no key");
    else if (byKey.has(n.key)) problems.push(`duplicate node key ${n.key}`);
    else byKey.set(n.key, n);
    if (!n.spec) problems.push(`${n.key}: spec is required`);
    if (!n.role || !ROLES[n.role]) {
      problems.push(`${n.key}: role must be one of ${Object.keys(ROLES).join(", ")}`);
    }
    if (!Array.isArray(n.writes) || n.writes.length === 0) {
      problems.push(
        `${n.key}: writes must be a non-empty glob list. A node that does not ` +
        `declare what it writes cannot be scheduled safely against its peers.`);
    }
    if (!n.acceptance) problems.push(`${n.key}: acceptance is required`);
  }
  for (const n of nodes) {
    for (const d of n.deps ?? []) {
      if (!byKey.has(d)) problems.push(`${n.key}: unknown dependency ${d}`);
    }
  }
  // cycles + depth, in hops
  const depth = new Map();
  const visiting = new Set();
  const walk = (key, stack = []) => {
    if (visiting.has(key)) {
      problems.push(`cycle: ${[...stack, key].join(" -> ")}`);
      return 0;
    }
    if (depth.has(key)) return depth.get(key);
    visiting.add(key);
    const n = byKey.get(key);
    const deps = n?.deps ?? [];
    const d = deps.length
      ? 1 + Math.max(...deps.map((x) => walk(x, [...stack, key])))
      : 0;
    visiting.delete(key);
    depth.set(key, d);
    return d;
  };
  for (const n of nodes) walk(n.key);
  const longest = Math.max(0, ...depth.values());
  if (longest > MAX_DEPTH) {
    problems.push(
      `longest dependency chain is ${longest} hops, over the limit of ${MAX_DEPTH}. ` +
      `Merge nodes: a deep chain serialises the run and makes a failure ` +
      `expensive to recover from.`);
  }
  return { problems, depth: longest };
}

/**
 * Pick the next wave.
 *
 * Ready tasks are ordered by critical-path length, then risk, then shortest
 * first, and then filtered so no two members write to overlapping scopes. A
 * conflicting task waits for the next wave -- it is NOT isolated into a new
 * worktree, which is what earns the right to stay in one checkout.
 */
export function planWave(readyNodes, { cap = 3, running = [] } = {}) {
  const scored = [...readyNodes].sort((a, b) => {
    const r = (x) => ({ high: 0, medium: 1, low: 2 }[x.riskClass ?? "medium"] ?? 1);
    return (b.criticalPath ?? 0) - (a.criticalPath ?? 0) ||
           r(a) - r(b) ||
           (a.estMinutes ?? 30) - (b.estMinutes ?? 30);
  });

  const wave = [];
  const deferred = [];
  const claimed = running.map((n) => n.writes ?? []);
  for (const n of scored) {
    if (wave.length >= cap) { deferred.push({ node: n, why: "cap" }); continue; }
    const clash = claimed.find((w) => writesConflict(w, n.writes ?? []));
    if (clash) { deferred.push({ node: n, why: "writes-conflict" }); continue; }
    wave.push(n);
    claimed.push(n.writes ?? []);
  }
  return { wave, deferred };
}

/**
 * How much parallelism this plan actually has, and therefore whether Orca buys
 * anything.
 *
 * Measured, not assumed: four of the six shipped recipes schedule as 1-1-1-1-1.
 * Every node waits for the one before it, so dispatching them as workers adds
 * a run, five tasks, five worker launches, five terminal settlements, five
 * wait windows and five paid workers -- to do the same work in the same order,
 * slower. It also makes Orca a requirement for people who never needed it.
 *
 * A wave of one is a step. The engine should say so and get out of the way.
 */
export function parallelismOf(nodes, { cap = 4 } = {}) {
  annotateCriticalPath(nodes);
  const done = new Set();
  const waves = [];
  let guard = 0;
  while (done.size < nodes.length && guard++ <= nodes.length + 1) {
    const ready = nodes.filter(
      (n) => !done.has(n.key) && (n.deps ?? []).every((d) => done.has(d)));
    if (!ready.length) break;                 // validateDag reports the cycle
    const { wave } = planWave(ready, { cap });
    waves.push(wave.map((n) => n.key));
    for (const n of wave) done.add(n.key);
  }
  const widths = waves.map((w) => w.length);
  const maxWidth = widths.length ? Math.max(...widths) : 0;
  return {
    waves,
    widths,
    maxWidth,
    // Nothing ever runs beside anything else, so there is no coordination to do.
    sequential: maxWidth <= 1,
    // What the parallel version would actually save, in wave count.
    wavesSaved: nodes.length - waves.length,
  };
}

/** Longest path to each node, used to prioritise the critical path first. */
export function annotateCriticalPath(nodes) {
  const byKey = new Map(nodes.map((n) => [n.key, n]));
  const dependents = new Map(nodes.map((n) => [n.key, []]));
  for (const n of nodes) {
    for (const d of n.deps ?? []) dependents.get(d)?.push(n.key);
  }
  const memo = new Map();
  // A cycle made this recurse until the stack ran out. `validateDag` reports
  // the cycle, but it is not the only caller: `parallelismOf` annotates first,
  // so a malformed DAG died with "Maximum call stack size exceeded" before
  // anything could say which two nodes point at each other.
  const onStack = new Set();
  const down = (key) => {
    if (memo.has(key)) return memo.get(key);
    if (onStack.has(key)) return 0;        // cycle: stop, let validateDag name it
    onStack.add(key);
    const kids = dependents.get(key) ?? [];
    const v = kids.length ? 1 + Math.max(...kids.map(down)) : 0;
    onStack.delete(key);
    memo.set(key, v);
    return v;
  };
  for (const n of nodes) n.criticalPath = down(n.key);
  return nodes;
}

// -------------------------------------------------------------- placement
/**
 * Where a worker runs.
 *
 * Default is the current checkout with a fresh agent terminal per worker.
 * Orca is explicit that parallelism and convenience are not isolation
 * requirements, and a new worktree needs either an explicit human request or a
 * named filesystem conflict that deferral cannot solve.
 */
export function placementFor(node, { gitBacked = true, explicitWorktree = false } = {}) {
  if (explicitWorktree && gitBacked) {
    return { worktree: node.stacked ? "new-child" : "new-top-level", reason: "requested" };
  }
  if (node.needsWorktree && gitBacked) {
    return {
      worktree: "new-top-level",
      reason: node.worktreeReason ?? "a named filesystem conflict was declared",
    };
  }
  return { worktree: "current", reason: "default: parallelism is not isolation" };
}

/** Concurrency ceiling. Four Claude TUIs in one checkout is already a lot. */
export function concurrencyCap({ level = 2, policyMax = 3, worktree = "current" } = {}) {
  const byLevel = [1, 2, 3, 4, 4][Math.max(0, Math.min(4, level))] ?? 3;
  const hard = worktree === "current" ? 4 : 6;
  return Math.max(1, Math.min(policyMax, byLevel, hard));
}
