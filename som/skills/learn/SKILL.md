---
name: learn
description: |
  Extract reusable patterns from what just happened and put them where the
  next run will see them. A finished `conduct` run already records what it
  measured by itself -- out-of-scope writes, repeated attempts, nodes a person
  had to unblock -- so this skill is for the lessons only a person can see. Reads a finished run, a debugging session, or a
  review, proposes patterns with a trigger, an action and cited evidence, and
  refuses anything it cannot check. Matched patterns are injected into future
  worker briefs, graded against the outcome of the run that carried them, and
  retired when they stop working.

  Use when the user says "배운 것 정리", "패턴 추출", "이거 기억해", "학습",
  "스킬로 만들어", "다음에도 이렇게", "회고", "learn", "패턴 확인",
  "뭘 배웠어", or right after a run finishes and something non-obvious was
  discovered.

  A cluster of patterns that keeps proving itself can be promoted into a real
  personal skill under the user's own skills directory, so what was learned
  applies in every conversation rather than only inside a som run. Promotion is
  earned, capped, always shown before it happens, and undone by deleting a
  folder.

  Boundary. This skill writes to the pattern library, and -- only on an
  explicit promote -- to `~/.claude/skills/som-*/SKILL.md`. It never edits
  `~/.claude/settings.json`, never writes CLAUDE.md, never touches Claude
  Code's own memory files, and never modifies a skill it did not generate.
  It never calls bkit.
argument-hint: "[이번에 알게 된 것] · list · brief <다음 할 일> · retire <id> · skills"
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion]
---

# learn — 이번에 알게 된 것을 다음 런이 읽게 만든다

## 무엇을 하는가

```
끝난 런 · 디버깅 · 리뷰
        │
        ▼  후보 뽑기 (사람이 아니라 근거에서)
   trigger + action + evidence
        │
        ▼  품질 게이트 (코드가 판정)
   근거 확인 · 일반론 거부 · 자격증명 거부 · 크기 상한 · 중복 병합
        │
        ▼  .som/patterns.json  또는  플러그인 데이터
        │
        ▼  다음 런의 brief() 가 최대 3개를 주입
        │
        ▼  그 태스크의 성공/실패로 채점 → 신뢰도 이동 → 계속 빗나가면 내려감
```

> 벤치마크: `oh-my-claudecode` 의 `learner`. 메타데이터 모양, exact → path →
> fuzzy 매칭 단계와 신뢰도 점수, 내용 해시 중복 제거, scope 우선순위를 가져왔다.
> 다른 점 넷은 아래 **게이트** 절에 있다.

> **설정할 것이 없습니다.** 아래 명령의 `${CLAUDE_PLUGIN_ROOT}` 는 Claude Code 가
> 이 스킬을 읽어 들일 때 실제 경로로 바꿔서 넘겨줍니다 — 그대로 복사해 쓰면 됩니다.
>
> ```bash
> node "${CLAUDE_PLUGIN_ROOT}/bin/som.mjs" --help
> ```
>
> 이 파일을 저장소에서 **직접 열어** 읽는 경우에만 치환이 일어나지 않습니다.
> 그때는 `/som:doctor` 둘째 줄(`plugin  <경로>`)의 값으로 손수 바꾸세요.

## 게이트 — 대부분의 후보는 거부된다

거부는 오류가 아니라 **정상**이다. 후보 대부분은 격언이고, 격언 도서관은 빈
도서관보다 나쁘다 — 모든 브리핑에서 토큰을 먹으면서 아무것도 알려주지 않는다.

| | 벤치마크 | som |
|---|---|---|
| 근거 | 필드 자체가 없다 | **필수, 그리고 디스크에서 확인한다** |
| 품질 | 추출 시점에 점수 1회 (이후 갱신 없음) | **결과로 이동하는 신뢰도** |
| 안 먹히는 패턴 | 감지 장치 없음 · 사람이 `/skill remove` | **결과로 자동 내림 — 기록은 남긴다** |
| 자격증명 | 처리 없음 | **쓰기 시점에 전 필드 거부** |

> 매칭 사다리도 그대로는 아니다. 벤치마크의 2단은 **글롭·정규식** 패턴 매칭이고
> 여기 2단은 경로 basename 매칭이다. 즉 정규식 trigger 라는 기능은 안 가져왔다.

