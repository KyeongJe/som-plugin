#!/usr/bin/env node
/**
 * Stop hook: notice a dead watcher.
 *
 * A stopped monitor looks exactly like a quiet run, so the one thing worth
 * saying at the end of a turn is "the loop is not running any more".
 * Fail-open: any problem here prints {} and exits 0.
 */
import { Store } from "../lib/state/store.mjs";

function out(obj) {
  try { process.stdout.write(JSON.stringify(obj)); } catch { /* ignore */ }
  process.exit(0);
}

try {
  const store = new Store(process.env.CLAUDE_PROJECT_DIR || process.cwd());
  const state = store.load();
  if (!state?.runId || state.stage === "DELIVER") out({});
  if (store.watcherFresh()) out({});

  const loop = store.loop();
  const last = loop.lastKeepaliveAt ? `마지막 신호 ${loop.lastKeepaliveAt}` : "신호 없음";
  out({
    hookSpecificOutput: {
      hookEventName: "Stop",
      additionalContext:
        `som run ${state.runId} 이 아직 열려 있는데 watcher 가 멈춰 있습니다 (${last}).\n` +
        `미ack delivery 는 재무장하면 그대로 다시 옵니다. conduct 를 다시 호출해 ` +
        `이어받으세요. Orca 가 아는 상태를 먼저 다시 읽고 진행하십시오.`,
    },
  });
} catch {
  out({});
}
