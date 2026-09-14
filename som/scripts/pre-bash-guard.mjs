#!/usr/bin/env node
/**
 * PreToolUse on the shell tools -- keep Snowflake access on the somsql
 * entrypoint.
 *
 * Matched against `Bash|PowerShell`, not Bash alone: this is a Windows machine
 * where PowerShell is the primary shell and arrives as its own tool, so a
 * Bash-only matcher left the shell people actually use unguarded.
 *
 * Fail-open by construction: any error, any unexpected payload shape, and this
 * prints {} and exits 0. A guard bug must never block unrelated work. The only
 * thing it ever denies is a command that explicitly names a Snowflake surface
 * and is not the entrypoint.
 */
import { readFileSync } from "node:fs";
import { judgeCommand } from "../lib/guard/snowflake.mjs";

function allow() {
  process.stdout.write("{}");
  process.exit(0);
}

let payload;
try {
  payload = JSON.parse(readFileSync(0, "utf8") || "{}");
} catch {
  allow();
}

try {
  const input = payload?.tool_input ?? {};
  // Both shell tools put the text in `command`, but read the alternatives too
  // rather than assume. A guard that silently reads nothing is worse than no
  // guard, because it still looks like one.
  const command = input.command ?? input.script ?? input.cmd ?? "";
  const verdict = judgeCommand(command);
  if (verdict.decision === "allow") allow();

  // A warning, not a refusal. The team had scripts that reach Snowflake before
  // this plugin existed, and denying them meant installing som broke work that
  // had nothing to do with som. The command runs; the person is told what the
  // sanctioned path would have added -- classification, the cost guard, the
  // ledger, and the approval step for a write.
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      additionalContext: verdict.reason,
    },
  }));
  process.exit(0);
} catch {
  allow();
}
