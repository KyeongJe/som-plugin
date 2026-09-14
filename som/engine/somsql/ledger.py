"""Append-only record of every Snowflake interaction.

One line per attempt, including the refusals. The refusals are the point: a
denied write that leaves no trace teaches nobody anything, and DELIVER quotes
this file into the bundle appendix so cost is visible per deliverable rather
than buried in a warehouse bill.

Written under `.som/` in the project, which is gitignored -- it is run state,
not source.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

LEDGER_NAME = "ledger.ndjson"
AUDIT_MD = "WRITE_LOG.md"


def state_dir(project: str | Path | None = None) -> Path:
    root = Path(project or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    d = root / ".som"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sql_hash(sql: str) -> str:
    """Stable hash of the statement, whitespace-normalised.

    Used as the result-cache key and as the join key between a ledger line and
    the artifact it fed, so the same query reformatted still hits the cache.
    """
    norm = " ".join(sql.split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def record(event: dict[str, Any], *, project: str | Path | None = None) -> Path:
    """Append one event. Never raises: losing a ledger line must not fail a run."""
    line = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "run_id": os.environ.get("SOM_RUN_ID"),
        "task_id": os.environ.get("SOM_TASK_ID"),
        **event,
    }
    p = state_dir(project) / LEDGER_NAME
    try:
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as e:                                  # pragma: no cover
        print(f"somsql: ledger write failed ({e}); continuing", file=sys.stderr)
    return p


def read(project: str | Path | None = None, *, limit: int | None = None) -> list[dict]:
    p = state_dir(project) / LEDGER_NAME
    if not p.exists():
        return []
    rows = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return rows[-limit:] if limit else rows


def cost_summary(project: str | Path | None = None) -> dict:
    """What DELIVER puts in the appendix."""
    rows = read(project)
    ran = [r for r in rows if r.get("verdict") == "ran"]
    return {
        "attempts": len(rows),
        "executed": len(ran),
        "denied_classifier": sum(1 for r in rows if r.get("verdict") == "denied_classifier"),
        "denied_guard": sum(1 for r in rows if r.get("verdict") == "denied_guard"),
        "cache_hits": sum(1 for r in rows if r.get("cache_hit")),
        "bytes_scanned": sum(int(r.get("bytes_scanned") or 0) for r in ran),
        "rows_returned": sum(int(r.get("rows") or 0) for r in ran),
        "elapsed_ms": sum(int(r.get("elapsed_ms") or 0) for r in ran),
        "distinct_statements": len({r.get("sql_hash") for r in rows if r.get("sql_hash")}),
    }


def write_log_md(project: str | Path | None = None) -> Path:
    """A human-readable log of refusals and any authorised write.

    Kept as markdown next to the machine-readable ledger because a refusal
    nobody reads is a refusal that gets routed around next time.
    """
    rows = [r for r in read(project)
            if r.get("verdict", "").startswith("denied") or r.get("kind") == "write"]
    p = state_dir(project) / AUDIT_MD
    lines = [
        "# Snowflake 거부 · 쓰기 기록",
        "",
        "`somsql` 이 거부한 것과 사람이 명시적으로 승인한 쓰기의 기록이다.",
        "거부는 조용히 넘어가면 다음에 우회되므로 남긴다.",
        "",
        f"총 {len(rows)}건",
        "",
        "| 시각 | 판정 | 코드 | 대상 | 경로 |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        # A write row carries what a refusal row does not: the verb, the
        # objects, who approved it and why. That is the half somebody needs
        # months later to explain a change.
        if r.get("kind") == "write":
            subject = "{} {} · {}행 · 승인 {} · {}".format(
                r.get("verb", ""), ", ".join(r.get("objects", []) or [])[:40],
                r.get("rows_affected", "?"), r.get("approver", ""),
                str(r.get("reason", ""))[:60])
        else:
            subject = str(r.get("subject", ""))[:60]
        lines.append("| {} | {} | {} | {} | {} |".format(
            r.get("ts", ""), r.get("verdict", ""), r.get("code", ""),
            subject, r.get("sql_path", "")))
    if not rows:
        lines.append("| — | — | — | 거부·쓰기 기록 없음 | — |")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
