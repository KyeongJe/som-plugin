"""The three skeletons must produce a real document, and validate() must never crash.

Two things were assumed and not checked.

**The skeletons had never been built.** `kpi` and `charter` were written,
committed, and named in four places; the only thing anyone had ever done with
them was `cat`. A path that exists is not a shape that passes `validate`.

**The validator crashed on ordinary mistakes.** Its entire job is to turn a bad
document into a list of sentences, and 14 of 21 wrong-shaped inputs made it
raise AttributeError from inside a loop instead -- including `"columns":
["이름", "역할"]`, which is what a person writes on a first attempt. They got a
CPython traceback naming `ir.py:278` rather than a sentence naming the field.

    python engine/tests/test_skeletons.py
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

SOM = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SOM / "engine"))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from somdoc import ir as IR  # noqa: E402

SKELETONS = ("rnr", "kpi", "charter")


# --------------------------------------------------------------------------
# a minimal, honest instance of every block type the skeletons name
# --------------------------------------------------------------------------
def _block(kind: str, title: str) -> dict | None:
    if kind == "docmeta":
        return None                                    # lives in ir["docmeta"]
    return {
        "decision_request": {"type": "decision_request", "items": [
            {"ask": "이 문서의 범위를 승인해 주세요",
             "rationale": "승인 전에는 다음 절을 시작하지 않습니다",
             "owner": "유닛장", "due": "2026-09-30"}]},
        "kpi_tiles": {"type": "kpi_tiles", "items": [
            {"label": "등록 지표", "value": "{{!m:kpi_count}}", "unit": "개"}]},
        "bullets": {"type": "bullets", "title": title, "items": [
            {"text": "원천이 확정되기 전까지 이 절은 미확정입니다"}]},
        "narrative": {"type": "narrative", "paragraphs": [
            "스켈레톤이 요구하는 구조를 채우기 위한 최소 서술입니다."]},
        "callout": {"type": "callout", "kind": "info", "title": title,
                    "body": "원천이 확정되면 이 절을 다시 씁니다."},
        "table": {"type": "table", "title": title,
                  "row_grain": "행 1개 = 항목 1개",
                  "columns": [{"label": "항목"}, {"label": "값"}],
                  "rows": [["미확정", "원천 확정 후 기입"]]},
        "matrix": {"type": "matrix", "title": title, "corner": "구분",
                   "columns": ["In", "Out"], "rows": ["범위"],
                   "cells": [["미확정", "미확정"]]},
        "heatgrid": {"type": "heatgrid", "title": title,
                     "columns": ["A"], "rows": ["B"], "cells": [[0]]},
        "chart": {"type": "chart", "kind": "bar", "title": title,
                  "takeaway": "아직 데이터가 없어 판단할 수 없습니다",
                  "categories": ["미확정"],
                  "series": [{"name": "건수", "values": [0]}]},
        "diagram": {"type": "diagram",
                    "svg": '<svg xmlns="http://www.w3.org/2000/svg" '
                           'width="10" height="10"></svg>'},
        "risks": {"type": "risks", "items": [
            {"risk": "원천이 확정되지 않음", "impact": "높음",
             "mitigation": "기준일까지 담당자를 지정한다", "owner": "kykim"}]},
        "changelog": {"type": "changelog", "items": [
            {"version": "v0.1", "date": "2026-09-13", "change": "최초 작성"}]},
        "appendix_source": {"type": "appendix_source", "items": [
            {"name": "미확정", "path": "(없음)", "rows": 0, "as_of": "2026-09-13"}]},
    }.get(kind)


def _instantiate(name: str) -> dict:
    skel = json.loads((SOM / "standard" / "skeletons" /
                       f"{name}.skeleton.json").read_text(encoding="utf-8"))
    sections = [
        {"id": s["id"], "title": s["title"],
         "blocks": [b for b in (_block(k, s["title"]) for k in s.get("blocks", [])) if b]}
        for s in skel["sections"]
    ]
    return {
        "sds_version": 1,
        "doc_type": skel["doc_type"],
        "theme": skel["theme"],
        "docmeta": {"title": skel["title"], "org": "SOM", "as_of": "2026-09-13",
                    "version": "v0.1", "author": "kykim", "approver": "유닛장",
                    "next_review": "2026-12-31", "audience": "팀원",
                    "slug": f"som-{name}"},
        "metrics": {"kpi_count": {"value": 0, "unit": "개",
                                  "formula": "대장 행 수", "source": "미확정"}},
        "sections": sections,
    }


def _somdoc(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(SOM / "engine"), "PYTHONUTF8": "1"}
    return subprocess.run([sys.executable, "-m", "somdoc", *args], env=env,
                          cwd=str(cwd), capture_output=True, text=True,
                          timeout=300, encoding="utf-8", errors="replace")


def test_every_skeleton_instantiates_into_a_valid_ir():
    for name in SKELETONS:
        with tempfile.TemporaryDirectory(prefix="som-skel-") as tmp:
            p = Path(tmp) / f"{name}.somdoc.json"
            p.write_text(json.dumps(_instantiate(name), ensure_ascii=False, indent=2),
                         encoding="utf-8")
            r = _somdoc("validate", str(p), cwd=Path(tmp))
            assert r.returncode == 0, (
                f"{name} 스켈레톤이 validate 를 통과하지 못합니다:\n{r.stdout}{r.stderr}")


def test_every_skeleton_renders_a_complete_bundle():
    for name in SKELETONS:
        with tempfile.TemporaryDirectory(prefix="som-skel-") as tmp:
            tmp_p = Path(tmp)
            p = tmp_p / f"{name}.somdoc.json"
            p.write_text(json.dumps(_instantiate(name), ensure_ascii=False, indent=2),
                         encoding="utf-8")
            r = _somdoc("build", str(p), "--emit", "html,xlsx",
                        "--out", str(tmp_p / "out"), "--as-of", "2026-09-13",
                        cwd=tmp_p)
            assert r.returncode == 0, f"{name} 렌더 실패:\n{r.stdout}{r.stderr}"

            out = tmp_p / "out"
            html = next(out.glob("*.html"))
            assert (out / "MANIFEST.json").exists(), f"{name}: MANIFEST.json 이 없습니다"
            assert next(out.glob("*.xlsx"), None), f"{name}: xlsx 가 없습니다"
            assert next((out / "ir").glob("*.json"), None), f"{name}: ir 사본이 없습니다"

            # The manifest's hash is the whole reproducibility claim.
            manifest = json.loads((out / "MANIFEST.json").read_text(encoding="utf-8"))
            digest = hashlib.sha256(html.read_bytes()).hexdigest()
            recorded = [e["sha256"] for e in manifest["emitted"] if e["file"] == html.name]
            assert recorded == [digest], (
                f"{name}: MANIFEST 의 sha256 이 실제 파일과 다릅니다")

            # Self-contained: the SVG namespace is an identifier, not a request.
            body = html.read_text(encoding="utf-8")
            outbound = body.count("http://") + body.count("https://") \
                - body.count("http://www.w3.org/2000/svg")
            assert outbound == 0, f"{name}: 외부 참조 {outbound}건"


def test_rerendering_the_committed_ir_copy_gives_the_same_bytes():
    """The bundle says it is self-contained. This is that sentence, executed."""
    for name in SKELETONS:
        with tempfile.TemporaryDirectory(prefix="som-skel-") as tmp:
            tmp_p = Path(tmp)
            p = tmp_p / f"{name}.somdoc.json"
            p.write_text(json.dumps(_instantiate(name), ensure_ascii=False, indent=2),
                         encoding="utf-8")
            assert _somdoc("build", str(p), "--emit", "html",
                           "--out", str(tmp_p / "a"), "--as-of", "2026-09-13",
                           cwd=tmp_p).returncode == 0
            copy = next((tmp_p / "a" / "ir").glob("*.json"))
            assert _somdoc("build", str(copy), "--emit", "html",
                           "--out", str(tmp_p / "b"), "--as-of", "2026-09-13",
                           "--no-manifest", cwd=tmp_p).returncode == 0
            first = next((tmp_p / "a").glob("*.html")).read_bytes()
            again = next((tmp_p / "b").glob("*.html")).read_bytes()
            assert first == again, f"{name}: ir 사본으로 재렌더하면 바이트가 다릅니다"


# --------------------------------------------------------------------------
# the validator must report, never raise
# --------------------------------------------------------------------------
def _doc(*blocks) -> dict:
    return {"sds_version": 1, "doc_type": "rnr", "theme": "paper",
            "docmeta": {"title": "T", "org": "SOM", "as_of": "2026-09-13",
                        "version": "v0.1", "slug": "t"},
            "sections": [{"id": "s", "title": "S", "blocks": list(blocks)}]}


WRONG_SHAPES = {
    "table.columns as strings": _doc(
        {"type": "table", "row_grain": "행 1개 = 사람 1명",
         "columns": ["이름", "역할"], "rows": [["김", "분석"]]}),
    "table.columns as a bare string": _doc(
        {"type": "table", "row_grain": "g", "columns": "이름", "rows": []}),
    "table.columns with a null entry": _doc(
        {"type": "table", "row_grain": "g", "columns": [None], "rows": []}),
    "table.rows as strings": _doc(
        {"type": "table", "row_grain": "g", "columns": [{"label": "A"}],
         "rows": ["not a list"]}),
    "chart.series as strings": _doc(
        {"type": "chart", "kind": "bar", "takeaway": "t", "series": ["a", "b"]}),
    "chart.series as a dict": _doc(
        {"type": "chart", "kind": "bar", "takeaway": "t", "series": {"name": "n"}}),
    "decision_request.items as strings": _doc(
        {"type": "decision_request", "items": ["승인해 주세요"]}),
    "risks.items as strings": _doc({"type": "risks", "items": ["위험"]}),
    "appendix_source.items as strings": _doc(
        {"type": "appendix_source", "items": ["some.xlsx"]}),
    "kpi_tiles.items as strings": _doc({"type": "kpi_tiles", "items": ["지표"]}),
    "bullets.items as strings": _doc({"type": "bullets", "items": ["첫째"]}),
    "changelog.items as strings": _doc({"type": "changelog", "items": ["v1"]}),
    "matrix.cells as strings": _doc(
        {"type": "matrix", "columns": ["A"], "rows": ["B"], "cells": ["x"]}),
    "heatgrid.cells as strings": _doc(
        {"type": "heatgrid", "columns": ["A"], "rows": ["B"], "cells": ["x"]}),
    "a block that is a string": _doc("not a block"),
    "a block that is a list": _doc(["type", "table"]),
    "a null section": {"sds_version": 1, "doc_type": "rnr", "theme": "paper",
                       "docmeta": {"title": "T", "org": "SOM", "as_of": "2026-09-13",
                                   "version": "v0.1", "slug": "t"},
                       "sections": [None]},
    "sections as a dict": {"sds_version": 1, "doc_type": "rnr", "theme": "paper",
                           "docmeta": {"title": "T", "org": "SOM",
                                       "as_of": "2026-09-13", "version": "v0.1",
                                       "slug": "t"},
                           "sections": {"id": "s"}},
    "metrics as a list": {"sds_version": 1, "doc_type": "rnr", "theme": "paper",
                          "docmeta": {"title": "T", "org": "SOM",
                                      "as_of": "2026-09-13", "version": "v0.1",
                                      "slug": "t"},
                          "metrics": [{"k": 1}], "sections": []},
    "docmeta missing entirely": {"sds_version": 1, "doc_type": "rnr",
                                 "theme": "paper", "sections": []},
}


def test_validate_reports_wrong_shapes_instead_of_raising():
    crashed = []
    for name, ir in WRONG_SHAPES.items():
        try:
            IR.validate(ir)
        except IR.IRError:
            pass                                        # a refusal is a message
        except Exception as e:
            where = traceback.extract_tb(sys.exc_info()[2])[-1]
            crashed.append(f"{name}: {type(e).__name__} at ir.py:{where.lineno} — {e}")
    assert not crashed, ("검증기가 예외로 죽습니다 (문제를 보고해야 합니다):\n  "
                         + "\n  ".join(crashed))


def test_a_wrong_shape_is_named_well_enough_to_fix():
    """"must be a list" is not enough; the message has to say which field."""
    problems = IR.validate(WRONG_SHAPES["table.columns as strings"])
    hits = [p for p in problems if "columns[0]" in p]
    assert hits, f"columns[0] 을 지목하는 메시지가 없습니다: {problems}"
    assert "object" in hits[0] or "객체" in hits[0], hits[0]


def test_the_emitters_do_not_crash_on_a_document_validate_refused():
    """iter_blocks is shared with the emitters, so it must survive the same input."""
    for name, ir in WRONG_SHAPES.items():
        try:
            list(IR.iter_blocks(ir))
        except Exception as e:
            raise AssertionError(f"iter_blocks({name}) 가 죽었습니다: "
                                 f"{type(e).__name__}: {e}") from e


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print(f"  PASS  {n}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {n}: {e}")
        except Exception as e:
            # A test that raises is a failure, not a reason to abandon the run.
            # Catching only AssertionError meant one crash hid every later
            # test -- the same shape of bug these tests were written about.
            failed += 1
            print(f"  FAIL  {n}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
