---
name: snowflake-safe
description: |
  The only path to Snowflake in this project. Reads (SELECT / WITH / SHOW /
  DESCRIBE) execute automatically and without any ceiling -- no byte, row or
  time limit, and no LIMIT injected on your behalf -- after a two-gate
  classifier. Cost is reported and recorded, never rationed.
  A write -- CREATE, INSERT, UPDATE, DELETE, DROP -- is described in plain
  words first: what it does, to which objects, whether it can be undone, and
  runs only against an approval bound to that exact statement. Results land in
  a parquet cache with a .meta.json sidecar, and every attempt including
  refusals and approvals is recorded in a ledger.

  Triggers: "Snowflake", "somsql", "SQL 실행", "쿼리 실행", "데이터 추출",
  "numbered SQL", "parquet 캐시", "테이블 만들어", "테이블 수정",
  "registry.json".

  Boundary. This skill is the only component in the plugin that opens a
  Snowflake connection; no other skill, agent, or command may. bkit has no
  Snowflake surface, so there is no overlap there. The Orca `orchestration`
  skill coordinates agents and knows nothing about SQL.
argument-hint: "[classify|plan|run|batch|write|explain|ledger] [file or dir]"
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]
---

# somsql — checked Snowflake access

## The rule

**Reads run. A write is described, approved, and recorded.**

Writes used to be refused outright. That was wrong for this team — creating a
table, correcting a row, dropping a scratch object are real work, and the
refusal blocked scripts that existed before this plugin did. What must not
happen is not "a write": it is **a write nobody looked at**.

So the shape is three steps, and the engine cannot skip any of them:

1. **describe** — `somsql` says what the statement does (`DELETE — 행을
   지웁니다`), which objects it touches, whether Time Travel can put it back,
   and what is alarming about it (`WHERE 절이 없습니다 — 모든 행에 적용됩니다`)
2. **approve** — the person types the statement's own 12-character hash. Edit
   one character and the hash changes and the approval no longer applies, so a
   yes to one statement cannot carry a different one
3. **record** — verb, objects, rows affected, approver and the written reason
   land in `.som/ledger.ndjson` and `.som/WRITE_LOG.md`

You cannot approve on the model's behalf. The hash comes out of a terminal and
goes back in from a person.

The wrapper is still defence in depth. The root fix for *unintended* writes is
a Snowflake role whose grants match what the person is allowed to do, and
requesting the right roles from DCOE remains the highest-value action here.

## Setup

```bash
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/engine" PYTHONUTF8=1
python -m somsql --help
```

Config discovery, first match wins:

| Order | Source | Notes |
|---|---|---|
| 1 | `SOMSQL_PROFILE` + `~/.snowflake/connections.toml` | official Snowflake CLI location |
| 2 | `SF_LOGIN_INFO=<path>` | legacy scripts already set this |
| 3 | `sf_login_info.json` in cwd or any parent | the existing convention |

Sample: `standard/secrets/sf_login_info.json.sample`. Interactive work uses
`"authenticator": "externalbrowser"` — no password in the file, and **the config
carrying a secret inline is refused**.

## Always plan before you run

```bash
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/engine" PYTHONUTF8=1
python -m somsql plan --dir sql/ --mode full
```

`plan` classifies and cost-guards every `NN_*.sql` in the folder **without
opening a connection**. No credits, no SSO prompt. It converts "the extract
died halfway through" into "these two files are not runnable, here is why".

Do this first, every time. Fix what it names, then run the batch.

## Running

```bash
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/engine" PYTHONUTF8=1
# one query
python -m somsql run --file sql/01_visit_gap.sql --mode full --out _cache/01.parquet

# a folder, on ONE connection -- one SSO prompt for the whole batch
python -m somsql batch --dir sql/ --out _cache/ --mode full

# byte estimate only, nothing executed
python -m somsql explain --file sql/01_visit_gap.sql
```

`--out` writes the parquet plus a `.meta.json` sidecar carrying `rowcount`,
`bytes_scanned`, `elapsed_ms`, and `sql_sha256`. ATTEST needs that sidecar; do
not skip it on a real extract.

### Modes

`--mode explore|full` still exists and is recorded in the ledger, but it no
longer restricts anything. There are **no ceilings on a read**: no byte limit,
no row limit, no statement timeout, and no LIMIT injected on your behalf.

