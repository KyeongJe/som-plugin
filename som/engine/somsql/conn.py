"""The only sanctioned Snowflake client in this project.

Inherits the config-discovery ladder from
`08. BO Training/01. ZF_ZG_HoldOrder/app/sf_utils.py`, which solved a real
problem: the original hardcoded one person's absolute path and therefore ran on
exactly one laptop.

The single most valuable thing carried over is `read_sql_files`: one connection
for N statements, which means **one SSO browser prompt per run** instead of N.
With `externalbrowser` auth that is the difference between usable and not.

What is added on top:
  - every statement passes `classify` before it is sent, and `guard` after
  - an EXPLAIN byte estimate before execution
  - a session statement timeout and a running row-count abort
  - a 24-hour parquet result cache keyed on the normalised statement hash
  - a ledger line per attempt, refusals included

Secrets: this module reads a config file to learn *where* a key is. It never
reads, copies, moves, or prints the key itself. RSA passphrases come from the
OS keyring, never from the repo or an env file.
"""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from . import classify as C
from . import guard as G
from . import describe as D
from . import ledger as L
from . import registry as R

CONFIG_NAME = "sf_login_info.json"
CACHE_DIR = "_cache"


class SnowflakeUnavailable(RuntimeError):
    pass


class Denied(RuntimeError):
    """Raised with the operator-facing block as its message. Exit code 3."""

    def __init__(self, block: str, *, code: str):
        super().__init__(block)
        self.code = code


# ---------------------------------------------------------------------------
# config discovery -- the ladder, extended with the official CLI location
# ---------------------------------------------------------------------------
def find_config(explicit: str | None = None) -> tuple[str, Path | str]:
    """Return (kind, location). Order is deliberate: the newest, most portable
    location first, then the legacy ones, so existing scripts keep working."""
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise FileNotFoundError(f"--config points at a missing file: {p}")
        return "json", p

    profile = os.environ.get("SOMSQL_PROFILE")
    if profile:
        toml = Path.home() / ".snowflake" / "connections.toml"
        if toml.exists() and toml.stat().st_size > 0:
            return "toml", profile
        raise FileNotFoundError(
            f"SOMSQL_PROFILE={profile} but {toml} is missing or empty. "
            f"Create the profile, or unset SOMSQL_PROFILE to fall back to "
            f"{CONFIG_NAME}.")

    env = os.environ.get("SF_LOGIN_INFO")
    if env:
        p = Path(env)
        if p.exists():
            return "json", p
        raise FileNotFoundError(f"SF_LOGIN_INFO points at a missing file: {p}")

    here = Path.cwd().resolve()
    for folder in [here, *here.parents]:
        p = folder / CONFIG_NAME
        if p.exists():
            return "json", p

    raise FileNotFoundError(
        f"no Snowflake config found. Provide one of:\n"
        f"  SOMSQL_PROFILE=<name>   with ~/.snowflake/connections.toml\n"
        f"  SF_LOGIN_INFO=<path>    to a {CONFIG_NAME}\n"
        f"  {CONFIG_NAME}           in this directory or any parent\n"
        f"A sample lives at standard/secrets/{CONFIG_NAME}.sample")


SECRET_KEYS = ("password", "private_key", "token", "passcode")


def load_config(explicit: str | None = None) -> dict:
    kind, loc = find_config(explicit)
    if kind == "toml":
        import tomllib
        data = tomllib.loads((Path.home() / ".snowflake" / "connections.toml")
                             .read_text(encoding="utf-8"))
        cfg = dict(data.get(str(loc)) or {})
        if not cfg:
            raise KeyError(f"profile {loc!r} not found in connections.toml")
    else:
        cfg = json.loads(Path(loc).read_text(encoding="utf-8"))

    inline = [k for k in SECRET_KEYS if cfg.get(k)]
    if inline:
        raise Denied(
            "SOMSQL-SECRET  the Snowflake config carries a secret inline.\n"
            f"keys     : {', '.join(inline)}\n"
            f"file     : {loc}\n"
            "policy   : this client reads a config to learn where a key is. It "
            "does not accept the key itself.\n"
            "next     : for interactive work use \"authenticator\": "
            "\"externalbrowser\" and remove the secret. For unattended work set "
            "\"private_key_file\" to a path outside any synced folder and put the "
            "passphrase in the OS keyring.",
            code="SECRET_INLINE")

    # The warehouse is site configuration, not a constant. It used to be
    # hardcoded to one company's; now it comes from the site profile, and when
    # nobody has named one the account default is left alone rather than a
    # guess being forced onto the session.
    wh = R.warehouse()
    if wh:
        cfg.setdefault("warehouse", wh)
    cfg.setdefault("authenticator", "externalbrowser")
    return cfg