### 1. 근거가 있고, 확인된다

```json
"evidence": [
  {"kind": "test", "ref": "test/e2e-conduct.mjs", "note": "절대경로로 고친 커밋"},
  {"kind": "run",  "ref": "run_44a255417f84"},
  {"kind": "file", "ref": "lib/orca/exec.mjs"},
  {"kind": "measurement", "ref": "73초 → 41초", "note": "wave 2회"}
]
```

`file` · `test` 는 **프로젝트 안의 실제 파일**이어야 한다 — 디렉토리도, `.` 도,
`../` 로 위로 나간 경로도 거부된다. `run` 은 **이 프로젝트가 실제로 기록한 런**이어야
한다 (모양만 맞는 id 는 거부). `measurement` 만으로는 안 된다 — 뒤에 아무것도 없는
숫자다.

**"쿼리는 작게 나누는 게 좋다" 는 패턴이 아니다.** 언제 그런지, 무엇을 하라는
것인지, 어디서 그걸 알게 됐는지가 없으면 저장하지 않는다.

### 2. action 은 실행 가능한 문장이다

| 거부 | 통과 |
|---|---|
| "테스트를 잘 작성하라" | "verifier 워커는 구현 코드를 읽지 말고 원자료에서 다시 세게 한다. 같은 코드를 두 번 읽는 건 검증이 아니다." |
| "주의하라" | "spec 에 파일 경로를 쓸 때 절대경로로 적는다. 워커 cwd 는 Orca 워크트리다." |
| "best practice 를 따르라" | "`orca.cmd` 가 아니라 형제 `.exe` 를 해석한다. `.cmd` 는 orchestration send 를 거부한다." |

### 3. 자격증명은 거부된다

패턴은 **앞으로의 모든 브리핑에 들어가는 지속 텍스트**다. 한 번 새는 게 아니라
영원히 샌다. private key 헤더, `token:`/`password:`/`pwd:` 류, AWS 키 모양,
GitHub·Slack 토큰, `scheme://user:pass@host` 형태가 감지되면 저장하지 않는다.
**`triggers` 와 `tags` 도 검사한다** — 둘 다 디스크에 남고 `tags` 는 매칭 키다.

정규식이 이길 수 없는 영역은 정직하게 남긴다: **base64 로 인코딩된 값, BEGIN 헤더
없는 키 본문은 통과한다.** 스캐너는 실수를 잡는 장치이고 결심한 유출을 막는 장치가
아니다.

### 4. 같은 패턴이 또 오면 확인(win)이다

내용 해시(triggers + action)가 같으면 새로 만들지 않고 **기존 패턴의 신뢰도를
올리고 근거를 합친다.** 두 번 일어난 일은 두 번 적을 게 아니라 한 번 더 확실한
것이다. `id` 를 직접 넘겨도 무시된다 — id 는 내용에서 파생된다.

**단, 이미 내려간(retired) 패턴이면 거부한다.** 예전엔 "저장됨" 으로 보고하면서
실제로는 매칭에 들어가지 않았다. 되살릴 값이 있으면 무엇이 달라졌는지 `action` 에
반영해 새 패턴으로 내라.

### 5. 크기와 트리거에 상한이 있다

| | 상한 | 왜 |
|---|---|---|
| `action` | 1,200자 | 매칭될 때마다 브리핑에 그대로 들어간다 |
| `title` / `trigger` / `why` | 120 / 300 / 600자 | 같은 이유 |
| `triggers` 각 항목 | **3자 이상**, 흔한 말 금지 | `"a"` 하나로 모든 브리핑에 걸렸고, 실제 패턴을 3칸에서 밀어냈다 |
| 브리핑 전체 | 4,000자 | 넘으면 뒤쪽 패턴을 생략하고 그 사실을 적는다 |

`action` 은 **질문이면 거부**된다 — 브리핑에 "할 것:" 으로 찍히기 때문이다.
그리고 파일·명령·식별자·기술 용어·수치 중 최소 하나를 짚어야 한다. 아무것도
안 짚는 문장은 이 저장소에 대한 말이 아니다.

## 절차

### 1. 후보 뽑기 — 기억이 아니라 기록에서

