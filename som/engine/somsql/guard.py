"""Cost report: what is this read about to do?

**Nothing here refuses a read.** It used to -- byte ceilings, row ceilings, a
LIMIT injected when the author wrote none -- and that was the wrong call for
this team. The warehouse bill belongs to another department, the person asking
knows what they need, and a tool that decides on their behalf how many rows is
too many only pushes the query somewhere this tool cannot see.

So every finding below is information, delivered before the query runs and
recorded after it:

  1  static AST   cartesian joins, missing date predicates, unbounded SELECT *,
                  join fan-out.  free
  2  EXPLAIN      byte estimate before execution. A cartesian shows up here as
                  an exploded row estimate and costs no credits to find.
  3  ledger       what it actually scanned, per statement, per deliverable.

Layer 2 needs a connection and lives in conn.py; this module owns layer 1 and
the vocabulary the others report in.

The one thing that still refuses is `classify`, and that is not about cost: a
statement which changes something goes through `somsql write`, which asks a
person. Reads run.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from . import registry as R

DIALECT = "snowflake"


@dataclass
class GuardFinding:
    code: str
    severity: str          # "deny" | "warn"
    message: str
    subject: str = ""


@dataclass
class GuardResult:
    allowed: bool
    sql: str                                   # possibly LIMIT-wrapped
    injected_limit: int | None = None
    findings: list[GuardFinding] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)

    @property
    def denials(self) -> list[GuardFinding]:
        return [f for f in self.findings if f.severity == "deny"]

    @property
    def warnings(self) -> list[GuardFinding]:
        return [f for f in self.findings if f.severity == "warn"]


def _tables(tree) -> list[exp.Table]:
    return list(tree.find_all(exp.Table))


def _predicate_columns(tree) -> set[str]:
    """Columns referenced in any WHERE or JOIN condition, upper-cased."""
    cols: set[str] = set()
    for where in tree.find_all(exp.Where):
        cols |= {c.name.upper() for c in where.find_all(exp.Column)}
    for join in tree.find_all(exp.Join):
        on = join.args.get("on")
        if on is not None:
            cols |= {c.name.upper() for c in on.find_all(exp.Column)}
    for qual in tree.find_all(exp.Qualify):
        cols |= {c.name.upper() for c in qual.find_all(exp.Column)}
    return cols


def _selects_star(tree) -> bool:
    for sel in tree.find_all(exp.Select):
        for e in sel.expressions:
            if isinstance(e, exp.Star):
                return True
            if isinstance(e, exp.Column) and isinstance(e.this, exp.Star):
                return True
    return False


def _has_limit(tree) -> bool:
    return tree.args.get("limit") is not None or bool(list(tree.find_all(exp.Limit)))


def static_checks(sql: str, *, mode: str = "explore") -> GuardResult:
    tree = sqlglot.parse_one(sql, read=DIALECT)
    findings: list[GuardFinding] = []
    tables = _tables(tree)
    names = [t.name for t in tables]

    # --- 1. cartesian joins -------------------------------------------------
    for join in tree.find_all(exp.Join):
        if join.args.get("on") or join.args.get("using"):
            continue
        kind = (join.args.get("kind") or "").upper()
        rel = join.this.sql(dialect=DIALECT) if join.this else "?"
        findings.append(GuardFinding(
            code="CARTESIAN_JOIN", severity="warn", subject=rel,
            message=(f"join onto {rel} has no ON or USING condition"
                     + (" (explicit CROSS JOIN)" if kind == "CROSS" else
                        " (comma join)")
                     + ". Every row of one side pairs with every row of the "
                       "other, so the result may be far larger than it looks. "
                       "The EXPLAIN estimate is the number to read."),
        ))

    # --- 2. date predicate on registry tables -------------------------------
    pred_cols = _predicate_columns(tree)
    for t in tables:
        wanted = R.date_columns_for(t.name)
        if not wanted:
            continue
        if not (pred_cols & set(wanted)):
            findings.append(GuardFinding(
                code="NO_DATE_PREDICATE", severity="warn", subject=t.name,
                message=(f"{t.name} is registered as a large object and the "
                         f"query narrows it by none of {', '.join(wanted)}. "
                         f"That is a full scan -- fine, if you meant it."),
            ))

    # --- 3. unbounded SELECT * on a large object ----------------------------
    if _selects_star(tree) and not _has_limit(tree):
        for t in tables:
            if R.is_large(t.name):
                findings.append(GuardFinding(
                    code="UNBOUNDED_STAR", severity="warn", subject=t.name,
                    message=(f"SELECT * over {t.name} with no LIMIT -- every "
                             f"column, every row."),
                ))
                break

    # --- 4. join fan-out ----------------------------------------------------
    n_rel = len(tables)
    if n_rel > R.JOIN_MAX:
        findings.append(GuardFinding(
            code="TOO_MANY_RELATIONS", severity="warn",
            subject=f"{n_rel} relations",
            message=(f"{n_rel} relations in one statement. Fan-out this wide "
                     f"is usually accidental; if it is not, ignore this."),
        ))
    elif n_rel > R.JOIN_WARN:
        findings.append(GuardFinding(
            code="MANY_RELATIONS", severity="warn", subject=f"{n_rel} relations",
            message=f"{n_rel} relations joined; watch the EXPLAIN estimate.",
        ))

    # --- 5. the query runs as written ---------------------------------------
    #
    # A LIMIT used to be injected here when the author wrote none. It was the
    # one thing in this module that could hand back a *wrong* answer rather
    # than an expensive one: 1,000 rows of a 410,000-row extract look exactly
    # like a complete result. The SQL now reaches the warehouse as written.
    return GuardResult(
        allowed=True, sql=sql, injected_limit=None,
        findings=findings, tables=names,
    )


def guard_block(g: GuardResult, *, path: str | None = None) -> str:
    """What this read is about to do, for a person to read before it runs.

    Not a refusal. It is printed and the query goes ahead -- the point is that
    nobody is surprised afterwards, not that anybody is stopped.
    """
    if not g.findings:
        return ""
    where = f"  file={path}" if path else ""
    lines = [f"SOMSQL-NOTE  이 읽기에 대해 알아둘 것{where}"]
    for x in g.findings:
        lines.append(f"  · {x.code} {x.subject}".rstrip())
        lines.append(f"      {x.message}")
    lines.append("  실행은 그대로 진행됩니다. 스캔량은 .som/ledger.ndjson 에 남습니다.")
    return "\n".join(lines)


def explain_verdict(bytes_assigned: int | None,
                    *, mode: str = "full") -> GuardFinding | None:
    """What EXPLAIN says this will scan. Reported, never enforced.

    `mode` is accepted and ignored. It survives so existing callers and the
    ledger keep their shape; there is no longer a ceiling to be over.
    """
    if bytes_assigned is None:
        return GuardFinding(
            code="EXPLAIN_UNAVAILABLE", severity="warn",
            message="EXPLAIN returned no byte estimate, so the size of this read "
                    "is not known until it runs.")
    mib = bytes_assigned / 1024 / 1024
    if mib >= 1024:
        return GuardFinding(
            code="LARGE_SCAN", severity="warn",
            subject=f"{mib / 1024:,.1f} GiB",
            message=(f"EXPLAIN estimates {mib / 1024:,.1f} GiB scanned. "
                     f"Running it -- this is a heads-up, not a refusal."))
    return GuardFinding(
        code="SCAN_ESTIMATE", severity="info",
        subject=f"{mib:,.0f} MiB",
        message=f"EXPLAIN estimates {mib:,.0f} MiB scanned.")
