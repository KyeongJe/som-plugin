/**
 * Live proof that a run actually teaches the next one.
 *
 * `e2e-conduct.mjs` proves scheduling, and it proves it on a run where
 * nothing goes wrong -- so it produces no patterns, correctly, and therefore
 * says nothing about whether the learning loop works at all.
 *
 * That loop was broken in a way no test caught: `propose()` was reachable
 * only from the CLI, so unless somebody typed `/som:learn` the library stayed
 * empty forever, patterns were never injected, and no skill was ever promoted.
 * Heavy real use produced exactly zero of each. Unit tests covered every
 * piece; nothing covered the wiring.
 *
 * So this runs a real worker that does something the engine can observe --
 * writes a file outside the glob its node declared -- and then checks the
 * whole chain end to end:
 *
 *   worker writes out of scope
 *     -> router records a violation
 *     -> finish() calls proposeFromRun()
 *     -> signalsFrom() drafts a pattern naming the node and the real path
 *     -> the gate accepts it
 *     -> .som/patterns.json exists and holds it
 *     -> the next brief would carry it
 *
 * Needs Orca and one paid worker. Not in the default suite for that reason;
 * `tools/release.mjs` runs its sibling before every release.
 *
 *   node test/e2e-learning.mjs [--keep]
 */
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { Conduct, RECIPE_DIR } from "../lib/conduct.mjs";
import { PatternLibrary } from "../lib/state/patterns.mjs";
import { AUTO_CONFIDENCE } from "../lib/domain/signals.mjs";

const KEEP = process.argv.includes("--keep");
const PROJECT = join(tmpdir(), "som-learning-" + Date.now());
const TEST_RECIPE = join(RECIPE_DIR, "_e2e_learn.json");

let failures = 0;
const log = (...a) => console.log(...a);
const check = (c, m) => { if (!c) failures += 1; log(`  ${c ? "PASS" : "FAIL"}  ${m}`); };

const slash = (p) => String(p).split("\\").join("/");
const DECLARED = slash(join(PROJECT, "docs/declared/a.txt"));
const OUTSIDE = slash(join(PROJECT, "src/outside/b.txt"));

/**
 * Two nodes so the plan is not single-agent -- a sequential plan never reaches
 * Orca at all, and this has to run the real dispatch path.
 *
 * The first node declares one file and is told to write two. That is the
 * signal: not misbehaviour by the worker, which did exactly what it was
 * asked, but a plan whose `writes` was wrong. The scheduler trusted that glob
 * to decide what could run beside it.
 */
const RECIPE = {
  id: "_e2e_learn",
  title: "e2e learning",
  summary: "does a run teach the next one",
  match: ["__never_matches__"],
  budgetMinutes: 12,
  defaults: {},
  nodes: [
    {
      key: "writes-wide", role: "renderer", deps: [],
      writes: [DECLARED],
      spec: `Create any missing parent directories and write exactly the line 'a ok' `
          + `into the absolute path ${DECLARED}. Then also create any missing parent `
          + `directories and write exactly the line 'b ok' into the absolute path `
          + `${OUTSIDE}. Write both files. Do nothing else.`,
      acceptance: "both files exist with the exact lines",
      riskClass: "low", estMinutes: 2,
    },
    {
      key: "sibling", role: "renderer", deps: [],
      writes: [slash(join(PROJECT, "docs/sibling/c.txt"))],
      spec: `Create any missing parent directories and write exactly the line 'c ok' `
          + `into the absolute path ${slash(join(PROJECT, "docs/sibling/c.txt"))}. `
          + `Do nothing else.`,
      acceptance: "the file exists with the exact line",
      riskClass: "low", estMinutes: 2,
    },
  ],
};

async function main() {
  mkdirSync(PROJECT, { recursive: true });
  writeFileSync(TEST_RECIPE, JSON.stringify(RECIPE, null, 2), "utf8");
  log(`project: ${PROJECT}\n`);

  const c = new Conduct({
    project: PROJECT, maxWorkers: 2, autonomy: 2, requireInterview: false,
    say: (s) => log(`  ${s}`),
  });

  log("=== run ===");
  const report = await c.run({
    objective: "e2e learning", recipeId: "_e2e_learn", slots: {},
  });

  log("\n=== the run itself ===");
  check(report.ok, `run reported ok (완료 ${report.completed}/${report.total})`);
  check(existsSync(DECLARED), "declared file written");
  check(existsSync(OUTSIDE), "out-of-scope file written");

  log("\n=== the signal was observed ===");
  const st = JSON.parse(readFileSync(join(PROJECT, ".som", "state.json"), "utf8"));
  const violations = Object.values(st.dispatches ?? {}).flatMap((d) => d.violations ?? []);
  check(violations.length > 0,
        `router recorded a write outside the declared glob (${violations.length})`);
  if (violations.length) log(`    ${violations.slice(0, 3).join(", ")}`);

  log("\n=== it became a pattern ===");
  const file = join(PROJECT, ".som", "patterns.json");
  check(existsSync(file), "patterns.json exists");
  check((report.patternsLearned ?? []).length > 0,
        `the report says what was learned (${(report.patternsLearned ?? []).length})`);

  const lib = new PatternLibrary(PROJECT);
  const all = lib.all();
  check(all.length > 0, `the library holds ${all.length}`);
  const p = all[0];
  if (p) {
    log(`    ${p.title}`);
    log(`    ${p.action}`);
    check(p.confidence === AUTO_CONFIDENCE,
          `it starts at the auto confidence (${p.confidence})`);
    check(p.source === "auto", "it is marked as engine-observed");
    check(/writes-wide/.test(p.title + p.action), "it names the node");
    check(p.action.includes("b.txt"), "it names the file that was actually touched");
    check((p.evidence ?? []).some((e) => e.kind === "run"), "the run id is cited");
  }

  log("\n=== the next run would see it ===");
  const brief = lib.brief({
    objective: "writes-wide 를 계획한다", recipe: "_e2e_learn",
    spec: "writes-wide", files: [], tags: [],
  });
  check(Boolean(brief), "a plausible next task matches the new pattern");
  if (brief) log(`    ${brief.split("\n").slice(0, 2).join("\n    ")}`);

  log(failures ? `\nlearning e2e FAILED (${failures})` : "\nlearning e2e PASSED");
  if (KEEP) log(`kept: ${PROJECT}`);
  else { try { rmSync(PROJECT, { recursive: true, force: true }); } catch { /* best effort */ } }
  try { rmSync(TEST_RECIPE, { force: true }); } catch { /* best effort */ }
  process.exit(failures ? 1 : 0);
}


main().catch((e) => {
  log(`learning e2e ERROR ${e?.stack ?? e}`);
  try { rmSync(TEST_RECIPE, { force: true }); } catch { /* best effort */ }
  process.exit(1);
});
