r"""
Phase 1: freeze the Transfermarkt source (transfermarkt-datasets) locally, with a manifest.

WHY
---
docs/phase-1-transfermarkt-evaluation.md adopted `dcaribou/transfermarkt-datasets`
as the Transfermarkt source. That project's collection pipeline has failed since
mid-July 2026 and updates are "paused indefinitely" (its GitHub discussion #383),
so what we use is a frozen snapshot -- same situation as the FBref data. This
makes a local copy and records exactly which bytes the project uses.

It downloads the gzipped CSVs (the immutable source bytes), unpacks each one,
and checksums both. Same contract as 10_freeze_fbref_snapshot.py: re-running
verifies instead of re-downloading, and a changed checksum stops the run.

Licence: CC0 for the dataset itself; the underlying data is Transfermarkt's.

Run:  .venv\Scripts\python scripts\14_freeze_transfermarkt.py
"""

import csv
import gzip
import hashlib
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW = REPO_ROOT / "data" / "raw" / "transfermarkt"
MANIFEST = REPO_ROOT / "reference" / "transfermarkt_snapshot_manifest.csv"
BASE = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data"

# Every table the project needs. clubs/games/competitions build the club mapping; transfers and
# player_valuations carry the money; club_games and appearances give managers and TM-side squads.
TABLES = ["competitions", "clubs", "games", "club_games", "transfers", "players", "player_valuations", "appearances"]
SEASON_COL = {"games": "season", "club_games": None, "transfers": "transfer_season", "player_valuations": None}
FIELDS = ["path", "role", "origin", "version", "source_url", "source_updated_at", "size_bytes",
          "sha256", "retrieved_at", "rows", "first_season", "last_season", "notes"]
NOTE = ("dcaribou/transfermarkt-datasets, CC0; updates paused since mid-July 2026 (discussion #383). "
        "games stop 2026-07-06, appearances 2026-06-28, valuations 2026-06-12; summer 2026 window incomplete")

session = requests.Session()
session.headers["User-Agent"] = "Transfer-Market-Efficiency snapshot freeze (github.com/tbrunsting)"
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rel(p: Path) -> str:
    return p.relative_to(REPO_ROOT).as_posix()


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def csv_stats(p: Path, season_col):
    with p.open(newline="", encoding="utf-8", errors="replace") as fh:
        seasons, n = set(), 0
        for row in csv.DictReader(fh):
            n += 1
            if season_col:
                seasons.add(str(row[season_col]))
    labels = sorted(s for s in seasons if s and s != "nan")
    return n, (labels[0] if labels else ""), (labels[-1] if labels else "")


old = {}
if MANIFEST.exists():
    with MANIFEST.open(newline="", encoding="utf-8") as fh:
        old = {r["path"]: r for r in csv.DictReader(fh)}
rows: dict[str, dict] = {}


def note_and_check(path: Path, digest: str, key: str) -> str:
    prior = old.get(key)
    if prior and prior["sha256"] != digest:
        sys.exit(f"STOP: {key} no longer matches the manifest checksum. Investigate before re-running.")
    return prior["retrieved_at"] if prior else now


for table in TABLES:
    url = f"{BASE}/{table}.csv.gz"
    gz, out = RAW / f"{table}.csv.gz", RAW / f"{table}.csv"
    RAW.mkdir(parents=True, exist_ok=True)
    if gz.exists():
        status = "already on disk"
    else:
        head = session.head(url, timeout=60)
        head.raise_for_status()
        tmp = gz.with_suffix(".part")
        with session.get(url, stream=True, timeout=300) as r:
            r.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
        tmp.replace(gz)
        status = "downloaded"
    modified = session.head(url, timeout=60).headers.get("Last-Modified", "")
    digest = sha256(gz)
    rows[rel(gz)] = {**{f: "" for f in FIELDS}, "path": rel(gz), "role": "source",
                     "origin": "r2:transfermarkt-datasets", "version": modified, "source_url": url,
                     "source_updated_at": modified, "size_bytes": gz.stat().st_size, "sha256": digest,
                     "retrieved_at": note_and_check(gz, digest, rel(gz)), "notes": NOTE}
    if not out.exists():
        with gzip.open(gz, "rb") as src, out.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    n, first, last = csv_stats(out, SEASON_COL.get(table, "season" if table == "games" else None))
    d2 = sha256(out)
    rows[rel(out)] = {**{f: "" for f in FIELDS}, "path": rel(out), "role": "derived", "origin": rel(gz),
                      "version": "gunzip", "size_bytes": out.stat().st_size, "sha256": d2,
                      "retrieved_at": note_and_check(out, d2, rel(out)), "rows": n,
                      "first_season": first, "last_season": last, "notes": "unpacked from origin"}
    print(f"  {status:<16} {table:<18} {gz.stat().st_size/1e6:>6.1f} MB gz -> {n:>9,} rows")

MANIFEST.parent.mkdir(parents=True, exist_ok=True)
with MANIFEST.open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=FIELDS)
    w.writeheader()
    for key in sorted(rows):
        w.writerow(rows[key])
total = sum(int(r["size_bytes"]) for r in rows.values() if r["role"] == "source")
print(f"\nmanifest: {rel(MANIFEST)}  ({len(TABLES)} tables, sources total {total/1e6:.1f} MB)")
