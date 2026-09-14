#!/usr/bin/env node
/**
 * SessionStart / PostCompact: hand the next session a small resume card.
 *
 * Deliberately short. Its job is to say a run exists and how to pick it up,
 * not to replay it -- Orca owns the real state and the session should
 * reconcile rather than trust these numbers.
 *
 * One file, two events. `session-start.mjs` and `post-compact.mjs` were
 * byte-identical copies that told the two events apart only by an environment
 * variable, with `"SessionStart"` hardcoded as the fallback -- so a PostCompact
 * hook that did not receive it reported the wrong event name back to the host.
 * The event now comes from the command line, where the hook entry already
 * knows it.
 */
import { Store, resumeCard } from "../lib/state/store.mjs";

const EVENTS = new Set(["SessionStart", "PostCompact"]);

function out(obj) {
  try { process.stdout.write(JSON.stringify(obj)); } catch { /* ignore */ }
  process.exit(0);
}

try {
  const i = process.argv.indexOf("--event");
  const named = i >= 0 ? process.argv[i + 1] : process.env.CLAUDE_HOOK_EVENT;
  // An unknown name would be reported to the host as the event that happened,
  // so fall back to nothing rather than to a guess.
  const event = EVENTS.has(named) ? named : null;

  const store = new Store(process.env.CLAUDE_PROJECT_DIR || process.cwd());
  const state = store.load();
  if (!event || !state?.runId || state.stage === "DELIVER") out({});
  const card = resumeCard(state, store.loop());
  if (!card) out({});
  out({
    hookSpecificOutput: { hookEventName: event, additionalContext: card },
  });
} catch {
  out({});
}
