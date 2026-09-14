---
description: 심층 인터뷰. 구성을 확인하고 라운드마다 질문 하나로 모호도를 낮춘다. 5% 미만이 되기 전에는 작업을 시작하지 않는다.
argument-hint: "[하고 싶은 일] · show <slug> 로 이어보기"
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, Skill, AskUserQuestion]
---

# /som:interview

Invoke the `interview` skill and follow its procedure exactly.

## Input
$ARGUMENTS

## Non-negotiables

1. **Phase 0 first.** Resolve the threshold and emit `모호도 문턱: N% (출처: ...)`
   as the very first line, before any greeting, state write, question or score.
   Do not use a hardcoded 5% -- read it, because settings may have tightened it.
2. **Round 0 before scoring.** Enumerate the top-level components and get them
   confirmed. Overall ambiguity is the **worst** active component, not the mean,
   so a detailed component must not stand in for a vague sibling.
3. **One question per round.** Head every question with
   `Round n | 대상 | 차원 | 지금 여기인 이유 | 모호도: n% (문턱 5%)`.
   Two questions in one round makes it impossible to tell which answer moved
   the number.
4. **Show the score after every answer.** Use the engine's renderer; do not
   reformat the table. Say the threshold again each round -- never leave the
   user wondering why work has not started.
5. **Never loosen the threshold.** Not by settings, not on request. The honest
   exit is to mark an unknown as 미확정 and record it, which is a legitimate
   answer and does lower ambiguity. Offer that instead.
6. **Score honestly.** A dimension with a written `gap` is capped at 0.89 by
   `lib/domain/clarity.mjs` and the cap is printed. Inflating to reach 5% only
   produces a table that contradicts itself.
7. **Round 20 is a stop, not a licence.** Write the spec as pending, say what
   is unresolved, and do not start work.

## Steps
1. Phase 0: threshold. Emit the marker line.
2. Detect greenfield vs brownfield (`explore` the cwd; existing source plus a
   request to modify it means brownfield, which adds the 기존 시스템 dimension
   at weight 0.15).
3. Round 0: topology confirmation. Lock it.
4. Loop: target the weakest active component × dimension, rotating between
   similarly weak components; ask; score; record via `Interviews.addRound()`;
   print `report()`. Challenge modes at rounds 4 / 6 / 8.
5. When ambiguity < threshold: crystallise `.som/specs/interview-{slug}.md`,
   show it, and ask for explicit approval.
6. On approval, hand off to `conduct`. The spec's answers fill the recipe's
   `asks`, so do not re-ask them. `conduct.plan()` re-reads the interview record
   and checks the threshold itself -- approval alone does not open that gate.

## State
`.som/interview/{slug}.json` holds every round's question, answer and score, so
the decision to start is reconstructible rather than remembered. Resume with
`--resume <slug>`; never re-ask a question that record already answers.
