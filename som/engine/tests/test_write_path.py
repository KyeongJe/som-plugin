"""The write path: describe, ask, then run.

Writes were refused outright until this team needed to create a table, and the
refusal turned out to block work the plugin had nothing to do with. So the
question changed from "is this a write?" to "does the person know what this
write does, and did they say yes to *this* statement?"

That makes three things worth testing, and none of them need a connection:

  1. the description is true -- right verb, right objects, and the warnings
     fire on the cases that actually hurt (no WHERE, DROP without IF EXISTS,
     OR REPLACE over something that already exists)
  2. the approval is bound to the statement -- a yes to one thing cannot carry
     a different thing, and editing a character invalidates it
  3. the ledger records what happened, including the refusals

The fourth thing -- that the statement reaches Snowflake -- is the one part
that cannot be tested here, so `write_sql` takes a connection and the test
hands it a fake one. What the fake proves is the ordering: nothing is sent
until the approval and the reason are both present.

    python engine/tests/test_write_path.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))

from somsql import conn as CN             # noqa: E402
from somsql import describe as D          # noqa: E402
from somsql import ledger as L            # noqa: E402


# ======================================================= describing a write
def test_a_read_is_not_a_write():
    """No ceremony for a read. `describe` returning None is what says so."""
    for sql in [
        "SELECT 1",
        "SELECT a, b FROM ANALYTICS.T WHERE d >= '2026-01-01'",
        "WITH x AS (SELECT 1 a) SELECT * FROM x",
        "SHOW TABLES",
        "DESCRIBE TABLE ANALYTICS.T",
        "EXPLAIN SELECT 1",
        "-- INSERT INTO t VALUES(1)\nSELECT 1",          # the verb is in a comment
    ]:
        assert D.describe(sql) is None, f"읽기를 쓰기로 봤습니다: {sql}"


def test_every_verb_in_the_table_is_described():
    """A verb with no description is the one that gets approved blind."""
    for verb, (what, _rev, sev) in D.OPERATIONS.items():
        assert what.strip(), verb
        assert sev in ("low", "medium", "high"), (verb, sev)
        assert not what.startswith("무엇을 하는지"), \
            f"{verb} 이 fallback 문구를 쓰고 있습니다"


def test_the_verb_and_the_target_are_read_off_the_statement():
    cases = [
        ("INSERT INTO ANALYTICS.PUBLIC.T SELECT * FROM ANALYTICS.PUBLIC.SRC", "INSERT", "ANALYTICS.PUBLIC.T"),
        ("UPDATE ANALYTICS.PUBLIC.T SET a = 1 WHERE id = 2", "UPDATE", "ANALYTICS.PUBLIC.T"),
        ("DELETE FROM ANALYTICS.PUBLIC.T WHERE id = 2", "DELETE", "ANALYTICS.PUBLIC.T"),
        ("CREATE TABLE ANALYTICS.PUBLIC.NEW_T (a int)", "CREATE", "ANALYTICS.PUBLIC.NEW_T"),
        ("DROP TABLE IF EXISTS ANALYTICS.PUBLIC.OLD_T", "DROP", "ANALYTICS.PUBLIC.OLD_T"),
        ("TRUNCATE TABLE ANALYTICS.PUBLIC.T", "TRUNCATE", "ANALYTICS.PUBLIC.T"),
        ("MERGE INTO ANALYTICS.PUBLIC.T t USING SRC s ON t.id = s.id "
         "WHEN MATCHED THEN UPDATE SET t.a = s.a", "MERGE", "ANALYTICS.PUBLIC.T"),
    ]
    for sql, verb, target in cases:
        d = D.describe(sql)
        assert d is not None, sql
        assert d.verb == verb, f"{sql} -> {d.verb}"
        assert target in d.objects, f"{sql} -> {d.objects}"


def test_a_ddl_form_sqlglot_models_as_a_command_still_names_its_target():
    """sqlglot returns several DDL shapes with no Table node at all.

    Reporting "(대상을 읽어내지 못함)" for a routine GRANT would train people
    to approve past the target line, which is the line that matters most.
    """
    for sql, target in [
        ("GRANT SELECT ON TABLE ANALYTICS.PUBLIC.T TO ROLE R", "ANALYTICS.PUBLIC.T"),
        ("CREATE OR REPLACE VIEW ANALYTICS.PUBLIC.V AS SELECT 1", "ANALYTICS.PUBLIC.V"),
        ("CREATE SCHEMA ANALYTICS.NEW_S", "ANALYTICS.NEW_S"),
    ]:
        d = D.describe(sql)
        assert d is not None, sql
        assert any(target in o for o in d.objects), f"{sql} -> {d.objects}"


def test_no_predicate_is_the_warning_that_matters_most():
    """UPDATE and DELETE without WHERE hit every row. That is the whole test."""
    for sql in ["UPDATE ANALYTICS.PUBLIC.T SET a = 1", "DELETE FROM ANALYTICS.PUBLIC.T"]:
        d = D.describe(sql)
        assert any("WHERE" in w for w in d.warnings), (sql, d.warnings)
        assert d.severity == "high", sql
    # And it does not fire when there is one.
    d = D.describe("DELETE FROM ANALYTICS.PUBLIC.T WHERE id = 1")
    assert not any("WHERE" in w for w in d.warnings), d.warnings


def test_or_replace_says_what_it_replaces():
    d = D.describe("CREATE OR REPLACE TABLE ANALYTICS.PUBLIC.T (a int)")
    assert any("OR REPLACE" in w for w in d.warnings), d.warnings
    assert d.severity == "high"


def test_drop_without_if_exists_is_flagged_as_an_error_not_a_danger():
    d = D.describe("DROP TABLE ANALYTICS.PUBLIC.T")
    assert any("IF EXISTS" in w for w in d.warnings), d.warnings
    d2 = D.describe("DROP TABLE IF EXISTS ANALYTICS.PUBLIC.T")
    assert not any("IF EXISTS" in w for w in d2.warnings), d2.warnings


def test_a_procedure_call_says_it_cannot_see_inside():
    d = D.describe("CALL ANALYTICS.PUBLIC.REBUILD_ALL()")
    assert d.verb == "CALL"
    assert d.reversible is False
    assert any("프로시저" in w for w in d.warnings), d.warnings


def test_a_temp_object_is_not_alarming():
    d = D.describe("CREATE TEMPORARY TABLE SCRATCH (a int)")
    assert d.severity == "low", d.severity


def test_a_keyword_is_never_reported_as_the_target():
    """Found by writing this test, and it was the worst kind of bug.

    The fallback regex took the word after the verb, so an INSERT whose target
    is a session variable described itself as writing to a table called
    "INTO" -- a confidently wrong answer on the one line a person actually
    reads before approving. Reporting nothing raises the severity and says
    "SQL 을 직접 확인하세요", which is the honest answer.
    """
    d = D.describe("INSERT INTO IDENTIFIER($t) VALUES (1)")
    assert d.verb == "INSERT"
    assert d.objects == [], d.objects
    assert d.severity == "high", d.severity
    assert any("대상" in w for w in d.warnings), d.warnings


def test_a_stage_operation_names_the_stage():
    """The other half of the same bug: `PUT file://a.csv @~/stage` reported
    its target as "file", the URL scheme."""
    for sql, target in [
        ("PUT file://a.csv @~/stage", "@~/stage"),
        ("REMOVE @~/stage/a.csv", "@~/stage/a.csv"),
    ]:
        d = D.describe(sql)
        assert d.objects == [target], f"{sql} -> {d.objects}"


# ============================================== the approval is a statement
def test_the_hash_survives_reindenting_and_not_an_edit():
    a = "DELETE FROM ANALYTICS.PUBLIC.T WHERE id = 1"
    same = "DELETE  FROM ANALYTICS.PUBLIC.T\n   WHERE id = 1\n"
    other = "DELETE FROM ANALYTICS.PUBLIC.T WHERE id = 2"
    assert D.sql_hash(a) == D.sql_hash(same), "들여쓰기만 바뀌어도 승인이 무효가 됩니다"
    assert D.sql_hash(a) != D.sql_hash(other), "한 글자 고쳐도 같은 승인이 통합니다"


def test_the_confirm_block_carries_everything_needed_to_decide():
    d = D.describe("DELETE FROM ANALYTICS.PUBLIC.VISIT_GAP")
    block = D.confirm_block(d, path="sql/90_purge.sql")
    for needed in ["SOMSQL-CONFIRM", "DELETE", "ANALYTICS.PUBLIC.VISIT_GAP",
                   "sql/90_purge.sql", d.sql_sha256[:12], "somsql write",
                   "--approve", "--reason", "WHERE"]:
        assert needed in block, f"확인 블록에 {needed!r} 이 없습니다:\n{block}"


def test_the_block_prints_the_command_that_would_run_it():
    """A question with no answerable next step gets routed around."""
    d = D.describe("CREATE TABLE ANALYTICS.PUBLIC.T (a int)")
    block = D.confirm_block(d, path="sql/10_new.sql")
    line = [x for x in block.splitlines() if "somsql write" in x]
    assert line, block
    assert "--file sql/10_new.sql" in line[0], line[0]
    assert f"--approve {d.sql_sha256[:12]}" in line[0], line[0]


# ================================================== write_sql, no connection
class FakeCursor:
    def __init__(self, sink):
        self.sink = sink
        self.rowcount = 7

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        self.sink.append(sql)


class FakeConn:
    """Records what was sent. Sending nothing is the assertion in most tests."""

    def __init__(self):
        self.sent: list[str] = []

    def cursor(self):
        return FakeCursor(self.sent)

    def close(self):
        pass


def _project():
    d = tempfile.mkdtemp(prefix="somsql-write-")
    return d


def _ledger(project) -> list[dict]:
    return L.read(project)


def test_a_read_sent_to_write_is_turned_back():
    try:
        CN.write_sql("SELECT 1", approve="deadbeef", reason="x", conn=FakeConn())
    except CN.Denied as e:
        assert e.code == "NOT_A_WRITE", e.code
    else:
        raise AssertionError("읽기가 쓰기 경로로 실행됐습니다")


def test_without_an_approval_nothing_is_sent_and_the_question_is_asked():
    p = _project()
    c = FakeConn()
    sql = "DELETE FROM ANALYTICS.PUBLIC.T WHERE id = 1"
    try:
        CN.write_sql(sql, reason="정리", conn=c, project=p, path="sql/90.sql")
    except CN.NeedsApproval as e:
        assert "SOMSQL-CONFIRM" in e.block
        assert e.description.verb == "DELETE"
    else:
        raise AssertionError("승인 없이 실행됐습니다")
    assert c.sent == [], f"승인 전에 문장이 전송됐습니다: {c.sent}"
    rows = [r for r in _ledger(p) if r.get("kind") == "write_attempt"]
    assert rows and rows[-1]["verdict"] == "needs_approval", _ledger(p)


def test_an_approval_for_a_different_statement_does_not_carry():
    """The point of hashing the statement rather than issuing a token."""
    p = _project()
    c = FakeConn()
    a = "DELETE FROM ANALYTICS.PUBLIC.T WHERE id = 1"
    b = "DELETE FROM ANALYTICS.PUBLIC.T WHERE id = 2"
    approval_for_a = D.sql_hash(a)[:12]
    try:
        CN.write_sql(b, approve=approval_for_a, reason="정리", conn=c, project=p)
    except CN.NeedsApproval:
        pass
    else:
        raise AssertionError("다른 문장의 승인이 통했습니다")
    assert c.sent == []


def test_an_edit_after_approval_invalidates_it():
    p = _project()
    c = FakeConn()
    original = "UPDATE ANALYTICS.PUBLIC.T SET a = 1 WHERE id = 1"
    approval = D.sql_hash(original)[:12]
    edited = original.replace("a = 1", "a = 99")
    try:
        CN.write_sql(edited, approve=approval, reason="정정", conn=c, project=p)
    except CN.NeedsApproval:
        pass
    else:
        raise AssertionError("승인 후 수정된 문장이 그대로 실행됐습니다")
    assert c.sent == []


def test_the_no_connection_path_actually_opens_one():
    """The bug this test exists for: `write_sql` called `connect()`.

    There is no `connect` in that module -- the connection is a context
    manager named `connection` -- so every real write ended in a NameError
    traceback *after* the approval had been accepted. Nothing caught it,
    because every other test passes a connection in and the one path that
    opens its own needs credentials this machine does not have.

    So the context manager is replaced here and the statement followed through
    it. No credentials, and the wiring is still checked.
    """
    import contextlib
    p = _project()
    c = FakeConn()
    opened = []

    @contextlib.contextmanager
    def fake_connection(config_path=None, *, mode="explore"):
        opened.append(mode)
        yield c

    real = CN.connection
    CN.connection = fake_connection
    try:
        sql = "CREATE TABLE ANALYTICS.PUBLIC.T3 (a int)"
        res = CN.write_sql(sql, approve=D.sql_hash(sql)[:12], reason="배선 확인",
                           project=p)
    finally:
        CN.connection = real

    assert opened == ["full"], f"연결을 열지 않았거나 모드가 틀립니다: {opened}"
    assert c.sent == [sql], c.sent
    assert res.rows_affected == 7


def test_an_approval_without_a_reason_is_refused_and_recorded():
    """The ledger line has to explain itself six months later.

    Recorded as well as refused: somebody had the right hash and meant to run
    this, and a refusal that leaves no trace is the one that gets routed
    around next time.
    """
    p = _project()
    c = FakeConn()
    sql = "CREATE TABLE ANALYTICS.PUBLIC.NEW_T (a int)"
    try:
        CN.write_sql(sql, approve=D.sql_hash(sql)[:12], reason="   ",
                     conn=c, project=p)
    except CN.Denied as e:
        assert e.code == "NO_REASON", e.code
    else:
        raise AssertionError("사유 없이 실행됐습니다")
    assert c.sent == []
    rows = [r for r in _ledger(p) if r.get("verdict") == "denied_no_reason"]
    assert rows, _ledger(p)


def test_an_approved_write_runs_once_and_lands_in_the_ledger():
    p = _project()
    c = FakeConn()
    sql = "CREATE TABLE ANALYTICS.PUBLIC.NEW_T (a int)"
    res = CN.write_sql(sql, approve=D.sql_hash(sql)[:12], reason="신규 집계 테이블",
                       approver="kykim", conn=c, project=p, path="sql/10_new.sql")
    assert c.sent == [sql], c.sent
    assert res.rows_affected == 7
    assert res.description.verb == "CREATE"

    rows = [r for r in _ledger(p) if r.get("kind") == "write"]
    assert len(rows) == 1, _ledger(p)
    r = rows[0]
    assert r["verdict"] == "executed"
    assert r["verb"] == "CREATE"
    assert r["reason"] == "신규 집계 테이블"
    assert r["approver"] == "kykim"
    assert r["sql_hash"] == D.sql_hash(sql)
    assert r["sql_path"] == "sql/10_new.sql"


def test_the_write_log_names_the_verb_the_target_and_the_reason():
    """The markdown log is what a person actually reads."""
    p = _project()
    c = FakeConn()
    sql = "DELETE FROM ANALYTICS.PUBLIC.T WHERE id = 1"
    CN.write_sql(sql, approve=D.sql_hash(sql)[:12], reason="중복 행 제거",
                 approver="kykim", conn=c, project=p)
    md = L.write_log_md(p).read_text(encoding="utf-8")
    for needed in ["DELETE", "ANALYTICS.PUBLIC.T", "중복 행 제거", "kykim"]:
        assert needed in md, f"WRITE_LOG.md 에 {needed!r} 이 없습니다:\n{md}"


def test_a_case_insensitive_or_padded_approval_still_matches():
    """The person copies the hash out of a terminal. Do not punish a space."""
    p = _project()
    c = FakeConn()
    sql = "CREATE TABLE ANALYTICS.PUBLIC.T2 (a int)"
    h = D.sql_hash(sql)[:12]
    CN.write_sql(sql, approve=f"  {h.upper()}  ", reason="테스트", conn=c, project=p)
    assert c.sent == [sql]


# ================================================== the CLI a person actually types
def _somsql(*args, cwd):
    env = {**os.environ, "PYTHONPATH": str(ENGINE), "PYTHONUTF8": "1",
           "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-m", "somsql", *args], cwd=cwd,
                          capture_output=True, text=True, env=env, timeout=180,
                          encoding="utf-8", errors="replace")


def test_run_describes_a_write_instead_of_refusing_it():
    """The path a person takes by accident: `somsql run` on a file that writes.

    It used to come back as a flat refusal. Exit 4 is not a failure -- it is
    the question, and the block it prints is the answer to "then what".
    """
    p = _project()
    f = Path(p) / "90_purge.sql"
    f.write_text("DELETE FROM ANALYTICS.PUBLIC.T WHERE id = 1;\n", encoding="utf-8")
    r = _somsql("run", "--file", str(f), cwd=p)
    assert r.returncode == 4, f"exit={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "SOMSQL-CONFIRM" in r.stdout, r.stdout
    assert "somsql write" in r.stdout, r.stdout


def test_write_dry_run_asks_without_connecting():
    """No credentials on this machine, and none needed to see the question."""
    p = _project()
    f = Path(p) / "10_new.sql"
    f.write_text("CREATE TABLE ANALYTICS.PUBLIC.NEW_T (a int);\n", encoding="utf-8")
    r = _somsql("write", "--file", str(f), "--dry-run", cwd=p)
    assert r.returncode == 4, f"exit={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "CREATE" in r.stdout and "ANALYTICS.PUBLIC.NEW_T" in r.stdout, r.stdout


def test_write_with_a_wrong_hash_asks_again_rather_than_running():
    p = _project()
    f = Path(p) / "90_purge.sql"
    f.write_text("DELETE FROM ANALYTICS.PUBLIC.T;\n", encoding="utf-8")
    r = _somsql("write", "--file", str(f), "--approve", "000000000000",
                "--reason", "테스트", cwd=p)
    assert r.returncode == 4, f"exit={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "SOMSQL-CONFIRM" in r.stdout, r.stdout


def test_write_without_a_reason_stops_before_any_connection_attempt():
    """Order matters: the reason is checked before SSO, so a missing --reason
    costs a message rather than a browser popup and a wasted minute."""
    p = _project()
    sql = "DELETE FROM ANALYTICS.PUBLIC.T;"
    f = Path(p) / "90_purge.sql"
    f.write_text(sql + "\n", encoding="utf-8")
    r = _somsql("write", "--file", str(f),
                "--approve", D.sql_hash(sql)[:12], cwd=p)
    assert r.returncode == 3, f"exit={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert "--reason" in r.stdout, r.stdout


def test_write_on_a_read_points_back_at_run():
    p = _project()
    f = Path(p) / "01_read.sql"
    f.write_text("SELECT 1;\n", encoding="utf-8")
    r = _somsql("write", "--file", str(f), cwd=p)
    assert r.returncode != 0
    assert "somsql run" in r.stdout, r.stdout


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
