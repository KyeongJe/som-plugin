---
description: 하고 싶은 일을 자연어로 말하면 적절한 레시피로 오케스트레이션을 돌린다 (문서 · PRD · 밤샘 검증 · 엑셀 분석 · Snowflake 분석 · 내부 도구).
argument-hint: "[하고 싶은 일] 예: R&R 문서 만들어줘 / PRD 필요해 / 이 엑셀 분석해줘 / 챗봇 밤새 확인해줘"
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, Skill, AskUserQuestion]
---

# /som

Invoke the `conduct` skill and follow its procedure.

This command is a shortcut, not the only door. The skill also fires when the
user simply names the work ("R&R 문서 만들어줘", "이 엑셀 분석해줘",
"밤새 확인해줘") -- no slash required.

## Input
$ARGUMENTS

## Steps
1. Preflight. If the runtime is down, orchestration is off, or this terminal is
   itself a dispatched worker, **stop and say so.** Do not work around it.
2. Pick a recipe from what the user said. Ambiguous, ask once. No match, show
   the closest options rather than inventing a recipe.
3. Ambiguity gate. Run `clarityGate({project, objective})`. If it is not `ok`,
   hand off to the `interview` skill and stop here -- work starts only below 5%
   ambiguity, and the first thing that skill prints is the threshold. Tell the
   user that is what is happening rather than going quiet.
4. Recipe `asks`. `missingAsks(recipe, slots)` lists the slots still empty and
   why each matters. Fill what the interview spec already answered; ask the
   rest **in one AskUserQuestion**, showing each `why`. A required answer left
   blank stops the run -- do not fill it in on the user's behalf. If the
   `analyze` source turns out to be Snowflake, switch to `data`.
5. Show the plan -- nodes, longest chain, first wave, rough duration -- then run.
6. Report: completed/total, files produced, per-worker three-sentence summary,
   and any terminal that was not accounted for.

## While it runs
- A worker question blocks that worker. Answer before acknowledging.
- An escalation never releases a terminal. The worker is alive.
- Never retry a failure with the same settings.
- A quiet window is a checkpoint. 15-60 minutes of silence on a coding task is
  normal.
