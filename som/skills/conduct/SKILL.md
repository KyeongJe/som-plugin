---
name: conduct
description: |
  Run a piece of SOM work as a supervised multi-agent Orca run: interview for
  what is missing, pick a recipe, build the task DAG, launch Claude workers in
  waves, settle every worker_done, and report. Recipes cover team documents,
  PRDs with screen and layout detail, repeated verification loops that can run
  overnight, analysis of an Excel or CSV file, and Snowflake analysis.

  Use when the user names one of those work products, whether or not they type
  a slash command: "R&R 문서", "KPI 대장", "팀 차터", "조직 R&R", "PRD",
  "화면 구성", "와이어프레임", "요구사항 문서", "엑셀 분석", "CSV 분석",
  "스프레드시트 집계", "Snowflake 분석", "매출 리포트", "밤새 확인",
  "반복 검증", "회귀 확인", "챗봇 확인", "내부 도구", "자동화 스크립트".
  Also "/som", "som 실행", "오케스트레이션 돌려", "워커 띄워", "wave", "conduct".

  Noun phrases only, on purpose. A bare verb like 분석 · 설계 · 보고서 belongs
  to whatever else the user has installed; taking it makes this plugin an
  irritant rather than a tool.

  Boundary. The Orca `orchestration` skill is authoritative for CLI grammar and
  is the mechanism; this skill is the content -- which recipes exist, what each
  stage produces, which role gets which model, and when to stop. It calls Orca
  through the plugin's own adapter rather than by hand. `orca-cli` owns full
  ownership handoffs and terminal control; this skill supervises instead, and
  the two are mutually exclusive paths. It never calls bkit and never runs PDCA.
argument-hint: "[무엇을 하고 싶은지 자연어로] 또는 [recipe id]"
user-invocable: true
allowed-tools: [Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion]
---

# conduct — Orca 위에서 일을 굴린다

## 무엇을 하는가

사용자가 하고 싶은 일을 말하면, 적절한 레시피를 골라 작업을 쪼개고, Claude 워커를
병렬로 띄우고, 끝날 때까지 지켜보고, 결과를 정리한다.

```
사용자 한 문장 ─► 레시피 선택 ─► DAG ─► wave 1..N (워커 병렬) ─► 정산 ─► 보고
```

## 레시피

| id | 언제 | 산출물 | Snowflake |
|---|---|---|---|
| `doc` | "문서 만들어줘", R&R · KPI · 팀 차터 | HTML + xlsx + 검증 리포트 | 불필요 |
| `prd` | "PRD 필요해", 화면 구성·레이아웃까지 | 문제정의 → 플로우 → 화면별 레이아웃 → 규칙 → 수용기준 → PRD | 불필요 |
| `watch` | "밤새 확인해줘", 반복 검증 | 라운드별 관측값 + 이상 건별 재현 절차 | 불필요 |
| `analyze` | 엑셀 · CSV 를 그대로 분석 | 파일 → parquet → metrics → 리포트 → 원본 대조 | **불필요** |
| `build` | 내부 도구 · 자동화 스크립트 | 인터페이스 → 구현 ∥ 하네스 → e2e → 인수인계 | 불필요 |
| `data` | Snowflake 분석·리포트 | numbered SQL → parquet → metrics → 리포트 → 숫자 검증 | 필요 |

레시피는 `skills/conduct/recipes/*.json` 이다. 새 종류의 일이 생기면 레시피를
하나 더 쓰면 되고, 엔진은 건드리지 않는다.

**여섯 중 다섯은 Snowflake 없이 돈다.** 팀원 상당수가 Snowflake 계정이 없으므로,
"분석해줘" 처럼 원천이 안 적힌 말은 `analyze` 로 간다 — 그리고 첫 질문이 데이터가
어디 있는지다. Snowflake 라고 답하면 그때 `data` 로 바꾼다. 반대로 하지 마라:
없는 계정을 전제로 시작하면 두 번 묻게 된다.

## 절차

### 0. 준비 확인

```bash
node "${CLAUDE_PLUGIN_ROOT}/bin/som.mjs" conduct preflight
```

세 가지 중 하나라도 걸리면 **멈추고 사용자에게 말한다. 우회하지 않는다.**

- Orca 런타임이 안 떠 있음 → `orca open`
- orchestration 실험 기능 꺼짐 → Settings > Experimental (**사람만 켤 수 있다**)
- 이 터미널이 이미 워커임 → 중첩 깊이가 1이라 워커는 워커를 못 띄운다. 코디네이터
  터미널에서 실행하라

