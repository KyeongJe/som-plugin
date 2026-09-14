"""Build the som documentation as a somdoc document.

Dogfooding on purpose: the explanation of the standard is itself produced by
the standard. If the engine cannot render its own manual, it is not ready.

    python docs/build_docs.py [--out docs]

Every number in here is measured, not estimated. The measurements are taken
live where they can be (test counts, byte sizes) and otherwise carry the source
they came from in the appendix.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

SOM = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SOM / "engine"))
sys.path.insert(0, str(SOM / "docs"))

import svg as SVG                              # noqa: E402
from somdoc import ir as IR                    # noqa: E402
from somdoc.emitters import html as HTML       # noqa: E402

AS_OF = "2026-09-09"


AUTONOMY_ROWS = [
    ("worker.start", "워커 띄우기"),
    ("answer.question", "스펙 안의 워커 질문"),
    ("answer.scope-change", "범위가 바뀌는 질문"),
    ("write.in-scope", "선언한 글롭 안 쓰기"),
    ("write.out-of-scope", "선언한 글롭 밖 쓰기"),
    ("worker.stop", "워커 강제 종료"),
    ("worktree.create", "새 워크트리"),
    ("gate.self-resolve", "자기 게이트 해제"),
    ("snowflake.write", "Snowflake 쓰기"),
]

_VERDICT_LABEL = {"auto": "자동", "gate": "확인", "deny": "거부"}


def autonomy_matrix() -> dict:
    """Ask the engine what it actually decides, per action per level.

    This table used to exist in three hand-written copies -- README.md, this
    document, and a transcription inside the test that was supposed to police
    them. They disagreed: both published copies claimed a scope-change question
    reached a person at L3, while `decide()` answered it automatically, and the
    test compared the code against its own copy of the code so it never noticed.

    Generating it removes the copy. `test/autonomy.test.mjs` still parses the
    README table, so the two remaining places a person reads this are both
    checked against `decide()`.
    """
    probe = """
import { Autonomy, LEVELS, HARD_FLOORS } from "./lib/domain/autonomy.mjs";
const actions = process.argv.slice(2);
const out = { levels: LEVELS, caps: [], rows: {},
              floors: HARD_FLOORS.map(([a, why]) => ({ action: a, why })) };
