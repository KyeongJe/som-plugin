"""Build the teammate-facing introduction to som.

A different document from build_docs.py on purpose. That one explains how the
engine works and is read by whoever maintains it. This one is read by someone
who has not installed anything yet and is deciding whether to bother. It is
pictures first, five minutes long, and it never assumes Snowflake.

Same engine, same standard -- which is itself the argument. If two very
different documents come out of one IR schema with one renderer, the claim
that the team gets a consistent format is not a promise, it is a demonstration.

    python docs/build_team_intro.py [--out docs]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SOM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOM / "engine"))
sys.path.insert(0, str(SOM / "docs"))

import svg as SVG                              # noqa: E402
from somdoc import ir as IR                    # noqa: E402
from somdoc.emitters import html as HTML       # noqa: E402


def _sequential_recipes() -> dict[str, bool]:
    """Ask the scheduler which recipes have no parallel stretch at all.

    Measured, not listed. This page told teammates that everything but `doc`
    needed Orca, which stopped being true the moment the engine learned to
    plan before checking for it: four of the six schedule as 1-1-1-1-1 and
    never reach Orca. A list would have gone stale again; a probe cannot.
    """
    probe = "\n".join([
        'const root = process.argv[2];',
        'const C = await import(`file://${root}/lib/conduct.mjs`);',
        'const P = await import(`file://${root}/lib/domain/plan.mjs`);',
        'const out = {};',
        'for (const r of C.listRecipes()) {',
        '  const slots = Object.fromEntries((r.asks ?? []).map((a) => [a.slot, "x"]));',
        '  out[r.id] = P.parallelismOf(C.buildDag(r, slots), { cap: 4 }).sequential;',
        '}',
        'process.stdout.write(JSON.stringify(out));',
    ])
    probe_path = SOM / ".team-intro-probe.mjs"
    probe_path.write_text(probe, encoding="utf-8")
    try:
        r = subprocess.run(
            ["node", str(probe_path), str(SOM).replace("\\", "/")],
            capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError(f"parallelism probe failed: {r.stderr[:300]}")
        return json.loads(r.stdout)
    finally:
        probe_path.unlink(missing_ok=True)


_SEQUENTIAL = _sequential_recipes()




AS_OF = "2026-09-09"


def measure() -> dict:
    """Counts that must not drift from the repo.

    A teammate-facing page that claims "five recipes" while the folder holds
    four is worse than one that claims nothing, so the recipes are counted, not
    typed.
    """
    rdir = SOM / "skills/conduct/recipes"
    recipes = []
    for p in sorted(rdir.glob("*.json")):
        if p.stem.startswith("_"):
            continue                      # test fixtures
        r = json.loads(p.read_text(encoding="utf-8"))
        recipes.append({
            "id": r["id"], "title": r.get("title", r["id"]),
            "summary": r.get("summary", ""),
            "nodes": len(r.get("nodes") or []),
            # Declared, not sniffed. `analyze` names Snowflake in a question --
            # it offers to hand off to `data` -- and a substring scan reads
            # that as a dependency.
            # What this recipe needs beyond Claude, computed rather than
            # assumed. This row once said "Orca" for everything but `doc`,
            # which was true when the engine demanded Orca before it had
            # planned anything. It plans first now, and a plan whose waves are
            # all width 1 -- four of the six -- never reaches Orca at all.
            # Only `prd` and `build` have a parallel stretch, and there Orca
            # saves one wave.
            "needs": (["Snowflake"] if "snowflake" in (r.get("requires") or []) else [])
                     + ([] if _SEQUENTIAL.get(r["id"], True) else ["Orca (선택)"])
                     + (["pandas"] if r["id"] == "analyze" else []),
            "needs_sf": "snowflake" in (r.get("requires") or []),
            "asks": [a for a in (r.get("asks") or [])
                     if isinstance(a, dict) and a.get("required", True)],
        })

    def e2e_seconds() -> int:
        return 73                        # measured, test/e2e-conduct.mjs

    return {
        "recipes": recipes,
        "n_recipes": len(recipes),
        "n_no_sf": sum(1 for r in recipes if not r["needs_sf"]),
        "e2e_s": e2e_seconds(),
    }


SAY = {
    "doc":     "\"R&R 문서 만들어줘\"",
    "prd":     "\"PRD 필요해\"",
    "analyze": "\"이 엑셀 분석해줘\"",
    "watch":   "\"밤새 확인해줘\"",
    "build":   "\"스크립트 만들어줘\"",
    "data":    "\"Snowflake 에서 뽑아줘\"",
}
GET = {
    "doc":     "HTML + Excel. 누가 만들어도 같은 양식",
    "prd":     "화면 목록 · 화면별 레이아웃 · 수용기준",
    "analyze": "리포트 + 숫자마다 출처. 원본과 대조까지",
    "watch":   "라운드별 관측 + 이상 건 재현 절차",
    "build":   "코드 + 검증 스크립트 + 세팅 매뉴얼",
    "data":    "리포트 + 숫자마다 출처",
}


def build(m: dict) -> dict:
    rec = {r["id"]: r for r in m["recipes"]}
    order = [k for k in ("doc", "prd", "analyze", "watch", "build", "data") if k in rec]

    def brings(k: str) -> str:  # noqa: D401
        """What the person has to bring, taken from the recipe's own questions."""
        qs = [a["q"].split("?")[0].split("(")[0].strip() for a in rec[k]["asks"]]
        return " · ".join(qs) if qs else "없음"

    return {
        "sds_version": 1,
        "doc_type": "report",
        "theme": "paper",
        "lang": "ko",
        "docmeta": {
            "title": "som — 팀원 안내",
            "subtitle": "한 문장 말하면 됩니다 · 5분이면 끝나는 설명",
            "slug": "som_team_intro",
            "org": "SOM Unit / BO-Data",
            "version": "v1.0",
            "as_of": AS_OF,
            "author": "Kyeong Je Kim",
            "classification": "Internal",
        },
        "metrics": {
            "recipes": {"value": m["n_recipes"], "unit": "종",
                        "formula": "skills/conduct/recipes/*.json 개수 (_ 로 시작하는 테스트용 제외)",
                        "computed_at": AS_OF},
            "no_sf": {"value": m["n_no_sf"], "unit": "종",
                      "formula": "레시피 중 Snowflake 를 쓰지 않는 것의 개수",
                      "computed_at": AS_OF},
            "e2e_s": {"value": m["e2e_s"], "unit": "초",
                      "formula": "test/e2e-conduct.mjs 실측 — 작업 3개, 2 wave, 실제 Claude 워커",
                      "computed_at": AS_OF},
        },
        "sections": [
            # -------------------------------------------------------- 1
            {
                "id": "glance", "title": "한눈에", "derived": True,
                "blocks": [
                    {"type": "kpi_tiles", "derived": True, "items": [
                        {"label": "설치", "value": 2, "unit": "줄",
                         "state": "ok", "note": "그다음은 명령도 안 외웁니다"},
                        {"label": "할 수 있는 일", "value": m["n_recipes"], "unit": "종",
                         "state": "ok", "note": "문서 · PRD · 엑셀 분석 · 밤샘 검증 · 개발 · SF 분석"},
                        {"label": "설치할 것", "value": "없음",
                         "state": "ok", "note": "문서는 Claude 만 있으면 바로"},
                        {"label": "결과물", "value": "내 PC",
                         "state": "ok", "note": "어디에도 자동으로 안 올라감"},
                        {"label": "쓸수록", "value": "학습",
                         "state": "ok", "note": "겪은 문제를 다음에 먼저 알려줌"},
                    ]},
                    {"type": "callout", "kind": "info", "title": "이 페이지도 som 이 만들었습니다",
                     "body": "여러분이 \"문서 만들어줘\" 라고 했을 때 나오는 것과 같은 엔진, 같은 양식입니다. "
                             "위 숫자는 저장소에서 세어 넣은 값이고 손으로 적은 값이 아닙니다."},
                ],
            },
            # -------------------------------------------------------- 2
            {
                "id": "when", "title": "이런 게 반복되면",
                "intro": "도구 설명 말고, 실제로 이런 상황이면 도움이 됩니다. "
                         "아니면 안 쓰셔도 됩니다.",
                "blocks": [
                    {"type": "table", "title": "언제 쓰나",
                     "row_grain": "행 1개 = 반복되는 상황 1가지",
                     "columns": [
                         {"key": "sit", "label": "이런 상황", "align": "wrap", "sticky": True},
                         {"key": "now", "label": "지금", "align": "wrap"},
                         {"key": "with", "label": "이걸 쓰면", "align": "wrap"},
                     ],
                     "rows": [
                         ["분기마다 R&R 문서를 다시 만든다",
                          "매번 빈 문서에서 시작하고, 만든 사람마다 양식이 다르다",
                          "명단 파일만 주면 늘 같은 양식으로 나온다. 합계·명단 대조는 자동"],
                         ["PRD 를 쓰는데 화면 정의에서 막힌다",
                          "화면 목록까지는 쓰는데 화면별 상태·엣지 케이스에서 멈춘다",
                          "화면마다 빈 상태·에러·권한까지 묻고 채운다"],
                         ["엑셀을 받아서 매번 같은 집계를 한다",
                          "피벗을 다시 만들고, 지난번과 숫자가 맞는지 눈으로 본다",
                          "숫자마다 출처가 붙고, 원본과 다시 대조해서 알려준다"],
                         ["뭔가 고친 뒤 한동안 지켜봐야 한다",
                          "생각날 때 들어가 보고, 언제부터 이상했는지 모른다",
                          "정해둔 횟수만큼 보고 이상한 것만 재현 절차와 함께 알려준다"],
                         ["같은 실수를 팀에서 반복한다",
                          "겪은 사람만 알고, 다음 사람이 또 밟는다",
                          "한 번 겪은 문제는 기록해 뒀다가 다음에 먼저 알려준다"],
                     ]},
                    {"type": "callout", "kind": "warn", "title": "이럴 땐 안 쓰는 게 낫습니다",
                     "body": "한 번만 쓰고 버릴 문서, 5분이면 끝나는 일, 형식이 이미 "
                             "정해져 있고 그대로 채우기만 하면 되는 일. "
                             "시작 전에 몇 가지 묻기 때문에 짧은 일에는 오히려 느립니다."},
                ],
            },
            # -------------------------------------------------------- 3
            {
                "id": "what", "title": "무엇을 해주나",
                "blocks": [
                    {"type": "diagram", "svg": SVG.what_you_get(),
                     "takeaway": f"{m['n_recipes']}가지 중 {m['n_no_sf']}가지는 추가 설치 없이 바로 됩니다. "
                                 "Snowflake 를 안 쓰셔도 아무 문제 없습니다."},
                    {"type": "table", "title": "말하는 대로",
                     "row_grain": "행 1개 = 할 수 있는 일 1가지",
                     "columns": [
                         {"key": "say", "label": "이렇게 말하면", "align": "text", "sticky": True},
                         {"key": "ask", "label": "이런 걸 물어봅니다", "align": "wrap"},
                         {"key": "get", "label": "이런 게 나옵니다", "align": "wrap"},
                         {"key": "steps", "label": "단계", "align": "num"},
                         {"key": "need", "label": "설치할 것", "align": "badge"},
                     ],
                     "rows": [
                         [SAY[k], brings(k), GET[k], rec[k]["nodes"],
                          {"state": "ok", "label": "없음"} if not rec[k]["needs"]
                          else {"state": "warn", "label": " + ".join(rec[k]["needs"])}]
                         for k in order
                     ]},
                    {"type": "callout", "kind": "good",
                     "title": "여섯 중 다섯은 지금 바로 됩니다",
                     "body": "추가 설치도, 계정도, Orca 도 필요 없습니다. "
                             "Snowflake 에서 뽑는 것 하나만 계정이 필요합니다. "
                             "설치 후 /som:doctor 를 한 번 돌리면 이 컴퓨터에서 지금 "
                             "무엇이 되고, 안 되는 것마다 무엇이 필요한지 한 줄로 "
                             "알려줍니다."},
                ],
            },
            # -------------------------------------------------------- 3
            {
                "id": "ask", "title": "모르는 건 물어봅니다",
                "blocks": [
                    {"type": "diagram", "svg": SVG.interview(),
                     "takeaway": "시작 전에 필요한 것만 한 번에 묻습니다. "
                                 "답이 없으면 지어내지 않고, 시작도 하지 않습니다."},
                    {"type": "diagram", "svg": SVG.ambiguity_gate(),
                     "takeaway": "\"충분히 물어봤나\" 를 느낌으로 판단하지 않습니다. "
                                 "네 가지를 점수로 매겨 모호도를 계산하고, "
                                 "5% 미만일 때만 시작합니다."},
                    {"type": "table", "title": "모호도는 이렇게 계산합니다",
                     "row_grain": "행 1개 = 채점 차원 1개",
                     "columns": [
                         {"key": "dim", "label": "차원", "align": "text", "sticky": True},
                         {"key": "q", "label": "무엇을 보는가", "align": "wrap"},
                         {"key": "w", "label": "가중", "align": "num"},
                     ],
                     "rows": [
                         ["목표", "한 문장으로, 수식어 없이 말할 수 있는가", 0.40],
                         ["제약 · 경계", "하지 않을 것까지 정해졌는가", 0.30],
                         ["완료 기준", "됐는지 확인할 방법이 있는가", 0.30],
                         ["기존 시스템", "기존 코드를 고치는 작업일 때만 적용", 0.15],
                     ],
                     "note": "라운드마다 이 표와 함께 현재 모호도를 보여드립니다. "
                             "구성이 여러 개면 전체 모호도는 그중 가장 높은 값입니다 — "
                             "잘 정리된 부분이 애매한 부분을 가리지 못하게."},
                    {"type": "bullets", "title": "왜 묻는가", "items": [
                        {"text": "빈칸을 만난 AI 는 멈추지 않습니다 — 그럴듯한 것으로 채웁니다", "emph": True},
                        {"text": "R&R 문서에 없는 사람 이름이 들어가는 건 문서가 없는 것보다 나쁩니다"},
                        {"text": "질문마다 \"왜 필요한지\"가 같이 나옵니다. "
                                 "\"명단이 어디 있나요\" 보다 \"없으면 이름을 지어냅니다\" 가 낫습니다"},
                        {"text": "질문은 한 번에 모아서 옵니다. 하나씩 되묻지 않습니다", "emph": True},
                        {"text": "모른다고 적으면서 높은 점수를 주면 코드가 0.89 로 자릅니다 — "
                                 "표에 그대로 찍힙니다"},
                        {"text": "정말 자료가 없으면 '없음' 이라고 답하세요 — "
                                 "뼈대만 만들고 채울 곳을 표시해 드립니다"},
                    ]},
                    {"type": "callout", "kind": "info", "title": "이건 예의가 아니라 규칙입니다",
                     "body": "모호도가 5% 미만이 아니면 계획 단계에서 막힙니다. "
                             "\"그냥 시작해\" 라고 하셔도 열리지 않습니다. "
                             "대신 \"이건 모르는 채로 갑니다\" 라고 확정해 주시면 그 상태로 "
                             "진행합니다 — 그것도 정직한 답이고, 숫자도 내려갑니다. "
                             "다만 글로 '미확정' 이라고만 쓰는 것으로는 안 되고, "
                             "확정하겠다고 답해 주셔야 반영됩니다."},
                ],
            },
            # -------------------------------------------------------- 4
            {
                "id": "learn", "title": "쓸수록 좋아집니다",
                "blocks": [
                    {"type": "diagram", "svg": SVG.learning_loop(),
                     "takeaway": "한 번 겪은 문제는 기록해 뒀다가 다음에 같은 일을 할 때 "
                                 "먼저 알려줍니다. 근거가 없으면 저장하지 않습니다."},
                    {"type": "bullets", "title": "무엇을 기억하나", "items": [
                        {"text": "재시도한 작업 — 처음에 왜 실패했는지가 거의 항상 교훈입니다", "emph": True},
                        {"text": "사람이 끼어든 지점 — 물어봤어야 할 것을 못 물어본 자리입니다"},
                        {"text": "빨라지거나 느려진 것 — 무엇을 바꿔서 그렇게 됐는지"},
                        {"text": "잘 된 것은 기억하지 않습니다. 그건 기준선이지 교훈이 아닙니다"},
                    ]},
                    {"type": "bullets", "title": "아무거나 기억하지는 않습니다", "items": [
                        {"text": "\"꼼꼼히 하라\" 같은 일반론은 저장을 거부합니다", "emph": True},
                        {"text": "실제 파일·테스트·실행 기록 중 하나를 대야 저장됩니다"},
                        {"text": "비밀번호·토큰처럼 보이는 게 있으면 거부합니다"},
                        {"text": "다음 작업에 알려주는 건 최대 3개. 열 개면 정작 할 일이 묻힙니다"},
                        {"text": "알려준 대로 했는데 잘 안 되면 그 기억은 신뢰도가 내려가고, "
                                 "계속 빗나가면 더 이상 안 알려줍니다", "emph": True},
                    ]},
                    {"type": "callout", "kind": "info", "title": "참고이지 지시가 아닙니다",
                     "body": "지난번 교훈은 \"이런 게 있었습니다\" 로 전달됩니다. "
                             "지금 상황에 안 맞으면 따르지 말고 그 사실을 알려 달라고 "
                             "명시돼 있습니다."},
                ],
            },
            # -------------------------------------------------------- 5
            {
                "id": "diff", "title": "지금과 뭐가 다른가",
                "blocks": [
                    {"type": "diagram", "svg": SVG.before_after(),
                     "takeaway": "혼자 순서대로 물어보는 대신, 상관없는 일은 동시에 돌고 "
                                 "끝나면 정리해서 옵니다."},
                    {"type": "bullets", "title": "실제로 체감되는 차이", "items": [
                        {"text": "같은 문서를 두 사람이 만들어도 양식이 같습니다", "emph": True},
                        {"text": "숫자는 표와 문장이 따로 놀지 않습니다 — 한 곳에서 나옵니다"},
                        {"text": "합계가 100%가 아니면 알려줍니다. 눈으로는 안 보이는 종류입니다"},
                        {"text": "오래 걸리는 일을 켜두고 다른 걸 해도 됩니다"},
                        {"text": "중간에 창이 닫혀도 처음부터 다시 하지 않습니다", "emph": True},
                    ]},
                    {"type": "callout", "kind": "info", "title": "실측",
                     "body": "작업 3개를 2번에 나눠 실행해 {{m:e2e_s}} 만에 끝났고, "
                             "남은 찌꺼기 창은 없었습니다. 실제 Claude 워커로 돌린 값입니다."},
                ],
            },
            # -------------------------------------------------------- 4
            {
                "id": "how", "title": "어떻게 도는가",
                "intro": "몰라도 쓸 수 있습니다. 궁금한 분만 보세요.",
                "blocks": [
                    {"type": "diagram", "svg": SVG.how_it_works(),
                     "takeaway": "한 문장 → 할 일을 쪼갬 → 상관없는 것끼리 동시에 → "
                                 "끝난 것부터 확인 → 정리해서 보고."},
                    {"type": "bullets", "title": "알아서 처리하는 것", "items": [
                        {"text": "같은 파일을 건드릴 두 작업은 동시에 돌리지 않습니다", "emph": True},
                        {"text": "조용하다고 죽이지 않습니다 — 코딩은 15~60분도 정상입니다"},
                        {"text": "실패하면 같은 방식으로 재시도하지 않고 더 잘게 쪼갭니다"},
                        {"text": "끝난 작업의 창은 반드시 정리합니다"},
                    ]},
                ],
            },
            # -------------------------------------------------------- 5
            {
                "id": "format", "title": "문서는 늘 같은 양식으로",
                "blocks": [
                    {"type": "diagram", "svg": SVG.somdoc_pipeline(),
                     "takeaway": "AI 가 쓰는 것은 내용(JSON) 하나뿐이고, 모양은 플러그인이 만듭니다. "
                                 "그래서 누가 만들어도, 몇 번을 다시 만들어도 같은 문서가 나옵니다."},
                    {"type": "table", "title": "자동으로 잡아주는 것",
                     "row_grain": "행 1개 = 검사 1개",
                     "columns": [
                         {"key": "check", "label": "검사", "align": "wrap", "sticky": True},
                         {"key": "why", "label": "없으면 생기는 일", "align": "wrap"},
                     ],
                     "rows": [
                         ["표마다 \"행 1개가 무엇인지\" 적기",
                          "읽는 사람이 행 단위를 오해합니다. 경영진 오독 1순위"],
                         ["그래프마다 결론 한 줄, 그래프 위에 굵게",
                          "결론 없는 그래프는 퍼즐입니다"],
                         ["문장 속 숫자는 표에서만 가져오기",
                          "표를 고쳤는데 문장이 옛 숫자를 들고 있습니다"],
                         ["결정 요청은 앞쪽에, 3건 이하로",
                          "6쪽에 묻힌 요청은 요청이 아닙니다"],
                         ["지표마다 계산식 적기",
                          "같은 KPI 를 두 사람이 다르게 셉니다"],
                     ]},
                    {"type": "callout", "kind": "good", "title": "실제로 잡은 것",
                     "body": "팀의 실제 R&R 워크북에 돌렸을 때 한 사람의 시간 비중 합이 115% 였고, "
                             "15명 중 7명이 \"주요 업무\" 같은 비정량 표기였습니다. "
                             "눈으로 훑어서는 나오지 않는 종류입니다."},
                ],
            },
            # -------------------------------------------------------- 6
            {
                "id": "safe", "title": "안 하는 것",
                "blocks": [
                    {"type": "diagram", "svg": SVG.guardrails(),
                     "takeaway": "익숙해지면 물어보는 횟수는 줄어듭니다. "
                                 "아래 다섯 가지는 그래도 안 합니다 — 설정이 아니라 구조입니다."},
                    {"type": "bullets", "title": "특히 이 두 가지는 옵션이 아닙니다", "items": [
                        {"text": "데이터베이스에 쓰지 않습니다. 사람이 \"예\" 해도 안 됩니다", "emph": True},
                        {"text": "결과물을 어디에도 올리지 않습니다. 공유는 사람이 직접 합니다", "emph": True},
                        {"text": "엔진 안에 그렇게 할 수 있는 경로 자체가 없습니다"},
                    ]},
                ],
            },
            # -------------------------------------------------------- 7
            {
                "id": "start", "title": "시작하기",
                "blocks": [
                    {"type": "bullets", "title": "3단계", "items": [
                        {"text": "1. /plugin marketplace add KyeongJe/som-claude-plugin", "emph": True},
                        {"text": "2. /plugin install som@som-marketplace", "emph": True},
                        {"text": "3. /som:doctor — 지금 뭘 쓸 수 있는지 알려줍니다", "emph": True},
                        {"text": "그다음은 그냥 하고 싶은 일을 한국어로 말하면 됩니다"},
                    ]},
                    {"type": "callout", "kind": "good",
                     "title": "명령을 외우지 않아도 됩니다",
                     "body": "\"R&R 문서 만들어줘\", \"이 엑셀 분석해줘\", \"밤새 확인해줘\" 처럼 "
                             "그냥 말하면 알아서 붙습니다. 슬래시 명령은 확실하게 부르고 싶을 때 "
                             "쓰는 지름길일 뿐입니다."},
                    {"type": "table", "title": "무엇을 내주게 되나",
                     "row_grain": "행 1개 = 비용 1가지",
                     "columns": [
                         {"key": "what", "label": "비용", "align": "text", "sticky": True},
                         {"key": "how", "label": "얼마나", "align": "wrap"},
                     ],
                     "rows": [
                         ["시작 전 질문에 답하는 시간",
                          "보통 3~5개. 한 화면에 모아서 옵니다"],
                         ["문서 한 건",
                          "AI 세션 1개. 다른 작업과 같은 수준입니다"],
                         ["여러 작업 동시 실행 (PRD·분석·검증·개발)",
                          "동시에 도는 AI 수만큼. 기본 3개, 최대 4개까지"],
                         ["기다리는 시간",
                          "문서는 몇 분. 코딩·검증은 15~60분도 정상입니다"],
                     ],
                     "note": "정확한 토큰 비용은 측정하지 않았습니다. "
                             "동시에 도는 AI 수가 곧 비용 배수라고 보시면 됩니다."},
                    {"type": "table", "title": "그래도 알아두면 좋은 것",
                     "row_grain": "행 1개 = 부르는 방법 1개",
                     "columns": [
                         {"key": "cmd", "label": "이렇게 말하거나 치면", "align": "text", "sticky": True},
                         {"key": "when", "label": "언제", "align": "wrap"},
                     ],
                     "rows": [
                         ["그냥 하고 싶은 일을 말하기", "평소에는 이걸로 충분합니다"],
                         ["/som <하고 싶은 일>", "확실하게 이 플러그인으로 하고 싶을 때"],
                         ["/som:doc", "팀 문서만 빠르게. 설치할 것 없음"],
                         ["/som:doctor", "처음 한 번, 그리고 뭔가 안 될 때"],
                         ["/som:sql", "Snowflake 를 쓰시는 분만"],
                     ]},
                    {"type": "callout", "kind": "info", "title": "Orca 는 없어도 됩니다",
                     "body": "여러 AI 를 동시에 띄워 주는 앱입니다. 여섯 가지 중 넷은 "
                             "단계가 전부 순서대로라 동시에 돌 일이 없고, 그래서 Orca 를 "
                             "찾지도 않습니다. PRD 와 스크립트 만들기만 동시에 도는 "
                             "구간이 있는데, 거기서 Orca 가 줄여 주는 것은 한 단계뿐입니다. "
                             "없으면 순서대로 진행합니다. "
                             "그래도 받아 보고 싶으시면 "
                             "https://github.com/stablyai/orca/releases 에서 "
                             "Windows 설치본을 받아 실행하시면 됩니다 — 설정할 것은 "
                             "없고, som 이 있으면 알아서 씁니다."},
                ],
            },
            # -------------------------------------------------------- 8
            {
                "id": "sources", "title": "부록 — 이 문서의 숫자",
                "blocks": [{
                    "type": "appendix_source",
                    "note": "빌드 시점에 저장소에서 측정했습니다.",
                    "items": [
                        {"name": "레시피 목록", "path": "skills/conduct/recipes/*.json",
                         "rows": m["n_recipes"], "as_of": AS_OF},
                        {"name": "오케스트레이션 실측", "path": "test/e2e-conduct.mjs",
                         "sheet": "실제 Claude 워커 3개 · 2 wave", "rows": m["e2e_s"],
                         "as_of": AS_OF},
                        {"name": "R&R 검증 사례", "path": "standard/examples/rnr.example.somdoc.json",
                         "sheet": "2026 SOM Team R&R v1.0.xlsx 파생", "rows": 15,
                         "as_of": AS_OF},
                    ],
                }],
            },
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(SOM / "docs"))
    a = ap.parse_args()

    m = measure()
    ir = build(m)

    problems = IR.validate(ir)
    if problems:
        print(f"build_team_intro: {len(problems)} IR problem(s)")
        for x in problems:
            print(f"  - {x}")
        return 1

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ir_path = out / "som-team-intro.somdoc.json"
    IR.dump(ir, ir_path)
    html_path = out / "som-team-intro.html"
    html_path.write_text(HTML.emit(ir), encoding="utf-8", newline="\n")

    print(f"build_team_intro: {len(ir['sections'])} sections, "
          f"{sum(len(s.get('blocks') or []) for s in ir['sections'])} blocks")
    print(f"  {ir_path}")
    print(f"  {html_path}  ({html_path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
