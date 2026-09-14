/**
 * Scratch directories that remove themselves.
 *
 * The tests used to call `mkdtempSync` directly and never clean up. That is
 * invisible in one run and enormous over a week: this project's own %TEMP%
 * held 5,690 leftover `som-*` directories by the time anyone looked. A
 * teammate running `node --test test/*.test.mjs` once leaves ~180 behind,
 * every time, forever.
 *
 * Each test file runs in its own process, so each gets its own exit handler.
 * `rmSync` is synchronous, which is what an `exit` listener requires -- an
 * async cleanup registered there silently never runs.
 *
 * Best effort by design: a killed process (Ctrl-C, SIGKILL) skips `exit` and
 * leaves its directories. That is the right trade -- a test helper should
 * never be the reason a suite hangs or fails.
 */
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const made = [];
let armed = false;

export function scratch(prefix = "som-test-") {
  // Defaulted rather than required: calling it bare failed inside mkdtempSync
  // with `The "path" argument must be of type string`, which points at node
  // internals instead of at the call.
  const dir = mkdtempSync(join(tmpdir(), prefix));
  made.push(dir);
  if (!armed) {
    armed = true;
    process.on("exit", () => {
      for (const p of made) {
        try { rmSync(p, { recursive: true, force: true }); } catch { /* best effort */ }
      }
    });
  }
  return dir;
}
