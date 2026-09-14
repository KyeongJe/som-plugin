# 설계 노트 — 벤치마크 대비 무엇을 다르게 했나

`README.md` 에서 옮겨온 것. 이 플러그인을 **고치거나 이어받는 사람**을 위한
문서이고, 쓰는 사람은 볼 필요가 없다.

벤치마크: [`Yeachan-Heo/oh-my-claudecode`](https://github.com/Yeachan-Heo/oh-my-claudecode)
의 `deep-interview` 스킬과 `src/hooks/learner/`.

## 모호도 게이트

벤치마크: [`oh-my-claudecode`](https://github.com/Yeachan-Heo/oh-my-claudecode) 의
`deep-interview` 스킬. 차원·가중치·토폴로지 게이트·진행 표를 그대로 가져왔다.
다른 점 셋:

| | 벤치마크 | som |
|---|---|---|
| 문턱 | 0.2, 자유 설정 | **0.05, 상한** — 더 조일 수만 있다 |
| 라운드 상한 도달 | 현재 명확도로 **진행** | **시작 거부**, 스펙을 미완성으로 남긴다 |
| 점수 계산 | 채점 프롬프트 안 | **코드** (`som/lib/domain/clarity.mjs`) |

세 번째가 앞의 둘을 지탱한다. 5% 를 맞추라는 압박을 받는 채점자는 **모르는 것을
적어두고도 점수를 올린다.** 그래서 산식이 코드에 있고, `gap` 이 비어 있지 않은
차원은 **0.89 로 잘린다** — 그리고 잘린 사실이 진행 표에 찍힌다.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="som/docs/img/interview-dark.svg">
  <img alt="한 문장을 말하면 빠진 것을 한 화면에 모아 묻고, 답을 받으면 시작하고 답이 없으면 시작하지 않는다" src="som/docs/img/interview-light.svg">
</picture>

## 패턴 학습

벤치마크: `oh-my-claudecode` 의 `learner`. 메타데이터 모양, exact → path → fuzzy
매칭 사다리와 신뢰도 점수, 내용 해시 중복 제거, scope 우선순위를 가져왔다.
다른 점 넷:

| | 벤치마크 | som |
|---|---|---|
| 근거 | 필드 자체가 없다 | **필수, 그리고 디스크에서 확인한다** |
| 품질 | 추출 시점 점수 1회 (이후 갱신 없음) | **결과로 움직이는 신뢰도** (성공 +8 · 실패 −20) |
| 안 먹히는 패턴 | 감지 장치 없음 · 사람이 `/skill remove` | **자동으로 내림 — 기록은 남긴다** |
| 자격증명 | 처리 없음 | **쓰기 시점 전 필드 거부** |

매칭 사다리도 그대로는 아니다 — 벤치마크의 2단은 **글롭·정규식** 패턴 매칭이고
여기 2단은 경로 basename 매칭이다. 정규식 trigger 는 안 가져왔다.

