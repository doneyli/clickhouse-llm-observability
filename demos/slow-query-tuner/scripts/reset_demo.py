#!/usr/bin/env python3
"""Put the tuning lab back the way sql/init/ left it, and clear stale kill sentinels.

    python demos/slow-query-tuner/scripts/reset_demo.py            # drop approved DDL, clear *.kill
    python demos/slow-query-tuner/scripts/reset_demo.py --dry-run  # say what it would do, change nothing

Act 5 approves a schema change (usually ADD PROJECTION) through the admin connection, and it
stays in the lab's volume. Left in place, it makes q3 fast without any DDL and can let the
--runaway target verify, so Acts 4 and 5 stop working. This drops every projection and skip
index on tuning_lab.web_events as tuner_admin. A changed ORDER BY cannot be undone with ALTER:
then it exits 1 and says how to recreate the lab.

It touches only the disposable clickhouse-tuning lab and this demo's checkpoints/ folder, never
Langfuse. Run seed_all.py afterwards if prompt labels moved. Standard library only.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_demo as v  # noqa: E402  (same folder; stdlib only)

CHECKPOINTS = v.DEMO_DIR / "checkpoints"


def leftover_ddl(ddl_tsv: str) -> List[Tuple[str, str]]:
    """(kind, name) for each projection and skip index in a SHOW CREATE TABLE result."""
    ddl = ddl_tsv.replace("\\n", "\n").replace("\\'", "'")
    found = []
    for kind in ("PROJECTION", "INDEX"):
        for m in re.finditer(rf"^\s*{kind}\s+`?([A-Za-z_][\w]*)`?", ddl, re.M):
            found.append((kind, m.group(1)))
    return found


def drop_statements(table: str, leftovers: List[Tuple[str, str]]) -> List[str]:
    return [f"ALTER TABLE {table} DROP {kind} IF EXISTS `{name}`" for kind, name in leftovers]


def clickhouse_exec(url: str, user: str, password: str, sql: str) -> None:
    """POST one statement (a GET is read-only on the server, so DDL needs POST)."""
    req = urllib.request.Request(url.rstrip("/") + "/", data=sql.encode(), method="POST",
                                 headers={"X-ClickHouse-User": user, "X-ClickHouse-Key": password})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200] if e.fp else ""
        raise v.ApiError(e.code, detail.strip() or str(e.reason)) from None
    except (urllib.error.URLError, OSError) as e:
        raise v.ApiError(None, f"{type(e).__name__}: {getattr(e, 'reason', e)}") from None


def clear_kill_sentinels(folder: Path, dry_run: bool) -> List[str]:
    names = sorted(p.name for p in folder.glob("*.kill")) if folder.is_dir() else []
    if not dry_run:
        for n in names:
            (folder / n).unlink()
    return names


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="report what would change, change nothing")
    args = ap.parse_args(argv)

    v.load_env(v.REPO_ROOT / ".env")  # for CLICKHOUSE_TUNING_HTTP_PORT; LANGFUSE_* are not used
    port = os.environ.get("CLICKHOUSE_TUNING_HTTP_PORT") or "8126"
    url = os.environ.get("TUNING_CH_URL") or f"http://localhost:{port}"
    user = os.environ.get("TUNING_CH_ADMIN_USER") or "tuner_admin"
    password = os.environ.get("TUNING_CH_ADMIN_PASSWORD") or "tuner_admin123"
    verb = "would" if args.dry_run else "did"

    killed = clear_kill_sentinels(CHECKPOINTS, args.dry_run)
    print(f"kill sentinels: {verb} remove {len(killed)}" + (f" ({', '.join(killed)})" if killed else ""))

    try:
        ddl = v.clickhouse_query(url, user, password, f"SHOW CREATE TABLE {v.LAB_TABLE}")
    except v.ApiError as e:
        print(f"ERROR: clickhouse-tuning at {url} as {user}: {e}")
        print("  start it: docker compose --profile demo up -d clickhouse-tuning")
        return 1
    statements = drop_statements(v.LAB_TABLE, leftover_ddl(ddl))
    for sql in statements:
        print(f"lab: {verb} run {sql}")
        if not args.dry_run:
            try:
                clickhouse_exec(url, user, password, sql)
            except v.ApiError as e:
                print(f"ERROR: {e}")
                return 1
    if not statements:
        print("lab: no projection or skip index to drop")

    if not args.dry_run and statements:
        ddl = v.clickhouse_query(url, user, password, f"SHOW CREATE TABLE {v.LAB_TABLE}")
    ok, detail = v.lab_shape(ddl) if not args.dry_run else (True, "")
    if not ok:
        print(f"ERROR: the lab table is still not in its seeded shape ({detail}). Recreate the lab:")
        print("  docker compose --profile demo rm -sf clickhouse-tuning, remove the clickhouse-tuning-data")
        print("  volume, then docker compose --profile demo up -d clickhouse-tuning")
        return 1
    print("done" + (" (dry run, nothing changed)" if args.dry_run else ": the lab is back to ORDER BY tuple() with no extras"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
