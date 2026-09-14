"""Classifier tests.

The first block is the regression suite for the three holes in the existing
`sf_utils.py` helper. Those are not hypothetical -- each was verified against
that code's actual logic before this module was written.

    python engine/tests/test_somsql.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))

from somsql import classify as C          # noqa: E402
from somsql import guard as G            # noqa: E402
from somsql import registry as R          # noqa: E402


def allow(sql: str, **kw) -> C.Classification:
    c = C.classify(sql, **kw)
    assert c.allowed, f"expected ALLOW, got DENY: {[f.code for f in c.findings]} :: {sql}"
    return c


def deny(sql: str, code: str | None = None, **kw) -> C.Classification:
    c = C.classify(sql, **kw)
    assert not c.allowed, f"expected DENY, got ALLOW :: {sql}"
    if code:
        codes = [f.code for f in c.findings]
        assert code in codes, f"expected {code} in {codes} :: {sql}"
    return c


# ===================================================== the three known holes
def test_hole_1_cte_wrapping_a_write():
    """First-token checks pass this. The root is Insert, and the walk sees it."""
    c = deny("WITH x AS (SELECT 1 a) INSERT INTO t SELECT * FROM x")
    assert c.roots == ["Insert"], c.roots


def test_hole_1b_cte_wrapping_an_update():
    deny("WITH c AS (SELECT 1 a) UPDATE t SET b = 1", "ROOT_NOT_A_READ")


def test_hole_1c_select_into_is_a_write():
    """Root is Select, so only the full walk catches this one."""
    deny("SELECT a INTO t2 FROM t", "WRITE_NODE_NESTED")


def test_hole_2_semicolon_inside_a_string_literal():
    """Naive `;` splitting mangles this into two chunks and misreads it."""
    allow("SELECT a FROM t WHERE n = 'a;DROP TABLE t'")


def test_hole_2b_dollar_quoted_semicolon():
    allow("SELECT $$a;b$$ AS x")


def test_hole_3_unknown_statement_kind_is_denied_by_omission():
    """A deny-list passes anything not on it. A whitelist does not."""
    deny("PUT file://a @s", "ROOT_NOT_A_READ")
    deny("GET @s file://a", "ROOT_NOT_A_READ")
    deny("CALL some_proc()", "ROOT_NOT_A_READ")


# ============================================================== reads pass
def test_plain_reads_pass():
    for sql in (
        "SELECT a FROM t WHERE d >= '2026-01-01'",
        "WITH x AS (SELECT 1 a) SELECT * FROM x",
        "SELECT a FROM t UNION SELECT a FROM u",
        "SELECT a FROM t INTERSECT SELECT a FROM u",
        "SHOW TABLES",
        "DESCRIBE TABLE t",
        'SELECT * FROM RAW.BI."EXT.ORDER_FACT" WHERE DOCDATE >= \'2026-01-01\'',
    ):
        allow(sql)


def test_keywords_in_comments_do_not_trip_the_gate():
    allow("/* DROP TABLE t */ SELECT 1")
    allow("-- DROP TABLE t\nSELECT 1")


def test_column_named_offset_is_not_a_set_statement():
    allow("SELECT offset_days FROM t WHERE offset_days > 1")


# ============================================================ writes denied
def test_every_write_kind_is_denied():
    for sql in (
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t WHERE a = 1",
        "MERGE INTO t USING s ON t.a = s.a WHEN MATCHED THEN UPDATE SET t.b = s.b",
        "CREATE VIEW v AS SELECT 1",
        "CREATE TEMPORARY TABLE t AS SELECT 1",
        "DROP TABLE t",
        "ALTER TABLE t ADD COLUMN c INT",
        "TRUNCATE TABLE t",
        "GRANT SELECT ON t TO ROLE r",
        "REVOKE SELECT ON t FROM ROLE r",
        "COPY INTO t FROM @stage",
        "USE WAREHOUSE wh",
        "SET x = 1",
    ):
        deny(sql)


def test_garbage_is_denied_not_allowed():
    """sqlglot parses `SELEKT * FRM t` as an Alias rather than failing, so the
    root whitelist is what keeps this closed."""
    c = deny("SELEKT * FRM t", "ROOT_NOT_A_READ")
    assert c.roots and c.roots[0] not in ("Select",), c.roots


def test_multi_statement_is_denied_for_agent_sql():
    deny("SELECT 1; SELECT 2", "MULTI_STATEMENT")
    allow("SELECT 1; SELECT 2", allow_multi=True)


def test_multi_statement_with_a_write_is_denied_even_when_multi_is_allowed():
    deny("SELECT 1; DROP TABLE t", allow_multi=True)


def test_empty_is_denied():
    deny("", "EMPTY")
    deny("   \n  ", "EMPTY")


# ==================================================== gate disagreement
def test_disagreement_is_recorded_and_denied():
    """A statement the parser reads as a Select but whose text carries a bare
    write token.

    Double quotes are identifiers in Snowflake, not string literals, so the
    token gate legitimately sees INSERT here while the parser sees a column
    reference. That is a real disagreement and it is denied: a column actually
    named "INSERT" is rarer than a bypass attempt, and the deny block tells the
    human which gate to overrule. Conservative on purpose.
    """
    c = C.classify('SELECT "INSERT" FROM t')
    codes = [f.code for f in c.findings]
    assert not c.allowed, "a token-gate hit must deny even when the parser allows"
    assert "CLASSIFIER_DISAGREE" in codes, codes
    f = next(x for x in c.findings if x.code == "CLASSIFIER_DISAGREE")
    assert f.gate == "both"
    assert "INSERT" in f.matched.upper()


def test_keyword_inside_an_identifier_does_not_trip_the_gate():
    """A word boundary does not match between E and _, so EXECUTE_FLAG
    is not EXECUTE.
    Identifiers that merely contain a keyword must pass."""
    allow("SELECT execute_plan_note, EXECUTE_FLAG, inserted_at FROM t")
    allow("SELECT created_on, updated_by FROM t")


# ============================================================= deny block
def test_deny_block_is_structured_and_actionable():
    sql = "WITH x AS (SELECT 1 a) INSERT INTO t SELECT * FROM x"
    c = C.classify(sql)
    block = C.deny_block(sql, c, path="sql/03_x.sql")
    for needle in ("SOMSQL-DENY", "code=", "gate=", "sql/03_x.sql",
                   "somsql write", "Do not rewrite"):
        assert needle in block, f"deny block missing {needle!r}\n{block}"
    # It must not read as an apology the agent can talk past.
    assert "sorry" not in block.lower()
    # And it must not still claim writes are impossible. The block used to say
    # "No autonomy level unlocks this", which is now false: it would send the
    # reader hunting for a workaround instead of at `somsql write`.
    assert "No autonomy level" not in block, block


# =============================================================== registry
#
# The object list used to be constants in registry.py, which published one
# company's schema map in a public repository and fitted nobody else's tables.
# It is a site profile on the local disk now, so these tests write one.
def _profile(tmp: Path, data: dict) -> "R.SiteProfile":
    (tmp / ".som").mkdir(parents=True, exist_ok=True)
    (tmp / ".som" / R.PROFILE_NAME).write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return R.SiteProfile(tmp)


def test_a_site_profile_marks_the_big_objects():
    with tempfile.TemporaryDirectory() as d:
        p = _profile(Path(d), {
            "large_tables": {"ORDER_LINE_HISTORY": ["DOCDATE", "POSTING_DATE"],
                             "EXT.ORDER_FACT": ["DOCDATE"]},
            "small_tables": ["DIM_PRODUCT"],
        })
        assert p.is_large("EXT.ORDER_FACT")
        assert p.is_large("order_line_history"), "matching must be case-insensitive"
        assert not p.is_large("DIM_PRODUCT")
        assert "DOCDATE" in p.date_columns_for("ORDER_LINE_HISTORY")
        assert p.date_columns_for("DIM_PRODUCT") is None


def test_no_object_name_is_compiled_into_the_plugin():
    """The reason this file changed. A public repo must not ship a schema map."""
    src = (ENGINE / "somsql" / "registry.py").read_text(encoding="utf-8")
    assert "LARGE_TABLES: dict" not in src, \
        "registry.py 에 테이블 목록이 다시 들어왔습니다"
    fresh = R.SiteProfile(Path(tempfile.gettempdir()) / "__som_no_profile__")
    assert fresh.large_tables == {}, fresh.large_tables
    assert fresh.warehouse is None, fresh.warehouse


def test_an_empty_registry_says_so_instead_of_going_quiet():
    """A guard that stops guarding without saying is the failure mode here."""
    fresh = R.SiteProfile(Path(tempfile.gettempdir()) / "__som_no_profile__")
    state = " ".join(fresh.describe_state())
    assert "비어" in state, state
    assert R.PROFILE_NAME in state, "어디에 적어야 하는지 알려주지 않습니다"


def test_a_broken_profile_is_reported_not_swallowed():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / ".som").mkdir(parents=True)
        (tmp / ".som" / R.PROFILE_NAME).write_text("{ not json", encoding="utf-8")
        p = R.SiteProfile(tmp)
        assert p.problems, "깨진 프로필이 조용히 빈 프로필로 처리됐습니다"
        assert any("JSON" in x for x in p.problems), p.problems


def test_a_large_table_with_no_date_column_is_refused_not_registered():
    """`{"T": []}` would read as registered while disabling the rule for T."""
    with tempfile.TemporaryDirectory() as d:
        p = _profile(Path(d), {"large_tables": {"T": []}})
        assert not p.is_large("T"), "빈 날짜 목록이 등록으로 처리됐습니다"
        assert p.problems, p.problems


def test_a_read_has_no_ceiling_left_to_hit():
    """The ceilings are gone, and this is what says so.

    Bytes scanned, rows returned and a session statement timeout used to be
    enforced. The warehouse bill belongs to another department, and a tool that
    decides on someone's behalf that 410,000 rows is too many gets worked
    around -- which puts the query somewhere with no ledger line at all.
    """
    for gone in ("MAX_BYTES", "MAX_ROWS", "STATEMENT_TIMEOUT", "EXPLORE_LIMIT"):
        assert not hasattr(R, gone), f"{gone} 이 다시 생겼습니다 — 읽기에는 상한이 없습니다"
    # What survives is not a limit: a cache that changes no answer, and a
    # relation count that earns a remark.
    assert R.CACHE_TTL_HOURS > 0
    assert R.JOIN_WARN < R.JOIN_MAX


def test_no_static_finding_refuses_a_read():
    """Every finding is information. `allowed` is unconditionally true."""
    for sql in (
        "SELECT * FROM a, b",                                   # cartesian
        "SELECT * FROM ORDER_LINE_HISTORY",                     # unbounded star
        "SELECT * FROM a JOIN b ON 1=1 JOIN c ON 1=1 JOIN d ON 1=1 "
        "JOIN e ON 1=1 JOIN f ON 1=1 JOIN g ON 1=1 JOIN h ON 1=1",   # fan-out
    ):
        g = G.static_checks(sql)
        assert g.allowed, sql
        assert g.denials == [], [f.code for f in g.denials]


def test_the_sql_reaches_the_warehouse_as_written():
    """No LIMIT injection. A truncated result looks exactly like a whole one."""
    for sql in ("SELECT a FROM t", "SELECT a FROM t WHERE d > '2026-01-01'"):
        g = G.static_checks(sql, mode="explore")
        assert g.sql == sql, f"{sql} -> {g.sql}"
        assert g.injected_limit is None


def test_a_big_estimate_is_a_remark_not_a_refusal():
    big = G.explain_verdict(40 * 1024 * 1024 * 1024)
    assert big.severity == "warn", big
    assert "GiB" in big.subject
    small = G.explain_verdict(5 * 1024 * 1024)
    assert small.severity == "info", small
    unknown = G.explain_verdict(None)
    assert unknown.severity == "warn"


def test_the_note_block_says_it_is_running_anyway():
    g = G.static_checks("SELECT * FROM a, b")
    block = G.guard_block(g, path="sql/01.sql")
    assert "SOMSQL-NOTE" in block, block
    assert "실행은 그대로" in block, block
    assert "SOMSQL-GUARD" not in block, "거부 블록 문구가 남아 있습니다"
    # Nothing to say about a plain read.
    assert G.guard_block(G.static_checks("SELECT a FROM t WHERE b = 1")) == ""


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
