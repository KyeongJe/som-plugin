#!/usr/bin/env node
/**
 * Host-managed Monitor: the coordinator loop for an active som run.
 *
 * Armed by the plugin's monitors manifest on `conduct` invocation, so the
 * 15-minute `check --wait` runs out-of-band and survives context compaction.
 * Each stdout line becomes one task notification to the model.
 *
 * Exits 0 immediately when there is no active run, so nothing is emitted and
 * nothing is consumed in sessions that have no run of their own.
 *
 * Emission is rationed on purpose: a monitor that over-emits gets stopped by
 * the host, and a stopped watcher is indistinguishable from a quiet run.
 */
import { Store } from "../lib/state/store.mjs";
import { Watcher } from "../lib/loop/watcher.mjs";

function arg(name, fallback) {
  const i = process.argv.indexOf(name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

const project = arg("--project", process.env.CLAUDE_PROJECT_DIR || process.cwd());

let state;
try {
  state = new Store(project).load();
} catch {
  process.exit(0);
}
if (!state?.runId || state.stage === "DELIVER") process.exit(0);

const watcher = new Watcher({ project });

for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => { watcher.stop(); process.exit(0); });
}

watcher
  .run(state.runId)
  .then(() => process.exit(0))
  .catch((e) => {
    // One line, then out. The Stop hook notices the stale keepalive and tells
    // the model to re-arm; a crash loop here would just burn the rate budget.
    process.stdout.write(JSON.stringify({
      k: "watcher_stopped",
      code: e.code ?? e.constructor.name,
      message: String(e.message).slice(0, 200),
    }) + "\n");
    process.exit(0);
  });