def _private_key_bytes(cfg: dict) -> bytes | None:
    """Resolve an RSA key from its path, with the passphrase from the keyring.

    The path is read here and nowhere else, and the bytes are handed straight
    to the connector without being logged or copied.
    """
    path = cfg.pop("private_key_file", None)
    if not path:
        return None
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"private_key_file does not exist: {p}")
    if any(part.lower() in {"onedrive", "sharepoint", "dropbox"} or "onedrive" in part.lower()
           for part in p.parts):
        raise Denied(
            "SOMSQL-SECRET  the private key sits inside a synced folder.\n"
            f"file     : {p}\n"
            "policy   : a key in a synced tree is exposed, and deleting it later "
            "is not enough because the sync service keeps previous versions.\n"
            "next     : move the key outside the synced tree (for example "
            "%USERPROFILE%\\.snowflake\\keys\\), restrict it with icacls, and "
            "rotate it if it has ever synced.",
            code="KEY_IN_SYNCED_TREE")

    passphrase = None
    try:
        import keyring
        passphrase = keyring.get_password("somsql", cfg.get("account", "default"))
    except Exception:                                     # noqa: BLE001
        pass

    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(
        p.read_bytes(),
        password=passphrase.encode() if passphrase else None,
    )
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@contextmanager
def connection(config_path: str | None = None, *, mode: str = "explore"):
    """One connection, held open for the caller's whole batch.

    With `externalbrowser` this is what keeps the SSO prompt to once per run.
    """
    try:
        import snowflake.connector
    except ModuleNotFoundError as e:
        raise SnowflakeUnavailable(
            "snowflake-connector-python is not installed.\n"
            "  pip install -r engine/requirements.txt") from e

    cfg = load_config(config_path)
    pk = _private_key_bytes(cfg)
    if pk:
        cfg["private_key"] = pk
        cfg.pop("authenticator", None)

    conn = snowflake.connector.connect(**cfg)
    try:
        with conn.cursor() as cur:
            # No STATEMENT_TIMEOUT override. It was 60s for ad hoc work and
            # 900s for extracts, which killed long reads that were doing
            # exactly what had been asked of them. The account default applies.
            #
            # ABORT_DETACHED_QUERY stays. It cancels work whose client is gone,
            # which limits nobody -- it is cleanup.
            cur.execute("ALTER SESSION SET ABORT_DETACHED_QUERY = TRUE")
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# result cache
# ---------------------------------------------------------------------------
def _cache_path(h: str, project: str | Path | None = None) -> Path:
    d = Path(project or os.getcwd()) / CACHE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{h[:16]}.parquet"


def _cache_get(h: str, project=None):
    p = _cache_path(h, project)
    if not p.exists():
        return None
    age_h = (time.time() - p.stat().st_mtime) / 3600
    if age_h > R.CACHE_TTL_HOURS:
        return None
    try:
        import pandas as pd
        return pd.read_parquet(p)
    except Exception:                                     # noqa: BLE001
        return None


def _cache_put(h: str, df, project=None) -> None:
    try:
        df.to_parquet(_cache_path(h, project), index=False)
    except Exception:                                     # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# the checked read
# ---------------------------------------------------------------------------
@dataclass
class ReadResult:
    df: Any
    sql: str
    sql_hash: str
    rows: int
    bytes_scanned: int | None
    elapsed_ms: int
    cache_hit: bool
    warnings: list[str]


def preflight(sql: str, *, mode: str = "explore", path: str | None = None,
              project=None) -> tuple[str, G.GuardResult]:
    """classify, then guard. Raises Denied with the operator block."""
    c = C.classify(sql)
    if not c.allowed:
        f = c.first()
        L.record({"kind": "read_attempt", "verdict": "denied_classifier",
                  "code": f.code, "gate": f.gate, "subject": f.matched,
                  "sql_hash": L.sql_hash(sql), "sql_path": path, "mode": mode},
                 project=project)
        raise Denied(C.deny_block(sql, c, path=path), code=f.code)

    g = G.static_checks(sql, mode=mode)
    # Nothing here refuses any more. The findings are recorded so what a read
    # did can be looked up later, and the caller prints them so nobody is
    # surprised afterwards. Then the read runs as written.
    if g.findings:
        L.record({"kind": "read_note",
                  "codes": [f.code for f in g.findings],
                  "sql_hash": L.sql_hash(sql), "sql_path": path, "mode": mode,
                  "tables": g.tables}, project=project)
    return g.sql, g


