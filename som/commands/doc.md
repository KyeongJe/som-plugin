---
description: SOM 팀 운영 문서(R&R · KPI · 팀 차터)를 SDS v1 표준으로 생성한다. 산출물은 로컬 파일.
argument-hint: "[rnr|kpi|charter] [원천 xlsx 경로 또는 설명]"
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, Skill]
---

# /som:doc

Invoke the `doc-standard` skill and follow its procedure end to end.

## Input
$ARGUMENTS

## Steps
1. If no doc type was given, ask which of `rnr` / `kpi` / `charter`.
2. Run INTAKE. **Do not proceed without the decision being requested.** A
   document that invents its own ask is worse than no document.
3. Follow `doc-standard`: skeleton -> IR -> humanize -> validate -> build.
4. Report the bundle paths. Do not copy anything to a shared folder -- that is
   a human action.

## Report back
- one line: doc type, sections, blocks, `ir_sha256` prefix, output sizes
- the humanize line: applied / rolled back, and the reason for each rollback
- any ATTEST finding: roster orphans, percentage allocations that do not sum
  to 100, unsourced claims
- the decision the document asks for, quoted

Run `/som:doctor` first if the environment has not been checked in this session.
