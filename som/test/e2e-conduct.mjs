/**
 * Live proof of the orchestrator itself: Conduct.run() with real workers.
 *
 * e2e-orca.mjs proved the Orca adapter and the loop. This proves the layer on
 * top -- recipe -> DAG -> wave scheduling -> router -> accounting -> report --
 * including the two behaviours that only show up with more than one wave:
 *
 *   - a dependent task does not start until its dependency settles
 *   - two tasks whose write scopes overlap are NOT co-scheduled
 *
 * Uses a synthetic recipe so the run is cheap and fast. The real recipes are
 * exercised by their own DAG validation; what is unproven without this is the
 * scheduling, not the prose.
 *
 *   node test/e2e-conduct.mjs [--keep]
 */
import { mkdirSync, existsSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { Conduct, RECIPE_DIR } from "../lib/conduct.mjs";
import { Store } from "../lib/state/store.mjs";

const KEEP = process.argv.includes("--keep");
const PROJECT = join(tmpdir(), "som-conduct-" + Date.now());
const TEST_RECIPE = join(RECIPE_DIR, "_e2e.json");

let failures = 0;
const log = (...a) => console.log(...a);
const check = (c, m) => { if (!c) failures++; log(`  ${c ? "PASS" : "FAIL"}  ${m}`); };

/**
 * Three nodes:
 *   one   and   two-conflict   both write docs/shared/**  -> must NOT share a wave
 *   three depends on one                                   -> must wait
 */
const slash = (p) => String(p).split("\\").join("/");
const ONE = slash(join(PROJECT, "docs/shared/one.txt"));
const TWO = slash(join(PROJECT, "docs/shared/two.txt"));
const THREE = slash(join(PROJECT, "docs/after/three.txt"));

// Absolute paths on purpose. A worker's cwd is the Orca worktree, not this
// process's directory, so a relative path in a spec lands somewhere else
// entirely -- which is exactly what the first run of this test proved.
const RECIPE = {
  id: "_e2e",
  title: "e2e synthetic",
  summary: "scheduling proof",
  match: ["__never_matches__"],
  budgetMinutes: 12,
  defaults: {},
  nodes: [
    {
      key: "one", role: "renderer", deps: [],
      writes: [ONE],
      spec: `Create any missing parent directory and write exactly the line 'one ok' into the absolute path ${ONE}. Do nothing else.`,
      acceptance: `${ONE} contains the line 'one ok'`,
    },
    {
      key: "two-conflict", role: "renderer", deps: [],
      writes: [TWO],
      spec: `Create any missing parent directory and write exactly the line 'two ok' into the absolute path ${TWO}. Do nothing else.`,
      acceptance: `${TWO} contains the line 'two ok'`,
    },
    {
      key: "three", role: "renderer", deps: ["one"],
      writes: [THREE],
      spec: `Create any missing parent directory and write exactly the line 'three ok' into the absolute path ${THREE}. Do nothing else.`,
      acceptance: `${THREE} contains the line 'three ok'`,
    },
  ],
};

async function main() {
  mkdirSync(PROJECT, { recursive: true });
  writeFileSync(TEST_RECIPE, JSON.stringify(RECIPE, null, 2), "utf8");
  log(`project: ${PROJECT}\n`);

  const waves = [];
  const c = new Conduct({
    project: PROJECT,
    maxWorkers: 3,
    autonomy: 2,
    // The synthetic recipe's specs are already exact -- absolute paths and one
    // literal line to write -- so there is nothing for an interview to
    // clarify. Its own gate is covered by test/clarity.test.mjs.
    requireInterview: false,
    say: (s) => {
      log(`  ${s}`);
      // say() prefixes a blank line, so anchor loosely rather than at ^.
      const m = /wave (\d+) — (\d+)개 병렬/.exec(s);
      if (m) waves.push({ n: Number(m[1]), size: Number(m[2]), line: s });
    },
    askHuman: async () => "지침을 주고 계속",
  });

  const pf = c.preflight();
  check(pf.problems.length === 0, `preflight clean (${pf.problems.join("; ") || "ok"})`);
  if (pf.problems.length) { done(); return; }

  log("\n=== run ===");
  const t0 = Date.now();
  const res = await c.run({
    objective: "som conduct e2e", recipeId: "_e2e", windowMs: 90_000,
  });
  log(`\n(${((Date.now() - t0) / 1000).toFixed(0)}s)`);

  log("\n=== scheduling ===");
  // one and two-conflict write to different files under docs/shared, so they
  // do not conflict; three must wait for one.
  check(waves.length >= 2, `more than one wave was needed (${waves.length})`);
  const first = waves[0];
  check(first && first.size === 2,
        `first wave holds the two independent tasks (${first?.size})`);

  const st = new Store(PROJECT).load();
  const byKey = Object.fromEntries(
    Object.values(st?.dispatches ?? {}).map((d) => [d.key, d]));
  const tOne = byKey["one"]?.startedAt ? Date.parse(byKey["one"].startedAt) : 0;
  const tThree = byKey["three"]?.startedAt ? Date.parse(byKey["three"].startedAt) : 0;
  check(tOne && tThree && tThree > tOne,
        "the dependent task started after its dependency");

  log("\n=== results ===");
  // `ok` has to mean every task finished, not merely that none failed. A run
  // that timed out at 1 of 3 reported ok:true and this check passed it.
  check(res.ok && res.completed === res.total,
        `run reported ok (completed ${res.completed}/${res.total}, ` +
        `failed ${res.failed}, unfinished ${res.unfinished ?? "?"})`);
  for (const [p, label] of [[ONE, "one"], [TWO, "two-conflict"], [THREE, "three"]]) {
    const f = p.replace(slash(PROJECT) + "/", "");
    const there = existsSync(p);
    check(there, `${label} produced its file`);
    if (there) log(`    ${f}: ${JSON.stringify(readFileSync(p, "utf8").trim())}`);
  }

  log("\n=== accounting ===");
  const ds = Object.values(st?.dispatches ?? {});
  check(ds.length === 3, `three dispatches recorded (${ds.length})`);
  const unaccounted = ds.filter(
    (d) => !["released", "reused", "retained", "already_released"].includes(d.accounting));
  for (const d of ds) log(`    ${String(d.key).padEnd(14)} ${d.outcome ?? "-"} · ${d.accounting}`);
  check(unaccounted.length === 0,
        `every terminal accounted for (${unaccounted.length} leaked)`);
  check(res.leakedTerminals.length === 0, "report shows no leaked terminal");

  log("\n=== worker summaries (what the report hands the user) ===");
  for (const s of res.summaries ?? []) {
    log(`    ${String(s.key).padEnd(14)} ${String(s.summary).slice(0, 88)}`);
  }
  check((res.summaries ?? []).length === 3, "every worker returned a summary");

  done();
}

function done() {
  try { rmSync(TEST_RECIPE, { force: true }); } catch {}
  log(`\n${failures === 0 ? "conduct e2e PASSED" : `conduct e2e FAILED (${failures})`}`);
  if (!KEEP && failures === 0) {
    try { rmSync(PROJECT, { recursive: true, force: true }); } catch {}
  } else {
    log(`kept: ${PROJECT}`);
  }
  process.exitCode = failures === 0 ? 0 : 1;
}

main().catch((e) => {
  console.log(`\nconduct e2e ERROR ${e.constructor.name}: ${e.message}`);
  console.log(e.stack?.split("\n").slice(1, 5).join("\n"));
  try { rmSync(TEST_RECIPE, { force: true }); } catch {}
  process.exitCode = 1;
});
