"""Say what a non-read statement will actually do, in a sentence a person can approve.

Writes used to be refused outright, which was wrong for this team: creating a
table, correcting a row, dropping a scratch object are all real work. What must
not happen is a write nobody looked at.

So the classifier's job changes from "refuse" to "describe": what kind of
operation, against which objects, whether it can be undone, and what the blast
radius looks like if the statement has no predicate. The person reads that and
decides. Nothing here connects to anything.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

try:
    import sqlglot
    from sqlglot import exp
except ImportError:                                  # pragma: no cover
    sqlglot = None
    exp = None

from .classify import strip_noise

# Operation -> (what it does, is it reversible, how alarming)
#
# "reversible" means Snowflake can put it back without a restore: Time Travel
# covers DML and dropped objects for the retention window. It does not mean
# harmless -- it means there is a road back.
OPERATIONS: dict[str, tuple[str, bool, str]] = {
    "INSERT":      ("행을 추가합니다", True, "low"),
    "UPDATE":      ("기존 행의 값을 바꿉니다", True, "high"),
    "DELETE":      ("행을 지웁니다", True, "high"),
    "MERGE":       ("일치하는 행은 바꾸고 없는 행은 추가합니다", True, "high"),
    "TRUNCATE":    ("테이블의 모든 행을 지웁니다", True, "high"),
    "CREATE":      ("객체를 새로 만듭니다", True, "low"),
    "DROP":        ("객체를 통째로 없앱니다", True, "high"),
    "ALTER":       ("객체의 정의를 바꿉니다", False, "high"),
    "GRANT":       ("권한을 부여합니다", True, "high"),
    "REVOKE":      ("권한을 회수합니다", True, "high"),
    "COPY":        ("외부 스테이지에서 데이터를 적재합니다", True, "medium"),
    "PUT":         ("로컬 파일을 스테이지에 올립니다", True, "low"),
    "REMOVE":      ("스테이지의 파일을 지웁니다", False, "high"),
    "CALL":        ("저장 프로시저를 실행합니다 — 내부에서 무엇을 하는지는 이 도구가 알 수 없습니다",
                    False, "high"),
    "USE":         ("세션의 컨텍스트를 바꿉니다", True, "low"),
    "SET":         ("세션 변수를 설정합니다", True, "low"),
}

# Verbs whose absence of a WHERE clause means "every row".
_NEEDS_PREDICATE = {"UPDATE", "DELETE"}

_VERB_RE = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|CREATE|DROP|ALTER|GRANT|REVOKE|"
    r"COPY|PUT|REMOVE|CALL|USE|SET)\b", re.IGNORECASE)
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_IFEXISTS_RE = re.compile(r"\bIF\s+(?:NOT\s+)?EXISTS\b", re.IGNORECASE)
_ORREPLACE_RE = re.compile(r"\bOR\s+REPLACE\b", re.IGNORECASE)
_TEMP_RE = re.compile(r"\b(TEMP|TEMPORARY|TRANSIENT)\b", re.IGNORECASE)

# Tokens that follow a verb without being the thing that changes.
#
# The fallback regex used to return whatever word came after the verb, so
# `INSERT INTO IDENTIFIER($t) VALUES (1)` described its target as a table
# called "INTO". That is worse than reporting nothing: the target line is the
# one a person actually reads before approving, and a confidently wrong name
# there is how a write against the wrong object gets a yes.
_NOT_A_NAME = frozenset({
    "INTO", "FROM", "TABLE", "VIEW", "SCHEMA", "DATABASE", "STAGE", "STREAM",
    "TASK", "FUNCTION", "PROCEDURE", "ROLE", "WAREHOUSE", "ALL", "OVERWRITE",
    "IF", "EXISTS", "NOT", "OR", "REPLACE", "TEMP", "TEMPORARY", "TRANSIENT",
    "SELECT", "VALUES", "SET", "ON", "TO", "USING", "WITH", "FILE",
    "IDENTIFIER",
})

# PUT / REMOVE / COPY act on a stage, which is not a table name and never
# looked like one: `PUT file://a.csv @~/stage` reported its target as "file".
_STAGE_RE = re.compile(r"(@[\w./$~]+)")


@dataclass
class WriteDescription:
    """What one statement does, in terms a person can approve or refuse."""
    verb: str                      # INSERT / CREATE / ...
    what: str                      # one sentence, Korean
    objects: list[str] = field(default_factory=list)
    reversible: bool = True
    severity: str = "medium"       # low | medium | high
    warnings: list[str] = field(default_factory=list)
    sql_sha256: str = ""

    @property
    def headline(self) -> str:
        target = ", ".join(self.objects) if self.objects else "(대상을 읽어내지 못함)"
        return f"{self.verb} — {self.what}  ·  대상: {target}"


def sql_hash(sql: str) -> str:
    """Stable identity for one statement, used to bind an approval to it.

    Normalised on whitespace only: an approval must not survive an edit that
    changes what runs, but it should survive re-indenting the same statement.
    """
    return hashlib.sha256(" ".join(sql.split()).encode("utf-8")).hexdigest()


def _objects(sql: str, verb: str) -> list[str]:
    """Fully qualified names the statement targets."""
    names: list[str] = []
    if sqlglot is not None:
        try:
            for tree in sqlglot.parse(sql, read="snowflake"):
                if tree is None:
                    continue
                for node in tree.find_all(exp.Table):
                    name = ".".join(
                        p for p in (node.catalog, node.db, node.name) if p)
                    if name and name not in names:
                        names.append(name)
        except Exception:                            # noqa: BLE001
            pass
    if names:
        return names
    # sqlglot models several DDL forms as a Command with no Table node, so fall
    # back to the token after the verb rather than reporting no target at all.
    m = re.search(rf"\b{verb}\b\s+(?:OR\s+REPLACE\s+)?(?:TEMP\w*\s+|TRANSIENT\s+)?"
                  r"(?:INTO\s+|FROM\s+)?"
                  r"(?:TABLE|VIEW|SCHEMA|DATABASE|STAGE|STREAM|TASK|FUNCTION|"
                  r"PROCEDURE|ROLE|WAREHOUSE)?\s*(?:IF\s+(?:NOT\s+)?EXISTS\s+)?"
                  r"([\w.\"$]+)", sql, re.IGNORECASE)
    name = m.group(1).strip('"') if m else ""
    if name.upper() in _NOT_A_NAME:
        # Reporting nothing raises the severity and prints "직접 확인하세요",
        # which is the honest answer. Reporting a keyword is a wrong answer
        # that reads like a right one.
        name = ""
    if not name:
        stage = _STAGE_RE.search(sql)
        if stage:
            return [stage.group(1)]
    return [name] if name else []


def describe(sql: str) -> WriteDescription | None:
    """Describe a write, or return None when the statement is a read.

    A read needs no approval and gets none of this ceremony.
    """
    bare = strip_noise(sql)
    m = _VERB_RE.search(bare)
    if not m:
        return None
    verb = m.group(1).upper()
    what, reversible, severity = OPERATIONS.get(
        verb, ("무엇을 하는지 이 도구가 분류하지 못했습니다", False, "high"))

    d = WriteDescription(verb=verb, what=what, reversible=reversible,
                         severity=severity, objects=_objects(bare, verb),
                         sql_sha256=sql_hash(sql))

    if verb in _NEEDS_PREDICATE and not _WHERE_RE.search(bare):
        d.warnings.append(
            f"WHERE 절이 없습니다 — 대상 테이블의 **모든 행**에 적용됩니다.")
        d.severity = "high"
    if verb == "DROP" and not _IFEXISTS_RE.search(bare):
        d.warnings.append("IF EXISTS 가 없어, 객체가 없으면 오류로 끝납니다.")
    if _ORREPLACE_RE.search(bare):
        d.warnings.append(
            "OR REPLACE 입니다 — 같은 이름이 이미 있으면 그 내용이 사라집니다.")
        d.severity = "high"
    if verb == "CREATE" and _TEMP_RE.search(bare):
        d.warnings.append("임시/휘발성 객체라 세션이 끝나면 사라집니다.")
        d.severity = "low"
    if verb == "CALL":
        d.warnings.append(
            "프로시저 내부는 이 도구가 볼 수 없습니다. 무엇을 바꾸는지 직접 확인하세요.")
    if not d.objects:
        d.warnings.append("대상 객체명을 읽어내지 못했습니다. SQL 을 직접 확인하세요.")
        d.severity = "high"

    return d


def confirm_block(d: WriteDescription, *, path: str | None = None) -> str:
    """The text a person reads before approving.

    Structured so a skill can show it verbatim. It says what happens, to what,
    whether it can be undone, and what to type to go ahead -- deliberately
    including the short hash, so approving one statement cannot approve another.
    """
    lines = [
        "SOMSQL-CONFIRM  읽기가 아닙니다. 진행하려면 확인이 필요합니다.",
        f"파일     : {path or '(직접 입력)'}",
        f"작업     : {d.verb} — {d.what}",
        f"대상     : {', '.join(d.objects) if d.objects else '(읽어내지 못함)'}",
        f"되돌리기 : {'Time Travel 로 복구 가능합니다' if d.reversible else '되돌릴 수 없습니다'}",
    ]
    for w in d.warnings:
        lines.append(f"주의     : {w}")
    lines += [
        f"문 해시  : {d.sql_sha256[:12]}",
        "",
        "승인하시면 이 명령으로 실행됩니다:",
        f"  python -m somsql write --file {path or '<파일>'} "
        f"--approve {d.sql_sha256[:12]} --reason \"<왜 필요한지>\"",
        "",
        "이 해시는 지금 이 문장에만 붙습니다. SQL 을 한 글자라도 고치면 승인은 무효입니다.",
    ]
    return "\n".join(lines)
