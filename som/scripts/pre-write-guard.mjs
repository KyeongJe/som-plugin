#!/usr/bin/env node
/**
 * PreToolUse:Write|Edit|NotebookEdit -- stop a file being authored that would
 * open its own Snowflake connection.
 *
 * Without this, the command guard is trivially bypassed: write the script in
 * one call, run it in the next. Same fail-open contract.
 */
import { readFileSync } from "node:fs";
import { judgeFileWrite } from "../lib/guard/snowflake.mjs";

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
  const path = input.file_path ?? input.notebook_path ?? "";
  // Write carries `content`; Edit carries `new_string`; NotebookEdit `new_source`.
  const content = input.content ?? input.new_string ?? input.new_source ?? "";
  const verdict = judgeFileWrite(path, content);
  if (verdict.decision === "allow") allow();

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
