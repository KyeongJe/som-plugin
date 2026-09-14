---
name: doc-standard
description: |
  Produce SOM team governance documents to the SOM Doc Standard (SDS v1) --
  R&R definitions, KPI registers, and team charters -- as a validated JSON IR
  rendered to a self-contained single-file HTML plus xlsx (and docx where
  implemented). Deliverables are local files; nothing is published.

  Use this ONLY when the user names a SOM document type or runs a /som: command.
  Triggers: "R&R 문서", "KPI 대장", "팀 차터", "팀 정의 문서", "SDS", "somdoc",
  "som doc standard", "R&R document", "KPI register", "team charter".

  Boundary. bkit:bkit-templates writes PDCA plan/design/analysis/report markdown
  for software features; this skill produces organisational governance documents
  as a typed IR rendered to four formats, shares no files or vocabulary with it,
  and never reads or writes .bkit/. The Orca `orchestration` skill is
  authoritative for Orca CLI grammar and multi-agent coordination mechanics;
  this skill needs neither and runs single-agent by design. `orca-cli` publishes
  shareable artifact links; this skill deliberately does not publish -- local
  files only.
argument-hint: "[rnr|kpi|charter] [source xlsx or description]"
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash]
---

# SOM Doc Standard (SDS v1)

## The one rule that shapes everything else

**The agent authors a JSON IR. It never writes HTML, xlsx, docx, or pptx.**

```
skeleton ─► you fill the IR ─► [humanize] ─► ir/*.somdoc.json ─► somdoc build ─► 4 formats
                                                  (committed)      (deterministic)
```

Consequences, both load-bearing:

- A teammate re-rendering the committed IR gets **byte-identical** output with no
  LLM pass. That is what makes this a standard rather than a style suggestion.
- Skeletons, themes, and emitters are plugin files, so HTML and xlsx cannot
  drift apart. Authoring them separately guarantees they will.

If you find yourself writing a `<table>` or a `<style>`, stop. That belongs to
the emitter.

## Setup

**Nothing to set up.** Claude Code substitutes `${CLAUDE_PLUGIN_ROOT}` with the
real path when it loads this skill, so the block below works as written.

```bash
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/engine" PYTHONUTF8=1
python -m somdoc --help
```

The substitution happens in the *skill text*, not in the shell. Two consequences
worth knowing: a block copied out of the repository file by hand still contains
the literal `${CLAUDE_PLUGIN_ROOT}` and will give you `PYTHONPATH=/engine` and
`No module named somdoc`; and `${CLAUDE_PLUGIN_ROOT}` is not an environment
variable, so exporting nothing and relying on the shell does not work either.
`/som:doctor` prints the path on its second line if you ever need it by hand.

Nothing here needs a `pip install`: the HTML path is Python standard library
only. Run `/som:doctor` if anything looks off.

## Procedure

### 1. INTAKE — settle these before writing any IR

Ask, and record the answers in the IR's `docmeta`:

| Needed | Why it gates |
|---|---|
| doc type: `rnr` / `kpi` / `charter` | picks the skeleton |
| audience: 팀원 / 유닛장 / COO | picks tone and depth |
| **the decision being requested** | R5 rejects a document without one |
| roster / metric source (xlsx path + sheet, or inline) | R3 and ATTEST need named sources |
| version (`v1.2`) and as-of date | R4 |

**Do not invent the ask.** A document that manufactures its own decision request
is worse than no document. If the user has not said what they want approved,
ask them.

### 2. BLUEPRINT — instantiate the skeleton

```bash
cat "${CLAUDE_PLUGIN_ROOT}/standard/skeletons/rnr.skeleton.json"     # rnr | kpi | charter
```

Tag every section `content-required`, `derived`, or `optional`. **Every
`content-required` section must have a named source before you start filling
prose.** This is the anti-hallucination gate for team documents: it removes the
room to invent.

Existing workbook as input? Start from it rather than retyping:

```bash
python "${CLAUDE_PLUGIN_ROOT}/standard/scripts/ingest_rnr_xlsx.py" \
  --xlsx "<file>.xlsx" --out ir/som-rnr.somdoc.json --as-of 2026-09-09 --version v1.0
```

It computes what it can (`derived` blocks) and leaves judgement as explicit
`TODO`. Fill the TODOs; do not delete them.

### 3. EXECUTE — fill the IR

Block types: `decision_request`, `kpi_tiles`, `bullets`, `narrative`, `callout`,
`table`, `matrix`, `heatgrid`, `chart`, `diagram`, `risks`, `changelog`,
`appendix_source`. See `standard/schema/somdoc.schema.json` and the worked
example at `standard/examples/rnr.example.somdoc.json`.

