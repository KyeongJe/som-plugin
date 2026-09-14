"""A teammate with nothing but Claude Code, following the README in order.

`test_bare_install.py` proves the document *renders* with every optional
package stubbed. This walks the path a person actually takes -- doctor, pick a
recipe, see the questions, settle the interview, run, render -- and asserts the
things that discourage someone from continuing:

  * `/som:doctor` is the first command the README names. It listed four of the
    six recipes as unavailable because it assumed everything but `doc` needed
    Orca. Five of six work. Telling someone their machine cannot do the work
    is worse than any missing feature.
  * `skills/doc-standard/SKILL.md` tells everyone to run `--emit html,xlsx`.
    Without openpyxl that raised an ImportError traceback *after* writing the
    HTML, leaving a bundle with no MANIFEST and no `ir/` copy.

Nothing here needs network, credentials, Orca, or a paid worker.

    python engine/tests/test_bare_walkthrough.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SOM = Path(__file__).resolve().parents[2]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Everything a teammate on a fresh laptop will not have.
OPTIONAL = ["pandas", "openpyxl", "sqlglot", "pyarrow", "snowflake", "docx",
            "pptx", "keyring"]


def bare_env(stub: Path) -> dict[str, str]:
    for name in OPTIONAL:
        (stub / f"{name}.py").write_text(
            "raise ImportError('simulated: not installed')\n", encoding="utf-8")
    return {
        **os.environ,
        "PYTHONPATH": f"{stub}{os.pathsep}{SOM / 'engine'}",
        "PYTHONUTF8": "1",
        # A name that cannot resolve, so any attempt to reach Orca fails.
        "ORCA_CLI_COMMAND": "__no_orca_on_this_machine__",
        # User site-packages off. A machine without the connector has no
        # `snowflake.pth` either, and leaving it on made the interpreter
        # print a traceback at startup trying to import the stub -- noise
        # that looked exactly like the crash these tests are checking for.
        "PYTHONNOUSERSITE": "1",
    }


def run(cmd: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          env=env, timeout=300, encoding="utf-8", errors="replace")


def node(*args: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    exe = shutil.which("node")
    assert exe, "node 를 찾을 수 없습니다."
    return run([exe, str(SOM / "bin" / "som.mjs"), *args],
               {**env, "SOM_PROJECT": str(cwd)}, cwd)


MINIMAL_IR = {
    "sds_version": 1, "doc_type": "rnr", "theme": "paper",
    "docmeta": {"title": "SOM R&R 정의 문서", "org": "SOM", "as_of": "2026-09-13",
                "version": "v0.1", "author": "kykim", "approver": "유닛장",
                "next_review": "2026-12-31", "audience": "팀원", "slug": "som-doc"},
    "metrics": {"headcount": {"value": 0, "unit": "명", "formula": "roster 행 수",
                              "source": "미확정 — 명단 없음"}},
    "sections": [
        {"id": "control", "title": "표지 · 문서 통제", "blocks": []},
        {"id": "decision", "title": "결정 요청", "blocks": [
            {"type": "decision_request", "items": [
                {"ask": "이 뼈대를 팀 표준으로 쓸지 확인해 주세요",
                 "rationale": "명단이 확정되면 같은 뼈대에 채워 넣습니다",
                 "owner": "유닛장", "due": "2026-09-30"}]}]},
        {"id": "glance", "title": "한눈에", "blocks": [
            {"type": "callout", "kind": "warn", "title": "아직 미확정입니다",
             "body": "명단 원천이 없어 인원 절은 비어 있습니다. 지어내지 않았습니다."}]},
        {"id": "matrix", "title": "인원별 R&R", "blocks": [
            {"type": "table", "title": "인원별 R&R", "row_grain": "행 1개 = 팀원 1명",
             "columns": [{"label": "이름"}, {"label": "역할"},
                         {"label": "시간 비중", "align": "num"}],
             "rows": [["미확정", "명단 확정 후 기입", "—"]]}]},
        {"id": "changelog", "title": "변경 이력", "blocks": [
            {"type": "changelog", "items": [
                {"version": "v0.1", "date": "2026-09-13", "change": "뼈대만 생성"}]}]},
        {"id": "appendix", "title": "부록 — 원천", "blocks": [
            {"type": "appendix_source", "items": [
                {"name": "미확정", "path": "(명단 없음)", "rows": 0,
                 "as_of": "2026-09-13"}]}]},
    ],
}


def test_doctor_does_not_tell_a_bare_machine_it_cannot_work():
    with tempfile.TemporaryDirectory(prefix="som-bare-") as tmp:
        tmp_p = Path(tmp)
        stub = tmp_p / "stub"; stub.mkdir()
        proj = tmp_p / "proj"; proj.mkdir()
        env = bare_env(stub)

        r = run([sys.executable, str(SOM / "standard" / "scripts" / "doctor.py"),
                 "--project", str(proj)], env, proj)
        assert r.returncode in (0, 2), f"doctor 가 {r.returncode} 로 끝났습니다:\n{r.stdout}"
        out = r.stdout
        assert "0 fail" in out, f"맨몸 머신에서 FAIL 이 있습니다:\n{out}"

        ready = out.split("지금 쓸 수 있는 것", 1)[-1].split("아직 안 되는 것", 1)[0]
        blocked = out.split("아직 안 되는 것", 1)[-1] if "아직 안 되는 것" in out else ""

        # Only a genuinely missing package blocks a recipe. Orca does not.
        for phrase in ['"R&R 문서 만들어줘"', '"밤새 확인해줘"', '"PRD 필요해"',
                       '"스크립트 만들어줘"']:
            assert phrase in ready, (
                f"{phrase} 이 '지금 쓸 수 있는 것' 에 없습니다 — Claude 만으로 됩니다.\n"
                f"{out}")
        for phrase in ['"이 엑셀 분석해줘"', '"Snowflake 에서 뽑아줘"']:
            assert phrase in blocked, f"{phrase} 은 패키지가 없으면 막혀야 합니다"

        assert "Orca 필요" not in blocked, (
            "Orca 를 차단 조건으로 표시했습니다. 없어도 전부 됩니다.\n" + blocked)


def test_the_cli_walks_the_documented_path_without_orca():
    with tempfile.TemporaryDirectory(prefix="som-bare-") as tmp:
        tmp_p = Path(tmp)
        stub = tmp_p / "stub"; stub.mkdir()
        proj = tmp_p / "proj"; proj.mkdir()
        env = bare_env(stub)
        req = "R&R 문서 만들어줘. 명단은 없고 뼈대만, 승인받을 건 없고, 팀원이 봅니다"

        r = node("conduct", "choose", req, env=env, cwd=proj)
        assert r.returncode == 0 and "doc" in r.stdout, f"레시피 선택 실패: {r.stdout}{r.stderr}"

        r = node("conduct", "asks", "--recipe", "doc", env=env, cwd=proj)
        assert r.returncode == 2, "빠진 슬롯이 있으면 2 로 끝나야 합니다"
        for slot in ("docType", "source", "decision", "audience"):
            assert slot in r.stdout, f"{slot} 질문이 없습니다"
        assert "왜:" in r.stdout, "질문에 이유가 붙지 않았습니다"

        r = node("interview", "new", req, "--recipe", "doc", env=env, cwd=proj)
        assert r.returncode == 0, f"interview new 실패: {r.stdout}{r.stderr}"
        slug = next(l.split(" ", 1)[1] for l in r.stdout.splitlines()
                    if l.startswith("slug:"))

        assert node("interview", "topology", slug, "--components",
                    '[{"id":"doc","name":"R&R 문서"}]',
                    env=env, cwd=proj).returncode == 0

        # Round 0.5: score what the person already said, quoting them.
        scores = json.dumps({
            "goal": {"score": 1, "justification": "원문: R&R 문서, 뼈대만", "gap": ""},
            "constraints": {"score": 1, "justification": "원문: 명단 없음, 팀원 대상",
                            "gap": ""},
            "criteria": {"score": 1, "justification": "원문: 승인 사항 없음", "gap": ""},
        }, ensure_ascii=False)
        r = node("interview", "round", slug, "--component", "doc",
                 "--dimension", "goal", "--question", "(먼저 말한 내용에서 채점)",
                 "--answer", req, "--scores", scores, env=env, cwd=proj)
        assert r.returncode == 0, f"한 라운드로 게이트가 열려야 합니다:\n{r.stdout}"
        assert "시작할 수 있습니다" in r.stdout, r.stdout

        answers = proj / "ans.json"
        answers.write_text(json.dumps(
            {"docType": "rnr", "source": "없음", "decision": "없음",
             "audience": "팀원"}, ensure_ascii=False), encoding="utf-8")

        r = node("conduct", "run", req, "--recipe", "doc",
                 "--answers", str(answers), env=env, cwd=proj)
        assert r.returncode == 0, f"Orca 없이 실행이 실패했습니다:\n{r.stdout}{r.stderr}"
        assert "순차" in r.stdout, "순차 판정을 보고하지 않았습니다"
        assert "Orca" not in r.stderr, f"Orca 를 찾으려 했습니다: {r.stderr[:200]}"
        for step in ("intake", "ir", "render", "attest"):
            assert step in r.stdout, f"{step} 단계가 인계되지 않았습니다"
        assert "통과 조건" in r.stdout, "각 단계의 통과 조건이 없습니다"


def test_the_documented_render_command_degrades_instead_of_crashing():
    """`--emit html,xlsx` is what doc-standard tells everyone to run."""
    with tempfile.TemporaryDirectory(prefix="som-bare-") as tmp:
        tmp_p = Path(tmp)
        stub = tmp_p / "stub"; stub.mkdir()
        proj = tmp_p / "proj"; (proj / "ir").mkdir(parents=True)
        env = bare_env(stub)

        ir_path = proj / "ir" / "som-doc.somdoc.json"
        ir_path.write_text(json.dumps(MINIMAL_IR, ensure_ascii=False, indent=2),
                           encoding="utf-8")

        r = run([sys.executable, "-m", "somdoc", "validate", str(ir_path)], env, proj)
        assert r.returncode == 0, f"validate 실패:\n{r.stdout}{r.stderr}"

        out_dir = proj / "docs" / "som-doc"
        r = run([sys.executable, "-m", "somdoc", "build", str(ir_path),
                 "--emit", "html,xlsx", "--out", str(out_dir),
                 "--as-of", "2026-09-13"], env, proj)

        assert "Traceback" not in r.stderr, (
            "openpyxl 이 없다고 스택 트레이스를 냈습니다:\n" + r.stderr[:600])
        assert r.returncode == 2, (
            f"부분 산출은 exit 2 여야 합니다 (실제 {r.returncode}):\n{r.stdout}{r.stderr}")
        assert "openpyxl" in r.stdout, "무엇을 설치해야 하는지 말하지 않았습니다"

        # The bundle is complete for what it could produce.
        html = next(out_dir.glob("*.html"))
        assert (out_dir / "MANIFEST.json").exists(), "MANIFEST 가 없습니다"
        assert next((out_dir / "ir").glob("*.json"), None), "ir 사본이 없습니다"
        assert not list(out_dir.glob("*.xlsx")), "만들 수 없는 xlsx 가 생겼습니다"

        man = json.loads((out_dir / "MANIFEST.json").read_text(encoding="utf-8"))
        assert [e["sha256"] for e in man["emitted"] if e["file"] == html.name] == \
               [hashlib.sha256(html.read_bytes()).hexdigest()], "sha256 불일치"
        assert any(x.get("format") == "xlsx" for x in man.get("skipped", [])), (
            "MANIFEST 가 xlsx 를 건너뛴 사실을 기록하지 않았습니다 — "
            "폴더를 받은 사람이 알 수 없습니다")


def test_the_bare_document_keeps_its_promises():
    with tempfile.TemporaryDirectory(prefix="som-bare-") as tmp:
        tmp_p = Path(tmp)
        stub = tmp_p / "stub"; stub.mkdir()
        proj = tmp_p / "proj"; (proj / "ir").mkdir(parents=True)
        env = bare_env(stub)
        ir_path = proj / "ir" / "som-doc.somdoc.json"
        ir_path.write_text(json.dumps(MINIMAL_IR, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        out_dir = proj / "docs" / "som-doc"
        run([sys.executable, "-m", "somdoc", "build", str(ir_path),
             "--emit", "html", "--out", str(out_dir), "--as-of", "2026-09-13"],
            env, proj)

        body = next(out_dir.glob("*.html")).read_text(encoding="utf-8")
        outbound = (body.count("http://") + body.count("https://")
                    - body.count("http://www.w3.org/2000/svg"))
        assert outbound == 0, f"외부 참조 {outbound}건 — 망분리 랩탑에서 깨집니다"
        assert "행 1개 = 팀원 1명" in body, "표의 행 단위가 렌더되지 않았습니다"
        assert "미확정" in body, "모르는 것을 미확정으로 남기지 않았습니다"


def test_a_missing_ir_is_a_sentence_not_a_traceback():
    """The path doc-standard's own example command uses, before it exists."""
    with tempfile.TemporaryDirectory(prefix="som-bare-") as tmp:
        tmp_p = Path(tmp)
        stub = tmp_p / "stub"; stub.mkdir()
        proj = tmp_p / "proj"; proj.mkdir()
        env = bare_env(stub)
        r = run([sys.executable, "-m", "somdoc", "validate",
                 "ir/som-rnr.somdoc.json"], env, proj)
        assert "Traceback" not in r.stderr, "없는 파일에 스택을 냈습니다:\n" + r.stderr[:400]
        assert r.returncode == 1
        assert "없습니다" in r.stdout, r.stdout


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
            failed += 1
            print(f"  FAIL  {n}: {type(e).__name__}: {e}")
    print()
    print(f"{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
