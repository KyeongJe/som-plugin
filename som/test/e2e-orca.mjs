/**
 * Live end-to-end proof: two real Claude workers, one wave, on the current
 * checkout.
 *
 * NOT part of `node --test`. It spawns real agent terminals in the running
 * Orca and costs real tokens, so it is invoked explicitly:
 *
 *   node test/e2e-orca.mjs [--keep]
 *
 * What it proves, and each of these was an open question until it ran:
 *   1. worker-start actually launches a Claude worker in the current checkout
 *   2. the worker receives Orca's injected preamble and reports worker_done
 *   3. the watcher's check --wait loop receives that delivery
 *   4. the ack handshake through ack.json releases the batch
 *   5. terminal accounting releases every settled dispatch
 *   6. two workers with disjoint writes really do run in parallel
 */
import { mkdirSync, existsSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { preflight } from "../lib/orca/exec.mjs";
import { run, task, worker, msg, fleet, useRun } from "../lib/orca/commands.mjs";
import { Store } from "../lib/state/store.mjs";
import { Watcher } from "../lib/loop/watcher.mjs";

const KEEP = process.argv.includes("--keep");
const PROJECT = join(tmpdir(), "som-e2e-" + Date.now());
const OUT = join(PROJECT, "out");

const log = (...a) => console.log(...a);
const ok = (c, m) => log(`  ${c ? "PASS" : "FAIL"}  ${m}`);
let failures = 0;
const check = (c, m) => { if (!c) failures++; ok(c, m); };

function spec(name, file) {
  // Deliberately tiny. The point is the lifecycle, not the work.
  return [
    `Write exactly the single line "${name} ok" into the file ${file}.`,
    `Create the parent directory if it does not exist. Do not create any other file.`,
    // The assertion below has always demanded this and the spec never asked
    // for it, so "every worker reported the file it modified" has been red
    // since the day it was written -- unnoticed, because this e2e needs Orca
    // and lives outside both the default suite and CI. The same omission in
    // `Conduct.brief()` meant the write-scope guard never fired on any real
    // run: checkWrites compared the declared globs against an empty list.
    `When you send worker_done, put the full path of every file you created `
    + `or changed in --files-modified.`,
    `Then report completion and stop. Do no other work.`,
  ].join(" ");
}

async function main() {
  mkdirSync(OUT, { recursive: true });
  log(`e2e project: ${PROJECT}\n`);

  log("=== preflight ===");
  const pf = preflight();
  log(`  orca ${pf.appVersion} · runtime ${pf.runtimeReady ? "ready" : "NOT ready"} · ` +
      `orchestration ${pf.orchestrationEnabled ? "enabled" : "DISABLED"}`);
  log(`  bin ${pf.binKind} bodySafe=${pf.bodySafe} · launchPrefs=${pf.supportsLaunchPreferences}`);
  check(pf.runtimeReady && pf.orchestrationEnabled, "runtime ready and orchestration enabled");
  if (!pf.runtimeReady || !pf.orchestrationEnabled) {
    log("\naborting: the runtime is not in a state where this can prove anything");
    process.exit(1);
  }
  // A worker cannot dispatch: nested depth is 1, counted from the issuing
  // terminal. Refuse rather than fail every worker-start later.
  if (pf.terminalHandle) {
    try {
      const wl = worker.list({});
      const mine = (wl.result?.workers ?? []).find(
        (w) => (w.agentTerminalHandle ?? w.agent_terminal_handle) === pf.terminalHandle &&
               !["succeeded", "failed", "stopped", "abandoned"].includes(
                 w.workerState ?? w.dispatchStatus ?? ""));
      check(!mine, "this terminal is a coordinator, not a dispatched worker");
    } catch { /* no run bound yet is fine */ }
  }

  log("\n=== run + tasks ===");
  const r = run.create({ objective: "som e2e: two workers, one wave" });
  const runId = r.result?.run?.id ?? r.result?.id;
  useRun(runId);
  log(`  run ${runId}`);
  check(Boolean(runId), "run created and bound");

  const fileA = join(OUT, "a.txt");
  const fileB = join(OUT, "b.txt");
  const tA = task.create({ spec: spec("alpha", fileA), title: "alpha" });
  const tB = task.create({ spec: spec("beta", fileB), title: "beta" });
  const idA = tA.result?.task?.id ?? tA.result?.id;
  const idB = tB.result?.task?.id ?? tB.result?.id;
  log(`  tasks ${idA} · ${idB}`);

  const ready = (task.list({ ready: true }).result?.tasks ?? []).length;
  check(ready === 2, `both tasks ready without deps (got ${ready})`);

  log("\n=== worker-start x2 (current checkout, one fresh terminal each) ===");
  const started = [];
  for (const [tid, label] of [[idA, "alpha"], [idB, "beta"]]) {
    const t0 = Date.now();
    try {
      const w = worker.start({
        task: tid, worktree: "current", agent: "claude",
        model: "sonnet", effort: "low",
      });
      const dispatch = w.result?.dispatch?.id ?? w.result?.dispatchId ?? w.result?.id;
      started.push({ tid, label, dispatch, handle: null });
      log(`  ${label}: dispatch ${dispatch} (${((Date.now() - t0) / 1000).toFixed(1)}s)`);
      const eff = w.result?.launch?.effective ?? {};
      const req = w.result?.launch?.requested ?? {};
      if (req.model && eff.model && req.model !== eff.model) {
        log(`  WARN ${label}: requested model ${req.model} but got ${eff.model}`);
      }
    } catch (e) {
      failures++;
      log(`  FAIL ${label}: ${e.code ?? e.constructor.name} ${String(e.message).slice(0, 160)}`);
      if (e.nextSteps?.length) log(`        nextSteps: ${e.nextSteps.join(" | ")}`);
    }
  }
  check(started.length === 2, "two workers started");
  if (started.length === 0) { report(); return; }

  // Terminal handles come from worker-list, not from the start receipt:
  // worker-list is camelCase (dispatchId / agentTerminalHandle) and is the
  // documented way to re-resolve a handle, including after a stale one.
  try {
    const wl = worker.list({}).result?.workers ?? [];
    for (const s2 of started) {
      const row = wl.find((w) => (w.dispatchId ?? w.dispatch_id) === s2.dispatch);
      s2.handle = row?.agentTerminalHandle ?? row?.agent_terminal_handle ?? null;
      log(`  ${s2.label}: terminal ${s2.handle ?? "unresolved"}`);
    }
    check(started.every((s2) => s2.handle), "every dispatch resolved a terminal handle");
  } catch (e) {
    failures++;
    log(`  FAIL worker-list: ${e.code ?? e.constructor.name}`);
  }

  // Both dispatched at once, before waiting: the guide's explicit ordering.
  const dispatched = (task.list({ status: "dispatched" }).result?.tasks ?? []).length;
  check(dispatched === started.length,
        `all wave members dispatched before waiting (${dispatched})`);

  log("\n=== watcher: check --wait loop ===");
  const store = new Store(PROJECT);
  const seen = [];
  const watcher = new Watcher({
    project: PROJECT,
    windowMs: 90_000,
    emit: (o) => {
      log(`  [emit] ${JSON.stringify(o)}`);
      if (o.k === "msg") seen.push(o);
      // Act as the router: ack as soon as the batch is emitted.
      if (o.k === "msg" || o.k === "batch") {
        const dId = store.loop().awaitingAck;
        if (dId) store.requestAck(dId);
      }
    },
  });

  const deadline = Date.now() + 8 * 60_000;
  const loopPromise = watcher.run(runId, { maxWindows: 8 });
  while (Date.now() < deadline) {
    const done = seen.filter((m) => m.type === "worker_done");
    if (done.length >= started.length) break;
    await new Promise((res) => setTimeout(res, 3_000));
  }
  watcher.stop();
  await loopPromise;

  const done = seen.filter((m) => m.type === "worker_done");
  check(done.length >= started.length,
        `worker_done received for every worker (${done.length}/${started.length})`);
  for (const m of done) {
    log(`  worker_done: task=${m.task} dispatch=${m.dispatch} outcome=${m.outcome}`);
  }
  // The payload is a JSON string on the wire. If these are null the router
  // cannot account for the dispatch it was just told about.
  check(done.every((m) => m.task && m.dispatch),
        "every worker_done carried its taskId and dispatchId");
  check(done.every((m) => m.outcome === "succeeded"),
        "every worker reported outcome=succeeded");
  check(done.every((m) => Array.isArray(m.files) && m.files.length > 0),
        "every worker reported the file it modified");
  check(done.every((m) => m.dispatch && started.some((s2) => s2.dispatch === m.dispatch)),
        "reported dispatch ids match the ones we started");
  check(Boolean(store.loop().lastAckedDeliveryId), "a delivery was acknowledged");

  log("\n=== the work itself ===");
  for (const [f, label] of [[fileA, "alpha"], [fileB, "beta"]]) {
    const there = existsSync(f);
    check(there, `${label} wrote its file`);
    if (there) log(`  ${label}: ${JSON.stringify(readFileSync(f, "utf8").trim())}`);
  }

  log("\n=== terminal accounting ===");
  for (const s of started) {
    try {
      const rel = worker.release({ dispatch: s.dispatch });
      const st = rel.result?.releaseState ?? rel.result?.state ?? "released";
      log(`  ${s.label}: ${st}`);
      check(["released", "retained", "already_released", "release_pending"].includes(st),
            `${s.label} accounted for (${st})`);
    } catch (e) {
      // release_unknown is the only exit-1 case; leaked is a report line, not a crash.
      log(`  ${s.label}: ${e.code} -- ${String(e.message).slice(0, 120)}`);
      check(e.code === "already_released", `${s.label} accounted for (${e.code})`);
    }
  }

  const settled = (task.list({ status: "completed" }).result?.tasks ?? []).length;
  log(`\n  tasks completed by worker_done alone: ${settled}/${started.length}`);
  check(settled === started.length,
        "a valid worker_done settled the task without task-update");

  report();
  if (!KEEP) { try { rmSync(PROJECT, { recursive: true, force: true }); } catch {} }
}

function report() {
  log(`\n${failures === 0 ? "e2e PASSED" : `e2e FAILED (${failures} check(s))`}`);
  process.exitCode = failures === 0 ? 0 : 1;
}

main().catch((e) => {
  console.log(`\ne2e ERROR ${e.constructor.name}: ${e.message}`);
  if (e.nextSteps?.length) console.log(`nextSteps: ${e.nextSteps.join(" | ")}`);
  process.exitCode = 1;
});