That last one mattered most. A LIMIT the author did not write could return
1,000 rows of a 410,000-row extract, and a truncated result looks exactly like
a complete one. The SQL now reaches the warehouse as written.

The warehouse bill belongs to another team. This tool reports cost; it does not
ration it.

## Exit codes — read them

| Exit | Meaning | What to do |
|---|---|---|
| 0 | ran | continue |
| 2 | ran, with warnings | **read the warnings.** A truncated explore result looks like a complete one |
| 3 | **DENIED** | stop |
| **4** | **not a read — the question is being asked** | show the block to the person and wait |
| 1 | usage or environment | fix the setup, `/som:doctor` |

**On exit 3: stop. Report the block verbatim. Ask the person who owns the
data.** Do not rewrite the SQL to get around it, and do not reach for another
client — trying is the behaviour this design exists to prevent.

**Exit 4 is not a failure.** It means the statement changes something and
nobody has said yes yet. Print the `SOMSQL-CONFIRM` block **verbatim** — it
already contains the exact command with the hash filled in — and stop. Do not
fill the hash in yourself and do not run it. Waiting is the correct outcome.

## What the classifier catches

Two gates. Either one saying deny is a deny.

1. **sqlglot AST** — default-deny whitelist on the statement root, then a full
   walk for a write node hiding inside an allowed root.
2. **token regex** — after comments and string literals are stripped.

Three holes in the older `sf_utils.py` helper that this closes:

| Hole | Example | Old | Now |
|---|---|---|---|
| first token only | `WITH x AS (...) INSERT INTO t ...` | passed | root is `Insert` |
| naive `;` split | `WHERE n = 'a;DROP TABLE t'` | mis-split | parser-based |
| deny-list | any statement kind not listed | passed | default-deny whitelist |

Two details worth knowing:

- **sqlglot rarely fails outright.** `SELEKT * FRM t` parses as an `Alias`, and
  unsupported syntax becomes a `Command`. So "parse failure means deny" would
  catch almost nothing — the *root whitelist* is what makes it fail closed.
- **`SELECT ... INTO t` has a `Select` root.** Only the full walk catches it.

If the two gates disagree, the query is denied and the disagreement is recorded
as `CLASSIFIER_DISAGREE`. One of the gates is wrong and a human should know
which. Known conservative case: `SELECT "INSERT" FROM t` — double quotes are
identifiers in Snowflake, so the token gate legitimately sees `INSERT`.

## What the cost report tells you

None of it refuses. Each finding is printed before the query runs and recorded
after it, and then the read goes ahead.

Cheapest first. The cheapest guard is the one that never touches the warehouse.

| Layer | Check | Cost |
|---|---|---|
| 1 | cartesian join (no `ON`/`USING`), missing date predicate on a registry table, unbounded `SELECT *`, > 7 relations | free |
| 2 | `EXPLAIN USING JSON` scan estimate | free — **a cartesian shows up here as an exploded row estimate and costs no credits to find** |
| 4 | session statement timeout + running row-count abort with `conn.cancel()` | — |
| 5 | 24-hour parquet result cache on the normalised statement hash | — |

Registry of large objects and their date columns:
`engine/somsql/registry.py`. Add an object there rather than special-casing a
query.

## Never do these

- open a connection any other way for *new* work. `import snowflake.connector`,
  `snowsql`, reusing `sf_utils` — the PreToolUse guards flag all of these, in
  both the Bash and the Write path, and let them run so pre-existing scripts
  keep working. A flagged command is outside the ledger: whatever it did leaves
  no trace to look up later.
- copy, move, or print a private key. This client reads a config to learn
  *where* a key is; it never handles the key itself. A key inside a synced
  folder is refused outright — deleting it later is not remediation, because
  the sync service keeps previous versions.
- put a password or `private_key` inline in the config. Refused.
- pass `--allow-multi` on agent-issued SQL. The numbered-file convention
  already gives one query per file.

## The ledger

```bash
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/engine" PYTHONUTF8=1
python -m somsql ledger --summary
```

Every attempt, refusals included, appended to `.som/ledger.ndjson`. Refusals are
the point: one that leaves no trace gets routed around next time.
`ledger --summary` also writes `.som/WRITE_LOG.md` — every refusal and every
authorised write, with its verb, target, rows affected, approver and reason, in
a form a person can read. DELIVER quotes the cost summary into the bundle
appendix so cost is visible per deliverable rather than buried in a warehouse
bill.

