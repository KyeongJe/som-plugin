"""Invariant tests for the humanize bridge.

These assert the properties that make it safe to run an LLM rewrite over an
exec-facing document: numbers, metric references, and protected identifiers
survive, and anything that does not survive is rolled back rather than shipped.

Run: python -m pytest engine/tests/test_humanize_io.py -q
 or: python engine/tests/test_humanize_io.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from somdoc import humanize_io as H  # noqa: E402

IR = {
    "docmeta": {
        "title": "2026 SOM Team R&R",
        "version": "v1.2",
        "as_of": "2026-09-09",
        # Must never be sent: a denied path segment, and prose-shaped.
        "note": "이 문서는 SOM 유닛의 역할과 책임을 정의하는 문서입니다.",
    },
    "sections": [
        {
            "id": "decision",
            "title": "결정 요청",
            "intro": "본 문서는 다음 세 가지 사항에 대한 승인을 요청하는 것을 목적으로 합니다.",
            "blocks": [
                {
                    "type": "decision_request",
                    "items": [
                        {
                            "ask": "BO/Data 서브팀 인원을 4명에서 5명으로 증원하는 것을 승인해 주시기 바랍니다.",
                            "rationale": "현재 SALESORG 1100 과 1400 을 4명이 담당하고 있으며, "
                                         "리드타임이 4.63 일로 목표 대비 초과된 상태입니다.",
                        }
                    ],
                },
                {
                    "type": "chart",
                    "kind": "bar",
                    "takeaway": "DO_TO_LABEL 단계가 전체 리드타임의 57% 를 차지하고 있는 것으로 나타났습니다.",
                    "series": [{"name": "lead time", "values": [0.2, 2.62, 1.44]}],
                },
                {
                    "type": "table",
                    "row_grain": "행 1개 = 거래선 × 주",
                    "caption": "거래선별 방문 주기 준수 현황을 정리한 표입니다.",
                    "columns": ["SHIPTO_KEY", "주기"],
                    "rows": [["0011011617", "14일"]],
                },
                {
                    "type": "bullets",
                    "items": [
                        {"text": "Delivery Block 비율은 {{m:delivery_block_rate}} 수준으로 확인되었습니다."},
                        {"text": "짧음"},
                    ],
                },
            ],
        }
    ],
}


def _cands():
    return {c["path"]: c for c in H.candidates(IR)}


def test_denied_and_short_fields_are_never_extracted():
    paths = _cands()
    assert not any("docmeta" in p for p in paths), "docmeta must never be humanized"
    assert not any("row_grain" in p for p in paths), "row_grain is normative"
    assert not any("columns" in p or ".rows" in p for p in paths), "table data must not be sent"
    # The 2-char bullet is below MIN_CHARS.
    assert "sections[0].blocks[3].items[1].text" not in paths


def test_expected_fields_are_extracted():
    paths = _cands()
    for expected in (
        "sections[0].intro",
        "sections[0].blocks[0].items[0].ask",
        "sections[0].blocks[0].items[0].rationale",
        "sections[0].blocks[1].takeaway",
        "sections[0].blocks[2].caption",
        "sections[0].blocks[3].items[0].text",
    ):
        assert expected in paths, f"missing {expected}"


def test_invariant_extraction():
    c = _cands()["sections[0].blocks[0].items[0].rationale"]
    assert c["digits"] == {"1100": 1, "1400": 1, "4": 1, "4.63": 1}
    assert c["terms"] == {"SALESORG": 1}
    ref = _cands()["sections[0].blocks[3].items[0].text"]
    assert ref["refs"] == {"{{m:delivery_block_rate}}": 1}


def _field(path):
    c = _cands()[path]
    return {
        "id": c["id"],
        "path": c["path"],
        "original": c["text"],
        "digits": dict(c["digits"]),
        "refs": dict(c["refs"]),
        "terms": dict(c["terms"]),
    }


def test_clean_rewrite_is_accepted():
    f = _field("sections[0].blocks[1].takeaway")
    good = "전체 리드타임의 57% 가 DO_TO_LABEL 단계에서 발생한다."
    assert H._check(f, good) == []


def test_number_loss_is_rejected():
    f = _field("sections[0].blocks[1].takeaway")
    bad = "리드타임 대부분이 DO_TO_LABEL 단계에서 발생한다."       # 57 dropped
    problems = H._check(f, bad)
    assert any("numbers lost" in p for p in problems), problems


def test_number_invention_is_rejected():
    f = _field("sections[0].blocks[1].takeaway")
    bad = "전체 리드타임의 62% 가 DO_TO_LABEL 단계에서 발생한다."   # 57 -> 62
    problems = H._check(f, bad)
    assert any("numbers lost" in p for p in problems)
    assert any("numbers invented" in p for p in problems)


def test_metric_ref_loss_is_rejected():
    f = _field("sections[0].blocks[3].items[0].text")
    bad = "Delivery Block 비율은 10.6% 다."                       # ref replaced by a literal
    problems = H._check(f, bad)
    assert any("metric refs changed" in p for p in problems), problems


def test_protected_term_loss_is_rejected():
    f = _field("sections[0].blocks[0].items[0].rationale")
    bad = ("현재 영업조직 1100 과 1400 을 4명이 맡고 있고, "
           "리드타임은 4.63 일로 목표를 4 일 넘겼다.")            # SALESORG dropped
    problems = H._check(f, bad)
    assert any("protected terms lost" in p for p in problems), problems


def test_truncation_is_rejected():
    f = _field("sections[0].intro")
    problems = H._check(f, "승인 요청.")
    assert any("length ratio" in p for p in problems), problems


def test_thousands_separator_is_meaning_preserving():
    f = {"original": "총 410,042 행", "digits": {"410042": 1}, "refs": {}, "terms": {}}
    assert H._check(f, "총 410042 행이다.") == []


def test_set_at_round_trips(tmp_path=None):
    ir = json.loads(json.dumps(IR))
    H.set_at(ir, "sections[0].blocks[3].items[0].text", "치환됨")
    assert ir["sections"][0]["blocks"][3]["items"][0]["text"] == "치환됨"
    H.set_at(ir, "sections[0].intro", "머리말")
    assert ir["sections"][0]["intro"] == "머리말"


def _run_all() -> int:
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(_run_all())
