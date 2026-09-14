---
description: Snowflake 실행 — 읽기는 상한 없이 자동 (필요한 만큼 가져옵니다). 쓰기는 무엇을 바꾸는지 보여주고 사람이 승인해야 실행.
argument-hint: "[plan|classify|run|batch|write|explain|ledger] [파일 또는 폴더]"
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, Skill]
---

# /som:sql

Invoke the `snowflake-safe` skill and follow its procedure.

## Input
$ARGUMENTS

## Steps
1. `export PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/engine" PYTHONUTF8=1`
2. **Always `plan` first** when a folder is involved. It classifies and
   cost-guards every file with no connection and no SSO prompt, so a bad file
   is found before any credits are spent.
3. Run what `plan` cleared. Use `--mode full --out` for a real extract so the
   `.meta.json` sidecar exists for ATTEST.
4. Read the exit code.

## Writing
A statement that is not a read goes through `write`:

1. `python -m somsql write --file <path> --dry-run` — prints what it does, to
   which objects, whether it can be undone, and the statement's hash. Nothing
   is sent and no connection is opened.
2. Show that block to the person **verbatim** and stop.
3. They run it back with `--approve <hash> --reason "<왜>"`.

Never fill in `--approve` yourself. The hash exists so that a person's yes
attaches to one exact statement; supplying it on their behalf removes the only
thing it was protecting.

## Exit code handling
- **4 — not a failure, a question.** The statement changes something. Print the
  `SOMSQL-CONFIRM` block verbatim and wait for the person. Do not approve it,
  do not rewrite it as a read.
- **3 — stop.** Print the `SOMSQL-DENY` or `SOMSQL-GUARD` block verbatim, say
  which gate fired, and ask the person who owns the data. Do not rewrite the
  SQL to get around it and do not try another client.
- **2 — read the warnings.** They say what the read did, not that it was
  refused. Nothing truncates a result any more, so a row count is the real
  row count.
- **0 —** report rows, elapsed, bytes scanned, and whether it was a cache hit.
  For a write: verb, target, rows affected, and where it was recorded.

## Report back
- one line per file: rows, elapsed, bytes scanned, cache hit
- every warning, quoted
- for a refusal: the code, the gate, and the exact subject
- where the parquet and sidecar landed
