# 릴리스 — 비공개에서 공개로

저장소가 둘입니다.

| | 무엇 | 누가 봅니까 |
|---|---|---|
| `som-claude-plugin` (**비공개**) | 개발. 전체 이력 | 만드는 사람 |
| `som-plugin` (**공개**) | 릴리스본만. 버전당 커밋 1개 | 팀 |

## 왜 나눴는가

한 저장소로 하려다 실제 사고가 있었습니다. 예시 파일에 진짜 Snowflake account
identifier 가 들어 있었고, 작업 트리에서 지워도 **이력에는 남았습니다.** 공개는
이력 전체를 공개합니다.

**이력을 복사하지 않는 것**이 이 구조의 전부입니다. 공개 저장소는 파일만 받고
릴리스마다 커밋 하나를 쌓습니다. 개발 중의 실수가 나중에 고쳐졌더라도 애초에
건너가지 않습니다.

## 한 방향입니다

`tools/release.mjs` 는 remote 를 추가하지도, fetch 하지도, merge 하지도
않습니다. 공개 저장소에 **직접 커밋하지 마세요.** 다음 릴리스가 조용히
되돌립니다 — 두 저장소 구조가 썩는 건 언제나 이 지점입니다.

공개본에 고칠 게 있으면 **여기서 고치고 다시 릴리스**합니다.

## 하는 법

```bash
# 1. CHANGELOG.md 에 이번 버전 항목을 쓴다 (없으면 릴리스가 거부됩니다)
# 2. plugin.json · marketplace.json 의 version 을 올린다 (3곳이 같아야 합니다)
# 3. 커밋한다 (작업 트리가 더러우면 거부됩니다)

node tools/release.mjs 0.2.0 --dry-run              # 게이트만 돌리고 멈춤
node tools/release.mjs 0.2.0 --public ../som-plugin # 복사 + 커밋, 푸시는 안 함
node tools/release.mjs 0.2.0 --public ../som-plugin --push
```

`--force` 는 없습니다. 하나라도 걸리면 아무것도 복사되지 않습니다.

## 게이트

| | 무엇을 봅니까 |
|---|---|
| 작업 트리 | 커밋 안 된 변경이 있으면 중단. 릴리스는 HEAD 기준입니다 |
| 테스트 | node 전체 · python 전체. 하나라도 실패하면 중단 |
| 발행된 숫자 | README 의 테스트 개수 = 실측 |
| doctor | fail 0 |
| **오케스트레이션 e2e** | 실제 워커로 `Conduct.run()` 실행. 단위 테스트가 안 건드리는 유일한 핵심 경로입니다 |
| **공개 위생** | 실제 account identifier · 접속 endpoint · private key 본문 · 개발자 홈 경로 · 매니페스트 밖으로 샌 이메일 |
| 포함 파일 | `.som/` · `_cache/` · `*.jsonl` · `*.parquet` · 키 파일 등이 섞였는지 |
| 버전 | plugin.json · marketplace.json 2곳, 3개가 전부 같은지 |
| CHANGELOG | 그 버전 항목이 있는지 |

**e2e 가 게이트에 있는 이유.** `Conduct.run()` 은 단위 테스트가 0건입니다 — Orca 와
유료 워커가 필요해서 240건짜리 node 스위트 어디도 부르지 않습니다. 그 사이로 실제
결함이 나갔습니다: `const pf` 가 `if` 블록 안으로 들어가면서 아래 두 사용처가
`ReferenceError` 를 냈고, **병렬 런(`prd`·`build`)이 전부 죽는데 373건은 초록**이었습니다.
손으로 돌려서 찾았는데, 손은 장치가 아닙니다. 그래서 여기 있습니다.

Orca 가 없는 머신이면 `--no-e2e` 로 건너뛸 수 있지만, **건너뛰었다고 출력에 찍힙니다.**

공개 위생 검사는 `tools/gate.mjs` 이고 `som/test/release.test.mjs` 가 검사합니다 —
막아야 할 것과 막으면 안 되는 것을 짝으로 둡니다. **오탐이 있는 게이트는
`--force` 를 부르고, 이 게이트에는 `--force` 가 없습니다.**

같은 검사 일부가 `som/engine/tests/test_manifest.py` 에도 있습니다. 그쪽은 매
커밋마다 돌아 비공개 저장소를 깨끗하게 유지하고, 이쪽은 실제로 나가는 파일
집합에 돌립니다. 릴리스 게이트가 상류 검사를 믿으면 리팩터 한 번에 아무것도
검사하지 않게 됩니다.

## 처음 한 번 — 공개 저장소 만들기

```bash
mkdir -p ../som-plugin && cd ../som-plugin
git init && git branch -M main
git remote add origin https://github.com/<계정>/som-plugin.git
```

**이 저장소를 remote 로 추가하지 마세요.** 릴리스 스크립트가 감지하고 거부합니다.

## 팀원 설치

```
/plugin marketplace add <계정>/som-plugin
/plugin install som@som-marketplace
```

## 남아 있는 사실

비공개 저장소의 **이력에는 예전 account identifier 가 그대로 있습니다.** 공개
저장소로는 건너가지 않지만, 비공개 저장소를 다시 공개로 바꾸면 그대로 드러납니다.
**바꾸지 마세요.**

그리고 그 값은 한 번 공개된 적이 있습니다. 실질적 방어선은 저장소가 아니라
Snowflake 계정의 **MFA 와 network policy** 입니다.