### 1. 레시피 고르기

사용자 말에서 자동으로 고른다. 애매하면 `AskUserQuestion` 으로 한 번 묻는다.
매칭이 안 되면 **레시피를 지어내지 말고** 어떤 것이 가까운지 보여주고 고르게 한다.

### 2. 인터뷰 — 게이트 두 개를 통과해야 한다

두 게이트는 서로 다른 것을 묻는다. **둘 다** 통과해야 `run()` 이 시작한다.

| | 무엇을 보는가 | 어디서 판정 |
|---|---|---|
| `asks` | 슬롯이 채워졌는가 (구조) | `missingAsks()` |
| 모호도 | 요청이 이해되었는가 (내용) | `clarityGate()` · **5% 미만** |

채워진 슬롯도 애매할 수 있다. "명단: 아무거나" 는 슬롯을 채우지만 아무것도
확정하지 않는다. 그래서 두 번째 게이트가 있다.

**모호도 게이트는 `interview` 스킬이 담당한다.** 구성 확인 → 라운드마다 질문
하나 → 차원별 채점 → 진행 표. 여기서 요약하지 않는다. `plan()` 이 `.som/interview/`
기록을 다시 읽어 문턱을 확인하므로, 인터뷰를 건너뛰면 계획 단계에서 막힌다.

```bash
node "${CLAUDE_PLUGIN_ROOT}/bin/som.mjs" interview gate "<요청 원문>" --recipe <id>
```

`ok` 가 아니면 **`interview` 스킬로 넘기고 여기서 멈춘다.** 모호한 상태로
워커를 띄우지 않는다.

### 2-1. `asks` — 한 번에 모아서

레시피의 `asks` 는 시작 전에 반드시 알아야 하는 슬롯이다. 인터뷰에서 이미 답이
나온 것은 **다시 묻지 않고** 스펙에서 채운다. 남은 것만 `AskUserQuestion` 으로
**한 화면에 모아서** 묻는다. 하나씩 되묻지 마라 — 답하는 사람 머릿속에 그림이
있는 건 지금이지 다섯 번째 질문 때가 아니다.

```bash
node "${CLAUDE_PLUGIN_ROOT}/bin/som.mjs" conduct asks --recipe doc --slots '{"이미":"아는 값"}'
```

각 `ask` 에는 `why` 가 붙어 있다 — 왜 필요한지 같이 보여주면 답이 달라진다.
"명단이 어디 있나요"보다 "없으면 사람 이름을 지어내게 됩니다"가 훨씬 잘 먹힌다.

**이건 예의가 아니라 게이트다.** 필수 `ask` 가 비어 있으면 `plan()` 이 문제로
올리고 `run()` 이 시작하지 않는다. 워커는 빈칸을 만나면 실패하지 않고 **그럴듯한
것으로 채운다** — R&R 문서에 없는 사람 이름이 들어가는 것이 문서가 없는 것보다
나쁘다. 사용자가 "그냥 알아서 해줘" 라고 하면, 지어내는 대신 **'없음'으로 받고
그 절을 미확정으로 표시**한다.

`analyze` 의 원천 질문에 "Snowflake 에 있다"는 답이 오면 `data` 로 바꿔서 다시
계획한다. 그 반대는 하지 않는다.

### 3. 계획 보여주기

DAG 를 만들고 사용자에게 보여준 뒤 시작한다. 노드 수, 최장 경로, 1차 wave 에 들어갈
작업, 예상 소요를 한 화면에 담는다. 이게 BLUEPRINT 게이트다.

### 3-1. Orca 가 필요한 계획인지 먼저 본다

`conduct plan` 이 wave 폭을 출력한다. **폭이 전부 1이면 Orca 를 쓰지 마라.**

```
노드 5개 · 최장 경로 4 · wave 1-1-1-1-1

전부 순차입니다 — Orca 도 워커도 필요 없습니다.
이 세션에서 아래 순서대로 직접 수행하세요:
  1. intake    planner
  2. ir        writer
  ...
```

동시에 도는 작업이 없으면 워커를 띄워서 얻는 것이 없다. 대신 런 생성 · 태스크
생성 · 워커 기동 · 터미널 정산 · 대기창을 노드 수만큼 치르고, **Orca 가 없는
팀원은 아예 시작을 못 한다.** 같은 일을 같은 순서로 이 세션에서 하면 그 전부가
0이다.