## Writes

### The procedure

```bash
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/engine" PYTHONUTF8=1

# 1. see what it would do -- no connection, no SSO prompt, nothing sent
python -m somsql write --file sql/90_fix_dupes.sql --dry-run
```

```
SOMSQL-CONFIRM  읽기가 아닙니다. 진행하려면 확인이 필요합니다.
파일     : sql/90_fix_dupes.sql
작업     : DELETE — 행을 지웁니다
대상     : ANALYTICS.PUBLIC.VISIT_GAP
되돌리기 : Time Travel 로 복구 가능합니다
주의     : WHERE 절이 없습니다 — 대상 테이블의 **모든 행**에 적용됩니다.
문 해시  : 1479c4ba168e

승인하시면 이 명령으로 실행됩니다:
  python -m somsql write --file sql/90_fix_dupes.sql --approve 1479c4ba168e --reason "<왜 필요한지>"

이 해시는 지금 이 문장에만 붙습니다. SQL 을 한 글자라도 고치면 승인은 무효입니다.
```

```bash
# 2. the person reads that, decides, and runs it with the hash and a reason
python -m somsql write --file sql/90_fix_dupes.sql   --approve 1479c4ba168e --reason "2026-09 중복 적재 정리" --approver kykim
```

`--reason` is not optional. It is what explains the change six months later,
and it is checked **before** any connection is opened, so forgetting it costs a
message rather than an SSO popup.

### What the model may and may not do

| | |
|---|---|
| write the SQL, run `--dry-run`, show the block | **yes** — that is the useful part |
| explain what the statement will do and what is risky | yes |
| fill in `--approve` from the block it just printed | **no** |
| suggest a different client or rewrite to dodge the block | **no** |

Running `somsql run` on a file that turns out to write does not fail: it prints
the same `SOMSQL-CONFIRM` block and exits 4. The write is not refused; it is
waiting for a person.

### What is described

Every non-read verb has a sentence, a reversibility flag, and a severity:
INSERT · UPDATE · DELETE · MERGE · TRUNCATE · CREATE · DROP · ALTER · GRANT ·
REVOKE · COPY · PUT · REMOVE · CALL · USE · SET. Four things raise the alarm
level on their own:

- **no WHERE on UPDATE/DELETE** — it applies to every row
- **OR REPLACE** — whatever is there now is gone
- **CALL** — the tool cannot see inside a procedure and says so
- **an unreadable target** — if `somsql` cannot name the object it says so and
  raises the severity rather than printing a guess. A confidently wrong target
  line is worse than no target line

### Pre-existing scripts

The team had scripts reaching Snowflake before this plugin existed. The
PreToolUse guards **warn** about them and let them run: they print what the
sanctioned path would have added (classification, the cost guard, the ledger,
the approval step) and step aside. Installing `som` does not break work that
has nothing to do with `som`.

## Domain vocabulary and the object registry

**This repository names no real table.** It used to: the large-table list, the
warehouse and the code-to-brand mapping were constants in `registry.py`, which
published one company's schema map to anyone who opened the repo and made the
plugin useless to anyone whose tables are named differently.

They live on the machine that runs the queries instead:

```
<project>/.som/registry.json        per project, gitignored
<plugin data>/registry.json         per operator, every project
```

Shape in `standard/registry.json.sample`. `large_tables` is the one that
matters — an object listed there is refused if a query touches it without a
date predicate. `vocabulary` is for codes a reader needs translated
(`SALESORG`, order types) and is documentation, not a rule.

**An empty registry weakens the guard and says so.** `somsql plan` and
`/som:doctor` both print it. The EXPLAIN estimate still works; what goes quiet
is the remark about scanning a large table without a date predicate.

## SQL file convention

`NN_<subject>_<grain>.sql`, **one query per file**, with a header comment block:

```sql
-- source     : ANALYTICS.PUBLIC.ORDER_LINE_HISTORY
-- grain      : 행 1개 = 거래선 × 주
-- date range : 2026-01-01 ~
-- owner      : kykim
-- last verified : 2026-09-09
```

One query per file is not tidiness. It is what lets the EXECUTE stage fan out
one worker per file.