```bash
node "${CLAUDE_PLUGIN_ROOT}/bin/som.mjs" paths            # 이 프로젝트의 기록 위치
```

무엇을 보는가:

- **재시도된 태스크** — 첫 시도가 왜 실패했나. 그게 거의 항상 패턴이다
- **선언 글롭 밖에 쓴 워커** — 계약이 틀렸거나 spec 이 애매했다
- **사람이 개입한 지점** — 게이트를 뒤집었거나 escalation 에 답한 곳
- **측정값이 바뀐 것** — 73초였다가 41초가 됐으면 무엇이 바뀌었나
- **"그건 그렇게 안 돼" 라고 알게 된 런타임 사실** — 이게 가장 값지다

무엇을 안 보는가: 잘 돌아간 것. 성공은 패턴이 아니고 기준선이다.

### 2. 제안 — 거부되면 이유가 온다

```bash
node "${CLAUDE_PLUGIN_ROOT}/bin/som.mjs" learn propose   --title "워커에게는 절대경로를 준다"   --trigger "워커 spec 에 파일 경로를 쓸 때"   --action "상대경로를 쓰지 말고 절대경로로 적는다. 워커 cwd 는 Orca 워크트리다."   --why "첫 e2e 에서 워커가 플러그인 디렉토리에 파일을 썼다"   --triggers "절대경로,worktree,워커 spec"   --evidence '[{"kind":"file","ref":"<이 프로젝트 안의 실제 파일>"}]'   --scope project
# 거부되면 이유를 줄마다 출력하고 exit 2 로 끝납니다.
```

거부되면 **고쳐서 다시 내거나, 패턴이 아니라고 인정하고 버린다.** 게이트를
우회할 방법을 찾지 마라 — 게이트가 이 기능의 전부다.

### 3. scope 고르기

| | 어디 | 언제 |
|---|---|---|
| `project` | `<프로젝트>/.som/patterns.json` | 이 저장소·이 데이터·이 팀에 대한 것. **커밋할 가치가 있는 쪽** |
| `user` | 플러그인 데이터 디렉토리 | 이 방식으로 일하는 것 자체에 대한 것. 프로젝트를 옮겨도 따라온다 |

애매하면 `project`. 동점이면 project 가 이긴다 — 이 저장소에서 배운 건 이
저장소에 대한 것이다.

### 4. 확인 — 다음 런이 실제로 보는가

```bash
node "${CLAUDE_PLUGIN_ROOT}/bin/som.mjs" learn brief "<다음에 할 일>"
node "${CLAUDE_PLUGIN_ROOT}/bin/som.mjs" learn list          # --all 이면 내린 것까지
```

`(매칭 없음)` 이면 `triggers` 가 실제 쓰는 말과 안 맞는다. 패턴 문장을 고치는
게 아니라 **triggers 를 고친다.**

## 재사용 — 자동이다

- `conduct.brief()` 가 노드마다 매칭해서 **최대 3개**를 주입한다. 3개다. 10개면
  기억한 교훈이 실제 작업을 묻는다. 3개보다 많이 매칭되면 도서관이 너무
  두루뭉술한 것이지 작은 게 아니다
- 주입된 패턴 id 는 **노드별로** 기록되고, `finish()` 가 **그 태스크의 결과로**
  채점한다 — 성공이면 +8, 실패면 −20. 런 단위로 묶어 채점하던 때는 한 노드가
  실패하면 그 런이 스친 무관한 패턴까지 전부 −20 을 먹어서, 부분 실패 두 번에
  도서관이 비었다
- 끝나지 않은 태스크는 어느 쪽으로도 채점하지 않는다 — 미완료는 조언에 대한
  증거가 아니다
- 손실이 이득보다 훨씬 비싸다. 한 번 잘못 이끈 패턴은 조용히 도운 몇 번보다
  이미 더 많은 피해를 줬다
- 신뢰도가 15 아래로 떨어지면 **내려간다.** 지우지 않는다 — 안 먹히게 된 패턴도
  이 저장소에 대한 정보다

## 절대 하지 않는 것

- **근거 없이 저장하지 않는다.** 기억은 근거가 아니다
- **일반론을 저장하지 않는다.** 모든 프로젝트에 해당하는 말은 이 프로젝트에
  대해 아무것도 말하지 않는다
