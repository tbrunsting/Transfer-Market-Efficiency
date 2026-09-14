r"""
Rebuild the warehouse from scratch: drop its objects, recreate them from sql/01_schema.sql.

The data itself is always reloaded from the frozen sources by 30_load_warehouse.py, so
dropping loses nothing that cannot be rebuilt byte for byte.

SAFETY. Before dropping anything it lists every table and view in the public and meta
schemas and refuses to continue if any of them is NOT one this project's schema creates.
Nothing that isn't ours is ever dropped.

Run: .venv\Scripts\python scripts\31_rebuild_warehouse.py
Then: .venv\Scripts\python scripts\30_load_warehouse.py
"""

import os
import re
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
load_dotenv(REPO / ".env")
schema_sql = (REPO / "sql" / "01_schema.sql").read_text(encoding="utf-8")
ours = {m.lower() for m in re.findall(r"CREATE (?:TABLE|VIEW)\s+([\w.]+)", schema_sql)}
ours = {o if "." in o else f"public.{o}" for o in ours}

with psycopg.connect(os.environ.get("DATABASE_URL", ""), autocommit=True) as conn:
    rows = conn.execute("""SELECT table_schema || '.' || table_name, table_type FROM information_schema.tables
                           WHERE table_schema IN ('public', 'meta')""").fetchall()
    present = {name.lower(): kind for name, kind in rows}
    foreign = sorted(set(present) - ours)
    if foreign:
        sys.exit(f"STOP: the database holds objects this project did not create, so nothing was dropped: {foreign}")
    print(f"dropping {len(present)} project objects: {sorted(present)}")
    with conn.transaction():
        for name, kind in present.items():
            if kind == "VIEW":
                conn.execute(f"DROP VIEW IF EXISTS {name} CASCADE")
        for name, kind in present.items():
            if kind != "VIEW":
                conn.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
        conn.execute("DROP SCHEMA IF EXISTS meta CASCADE")
    conn.execute(schema_sql)
    n = conn.execute("""SELECT count(*) FROM information_schema.tables
                        WHERE table_schema IN ('public', 'meta')""").fetchone()[0]
    print(f"recreated from sql/01_schema.sql: {n} tables and views")