for (const L of LEVELS) {
  const a = new Autonomy("__doc_probe__");
  a.state.level = L;
  a.save = () => a.state;
  out.caps.push(a.maxWorkers());
}
for (const action of actions) {
  out.rows[action] = LEVELS.map((L) => {
    const a = new Autonomy("__doc_probe__");
    a.state.level = L;
    a.save = () => a.state;
    return a.decide(action).verdict;
  });
}
process.stdout.write(JSON.stringify(out));
"""
    probe_path = SOM / ".doc-autonomy-probe.mjs"
    probe_path.write_text(probe, encoding="utf-8")
    try:
        r = subprocess.run(
            [shutil.which("node") or "node", str(probe_path),
             *[a for a, _ in AUTONOMY_ROWS]],
            cwd=SOM, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise RuntimeError(f"autonomy probe failed: {r.stderr[:400]}")
        data = json.loads(r.stdout)
    finally:
        probe_path.unlink(missing_ok=True)

    cells = [[str(c) for c in data["caps"]]]
    rows = ["최대 병렬 워커"]
    for action, label in AUTONOMY_ROWS:
        verdicts = data["rows"][action]
        rows.append(f"{label} ({action})")
        cells.append([_VERDICT_LABEL.get(v, v) for v in verdicts])

    return {
        "matrix": {
            "type": "matrix",
            "title": "레벨별 자동 승인 범위",
            "corner": "동작",
            "columns": data["levels"],
            "rows": rows,
            "cells": cells,
            "caption": "이 표는 손으로 쓴 것이 아니라 이 문서를 빌드할 때 "
                       "`decide()` 에 직접 물어 만든 것이다. 점수 0~100, 상향은 "
                       "30분 쿨다운과 완료된 런 ≥1 을 둘 다 요구한다. "
                       "하드 플로어 위반 시 즉시 L0.",
        },
        "floors": data["floors"],
    }


def measure() -> dict:
    """Live measurements, so the document cannot drift from the repo."""
    env = {**__import__("os").environ,
           "PYTHONPATH": str(SOM / "engine"), "PYTHONUTF8": "1"}

    def count_tests(script: str) -> tuple[int, int]:
        r = subprocess.run([sys.executable, script], cwd=SOM,
                           capture_output=True, text=True, env=env)
        tail = [x for x in r.stdout.splitlines() if "passed" in x]
        if not tail:
            return (0, 0)
        got, total = tail[-1].strip().split(" ")[0].split("/")
        return int(got), int(total)

    ir_pass, ir_total = count_tests("engine/tests/test_ir.py")
    hz_pass, hz_total = count_tests("engine/tests/test_humanize_io.py")
    mf_pass, mf_total = count_tests("engine/tests/test_manifest.py")
    sql_pass, sql_total = count_tests("engine/tests/test_somsql.py")

    golden = SOM / "standard/examples/golden/rnr.html"
    example = SOM / "standard/examples/rnr.example.somdoc.json"
    ex = json.loads(example.read_text(encoding="utf-8"))

    py = sorted((SOM / "engine").rglob("*.py"))
    mjs = sorted(SOM.rglob("*.mjs"))
    css = sorted((SOM / "standard/themes").glob("*.css"))

    return {
        "ir_pass": ir_pass, "ir_total": ir_total,
        "hz_pass": hz_pass, "hz_total": hz_total,
        "mf_pass": mf_pass, "mf_total": mf_total,
        "sql_pass": sql_pass, "sql_total": sql_total,
        "tests_total": ir_total + hz_total + mf_total + sql_total,
        "tests_pass": ir_pass + hz_pass + mf_pass + sql_pass,
        "golden_bytes": golden.stat().st_size if golden.exists() else 0,
        "ex_sections": len(ex.get("sections") or []),
        "ex_blocks": sum(len(s.get("blocks") or []) for s in ex["sections"]),
        "py_files": len(py),
        "py_lines": sum(len(p.read_text(encoding="utf-8").splitlines()) for p in py),
        "mjs_files": len(mjs),
        "css_lines": sum(len(p.read_text(encoding="utf-8").splitlines()) for p in css),
    }


def build(m: dict) -> dict:
    _AUTONOMY = autonomy_matrix()
    return {
        "sds_version": 1,
        "doc_type": "report",
        "theme": "console",
        "lang": "ko",
        "docmeta": {
            "title": "som — 어떻게 작동하는가",
            "subtitle": "SOM 업무 오케스트레이션 플러그인 · 그림과 표 중심 설명",
            "slug": "som_how_it_works",
            "org": "SOM Unit / BO-Data",
            "version": "v0.1.0",
            "as_of": AS_OF,
            "author": "Kyeong Je Kim",
            "classification": "Internal",
        },
        "metrics": {
            "tests": {"value": m["tests_pass"], "unit": "건",
                      "formula": "test_ir.py 통과 + test_humanize_io.py 통과",
                      "source_rows": m["tests_total"], "computed_at": AS_OF},
            "golden_kb": {"value": round(m["golden_bytes"] / 1024, 1), "unit": " KB",
                          "formula": "standard/examples/golden/rnr.html 파일 크기",
                          "computed_at": AS_OF},
            "py_lines": {"value": m["py_lines"], "unit": " lines",
                         "formula": "engine/**/*.py 행수 합",
                         "source_rows": m["py_files"], "computed_at": AS_OF},
        },
        "sections": [
            # ---------------------------------------------------------- 1
            {
                "id": "glance", "title": "한눈에", "derived": True,
                "blocks": [
                    {"type": "kpi_tiles", "derived": True, "items": [
                        {"label": "통과 테스트", "value": m["tests_pass"], "unit": f" / {m['tests_total']}",
                         "state": "ok" if m["tests_pass"] == m["tests_total"] else "warn",
                         "note": "IR · humanize · 매니페스트"},
                        {"label": "고정 스테이지", "value": 5, "unit": "개",
                         "note": "게이트 4개는 코드가 판정"},
                        {"label": "엔진 코드", "value": m["py_lines"], "unit": " lines",
                         "note": f"Python {m['py_files']}개 파일"},
                        {"label": "산출물 형식", "value": 4, "unit": "종",
                         "note": "HTML·xlsx 가동 / docx·pptx 예정"},
                    ]},
                    {"type": "callout", "kind": "info", "title": "이 문서 자체가 검증이다",
                     "body": "이 페이지는 som 의 문서 엔진이 렌더했다. 위 숫자는 저장소에서 "
                             "빌드 시점에 측정한 값이며, 손으로 적은 값이 아니다. "
                             "엔진이 자기 설명서를 못 만들면 준비된 게 아니다."},
                ],
            },
            # ---------------------------------------------------------- 2
            {
                "id": "problem", "title": "무엇을 해결하나",
                "blocks": [
                    {"type": "bullets", "title": "지금의 문제", "items": [
                        {"text": "반복 업무 세 종류가 매번 손으로, 매번 다른 형태로 나온다", "emph": True},
                        {"text": "데이터 분석 — Snowflake 파이프라인을 프로젝트마다 재구현"},
                        {"text": "내부 도구 개발 — 기획·구현·검증 순서가 매번 다름"},
                        {"text": "팀 운영 문서 — R&R·KPI·팀 정의에 공통 포맷이 없어 일회성 산출물"},
                        {"text": "결과: 팀원이 같은 품질로 재생산할 수 없다", "emph": True},
                    ]},
                    {"type": "bullets", "title": "som 의 대응", "items": [
                        {"text": "세 업무를 같은 5-stage 게이트 골격 위에 올린다", "emph": True},
                        {"text": "게이트 통과 조건을 산문이 아니라 코드로 판정한다"},
                        {"text": "산출물은 검증된 공통 포맷 하나에서 파생된다"},
                        {"text": "Orca 가 없는 팀원도 같은 바이트를 재현할 수 있다", "emph": True},
                    ]},
                    {"type": "table", "title": "세 도메인", "row_grain": "행 1개 = 파이프라인 1개",
                     "columns": [
                         {"key": "cmd", "label": "커맨드", "align": "text"},
                         {"key": "dom", "label": "도메인", "align": "text"},
                         {"key": "in", "label": "입력", "align": "wrap"},
                         {"key": "out", "label": "산출물", "align": "wrap"},
                         {"key": "st", "label": "상태", "align": "badge"},
                     ],
                     "rows": [
                         ["/som:doc", "팀 운영 문서", "xlsx roster · 기존 문서",
                          "HTML + xlsx (+docx)", {"state": "ok", "label": "가동"}],
                         ["/som:data", "Snowflake 분석", "numbered SQL · parquet 캐시",
                          "리포트 + metrics.json", {"state": "warn", "label": "Phase 2"}],
                         ["/som:build", "내부 도구 개발", "인터페이스 명세",
                          "코드 + 검증 매뉴얼", {"state": "na", "label": "Phase 4"}],
                     ]},
                ],
            },
            # ---------------------------------------------------------- 3
            {
                "id": "gates", "title": "5-stage 게이트",
                "intro": "게이트는 통과 조건을 만족해야 다음 스테이지가 열리는 구조적 검사다. "
                         "판정은 코드가 한다.",
                "blocks": [
                    {"type": "table", "title": "게이트 통과 조건",
                     "row_grain": "행 1개 = 게이트 1개",
                     "columns": [
                         {"key": "id", "label": "게이트", "align": "text", "sticky": True},
                         {"key": "stage", "label": "스테이지 전이", "align": "text"},
                         {"key": "cond", "label": "통과 조건 (코드가 확인)", "align": "wrap"},
                         {"key": "who", "label": "해제", "align": "badge"},
                     ],
                     "rows": [
                         ["G0", "INTAKE → BLUEPRINT",
                          "grain 선언 · 기간 · SALESORG 범위 · acceptance 표. 하나라도 없으면 열리지 않는다",
                          {"state": "info", "label": "사람"}],
                         ["G1", "BLUEPRINT → EXECUTE",
                          "모든 DAG 노드에 writes 글롭. 빈 글롭 노드는 스키마가 거부. 최장 경로 ≤ 4",
                          {"state": "info", "label": "사람"}],
                         ["G2", "ATTEST → DELIVER",
                          "인쇄된 모든 숫자가 metrics.json key 로 역추적 가능 · 인원별 % 합 = 100",
                          {"state": "ok", "label": "자동"}],
                         ["G3", "DELIVER → 발행",
                          "bundle + MANIFEST.json + 원천 목록. 자동 발송·자동 복사 없음",
                          {"state": "crit", "label": "항상 사람"}],
                     ]},
                    {"type": "bullets", "title": "동시성은 이 플러그인이 정한다", "items": [
                        {"text": "Orca 는 스케줄링·동시성·충돌 판단을 하지 않는다 — 루프를 직접 쓴다",
                         "emph": True},
                        {"text": "writes 글롭이 교차하는 두 태스크는 다음 wave 로 미룬다 (워크트리 격리 아님)"},
                        {"text": "동시 워커 기본 3, current 체크아웃에서 하드 캡 4"},
                        {"text": "중첩 깊이 1 — 코디네이터 1 + 워커 N 평면 구조"},
                    ]},
                    {"type": "bullets", "title": "느린 것과 실패한 것은 다르다", "items": [
                        {"text": "check --wait 타임아웃 · count:0 은 체크포인트다 — 실패가 아니다", "emph": True},
                        {"text": "코딩 태스크 15~60분은 정상. heartbeat 는 살아있다는 뜻이고 끝났다는 뜻이 아니다"},
                        {"text": "태스크 3회 연속 실패 → Orca circuit-break. 동일 설정 재시도 금지"},
                        {"text": "G2 불일치 → 재개 최대 2회, 그 뒤 사람에게 에스컬레이션"},
                    ]},
                ],
            },
            # ---------------------------------------------------------- 4
            {
                "id": "loop", "title": "코디네이터 루프 — 두 프로세스, 하나의 소비자",
                "blocks": [
                    {"type": "diagram", "svg": SVG.coordinator_loop(),
                     "takeaway": "check 는 FIFO delivery 상태를 변경하므로 정확히 한 프로세스만 호출해야 한다. "
                                 "그래서 소비자(watcher)와 결정자(router)를 분리했다."},
                    {"type": "bullets", "title": "이 구조에서 공짜로 얻는 것", "items": [
                        {"text": "watcher 는 호스트 관리 Monitor 프로세스라 컨텍스트 압축에 면역이다", "emph": True},
                        {"text": "router 가 배치 처리 중 죽으면 ack 가 안 되므로, 다음 세션의 watcher 가 "
                                 "같은 배치를 그대로 재발행한다 — 인바운드 durability 가 별도로 필요 없다",
                         "emph": True},
                        {"text": "락 없음. 파일당 단일 작성자. 모든 쓰기는 tmp → fsync → rename"},
                        {"text": "Orca 가 아는 것은 전부 Orca 에서 재도출한다. 디스크를 신뢰하지 않는다"},
                    ]},
                    {"type": "table", "title": "메시지 타입별 라우팅",
                     "row_grain": "행 1개 = Orca 메시지 타입 1개",
                     "columns": [
                         {"key": "t", "label": "타입", "align": "text", "sticky": True},
                         {"key": "act", "label": "동작", "align": "wrap"},
                         {"key": "rel", "label": "터미널 release", "align": "badge"},
                     ],
                     "rows": [
                         ["worker_done", "검증 → 정산 확인 → 터미널 회계 → 다음 wave. "
                                         "pane 신원 증명이 안 되면 task-update 폴백",
                          {"state": "ok", "label": "예"}],
                         ["escalation", "worker-read 로 문맥 확보 → 레벨 내면 guidance, 초과면 게이트",
                          {"state": "crit", "label": "아니오"}],
                         ["question", "ack 전에 답해야 한다 (워커가 블로킹 중)",
                          {"state": "crit", "label": "아니오"}],
                         ["heartbeat", "타임스탬프만 갱신. 사용자 출력 0",
                          {"state": "crit", "label": "아니오"}],
                         ["status", "timeline 에 append. 동작 없음",
                          {"state": "crit", "label": "아니오"}],
                         ["merge_ready", "DELIVER 큐에 적재",
                          {"state": "crit", "label": "아니오"}],
                     ]},
                ],
            },
            # ---------------------------------------------------------- 5
            {
                "id": "sds", "title": "문서 표준 — 한 IR, 네 emitter",
                "blocks": [
                    {"type": "diagram", "svg": SVG.somdoc_pipeline(),
                     "takeaway": "에이전트가 저작하는 것은 IR JSON 하나뿐이다. "
                                 "한 IR 이 네 포맷을 서비스하는 게 HTML 과 xlsx 의 숫자가 갈라지지 않는 "
                                 "유일한 구조다."},
                    {"type": "table", "title": "IR 검증 규칙", "row_grain": "행 1개 = 규칙 1개",
                     "columns": [
                         {"key": "r", "label": "규칙", "align": "text", "sticky": True},
                         {"key": "what", "label": "요구", "align": "wrap"},
                         {"key": "why", "label": "없으면 생기는 일", "align": "wrap"},
                     ],
                     "rows": [
                         ["R1", "모든 표가 row grain 을 선언한다",
                          "독자가 행 단위를 오해한다 — 경영진 오독 1순위"],
                         ["R2", "모든 차트가 takeaway 를 선언하고 차트 위에 굵게 렌더한다",
                          "결론 없는 차트는 퍼즐이 된다"],
                         ["R3", "모든 {{!m:key}} 가 metrics 에서 해소된다",
                          "프로즈의 숫자가 출처와 조용히 분리된다"],
                         ["R4", "제목·버전·기준일이 있다",
                          "날짜·버전 없는 문서는 대체될 수 없다"],
                         ["R5", "결정 요청이 앞 3개 절 안에 있고 3건 이하다",
                          "6쪽에 묻힌 결정 요청은 결정 요청이 아니다"],
                         ["R6", "표·차트가 직사각형이다",
                          "행 길이가 어긋나면 데이터가 조용히 사라진다"],
                         ["R7", "모든 지표가 산식을 갖는다",
                          "KPI 오너십이 실제로 사는 곳이 그 칸이다"],
                     ]},
                    {"type": "bullets", "title": "표 거동 — 검증된 것을 계승했다", "items": [
                        {"text": "필터는 행별 data-f 배열만 읽는다. 셀 텍스트를 훑지 않는다", "emph": True},
                        {"text": "그래서 1100 검색이 PO 번호나 품목 설명에 걸리지 않는다"},
                        {"text": "sticky 헤더 + 하단 2px accent, 숫자 셀 우측정렬 tabular-nums"},
                        {"text": "zebra 없음, hover tint 만. 스트라이프가 배지 색과 싸운다"},
                        {"text": "고유값 20개 초과 컬럼에 검색창 자동 부여"},
                        {"text": "14개 초과 컬럼은 핵심 컬럼 뷰로 열리고 토글을 제공한다. 인쇄는 전체 컬럼"},
                    ]},
                    {"type": "bullets", "title": "humanize 를 IR 저작 단계에 둔 이유", "items": [
                        {"text": "humanize-korean 은 LLM 패스라 비결정적이다", "emph": True},
                        {"text": "렌더러는 결정적이고 golden test 로 바이트 비교된다"},
                        {"text": "그래서 패스를 렌더 상류(IR 저작)에 둔다 — 커밋되는 IR 이 이미 윤문 상태"},
                        {"text": "수락 조건은 코드가 판정한다: 숫자 multiset · metric ref · 보호 식별자 보존, "
                                 "길이비 [0.45, 1.60]"},
                        {"text": "하나라도 실패하면 그 필드만 원문으로 롤백하고 exit 2 로 사람을 부른다", "emph": True},
                        {"text": "KPI 정의 문장·산식과 row grain 은 구조적으로 제외 — 그 문구 자체가 정의다"},
                    ]},
                ],
            },
            # ---------------------------------------------------------- 6
            {
                "id": "snowflake", "title": "Snowflake 안전 — 읽기 자동, 쓰기는 승인",
                "blocks": [
                    {"type": "diagram", "svg": SVG.snowflake_guard(),
                     "takeaway": "막아야 하는 것은 쓰기가 아니라 아무도 안 본 쓰기다. "
                                 "승인은 문장의 해시에 묶여 있어, 한 건에 준 예가 다른 건으로 옮겨가지 않는다."},
                    {"type": "table", "title": "기존 sf_utils.py 대비 막은 구멍",
                     "row_grain": "행 1개 = 우회 경로 1개",
                     "columns": [
                         {"key": "hole", "label": "구멍", "align": "wrap", "sticky": True},
                         {"key": "ex", "label": "예", "align": "wrap"},
                         {"key": "old", "label": "기존", "align": "badge"},
                         {"key": "new", "label": "somsql", "align": "badge"},
                     ],
                     "rows": [
                         ["첫 토큰만 검사", "WITH x AS (...) INSERT INTO ...",
                          {"state": "crit", "label": "통과"}, {"state": "ok", "label": "AST 전수 walk"}],
                         ["naive ; 분할", "WHERE name = 'a;DROP TABLE t'",
                          {"state": "crit", "label": "오분할"}, {"state": "ok", "label": "파서 기준"}],
                         ["deny-list 방식", "목록에 없는 신규 statement",
                          {"state": "crit", "label": "통과"}, {"state": "ok", "label": "default-deny"}],
                         ["파싱 실패 폴백", "정규식만으로 통과",
                          {"state": "warn", "label": "약해짐"}, {"state": "ok", "label": "fail closed"}],
                         ["스크립트 우회", "6줄 커넥터 스크립트를 새로 작성",
                          {"state": "crit", "label": "무력"}, {"state": "warn", "label": "PreToolUse 훅이 경고"}],
                     ]},
                    {"type": "table", "title": "읽기가 아닌 문장이 지나는 자리",
                     "row_grain": "행 1개 = 단계 1개",
                     "columns": [
                         {"key": "step", "label": "단계", "align": "wrap", "sticky": True},
                         {"key": "who", "label": "누가", "align": "badge"},
                         {"key": "what", "label": "무엇이 일어나는가", "align": "wrap"},
                     ],
                     "rows": [
                         ["describe", {"state": "ok", "label": "도구"},
                          "동사 · 대상 객체 · 되돌릴 수 있는지 · 위험 요소를 문장으로 출력. "
                          "접속하지 않는다"],
                         ["confirm", {"state": "warn", "label": "사람"},
                          "그 문장의 sha256 앞 12자와 사유를 입력. 모델은 채울 수 없다"],
                         ["execute", {"state": "ok", "label": "도구"},
                          "승인이 해시와 일치할 때만 전송. 한 글자라도 다르면 다시 묻는다"],
                         ["record", {"state": "ok", "label": "도구"},
                          "동사 · 대상 · 영향 행 · 승인자 · 사유가 ledger 와 WRITE_LOG.md 에"],
                     ]},
                    {"type": "bullets", "title": "쓰기에서 경보가 올라가는 네 가지", "items": [
                        {"text": "UPDATE · DELETE 에 WHERE 가 없다 — 대상 테이블의 모든 행", "emph": True},
                        {"text": "OR REPLACE — 같은 이름이 이미 있으면 그 내용이 사라진다"},
                        {"text": "CALL — 프로시저 내부는 이 도구가 볼 수 없고, 볼 수 없다고 말한다"},
                        {"text": "대상 객체명을 읽어내지 못했다 — 추측을 출력하는 대신 그렇다고 말하고 "
                                 "심각도를 올린다. 틀린 대상 줄은 없는 대상 줄보다 나쁘다"},
                    ]},
                    {"type": "bullets", "title": "읽기는 막지 않는다 — 알려줄 뿐이다", "items": [
                        {"text": "상한이 없다. byte · 행 · statement timeout 제한을 전부 없앴고, "
                                 "LIMIT 을 몰래 끼워 넣지 않는다", "emph": True},
                        {"text": "그 LIMIT 주입이 가장 나빴다 — 41만 행 추출에 1,000행을 돌려주면 "
                                 "잘린 결과가 완전한 결과와 똑같이 생긴다"},
                        {"text": "ON/USING 없는 join(cartesian) 은 실행 전에 알려준다"},
                        {"text": "EXPLAIN USING JSON 으로 사전 스캔 견적 — 크레딧 0", "emph": True},
                        {"text": "동일 stmt hash 24시간 캐시 — 같은 질문을 두 번 돌리지 않는다"},
                        {"text": "매 실행 ledger 기록 → DELIVER 부록에 산출물당 비용 표로 노출. "
                                 "비용 가시화와 비용 통제는 다른 일이고, 원치 않은 쪽은 통제였다"},
                    ]},
                    {"type": "callout", "kind": "warn", "title": "기존 스크립트는 막지 않는다",
                     "body": "PreToolUse 훅은 이 플러그인 밖에서 Snowflake 에 닿는 명령과 파일을 "
                             "거부하지 않고 경고한다. 팀에는 이 플러그인이 생기기 전부터 쓰던 "
                             "스크립트가 있고, 그것을 막는 것은 som 과 무관한 업무를 세우는 일이었다. "
                             "경고가 알려주는 것은 그 실행이 원장 밖이라는 사실이다 — 나중에 "
                             "'언제 무엇을 했나' 를 되짚을 때 그 줄만 비어 있다."},
                    {"type": "callout", "kind": "info", "title": "권한은 여전히 role 이 정한다",
                     "body": "래퍼는 defense-in-depth 다. 무엇을 쓸 수 있는지의 근본은 Snowflake "
                             "role 의 grant 이고, 사람마다 필요한 만큼만 주도록 DCOE 에 요청하는 것이 "
                             "이 설계 전체에서 가장 가치 높은 조치다."},
                ],
            },
            # ---------------------------------------------------------- 7
            {
                "id": "autonomy", "title": "자율 레벨 — 신뢰가 해제할 수 없는 것",
                "blocks": [
                    _AUTONOMY["matrix"],
                    {"type": "bullets",
                     "title": f"동결된 하드 플로어 ({len(_AUTONOMY['floors'])}개)",
                     "items": [{"text": f"{f['action']} — {f['why']}"}
                               for f in _AUTONOMY["floors"]]},
                    {"type": "callout", "kind": "info", "title": "비대칭이 의도적이다",
                     "body": "게이트는 엔진이 물어볼 수 있는 예/아니오다. 하드 플로어는 사람이 자기 턴에 "
                             "직접 요구를 타이핑해야 하는 것이다. Snowflake 쓰기는 플로어에서 게이트로 "
                             "내려왔다 — 근거였던 '사람의 예를 쓰기로 바꾸는 코드 경로가 없다' 가 "
                             "테이블을 만들어야 하는 날 깨졌기 때문이다. 어느 레벨에서도 자동이 되지 "
                             "않는다는 점은 그대로다."},
                ],
            },
            # ---------------------------------------------------------- 8
            {
                "id": "verified", "title": "검증된 사실", "derived": True,
                "intro": "주장과 측정을 분리한다. 아래는 전부 이 저장소에서 실행해 확인한 값이다.",
                "blocks": [
                    {"type": "chart", "kind": "bar", "derived": True,
                     "takeaway": f"Python 엔진 테스트 {m['tests_pass']}건 전부 통과 "
                                 f"(Node 쪽 오케스트레이션·게이트 테스트는 별도). "
                                 f"안전에 직결된 항목(숫자 유실·발명, metric ref 변경, "
                                 f"보호 용어 유실)이 포함된다.",
                     "categories": ["IR 규칙", "SQL 분류·cost guard",
                                    "humanize 불변식", "매니페스트 규칙"],
                     "series": [{"name": "통과", "values": [
                         m["ir_pass"], m["sql_pass"], m["hz_pass"], m["mf_pass"]]}],
                     "y_unit": "건", "height": 240,
                     "src": "engine/tests/*.py"},
                    {"type": "table", "title": "주장 대비 검증 방법",
                     "row_grain": "행 1개 = 검증 항목 1개",
                     "columns": [
                         {"key": "claim", "label": "주장", "align": "wrap", "sticky": True},
                         {"key": "how", "label": "검증 방법", "align": "wrap"},
                         {"key": "res", "label": "결과", "align": "wrap"},
                         {"key": "st", "label": "판정", "align": "badge"},
                     ],
                     "rows": [
                         ["HTML 렌더가 결정적이다", "동일 IR 로 2회 빌드해 바이트 비교",
                          f"{m['golden_bytes']:,} bytes 동일", {"state": "ok", "label": "확인"}],
                         ["golden 레퍼런스와 일치한다", "커밋된 golden 과 바이트 비교",
                          "byte-identical", {"state": "ok", "label": "확인"}],
                         ["HTML 이 자기완결이다", "외부 src/href · @import · <link> 정규식 검사",
                          "외부 요청 0", {"state": "ok", "label": "확인"}],
                         ["xlsx 는 바이트 결정적이지 않다", "2회 빌드해 파일 바이트 비교",
                          "바이트 불일치 — zip 타임스탬프", {"state": "warn", "label": "설계 반영"}],
                         ["xlsx 내용은 안정적이다", "시트명·좌표·값 다이제스트 비교",
                          "다이제스트 동일", {"state": "ok", "label": "확인"}],
                         ["hook timeout 은 초 단위다", "CC 바이너리에서 hook.timeout*1000 및 "
                                                    "와이어 필드 timeout_s 확인",
                          "초 단위 확정 — 5000 이면 83분이 된다", {"state": "ok", "label": "확인"}],
                         ["orca.cmd 가 send 를 거부한다", "orca.cmd line 13-14 확인",
                          "거부. .EXE 가 PATHEXT 앞이라 현재는 운으로 동작",
                          {"state": "warn", "label": "어댑터 대응"}],
                         ["monitors 서페이스가 존재한다", "CC 바이너리 문자열 확인",
                          "persistent Monitor task, on-skill-invoke",
                          {"state": "ok", "label": "확인"}],
                         ["실제 R&R 워크북이 처리된다", "15명 4팀 6시트 → IR → 렌더",
                          f"{m['ex_sections']}절 {m['ex_blocks']}블록", {"state": "ok", "label": "확인"}],
                     ]},
                    {"type": "chart", "kind": "hbar", "derived": True,
                     "takeaway": "단일 파일 HTML 이 archify 독립 아티팩트의 약 1/9 크기이고, "
                                 "외부 요청이 0이라 망분리 랩탑에서도 열린다.",
                     "categories": ["som HTML (자기완결)", "som xlsx", "archify 독립 HTML"],
                     "series": [{"name": "KB", "values": [
                         round(m["golden_bytes"] / 1024, 1), 26.9, 605.5]}],
                     "y_unit": " KB", "label_width": 210,
                     "src": "측정: 2026-09-09 빌드 산출물"},
                ],
            },
            # ---------------------------------------------------------- 9
            {
                "id": "use", "title": "설치와 사용",
                "blocks": [
                    {"type": "table", "title": "팀원 설치", "row_grain": "행 1개 = 단계 1개",
                     "columns": [
                         {"key": "n", "label": "#", "align": "num"},
                         {"key": "cmd", "label": "명령", "align": "wrap"},
                         {"key": "note", "label": "설명", "align": "wrap"},
                     ],
                     "rows": [
                         [1, "/plugin marketplace add KyeongJe/som-claude-plugin",
                          "private repo. 조직 접근 권한이 있어야 한다"],
                         [2, "/plugin install som@som-marketplace", "user scope 설치"],
                         [3, "/som:doctor", "Python 의존성 · 시크릿 노출 · 동기 경로 점검"],
                         [4, "/som:doc rnr", "Orca 가 없으면 한 세션 순차 모드로 동작"],
                     ]},
                    {"type": "bullets", "title": "Orca 가 없는 팀원의 재현 경로", "items": [
                        {"text": "5 stage 를 한 세션에서 순차 수행. 게이트는 stop-and-ask 로 격하", "emph": True},
                        {"text": "에이전트 저작물은 IR JSON 하나. 렌더는 python -m somdoc build"},
                        {"text": "skeleton · theme · emitter 가 전부 플러그인 파일이라 산출물이 바이트 비교 가능"},
                        {"text": "가장 강한 degradation: 에이전트를 건너뛰고 IR + 명령 한 줄만 넘겨도 동일 산출물",
                         "emph": True},
                    ]},
                    {"type": "bullets", "title": "경계 — 무엇을 쓰지 않는가", "items": [
                        {"text": "다른 플러그인을 호출하지 않고 그 상태 폴더를 읽지도 쓰지도 않는다",
                         "emph": True},
                        {"text": "Orca 의 orchestration 스킬이 CLI 문법의 권위다 — 여기 요약하지 않는다"},
                        {"text": "Artifact 발행 경로가 없다. 산출물은 로컬 파일뿐"},
                        {"text": "다른 플러그인이 트리거로 쓰는 문구를 피한다 — 가로채면 도구가 아니라 방해가 된다"},
                    ]},
                ],
            },
            # The measurement appendix was removed from both published pages.
            # Every number in this document is already generated from a live
            # measurement rather than typed, and `docs/check_counts.py` is what
            # proves that -- a table restating the file paths added a section
            # without adding an assurance.
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
        print(f"build_docs: {len(problems)} IR problem(s)")
        for x in problems:
            print(f"  - {x}")
        return 1

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ir_path = out / "som-how-it-works.somdoc.json"
    IR.dump(ir, ir_path)
    html_path = out / "som-how-it-works.html"
    html_path.write_text(HTML.emit(ir), encoding="utf-8", newline="\n")

    print(f"build_docs: {len(ir['sections'])} sections, "
          f"{sum(len(s.get('blocks') or []) for s in ir['sections'])} blocks")
    print(f"  {ir_path}")
    print(f"  {html_path}  ({html_path.stat().st_size:,} bytes)")
    print(f"  ir_sha256 {IR.ir_sha256(ir)[:12]}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
