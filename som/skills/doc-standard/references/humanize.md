# Korean prose pass (`humanize-korean` integration)

Every som deliverable that carries Korean prose to a human reader goes through
one guarded rewrite pass before it is rendered. This file is the canonical
procedure; `doc-standard` and the DATA pipeline's narrative step both point here
rather than restating it.

## Why it sits where it sits

`humanize-korean` is an LLM pass, so it is **not deterministic**. The somdoc
renderer *is* deterministic and is byte-compared by the golden test.

So the pass runs on the **IR, during authoring** — upstream of the renderer:

```
skeleton ─► doc-drafter fills prose ─► [ HUMANIZE PASS ] ─► ir/*.somdoc.json (committed)
                                                                   │
                                                        somdoc build (deterministic)
                                                                   ▼
                                                     HTML / xlsx / docx / pptx
```

Consequences, both load-bearing:

- The committed IR is already humanized, so the golden test stays byte-exact.
- A teammate who re-renders from the committed IR gets identical output without
  re-running any LLM pass.

**Never** run the pass on rendered HTML, on xlsx, or on the payload of a
`somdoc build`. The IR is the only input it ever touches.

## What is sent, and what is structurally excluded

`engine/somdoc/humanize_io.py` decides this in code, not by prompt. Only the
block-type/field pairs in `PROSE_FIELDS` are eligible:

| Block | Fields sent |
|---|---|
| `section` | `intro` |
| `bullets` | `items[].text` |
| `narrative` | `paragraphs[]` |
| `callout` | `body` |
| `decision_request` | `items[].ask`, `items[].rationale` |
| `chart` | `takeaway` |
| `table` / `matrix` / `heatgrid` | `caption`, `note` |
| `risks` | `items[].mitigation` |

Anything whose JSON path contains a `DENY_PATH_TOKENS` segment is never sent,
even if a block type above would otherwise allow it. That list includes
`docmeta`, `appendix_source`, `changelog`, `kpi_tiles`, table `rows` /
`columns` / `cells` / `headers`, `metrics`, `source`, `sql`, `row_grain`,
`unit`, `id`, `key`, `role`, `owner`, and `title`.

Two exclusions deserve their own sentence, because they look like prose and are
not:

- **`definition` and `formula`** in the KPI 정의 대장. Their wording *is* the
  definition. Rewriting them changes the metric, not the style.
- **`row_grain`** ("행 1개 = 거래선 × 주"). It is a contract with the reader.

Fields shorter than 12 characters are skipped — there is nothing to gain.

## Procedure

Run from the project root. Set `SOM` first -- **this is a reference file, so
nothing substitutes the path for you the way it does in a SKILL.md.**
`/som:doctor` prints it on its second line (`plugin  <path>`).


### 1. Extract

```bash
SOM=<플러그인 경로>     # /som:doctor 둘째 줄 `plugin`
PYTHONPATH="$SOM/engine" PYTHONUTF8=1 python -m somdoc.humanize_io extract \
  --ir       ir/<doc>.somdoc.json \
  --out      .som/humanize/<doc>/in.md \
  --manifest .som/humanize/<doc>/manifest.json
```

Exit 4 means no eligible prose — skip the rest, that is a normal outcome for a
table-only document.

The payload is prose only, delimited by `<!-- SOM-F NNNN -->` markers. Do not
add instructions, headers, or commentary to the payload file: the humanizer
strips chatbot-frame sentences and would be reading our scaffolding as content.
Strength and genre are passed at invocation instead (step 2).

### 2. Invoke the `humanize-korean` skill

Invoke the **skill**, not `/humanize` or `/humanize-redo` — those two commands
declare `disable-model-invocation: true` and are reserved for the user.

Hand it the payload path and these settings:

- `장르: 리포트` for DATA narratives and KPI documents;
  `장르: 공적` for R&R and charter documents (they are approval documents).
- `강도: 보수` — always. These are exec-facing governance documents, and
  conservative strength is what keeps content anchors in their original wording.
- Leave the route to the skill's own `route_hint`. Do not force `--strict`
  unless a previous pass came back `hold_and_report`.

It writes its result to **`_workspace/{YYYY-MM-DD-NNN}/final.md`, relative to
cwd**, with a trailing `<!-- HUMANIZE-SUMMARY ... -->` comment block. Read the
run_id it prints in its status line; do not guess it. The summary block is
ignored by our parser, so `final.md` can be passed through unedited.

If it reports `verdict=hold_and_report`, stop and report that to the user before
applying anything.

### 3. Apply, with invariants enforced

```bash
SOM=<플러그인 경로>     # /som:doctor 둘째 줄 `plugin`
PYTHONPATH="$SOM/engine" PYTHONUTF8=1 python -m somdoc.humanize_io apply \
  --manifest  .som/humanize/<doc>/manifest.json \
  --humanized _workspace/<run_id>/final.md \
  --report    .som/humanize/<doc>/report.json
```

Per field, the rewrite is accepted only if all of these hold:

| Invariant | Rationale |
|---|---|
| Every number survives, as a multiset | `"4명에서 5명으로"` becoming `"한 명 늘리는"` loses the ask. Thousands separators are normalised, so `410,042` → `410042` passes. |
| No number is invented | A rewrite that turns 57% into 62% is a fabrication, not a style edit. |
| Every `{{m:...}}` metric ref survives, unchanged | A literal substituted for a ref silently decouples the sentence from `metrics.json`. |
| Every protected ASCII identifier survives | `SALESORG`, `ANALYTICS`, `DIM_PRODUCT`, `DO_TO_LABEL`, `MOB`. Lowercase words of ≤4 chars are ordinary English and are not protected. |
| Length ratio within `[0.45, 1.60]` | The floor is loose on purpose: stripping Korean AI padding legitimately cuts 30–45%. The ceiling is tight because invented padding is always suspicious. |
| Non-empty | — |

A field that fails **any** check is rolled back to its original text and listed
in the report. Meaning preservation outranks naturalness, and that call is made
by code here, never by a prompt.

### 4. Read the exit code

| Exit | Meaning | What to do |
|---|---|---|
| `0` | Every field either applied cleanly or was already good | Continue to render |
| `2` | `SOMDOC-HUMANIZE-HOLD` — something was rolled back, a marker went missing, or a stray marker appeared | The IR is safe (originals kept). Report the rolled-back paths and their reasons to the user, then continue to render **or** re-run step 2 for those fields. Do not hand-edit the IR to force a rewrite through. |
| `4` | Nothing eligible | Continue to render |

Missing or stray markers mean the marker scaffolding was disturbed, not that
the prose was bad. Re-run step 1 and step 2 in that case.

## Reporting to the user

Report the pass in one line plus the exceptions, never the full diff:

```
윤문: 6개 필드 중 5개 적용 (문장 기준 84%), 1개 롤백
  ROLLBACK sections[0].blocks[0].items[0].ask — 숫자 유실 ['4','5']
```

`.som/humanize/<doc>/report.json` holds the per-field detail. It is run state,
gitignored, and is referenced from `MANIFEST.json` at DELIVER so the bundle
records that the pass ran and what it declined.

## Boundary

This procedure is som's use of a third-party plugin. `humanize-korean` remains
authoritative for its own behaviour, taxonomy, and route selection — see
`orca skills`-style deference: read its SKILL.md rather than restating its rules
here. som owns exactly two things: which fields are eligible, and which
rewrites are accepted.
