"""somsql CLI -- the only sanctioned path to Snowflake in this project.

    python -m somsql classify --file sql/01_x.sql        # no connection needed
    python -m somsql classify --sql "SELECT 1"
    python -m somsql plan     --dir sql/                 # classify a whole folder
    python -m somsql run      --file sql/01_x.sql [--mode full] [--out _cache/01.parquet]
    python -m somsql batch    --dir sql/ --out _cache/   # one connection, one SSO prompt
    python -m somsql explain  --file sql/01_x.sql        # EXPLAIN byte estimate only
    python -m somsql ledger   [--summary]
    python -m somsql write    --file <path> --dry-run           # what would it do?
    python -m somsql write    --file <path> --approve <hash> --reason "<why>"

Exit codes:
    0  ok
    1  usage or environment error
    2  warnings only (the read ran, but read them)
    3  DENIED -- classifier, cost guard, or secret policy
    4  NOT A READ -- the statement changes something and nobody has said yes yet

Exit 3 is not a retryable error. Stop, report the block verbatim, and ask the
person who owns the data. Do not rewrite the SQL to get around it and do not
reach for another client.

Exit 4 is not an error at all. It is the question being asked: print the
SOMSQL-CONFIRM block verbatim -- it already contains the exact command with the
hash filled in -- and wait for a person. Do not fill the hash in yourself.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import classify as C
from . import conn as CN
from . import describe as D
from . import ledger as L
from . import registry as R

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DENIED = 3
NEEDS_APPROVAL = 4       # not a failure: the question being asked


def _sql_from(a: argparse.Namespace) -> tuple[str, str | None]:
    if getattr(a, "sql", None):
        return a.sql, None
    p = Path(a.file)
    if not p.exists():
        raise SystemExit(f"somsql: file not found: {p}")
    return p.read_text(encoding="utf-8"), str(p)


# ---------------------------------------------------------------------------
def cmd_classify(a) -> int:
    sql, path = _sql_from(a)
    c = C.classify(sql, allow_multi=a.allow_multi)
    if a.json:
        print(json.dumps({
            "verdict": c.verdict, "roots": c.roots,
            "findings": [f.__dict__ for f in c.findings],
        }, ensure_ascii=False, indent=2))
        return 0 if c.allowed else DENIED
    if c.allowed:
        print(f"somsql classify: READ  roots={c.roots}" + (f"  {path}" if path else ""))
        return 0
    print(C.deny_block(sql, c, path=path))
    return DENIED


def cmd_plan(a) -> int:
    """Classify every numbered file in a folder without connecting.

    Run this before a batch: it turns 'the extract failed halfway through' into
    'these two files are not reads', at zero cost and with no SSO prompt.
    """
    files = CN.sql_files_in(a.dir)
    if not files:
        print(f"somsql plan: no NN_*.sql files in {a.dir}")
        return 1
    # Say what the cost guard actually knows before reporting what it decided.
    # An empty site registry means the date-predicate rule cannot fire, and a
    # clean "3/3 runnable" from a guard that is half asleep is worse than no
    # report at all.
    for line in R.profile(reload=True).describe_state():
        print(f"  note   {line}")
    bad = 0
    for name, p in files.items():
        sql = p.read_text(encoding="utf-8")
        # Go through preflight for everything, so a classifier refusal lands in
        # the ledger on this path too. `plan` is often the only thing that runs
        # before a human gives up on a batch; its refusals must be recorded.
        try:
            _, g = CN.preflight(sql, mode=a.mode, path=str(p))
            warn = f"  [{len(g.warnings)} warn]" if g.warnings else ""
            print(f"  READ   {name}{warn}")
        except CN.Denied as e:
            bad += 1
            gate = "DENY " if e.code in ("ROOT_NOT_A_READ", "WRITE_NODE_NESTED",
                                         "WRITE_TOKEN", "CLASSIFIER_DISAGREE",
                                         "MULTI_STATEMENT", "PARSE_FAILED", "EMPTY") else "GUARD"
            print(f"  {gate}  {name}  code={e.code}")
    print(f"\n{len(files) - bad}/{len(files)} runnable")
    if bad:
        print("nothing was executed and no connection was opened, so no SSO "
              "prompt appeared. Fix the files above before running the batch.")
    return DENIED if bad else 0


def cmd_run(a) -> int:
    sql, path = _sql_from(a)
    # A write sent to `run` used to hit the classifier and come back as a flat
    # refusal. It is a real request; it just needs someone to look at it first.
    d = D.describe(sql)
    if d is not None:
        print(D.confirm_block(d, path=path))
        return NEEDS_APPROVAL
    try:
        res = CN.read_sql(sql, mode=a.mode, path=path, use_cache=not a.no_cache)
    except CN.Denied as e:
        print(str(e))
        return DENIED
    except CN.SnowflakeUnavailable as e:
        print(f"somsql: {e}")
        return 1

    src = "cache" if res.cache_hit else "warehouse"
    scanned = (f"{res.bytes_scanned / 1024 / 1024:,.1f} MiB"
               if res.bytes_scanned else "n/a")
    print(f"somsql run: {res.rows:,} rows from {src} in {res.elapsed_ms:,} ms "
          f"(scanned {scanned})  hash={res.sql_hash[:12]}")
    for w in res.warnings:
        print(f"  warn  {w}")

    if a.out:
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        res.df.to_parquet(out, index=False)
        meta = out.with_suffix(".meta.json")
        meta.write_text(json.dumps(dict(CN.iter_meta(res)) | {"sql_path": path},
                                   ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        print(f"  wrote {out}  +  {meta.name}")
    elif a.head:
        with_pd = res.df.head(a.head)
        print(with_pd.to_string(index=False))
    return 2 if res.warnings else 0


def cmd_batch(a) -> int:
    files = CN.sql_files_in(a.dir)
    if not files:
        print(f"somsql batch: no NN_*.sql files in {a.dir}")
        return 1
    print(f"somsql batch: {len(files)} files on one connection "
          f"(one SSO prompt for the whole batch)")
    try:
        results = CN.read_sql_files(files, mode=a.mode,
                                    progress=lambda n, p: print(f"  -> {n}"))
    except CN.Denied as e:
        print(str(e))
        print("\nnothing was executed: the whole batch is classified before the "
              "connection opens, so a refused file stops it before any SSO prompt.")
        return DENIED
    except CN.SnowflakeUnavailable as e:
        print(f"somsql: {e}")
        return 1

    out_dir = Path(a.out) if a.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, res in results.items():
        total += res.rows
        print(f"  {name:34} {res.rows:>9,} rows  {res.elapsed_ms:>6,} ms"
              + ("  (cache)" if res.cache_hit else ""))
        if out_dir:
            p = out_dir / f"{name}.parquet"
            res.df.to_parquet(p, index=False)
            p.with_suffix(".meta.json").write_text(
                json.dumps(dict(CN.iter_meta(res)), ensure_ascii=False,
                           indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"\n{total:,} rows total")
    return 0


def cmd_explain(a) -> int:
    sql, path = _sql_from(a)
    try:
        checked, g = CN.preflight(sql, mode=a.mode, path=path)
    except CN.Denied as e:
        print(str(e))
        return DENIED
    try:
        with CN.connection(mode=a.mode) as conn:
            est = CN.explain_bytes(conn, checked)
    except CN.SnowflakeUnavailable as e:
        print(f"somsql: {e}")
        return 1
    if est is None:
        print("somsql explain: EXPLAIN 이 바이트 견적을 주지 않았습니다 — "
              "이 읽기의 크기는 실행해 봐야 알 수 있습니다")
        return 2
    mib = est / 1024 / 1024
    size = f"{mib / 1024:,.1f} GiB" if mib >= 1024 else f"{mib:,.1f} MiB"
    print(f"somsql explain: {size} 스캔 예상")
    # No ceiling, so no verdict. The number is the answer; what to do about it
    # belongs to whoever asked for the data.
    return 0


def cmd_ledger(a) -> int:
    if a.summary:
        print(json.dumps(L.cost_summary(), ensure_ascii=False, indent=2))
        print(f"\nhuman-readable refusal log: {L.write_log_md()}")
        return 0
    rows = L.read(limit=a.limit)
    if not rows:
        print("somsql ledger: empty")
        return 0
    for r in rows:
        print(f"  {r.get('ts','')}  {r.get('verdict',''):20} "
              f"{str(r.get('code') or ''):22} rows={r.get('rows','-')} "
              f"{r.get('sql_path') or r.get('sql_hash','')[:12]}")
    return 0


def cmd_write(a) -> int:
    """Run a statement that is not a read, after a person has approved it.

    Writes used to be refused outright. That was wrong for this team: creating
    a table, correcting a row and dropping a scratch object are real work. What
    must not happen is a write nobody looked at, so the shape is describe, ask,
    then run -- and the approval is bound to the statement's own hash, so a yes
    to one thing cannot carry a different thing.
    """
    sql, path = _sql_from(a)
    d = D.describe(sql)
    if d is None:
        print("somsql write: 이건 읽기입니다. `somsql run` 을 쓰세요 — "
              "읽기에는 승인 절차가 없습니다.")
        return 1

    if a.dry_run:
        print(D.confirm_block(d, path=path))
        return NEEDS_APPROVAL

    try:
        res = CN.write_sql(sql, approve=a.approve, reason=a.reason or "",
                           approver=a.approver or "", path=path)
    except CN.NeedsApproval as e:
        print(e.block)
        return NEEDS_APPROVAL
    except CN.Denied as e:
        print(str(e))
        return DENIED
    except CN.SnowflakeUnavailable as e:
        print(f"somsql: {e}")
        return 1

    n = res.rows_affected
    print(f"somsql write: {res.description.verb} 완료 "
          f"({'영향 행 ' + format(n, ',') if n >= 0 else '행 수 미보고'}) "
          f"in {res.elapsed_ms:,} ms  hash={res.sql_hash[:12]}")
    print(f"  대상  {', '.join(res.description.objects) or '(읽어내지 못함)'}")
    print(f"  사유  {a.reason}")
    print("  기록  .som/ledger.ndjson · .som/WRITE_LOG.md")
    if not res.description.reversible:
        print("  주의  이 작업은 되돌릴 수 없습니다.")
    return 0

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="somsql", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_source(p, *, need=True):
        g = p.add_mutually_exclusive_group(required=need)
        g.add_argument("--file")
        g.add_argument("--sql")

    c = sub.add_parser("classify", help="read or not, without connecting")
    add_source(c)
    c.add_argument("--allow-multi", action="store_true",
                   help="permit several statements (human-reviewed scripts only)")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_classify)

    p = sub.add_parser("plan", help="classify every NN_*.sql in a folder, no connection")
    p.add_argument("--dir", required=True)
    p.add_argument("--mode", default="full", choices=("explore", "full"))
    p.set_defaults(fn=cmd_plan)

    r = sub.add_parser("run", help="run one checked read")
    add_source(r)
    r.add_argument("--mode", default="explore", choices=("explore", "full"))
    r.add_argument("--out", help="write the result to parquet plus a .meta.json sidecar")
    r.add_argument("--head", type=int, default=0, help="print the first N rows")
    r.add_argument("--no-cache", action="store_true")
    r.set_defaults(fn=cmd_run)

    b = sub.add_parser("batch", help="run a folder of numbered SQL on one connection")
    b.add_argument("--dir", required=True)
    b.add_argument("--out", help="directory for parquet + sidecars")
    b.add_argument("--mode", default="full", choices=("explore", "full"))
    b.set_defaults(fn=cmd_batch)

    e = sub.add_parser("explain", help="byte estimate only, nothing executed")
    add_source(e)
    e.add_argument("--mode", default="explore", choices=("explore", "full"))
    e.set_defaults(fn=cmd_explain)

    lg = sub.add_parser("ledger", help="what has been attempted, refusals included")
    lg.add_argument("--summary", action="store_true")
    lg.add_argument("--limit", type=int, default=25)
    lg.set_defaults(fn=cmd_ledger)

    d = sub.add_parser("write", help="run a non-read statement after approval")
    d.add_argument("--file", help="path to the .sql file")
    d.add_argument("--sql", help="the statement inline, instead of --file")
    d.add_argument("--approve", help="the 12-char hash the confirm block printed")
    d.add_argument("--reason", help="why this change is being made; goes in WRITE_LOG.md")
    d.add_argument("--approver", help="who approved it")
    d.add_argument("--dry-run", action="store_true",
                   help="print the confirmation block and stop")
    d.set_defaults(fn=cmd_write)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except CN.Denied as ex:
        print(str(ex))
        return DENIED
    except FileNotFoundError as ex:
        print(f"somsql: {ex}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
