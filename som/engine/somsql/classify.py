"""Is this SQL a read?

Two independent gates. Either one saying deny is a deny.

  Gate 1  sqlglot AST, default-deny whitelist on the statement root, then a
          full walk for any write node hiding inside an allowed root.
  Gate 2  comment- and string-stripped token regex.

Why both. The AST gate is the accurate one, but sqlglot almost never fails
outright -- `SELEKT * FRM t` parses as an `Alias`, and unsupported syntax falls
back to a `Command` node. So "parse failure means deny" would catch almost
nothing on its own; the root whitelist is what makes the gate fail closed. The
regex gate then exists to disagree: when the parser allows something the token
scan flags, that disagreement is itself worth a human's attention, and the
query is denied.

What this replaces. The existing helper in
`08. BO Training/01. ZF_ZG_HoldOrder/app/sf_utils.py` checks only the first
token of each `;`-separated chunk. Three consequences, all verified:

  `WITH x AS (...) INSERT INTO t ...`   first token is WITH, so it passed
  `WHERE n = 'a;DROP TABLE t'`          split on the string's semicolon
  a statement kind not on its deny-list  passed by omission
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

try:
    import sqlglot
    from sqlglot import exp
except ModuleNotFoundError as e:      # pragma: no cover - guarded at the edges
    raise ModuleNotFoundError(
        "somsql requires sqlglot. Without it the classifier would fall back to a "
        "regex, which is exactly the weakening this module exists to prevent.\n"
        "  pip install -r engine/requirements.txt"
    ) from e

DIALECT = "snowflake"

# Statement roots that are reads. Everything else is denied, including
# sqlglot's `Command` fallback for syntax it does not model.
ALLOWED_ROOTS: tuple[type, ...] = (
    exp.Select,
    exp.Union, exp.Intersect, exp.Except,
    exp.Subquery,
    exp.Show,
    exp.Describe,
)

# Write-ish nodes. Denied wherever they appear, including nested inside an
# otherwise-allowed root -- that is what catches `SELECT ... INTO t`.
DENIED_NODES: tuple[type, ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge,
    exp.Create, exp.Drop, exp.Alter,
    exp.Grant, exp.Revoke,
    exp.TruncateTable,
    exp.Copy,
    exp.Into,
    exp.Use, exp.Set,
    exp.Command,           # sqlglot's fallback: unmodelled syntax, fail closed
)

# Gate 2. Applied after comments and string literals are removed.
_TOKEN_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|CREATE|REPLACE|DROP|ALTER|TRUNCATE"
    r"|GRANT|REVOKE|COPY|PUT|GET|CALL|EXECUTE|USE|UNSET"
    r"|BEGIN|COMMIT|ROLLBACK|INTO)\b",
    re.IGNORECASE,
)
# `SET` only counts as a write when it starts a statement: `SET x = 1`. Inside
# `UPDATE ... SET` the UPDATE token already fires, and a column named "offset"
# must not.
_LEADING_SET_RE = re.compile(r"(?:^|;)\s*SET\b", re.IGNORECASE)

# Comment stripping, lifted from sf_utils.py and kept as the second gate only.
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_SINGLE_QUOTED_RE = re.compile(r"'(?:''|[^'])*'", re.DOTALL)
_DOLLAR_QUOTED_RE = re.compile(r"\$\$.*?\$\$", re.DOTALL)


class Verdict:
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class Finding:
    code: str
    gate: str
    message: str
    statement: int = 1
    matched: str = ""


@dataclass
class Classification:
    verdict: str
    statements: list[str] = field(default_factory=list)
    roots: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.verdict == Verdict.ALLOW

    def first(self) -> Finding | None:
        return self.findings[0] if self.findings else None


def strip_noise(sql: str) -> str:
    """Remove comments and quoted literals so the token gate cannot be fooled
    by a keyword inside a string or a comment."""
    out = _BLOCK_COMMENT_RE.sub(" ", sql)
    out = _LINE_COMMENT_RE.sub(" ", out)
    out = _DOLLAR_QUOTED_RE.sub(" '' ", out)
    out = _SINGLE_QUOTED_RE.sub(" '' ", out)
    return out


def _node_name(n: Any) -> str:
    return type(n).__name__


def _walk(tree: Any):
    """sqlglot's walk() yields either a node or a (node, parent, key) tuple
    depending on version. Normalise."""
    for item in tree.walk():
        yield item[0] if isinstance(item, tuple) else item


def _ast_gate(sql: str) -> tuple[list[str], list[Finding]]:
    findings: list[Finding] = []
    try:
        trees = sqlglot.parse(sql, read=DIALECT)
    except Exception as e:                       # noqa: BLE001
        return [], [Finding(
            code="PARSE_FAILED", gate="parser",
            message=f"sqlglot could not parse this SQL ({type(e).__name__}). "
                    f"A human must look at it; the classifier does not guess.",
        )]

    trees = [t for t in trees if t is not None]
    if not trees:
        return [], [Finding(code="EMPTY", gate="parser",
                            message="no statement found")]

    roots = [_node_name(t) for t in trees]
    for i, tree in enumerate(trees, start=1):
        if not isinstance(tree, ALLOWED_ROOTS):
            findings.append(Finding(
                code="ROOT_NOT_A_READ", gate="parser", statement=i,
                matched=_node_name(tree),
                message=f"statement root is {_node_name(tree)}, which is not a read. "
                        f"Allowed roots: {', '.join(t.__name__ for t in ALLOWED_ROOTS)}. "
                        f"sqlglot reports Command for syntax it does not model, so "
                        f"anything it cannot classify lands here.",
            ))
            continue
        for node in _walk(tree):
            if isinstance(node, DENIED_NODES):
                findings.append(Finding(
                    code="WRITE_NODE_NESTED", gate="parser", statement=i,
                    matched=_node_name(node),
                    message=f"a {_node_name(node)} node sits inside an otherwise "
                            f"readable statement. Wrapping a write in a CTE or a "
                            f"SELECT ... INTO does not make it a read.",
                ))
                break
    return roots, findings


def _token_gate(sql: str) -> list[Finding]:
    cleaned = strip_noise(sql)
    findings: list[Finding] = []
    seen: set[str] = set()
    for m in _TOKEN_RE.finditer(cleaned):
        kw = m.group(1).upper()
        if kw in seen:
            continue
        seen.add(kw)
        findings.append(Finding(
            code="WRITE_TOKEN", gate="regex", matched=kw,
            message=f"token {kw} appears outside comments and string literals",
        ))
    if _LEADING_SET_RE.search(cleaned):
        findings.append(Finding(
            code="WRITE_TOKEN", gate="regex", matched="SET",
            message="a statement begins with SET, which changes session state",
        ))
    return findings


def classify(sql: str, *, allow_multi: bool = False) -> Classification:
    """Classify SQL as a read or not.

    `allow_multi` permits several statements in one string. Agent-issued SQL
    always leaves it False: the numbered-file convention is already one query
    per file, so multi-statement support buys nothing and widens the surface.
    """
    text = sql.strip()
    if not text:
        return Classification(Verdict.DENY, findings=[
            Finding(code="EMPTY", gate="input", message="empty SQL")])

    roots, ast_findings = _ast_gate(text)
    token_findings = _token_gate(text)

    findings = list(ast_findings)

    if not allow_multi and len(roots) > 1:
        findings.append(Finding(
            code="MULTI_STATEMENT", gate="input",
            message=f"{len(roots)} statements in one string. Agent-issued SQL is "
                    f"one query per call; the numbered-file convention already "
                    f"gives one query per file.",
        ))

    # The disagreement case. The parser allowed everything, the token scan did
    # not. That is a finding in its own right, not noise to be smoothed over.
    if not ast_findings and token_findings:
        findings.append(Finding(
            code="CLASSIFIER_DISAGREE", gate="both",
            matched=",".join(f.matched for f in token_findings),
            message="the parser read this as a read but the token scan found "
                    f"{', '.join(f.matched for f in token_findings)}. The "
                    f"disagreement is denied and recorded: one of the two gates "
                    f"is wrong and a human should know which.",
        ))
    elif token_findings:
        findings.extend(token_findings)

    verdict = Verdict.DENY if findings else Verdict.ALLOW
    return Classification(verdict=verdict, statements=[text], roots=roots,
                          findings=findings)


def deny_block(sql: str, c: Classification, *, path: str | None = None) -> str:
    """The operator-facing denial. Structured, quotable, and it names the fix.

    Deliberately not a friendly apology: an agent reading this must stop and
    report it verbatim, not rewrite the SQL to get around it.
    """
    f = c.first()
    assert f is not None
    where = f" file={path}" if path else ""
    matched = f"\nmatched  : {f.matched}" if f.matched else ""
    lines = [
        f"SOMSQL-DENY  code={f.code}  gate={f.gate}  stmt={f.statement}/{max(len(c.roots), 1)}{where}",
        f"reason   : {f.message}{matched}",
        "policy   : this refusal is the read path. A statement that changes "
        "something is not refused -- it is described and approved through "
        "`somsql write`, which is a different command on purpose.",
        "next     : stop. Report this block verbatim and ask the person who owns "
        "the data. Do not rewrite the SQL to get around it, and do not reach for "
        "another client.",
    ]
    if len(c.findings) > 1:
        lines.append("also     : " + "; ".join(
            f"[{x.gate}] {x.code} {x.matched}".strip() for x in c.findings[1:]))
    return "\n".join(lines)