Non-negotiables the validator enforces:

- **`table.row_grain`** — "행 1개 = 팀원 1명". Misreading the row unit is the
  most common way one of these documents gets misused.
- **`chart.takeaway`** — the conclusion, rendered above the chart in bold. A
  chart without one is a puzzle.
- **numbers in prose** go in as `{{!m:key}}` references resolved from `metrics`,
  never typed twice. Every metric carries a `formula`.
- **`decision_request`** — within the first three sections, at most three asks.

Chart kinds: `bar`, `hbar`, `line`, `dot-strip`, `heat-grid`. There is no pie,
no donut, and no stacked area, on purpose. Four series maximum -- split the
chart rather than adding a fifth colour.

Validate as you go:

```bash
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/engine" PYTHONUTF8=1
python -m somdoc validate ir/som-rnr.somdoc.json --draft   # mid-authoring
python -m somdoc validate ir/som-rnr.somdoc.json           # before rendering
```

### 4. Korean prose pass

Every document that carries Korean prose to a human goes through the guarded
`humanize-korean` pass. Full procedure: `references/humanize.md`.

**This step is optional and depends on a plugin som does not ship.**
`humanize-korean` is a separate marketplace plugin. If it is not installed,
skip straight to render — the document is correct without it, just stiffer
Korean. Nothing downstream requires it, and `somdoc build` does not check.

Check first, and say which branch you took:

```bash
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/engine" PYTHONUTF8=1
# 설치되어 있으면 이 경로, 아니면 건너뛰고 렌더로.
python -m somdoc.humanize_io extract --ir ir/<doc>.somdoc.json \
  --out .som/humanize/<doc>/in.md --manifest .som/humanize/<doc>/manifest.json

# humanize-korean 스킬을 강도: 보수 로 호출한다 (/humanize 커맨드가 아니라 스킬).
# 그 스킬이 상태 줄에 run_id 를 출력한다. 추측하지 말고 그 값을 쓴다.

python -m somdoc.humanize_io apply --manifest .som/humanize/<doc>/manifest.json \
  --humanized _workspace/<run_id>/final.md --report .som/humanize/<doc>/report.json
```
Exit 2 is `SOMDOC-HUMANIZE-HOLD`: some fields were rolled back and the IR kept
the originals. Report which paths and why, then continue or re-run. **Never
hand-edit the IR to force a rejected rewrite through.**

### 5. ATTEST — the checks that are specific to team documents

- every person named exists in the roster source (**zero orphans**)
- **per-person task percentages sum to 100** where a numeric allocation is given;
  report the people whose allocation is unspecified rather than passing silently
- zero claims without a named source
- the `decision_request` block exists and sits near the top

### 6. DELIVER

```bash
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/engine" PYTHONUTF8=1
python -m somdoc build ir/<doc>.somdoc.json --emit html,xlsx \
  --out "docs/$(date +%F)_<slug>" --as-of 2026-09-09
```

Bundle layout, and what each format is *for*:

| File | Role |
|---|---|
| `*.html` | **reading artifact** -- self-contained, opens with no licence and no network |
| `*.xlsx` | **data artifact** -- every number printed in the HTML is a real cell here |
| `MANIFEST.json` | `ir_sha256`, engine version, sources, as-of, humanize report |
| `ir/*.somdoc.json` | the reproducible input |

Then stop and hand the paths to the user. **Copying to a shared folder is a
human action.** There is no publish path in this plugin.

## Theme

- `paper` — team governance documents. Warm ground, red accent.
- `console` — data reports and KPI documents. Cool ground, blue accent.

Both are token sets in `standard/themes/`. Do not hand-pick colours: use
`var(--sds-*)`.

## Two things that look like prose and are not

Never rewrite these, and never let the humanize pass touch them (it is
structurally blocked from doing so):

- **KPI `definition` and `formula`** — the wording *is* the metric. Changing it
  changes what is measured.
- **`row_grain`** — it is a contract with the reader.

## Verifying a change to the standard itself

```bash
export PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/engine" PYTHONUTF8=1
python engine/tests/test_ir.py                    # IR rules + golden render
python -m somdoc golden standard/examples/rnr.example.somdoc.json \
  --against standard/examples/golden/rnr.html --as-of 2026-09-09
```

The golden test byte-compares the HTML. If it fails and the change was
intended, re-run with `--update` and commit the new reference in the same
commit as the change that caused it.

`.xlsx` is intentionally **not** byte-compared: zip entries carry timestamps, so
file bytes differ between runs for reasons unrelated to the document.
`content_digest()` hashes sheet names, coordinates, and values instead.