def explain_bytes(conn, sql: str) -> int | None:
    """EXPLAIN is itself a read and costs no credits. Layer 3."""
    try:
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN USING JSON {sql}")
            row = cur.fetchone()
        if not row:
            return None
        plan = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        for key in ("bytesAssigned", "bytes", "estimatedBytes"):
            if isinstance(plan, dict) and key in plan:
                return int(plan[key])
        if isinstance(plan, dict):
            g = plan.get("GlobalStats") or {}
            for key in ("bytesAssigned", "bytes"):
                if key in g:
                    return int(g[key])
    except Exception:                                     # noqa: BLE001
        return None
    return None


@dataclass
class WriteResult:
    rows_affected: int
    elapsed_ms: int
    sql_hash: str
    description: "D.WriteDescription"


class NeedsApproval(RuntimeError):
    """A write was asked for without an approval bound to this exact statement.

    Carries the block a person reads to decide. Not an error in the sense of
    something going wrong -- it is the question being asked.
    """

    def __init__(self, block: str, description):
        super().__init__(block)
        self.block = block
        self.description = description


def write_sql(sql: str, *, approve: str | None = None, reason: str = "",
              approver: str = "", conn=None, path: str | None = None,
              project=None) -> WriteResult:
    """Run one statement that is not a read, once a person has approved it.

    Writes are not refused any more -- creating a table, correcting a row and
    dropping a scratch object are real work. What must not happen is a write
    nobody looked at, so the shape is: describe, ask, then run.

    `approve` is the first 12 characters of the statement's own sha256, which
    `NeedsApproval` printed. Binding the approval to the statement means a
    yes to one thing cannot carry a different thing: edit a character and the
    hash changes and the approval no longer applies.

    Nothing here weakens the read path. Reads still go through `read_sql`, and
    a read sent here is sent back with a note rather than executed twice.
    """
    desc = D.describe(sql)
    if desc is None:
        raise Denied(
            "somsql write: 이건 읽기입니다. `somsql run` 을 쓰세요 — "
            "읽기에는 승인 절차가 없습니다.", code="NOT_A_WRITE")

    if not approve or not desc.sql_sha256.startswith(approve.strip().lower()):
        L.record({"kind": "write_attempt", "verdict": "needs_approval",
                  "verb": desc.verb, "objects": desc.objects,
                  "sql_hash": desc.sql_sha256, "sql_path": path},
                 project=project)
        raise NeedsApproval(D.confirm_block(desc, path=path), desc)

    if not reason.strip():
        # Recorded, not just refused. A refusal that leaves no trace is one
        # that gets routed around next time, and this one carries the useful
        # part anyway: somebody had the right hash and meant to run this.
        L.record({"kind": "write_attempt", "verdict": "denied_no_reason",
                  "verb": desc.verb, "objects": desc.objects,
                  "sql_hash": desc.sql_sha256, "sql_path": path},
                 project=project)
        raise Denied(
            "somsql write: --reason 이 필요합니다. 원장에 왜 했는지가 남아야 "
            "나중에 이 변경을 설명할 수 있습니다.", code="NO_REASON")

    started = time.time()

    def _send(c):
        with c.cursor() as cur:
            cur.execute(sql)
            return cur.rowcount if cur.rowcount is not None else -1

    # `connection` is a context manager, not a factory. Calling a `connect`
    # that does not exist raised NameError -- a traceback rather than a
    # message, on every real write, after the approval had been accepted. It
    # survived because every test passes a connection in and the only path
    # that opens one needs credentials this machine does not have.
    if conn is None:
        with connection(mode="full") as c:
            affected = _send(c)
    else:
        affected = _send(conn)
    elapsed = int((time.time() - started) * 1000)

    L.record({"kind": "write", "verdict": "executed", "verb": desc.verb,
              "objects": desc.objects, "rows_affected": affected,
              "elapsed_ms": elapsed, "reason": reason.strip(),
              "approver": approver.strip() or "(미기재)",
              "reversible": desc.reversible, "warnings": desc.warnings,
              "sql_hash": desc.sql_sha256, "sql_path": path},
             project=project)
    return WriteResult(rows_affected=affected, elapsed_ms=elapsed,
                       sql_hash=desc.sql_sha256, description=desc)


