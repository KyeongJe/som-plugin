"""Cost-guard tuning, and the site profile that names this deployment's objects.

Two kinds of thing used to live here and only one of them belongs in code.

The ceilings below are engineering judgement: how many bytes an ad-hoc query
may scan, how long a statement may run, when a join fan-out stops being a join.
They are the same wherever this plugin is installed, so they ship with it.

The object names were not. `LARGE_TABLES`, the warehouse, the domain codes --
those describe one company's Snowflake account, and this repository is public.
Publishing a schema map is not a credential leak, but it is free reconnaissance
for anyone who later obtains one, and it made the plugin unusable for anyone
whose tables are named differently. So they move to a **site profile**: a JSON
file on the machine that runs the queries, never in this repository.

    <project>/.som/registry.json          (per project, gitignored)
    <plugin data>/registry.json           (per operator, all projects)

Shape -- see standard/registry.json.sample:

    {
      "warehouse": "MY_WH",
      "large_tables": {"SALES_FACT": ["ORDER_DATE", "SHIP_DATE"]},
      "small_tables": ["DIM_CUSTOMER"],
      "vocabulary": {"SALESORG": {"1100": "..."}}
    }

**An empty profile weakens the date-predicate guard, and that is said out
loud** rather than left to be discovered. `describe_state()` is what `plan`
and `/som:doctor` print. A guard that quietly stops guarding is the failure
this whole module exists to avoid.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Reads are not capped
# ---------------------------------------------------------------------------
# There were four ceilings here: bytes scanned, rows returned, a session
# statement timeout, and a LIMIT injected when the author wrote none. All four
# are gone.
#
# The reasoning behind them was about credits, and the warehouse bill belongs
# to another department. Someone asking for 410,000 rows knows they want
# 410,000 rows, and a tool that decides otherwise on their behalf gets worked
# around -- which puts the query somewhere with no ledger line at all.
#
# The injected LIMIT was worse than expensive. It could return 1,000 rows of a
# 410,000-row extract, and a truncated result looks exactly like a complete one.
#
# What remains is reporting: `somsql` still says what a query is about to scan
# and records what it did. Cost visibility and cost control are different
# things, and only the second one was unwanted.

# Result cache window. Not a limit -- the cheapest read is the one that does
# not run twice, and reusing a cached answer changes no answer.
CACHE_TTL_HOURS = 24

# Relation counts that earn a remark, never a refusal. Fan-out this wide is
# usually accidental; when it is not, the remark costs one line of output.
JOIN_WARN, JOIN_MAX = 5, 7

PROFILE_NAME = "registry.json"


# ---------------------------------------------------------------------------
# Site profile -- loaded from disk, empty by default
# ---------------------------------------------------------------------------
def _plugin_data_dir() -> Path:
    explicit = os.environ.get("CLAUDE_PLUGIN_DATA")
    if explicit:
        return Path(explicit)
    return (Path.home() / ".claude" / "plugins" / "data"
            / "som-som-marketplace")


def profile_paths(project: str | Path | None = None) -> list[Path]:
    """Where a site profile may live, in the order they are merged.

    Project first so a repository can pin the objects its own SQL touches;
    the operator file fills in anything the project did not name.
    """
    root = Path(project or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    return [root / ".som" / PROFILE_NAME, _plugin_data_dir() / PROFILE_NAME]


def _read(path: Path) -> dict[str, Any]:
    """A malformed profile is reported, never silently treated as empty.

    Returning `{}` on a syntax error would turn "my guard stopped working"
    into a mystery. The caller prints this and carries on with what it has.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"__error__": f"{path}: JSON 을 읽지 못했습니다 — {e}"}
    return data if isinstance(data, dict) else {
        "__error__": f"{path}: 최상위가 객체가 아닙니다"}


class SiteProfile:
    """This deployment's objects. Everything here comes off the local disk."""

    def __init__(self, project: str | Path | None = None):
        self.sources: list[str] = []
        self.problems: list[str] = []
        self.warehouse: str | None = None
        self.large_tables: dict[str, tuple[str, ...]] = {}
        self.small_tables: frozenset[str] = frozenset()
        self.vocabulary: dict[str, dict[str, str]] = {}

        merged: dict[str, Any] = {}
        for p in reversed(profile_paths(project)):     # operator, then project
            if not p.is_file():
                continue
            data = _read(p)
            if "__error__" in data:
                self.problems.append(str(data["__error__"]))
                continue
            merged.update(data)
            self.sources.append(str(p))
        self.sources.reverse()

        wh = merged.get("warehouse")
        self.warehouse = str(wh) if isinstance(wh, str) and wh.strip() else None

        large = merged.get("large_tables")
        if isinstance(large, dict):
            for name, cols in large.items():
                if not isinstance(name, str):
                    continue
                if isinstance(cols, str):
                    cols = [cols]
                if not isinstance(cols, (list, tuple)) or not cols:
                    self.problems.append(
                        f"large_tables['{name}'] 에 날짜 컬럼이 없습니다 — "
                        "빈 목록은 이 테이블의 가드를 끄는 것과 같습니다")
                    continue
                self.large_tables[name.upper()] = tuple(
                    str(c).upper() for c in cols)

        small = merged.get("small_tables")
        if isinstance(small, (list, tuple)):
            self.small_tables = frozenset(
                str(s).upper() for s in small if isinstance(s, str))

        vocab = merged.get("vocabulary")
        if isinstance(vocab, dict):
            self.vocabulary = {
                str(k): {str(a): str(b) for a, b in v.items()}
                for k, v in vocab.items() if isinstance(v, dict)}

    # -- queries -----------------------------------------------------------
    def date_columns_for(self, table_name: str) -> tuple[str, ...] | None:
        return self.large_tables.get(table_name.upper())

    def is_large(self, table_name: str) -> bool:
        return table_name.upper() in self.large_tables

    @property
    def empty(self) -> bool:
        return not self.large_tables

    def describe_state(self) -> list[str]:
        """What a person needs to know before trusting the cost guard."""
        out = list(self.problems)
        if self.empty:
            out.append(
                "대형 테이블 registry 가 비어 있습니다 — 날짜 조건 없는 전체 스캔을 "
                "막는 가드가 동작하지 않습니다. EXPLAIN 바이트 견적과 행 상한은 "
                "그대로 돕니다.\n"
                f"  등록하려면: .som/{PROFILE_NAME} 에 large_tables 를 적으세요 "
                "(예시: standard/registry.json.sample)")
        else:
            where = ", ".join(self.sources) or "(기본값)"
            out.append(f"registry: 대형 테이블 {len(self.large_tables)}개 · "
                       f"소형 {len(self.small_tables)}개 · {where}")
        return out


# A process-wide default, so the existing module-level helpers keep working.
_DEFAULT: SiteProfile | None = None


def profile(project: str | Path | None = None, *, reload: bool = False) -> SiteProfile:
    global _DEFAULT
    if reload or _DEFAULT is None or project is not None:
        p = SiteProfile(project)
        if project is None:
            _DEFAULT = p
        return p
    return _DEFAULT


def date_columns_for(table_name: str) -> tuple[str, ...] | None:
    """Date columns that satisfy the predicate rule, or None if unregistered."""
    return profile().date_columns_for(table_name)


def is_large(table_name: str) -> bool:
    return profile().is_large(table_name)


def warehouse() -> str | None:
    """The warehouse to pin, or None to leave the account default alone."""
    return profile().warehouse
