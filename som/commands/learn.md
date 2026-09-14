---
description: 이번에 알게 된 것에서 재사용 가능한 패턴을 뽑아 다음 런이 읽게 만든다. 근거 없는 교훈은 거부한다.
argument-hint: "[이번에 알게 된 것] · list · brief <다음 할 일> · retire <id> · skills"
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, Skill, AskUserQuestion]
---

# /som:learn

Invoke the `learn` skill and follow its procedure.

## Input
$ARGUMENTS

## Non-negotiables

1. **Candidates come from the record, not from memory.** Read `.som/state.json`
   for retried tasks, out-of-scope writes, human interventions and changed
   measurements. What went smoothly is the baseline, not a pattern.
2. **Every pattern cites evidence and the evidence is checked.** `file` and
   `test` must exist on disk, `run` must be in this project's state. A
   `measurement` alone is not enough -- it is a number with nothing behind it.
3. **Refusal is the normal outcome.** Most candidate lessons are proverbs. When
   `propose()` refuses, fix the pattern or drop it. Do not look for a way past
   the gate; the gate is the entire feature.
4. **Never write outside the library.** Not `~/.claude/settings.json`, not
   CLAUDE.md, not Claude Code's own memory. Those belong to the user.
5. **Never auto-share.** Whether the project-scope file gets committed is the
   user's call.
6. **A generated skill is shown before it is written.** `learn skills plan`
   prints what would be created and why the rest was held; only then, and only
   with the user's yes, `learn skills promote --yes`. Writing into someone's
   home directory and telling them afterwards is the wrong order.
7. **Never touch a skill this plugin did not generate.** The
   `som-generated: true` marker is the licence. Same name, no marker: report
   it, leave it.

## Steps
1. Read the run record. List candidates with what each one is grounded in.
2. For each, draft `{title, trigger, action, why, triggers[], evidence[]}`.
   The action must be an imperative sentence someone could follow; "주의하라"
   is not one.
3. `propose()` each. Report what was stored, what was merged into an existing
   pattern (a repeat is confirmation, so it raises confidence), and what was
   refused with the reasons verbatim.
4. Verify reuse: run `lib.brief({...})` with a plausible next task and show
   whether the new pattern actually surfaces. If it does not, fix `triggers`,
   not the pattern text.
5. If the user asked about skills, or a cluster looks mature, run
   `learn skills plan`, show it verbatim, and stop. Report both halves — what
   is ready and why each held cluster is not — then ask.
5. Say where the file is and that committing the project-scope one is a choice.

## Reading and pruning
- `--list` shows live patterns with confidence, uses and losses, plus retired
  ones separately.
- `--retire <id>` takes one out of the match set by hand. It stays on file --
  a pattern that stopped working is still information about this repo.
- Retirement is normally automatic: confidence below 15 after repeated losses.