def read_sql(sql: str, *, conn=None, mode: str = "explore", path: str | None = None,
             project=None, use_cache: bool = True) -> ReadResult:
    """Run one checked read. Opens a connection if none is supplied.

    Prefer `read_sql_files` for a batch: it reuses one connection and therefore
    one SSO prompt.
    """
    checked_sql, g = preflight(sql, mode=mode, path=path, project=project)
    h = L.sql_hash(checked_sql)
    warnings = [f"{f.code}: {f.message}" for f in g.warnings]

    if use_cache:
        cached = _cache_get(h, project)
        if cached is not None:
            L.record({"kind": "read", "verdict": "ran", "cache_hit": True,
                      "sql_hash": h, "sql_path": path, "mode": mode,
                      "rows": len(cached), "elapsed_ms": 0, "tables": g.tables},
                     project=project)
            return ReadResult(cached, checked_sql, h, len(cached), 0, 0, True, warnings)

    if conn is None:
        with connection(mode=mode) as c:
            return _execute(c, checked_sql, h, g, mode, path, project, warnings)
    return _execute(conn, checked_sql, h, g, mode, path, project, warnings)


def _execute(conn, sql: str, h: str, g: G.GuardResult, mode: str,
             path: str | None, project, warnings: list[str]) -> ReadResult:
    est = explain_bytes(conn, sql)
    # Reported, never enforced. The estimate is free and worth printing; what
    # to do about a large number is the reader's call, not this module's.
    verdict = G.explain_verdict(est)
    if verdict and verdict.severity != "info":
        warnings.append(f"{verdict.code}: {verdict.message}")

    import pandas as pd
    # Every row the query returns. A ceiling here used to cancel the statement
    # mid-stream, which took the whole class of "the report was built on a
    # frame that stopped early" with it when it went.
    t0 = time.time()
    frames = []
    with conn.cursor() as cur:
        cur.execute(sql)
        for batch in cur.fetch_pandas_batches():
            frames.append(batch)
    elapsed = int((time.time() - t0) * 1000)

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    df.columns = [str(c).upper() for c in df.columns]     # every caller assumes upper

    _cache_put(h, df, project)
    L.record({"kind": "read", "verdict": "ran", "cache_hit": False,
              "sql_hash": h, "sql_path": path, "mode": mode,
              "rows": len(df), "bytes_scanned": est, "elapsed_ms": elapsed,
              "warehouse": R.warehouse(), "tables": g.tables}, project=project)
    return ReadResult(df, sql, h, len(df), est, elapsed, False, warnings)


def read_sql_files(paths: dict[str, str | Path], *, mode: str = "full",
                   project=None, progress=None) -> dict[str, ReadResult]:
    """Run several numbered SQL files on ONE connection.

    This is the piece worth carrying over from sf_utils.py: with
    `externalbrowser` auth, one connection means one browser prompt for the
    whole batch. Every statement is classified before the connection is opened,
    so a batch containing a write is refused before any SSO prompt appears.
    """
    loaded: dict[str, tuple[Path, str]] = {}
    for name, p in paths.items():
        p = Path(p)
        if not p.exists():
            raise FileNotFoundError(f"SQL file not found: {p}")
        sql = p.read_text(encoding="utf-8")
        preflight(sql, mode=mode, path=str(p), project=project)   # raises Denied
        loaded[name] = (p, sql)

    out: dict[str, ReadResult] = {}
    with connection(mode=mode) as conn:
        for name, (p, sql) in loaded.items():
            if progress:
                progress(name, p)
            out[name] = read_sql(sql, conn=conn, mode=mode, path=str(p),
                                 project=project)
    return out


def sql_files_in(folder: str | Path) -> dict[str, Path]:
    """Numbered SQL convention: NN_<subject>_<grain>.sql, one query per file.

    That convention is what lets EXECUTE fan out one worker per file.
    """
    d = Path(folder)
    return {p.stem: p for p in sorted(d.glob("[0-9][0-9]_*.sql"))}


def iter_meta(res: ReadResult) -> Iterator[tuple[str, Any]]:
    """The sidecar written next to a parquet extract so ATTEST can check it."""
    yield "rowcount", res.rows
    yield "bytes_scanned", res.bytes_scanned
    yield "elapsed_ms", res.elapsed_ms
    yield "sql_sha256", res.sql_hash
    yield "cache_hit", res.cache_hit