- **성공을 패턴으로 만들지 않는다.** 잘 된 건 기준선이다
- **게이트를 우회하지 않는다.** 거부되면 고치거나 버린다
- **사람의 설정을 건드리지 않는다.** `~/.claude/settings.json`, `CLAUDE.md`,
  Claude Code 자체 메모리는 사용자 것이다. 유일한 예외는 `learn skills promote`
  가 만드는 `~/.claude/skills/som-*/` 이고, 그건 **추가**이지 수정이 아니다
- **내가 안 만든 스킬은 절대 건드리지 않는다.** `som-generated: true` 표식이
  없는 파일은 이름이 같아도 그대로 둔다
- **자동으로 공유하지 않는다.** `project` 스코프 파일을 커밋할지는 사람이 정한다

## 스킬로 승격 — 초개인화

패턴은 som 런 안에서만 보인다. `conduct` 가 브리핑을 만들 때 주입되기 때문에,
사용자가 그냥 Claude 를 열어 다른 일을 하면 배운 게 하나도 안 보인다.

반복해서 증명된 **패턴 묶음**은 사용자 개인 스킬 디렉토리의 진짜 `SKILL.md`
로 승격된다. 거기가 Claude Code 가 묻지 않고 읽는 유일한 자리다.

```bash
node "${CLAUDE_PLUGIN_ROOT}/bin/som.mjs" learn skills plan       # 아무것도 안 씀
node "${CLAUDE_PLUGIN_ROOT}/bin/som.mjs" learn skills promote --yes
node "${CLAUDE_PLUGIN_ROOT}/bin/som.mjs" learn skills list
node "${CLAUDE_PLUGIN_ROOT}/bin/som.mjs" learn skills retire som-<이름>
```

**항상 `plan` 을 먼저 돌려 사용자에게 보여주고, 승인받은 다음에만 `promote`
한다.** 사람의 홈 디렉토리에 파일을 만드는 일을 먼저 하고 나중에 알리는 건
순서가 틀렸다.

### 승격 기준 — 판단이 아니라 산술

| | 왜 |
|---|---|
| 관련 패턴 **3건 이상** | 하나는 관찰이고, 셋은 일하는 방식이다 |
| 평균 신뢰도 **70 이상** | 반복해서 맞아야만 올라가는 숫자다 |
| 합계 적용 **6회 이상** | 실제로 쓰인 적이 있어야 한다 |
| 구성원 전원이 **한 번은 맞음** | 한 번도 안 맞은 게 섞이면 묶음 전체를 보류 |
| 맞음 ≥ 빗나감 | 더 자주 틀리는 습관은 습관이 아니다 |
| 전체 **8개까지** | 스킬 설명문은 **모든 대화에서 항상 읽힌다.** 늘리는 대신 안 쓰는 걸 retire 한다 |

보류된 묶음은 사유가 같이 나온다. 통과만 알려주는 게이트는 아무것도 못
가르친다.

### 안전

- 이름은 **항상 `som-` 으로 시작**한다 — 사용자가 직접 만든 스킬을 가릴 수 없다
- `som-generated: true` 표식이 **허가증**이다. 그게 없으면 덮어쓰지도 지우지도
  않고, 이름이 같으면 그대로 두고 보고만 한다
- 디렉토리당 `SKILL.md` **하나뿐.** 스크립트도 실행 파일도 없다
- 되돌리기는 폴더 삭제 또는 `learn skills retire`
- 생성된 파일은 출처 패턴 id·신뢰도·적용 횟수를 본문에 싣고, **"안 맞으면
  따르지 말고 말해 달라"** 는 문장으로 끝난다. 관찰에서 나온 것이라 틀릴 수 있다

## 상태

| 파일 | 내용 |
|---|---|
| `<프로젝트>/.som/patterns.json` | 이 저장소가 가르친 것 (커밋 가능) |
| `<플러그인 데이터>/patterns.json` | 이 운영자가 배운 것 |
| `~/.claude/skills/som-*/SKILL.md` | 승격된 개인 스킬 |

둘 다 `tmp → fsync → rename` 으로 쓴다. 내려간 패턴도 남으므로 "왜 이제 이
패턴을 안 쓰나" 에 답할 수 있다.
