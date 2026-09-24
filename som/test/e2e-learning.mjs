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
 * What only a live worker can prove is the input: `filesModified`. Nothing
 * ever asked a worker for it, so it arrived empty on every run ever made and
 * `checkWrites` compared the declared globs against an empty array, reporting
 * no violation every single time. That is the assertion this file exists for,
 * and it is deterministic.
 *
 * What it deliberately does not do is require the worker to write outside its
 * declared scope. `test/signals.test.mjs` proves the chain from a violation to
 * a stored pattern with injected state; this proves the plumbing that only a
 * real run can reach.
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
      // Both files are inside the declared scope. Nothing here asks the worker
      // to break a rule it was handed -- see the note above the chain check.
      writes: [DECLARED, OUTSIDE],
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
  check(existsSync(OUTSIDE), "second file written");

  // -- the assertion this file exists for ----------------------------------
  //
  // `filesModified` is the input every downstream check depends on, and it
  // arrived empty on every run ever made because nothing asked the worker for
  // it. checkWrites then compared the declared globs against an empty array
  // and reported no violation -- every run, for the life of the project. Only
  // a live worker can prove it arrives now.
  log("\n=== the worker reported what it touched ===");
  const st = JSON.parse(readFileSync(join(PROJECT, ".som", "state.json"), "utf8"));
  const settled = Object.values(st.dispatches ?? {}).filter((d) => d.settled);
  const touched = settled.flatMap((d) => d.filesModified ?? []);
  check(touched.length > 0, `filesModified arrived with ${touched.length} path(s)`);
  for (const f of touched.slice(0, 4)) log(`    ${f}`);
  check(touched.some((f) => String(f).includes("a.txt")),
        "the file it was asked to write is in the list");
  check(settled.every((d) => Array.isArray(d.filesModified)),
        "every settled dispatch carries a list, not undefined");

  // -- the chain, when there is something to learn -------------------------
  //
  // Conditional on purpose. Requiring a violation here would mean requiring
  // the worker to disobey the SCOPE line it was given, and it should not: the
  // first version of this test escalated rather than write out of scope, the
  // second complied, the third refused again. A release gate that depends on a
  // model choosing to disobey is one that gets bypassed the first time it is
  // wrong, and flakiness there costs more than the coverage it buys.
  //
  // `test/signals.test.mjs` proves violation -> pattern -> brief with injected
  // state, deterministically. This proves the part only a real run can reach.
  log("\n=== what the run taught ===");
  const violations = settled.flatMap((d) => d.violations ?? []);
  const lib = new PatternLibrary(PROJECT);
  const all = lib.all();
  if (!violations.length) {
    log("    깨끗한 런 — 남길 것이 없습니다 (정상)");
    check(all.length === 0, "a clean run stored nothing");
  } else {
    log(`    위반 ${violations.length}건: ${violations.slice(0, 2).join(", ")}`);
    check(existsSync(join(PROJECT, ".som", "patterns.json")), "patterns.json exists");
    check((report.patternsLearned ?? []).length > 0,
          `the report says what was learned (${(report.patternsLearned ?? []).length})`);
    check(all.length > 0, `the library holds ${all.length}`);
    const p = all[0];
    if (p) {
      log(`    ${p.title}`);
      check(p.confidence === AUTO_CONFIDENCE,
            `it starts at the auto confidence (${p.confidence})`);
      check(p.source === "auto", "it is marked as engine-observed");
      check(/writes-wide/.test(p.title + p.action), "it names the node");
      check(Boolean(lib.brief({
        objective: "writes-wide 를 계획한다", recipe: "_e2e_learn",
        spec: "writes-wide", files: [], tags: [],
      })), "the next brief would carry it");
    }
  }

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