현재 여섯 중 넷이 순차다 — `doc` · `analyze` · `data` · `watch`.
`build` 와 `prd` 만 wave 폭 2 구간이 있다.

`conduct run` 은 순차 계획이면 Orca 를 **확인조차 하지 않고** 단계 목록을
돌려준다. 그 목록을 순서대로 수행하는 것이 실행이다. 각 단계에 `spec` 과
`통과 조건` 이 붙어 있으니 그것을 기준으로 삼는다.

### 4. 실행 (워커가 필요한 계획일 때)

```bash
# 먼저 계획만. 워커를 하나도 띄우지 않는다.
node "${CLAUDE_PLUGIN_ROOT}/bin/som.mjs" conduct run "<요청>" --recipe <id> --answers answers.json --dry-run

# 실제 실행. wave 승인은 사용자에게 물어본 다음 --approve-waves 로 넘긴다.
node "${CLAUDE_PLUGIN_ROOT}/bin/som.mjs" conduct run "<요청>" --recipe <id> --answers answers.json
```

`answers.json` 은 2-1 에서 모은 슬롯 답이다. **빠진 답이 있으면 지어내지 않고
exit 3 으로 멈추고** `SOM-ASK` 로 무엇이 없는지 낸다. 그것을 사용자에게 묻고
`answers.json` 에 채워 다시 부른다. 한 번에 못 끝내는 것이 정상이다.

exit 3 은 실패가 아니라 **질문**이다. exit 2 가 막힘이고 0 이 완료다.

이 스킬이 사람 대신 판단하는 지점은 세 개뿐이다.

- **워커 질문(`question`)** — 워커가 막혀 있다. ack 전에 답해야 한다. 스펙에 답이
  있으면 답하고, 범위가 바뀌는 질문이면 사용자에게 올린다
- **막힘(`escalation`)** — 지침을 주거나, 그 태스크만 blocked 로 두고 진행하거나,
  런을 멈춘다. **터미널을 절대 release 하지 않는다** — 워커는 살아 있다
- **실패** — 같은 설정으로 재시도하지 않는다. 모델을 한 단계 올리거나 작업을 더
  잘게 쪼갠다. Orca 는 3회 연속 실패에서 circuit-break 한다

### 5. 보고

- 완료 N/M, 실패가 있으면 무엇이 왜
- 만들어진 파일 목록
- 워커별 3문장 요약
- 정리 안 된 터미널이 있으면 그것도 (숨기지 않는다)

## 절대 하지 않는 것

- **느리다고 워커를 죽이지 않는다.** `check --wait` 타임아웃과 `count:0` 은
  체크포인트다. 코딩 태스크 15~60분은 정상이고, heartbeat 는 살아 있다는 뜻이지
  끝났다는 뜻이 아니다
- **`orchestration check` 를 직접 호출하지 않는다.** 소비자는 하나여야 한다.
  엔진의 watcher 가 그 역할이고, 상태를 볼 때는 `--peek` 만 쓴다
- **병렬을 이유로 워크트리를 만들지 않는다.** 파일이 겹치면 다음 wave 로 미룬다
- **worker_done 정산 후 터미널 회계를 건너뛰지 않는다.** 태스크 상태와 터미널
  소유권은 별개고, 건너뛰면 태스크마다 살아있는 에이전트 터미널이 하나씩 샌다
- **산출물을 발행하거나 공유 폴더에 복사하지 않는다.** 사람이 한다

## 상태 · 복구

`.som/` 에 남는다. `state.json`(계획·결정), `loop.json`(watcher), `events.ndjson`.

세션이 죽어도 된다. Orca 가 아는 것은 전부 Orca 에서 다시 읽고, 로컬에는 Orca 가
알 수 없는 것만 있다. 미ack 상태의 delivery 는 다음 세션에서 그대로 다시 온다.

이어받을 때: `orca orchestration run-current --json` 으로 run 을 확인하고
`run-use` 로 바인딩한 뒤, `task-list` · `worker-list` · `gate-list` 로 실제 상태를
다시 읽는다. **`.som/state.json` 의 숫자는 방향 잡는 용도이지 근거가 아니다.**

## 참고

- CLI 문법의 권위는 `orca skills get orchestration --full` 이다. 여기 요약하지 않는다
- 레시피 노드는 `writes` 글롭이 필수다. 없으면 스케줄러가 옆에 무엇을 같이 돌려도
  되는지 판단할 수 없어서 검증이 거부한다
