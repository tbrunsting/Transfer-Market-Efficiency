r"""
Phase 1, step 1: freeze the FBref data sources locally, with a checksummed manifest.

WHY
---
On 20 January 2026 Opta terminated FBref's data licence and FBref removed all
Opta advanced stats. The worldfootballR data repository, archived on
2025-09-18, is effectively the last public copy of that data. This script
makes a local copy of every file the project depends on and records exactly
which bytes were used, because GitHub publishes no checksums for these files.

WHAT IT FETCHES (about 170 MB, into data/raw/fbref/, which is gitignored)
- worldfootballR_data release fb_big5_advanced_season_stats: all files
- worldfootballR_data release old_fb_big5_advanced_season_stats: all files
- fbref_to_tm_mapping.csv, pinned to the repository's final commit
- Kaggle "FBref 2017-2024 for Europe's Top 5 leagues", version 2 (source of
  SCA per 90; see docs/phase-1-data-coverage.md)
Then it runs 11_rds_to_csv.R to convert each .rds to CSV.

THE MANIFEST (reference/fbref_snapshot_manifest.csv, tracked in git)
One row per file: where it came from, its source date, size, SHA-256, rows and
season range. Anyone can re-run this script and prove they have identical data.

SAFE TO RE-RUN
A file already on disk is hashed and compared with the manifest. If it matches
it's skipped; if it doesn't, the script stops -- these sources are frozen, so a
changed file means corruption or a changed source, and needs a human to look.

Run:  .venv\Scripts\python scripts\10_freeze_fbref_snapshot.py
"""

import csv
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW = REPO_ROOT / "data" / "raw" / "fbref"
MANIFEST = REPO_ROOT / "reference" / "fbref_snapshot_manifest.csv"
R_SCRIPT = Path(__file__).with_name("11_rds_to_csv.R")
RSCRIPT = os.environ.get("RSCRIPT") or shutil.which("Rscript") or r"C:\Program Files\R\R-4.4.1\bin\Rscript.exe"

GH_REPO = "JaseZiv/worldfootballR_data"
RELEASES = ["fb_big5_advanced_season_stats", "old_fb_big5_advanced_season_stats"]
MAPPING_PATH = "raw-data/fbref-tm-player-mapping/output/fbref_to_tm_mapping.csv"
KAGGLE_REF = "akshankrithick/fbref-2017-2024-for-europes-top-5-leagues"
KAGGLE_VERSION = 2

FIELDS = ["path", "role", "origin", "version", "source_url", "source_updated_at", "size_bytes",
          "sha256", "retrieved_at", "rows", "first_season", "last_season", "notes"]

ARCHIVE_NOTE = "worldfootballR_data archived 2025-09-18; FBref's Opta data removed 2026-01-20"
NOTES = {
    "fb_big5_advanced_season_stats/big5_player_passing.rds":
        "Seasons before 2022/23 are an older data version (Prog ~15% low, KP ~2% low): take PrgP/xAG from the standard file",
    "old_fb_big5_advanced_season_stats/big5_player_gca.rds":
        "Pre-Feb-2023 FBref revision; complete to 2021/22, 2022/23 to matchweek 23. SCA comes from Kaggle instead",
    "kaggle": "MIT licence covers the uploader's work; underlying data belongs to Sports Reference/Opta. Source of SCA per 90",
    "mapping": "FBref player URL to Transfermarkt player URL. MIXED ENCODING: UTF-8 except 3 lines in "
               "Windows-1252 (4900 Fransergio, 12081 Piero Hincapie, 14141 Theo Le Bris); decode per line "
               "with a cp1252 fallback when loading",
}

session = requests.Session()
session.headers["User-Agent"] = "Transfer-Market-Efficiency snapshot freeze (github.com/tbrunsting)"
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def api(url: str) -> dict | list:
    r = session.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with session.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    tmp.replace(dest)


def csv_stats(path: Path, season_col: str | None = None) -> tuple[int, str, str]:
    """Row count (records, not lines) and season range for a CSV we fetched or produced."""
    # errors="replace": the player mapping CSV has 3 lines in Windows-1252 (see NOTES). Counting
    # records doesn't depend on those characters, and the file itself is never modified.
    with path.open(newline="", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh)
        seasons, n = set(), 0
        for row in reader:
            n += 1
            if season_col:
                seasons.add(row[season_col])
    labels = sorted(f"{s[:4]}/{s[-2:]}" for s in seasons if s)
    return n, (labels[0] if labels else ""), (labels[-1] if labels else "")


old = {}
if MANIFEST.exists():
    with MANIFEST.open(newline="", encoding="utf-8") as fh:
        old = {row["path"]: row for row in csv.DictReader(fh)}
rows: dict[str, dict] = {}


def first_seen(key: str, digest: str) -> str:
    """Keep the original timestamp for an unchanged file, so re-runs don't rewrite the manifest."""
    prior = old.get(key)
    return prior["retrieved_at"] if prior and prior["sha256"] == digest else now


def secure(dest: Path, url: str, expected_size: int | None = None, **meta) -> None:
    """Make sure dest holds the frozen file: download if missing, verify against the manifest either way."""
    key = rel(dest)
    prior = old.get(key)
    if dest.exists():
        status = "already on disk"
    else:
        download(url, dest)
        status = "downloaded"
    digest, size = sha256(dest), dest.stat().st_size
    if expected_size is not None and size != expected_size:
        sys.exit(f"STOP: {key} is {size} bytes but the source lists {expected_size}.")
    if prior and prior["sha256"] != digest:
        sys.exit(f"STOP: {key} no longer matches the manifest checksum. The source is frozen, so investigate before re-running.")
    rows[key] = {**{f: "" for f in FIELDS}, **meta, "path": key, "source_url": url, "size_bytes": size,
                 "sha256": digest, "retrieved_at": prior["retrieved_at"] if prior else now}
    print(f"  {status + (', checksum matches manifest' if prior else ''):<45} {key}")


# 1. Both worldfootballR releases, every file.
for tag in RELEASES:
    release = api(f"https://api.github.com/repos/{GH_REPO}/releases/tags/{tag}")
    print(f"\n{tag}: {len(release['assets'])} files")
    for a in sorted(release["assets"], key=lambda a: a["name"]):
        name = f"{tag}/{a['name']}"
        secure(RAW / "worldfootballR" / tag / a["name"], a["browser_download_url"], expected_size=a["size"],
               role="source", origin=f"github:{GH_REPO}", version=f"release {tag}",
               source_updated_at=a["updated_at"], notes=NOTES.get(name, ARCHIVE_NOTE))

# 2. The player mapping CSV, pinned to the repository's final commit (it's archived, so this never moves).
head = api(f"https://api.github.com/repos/{GH_REPO}/commits/master")
sha, when = head["sha"], head["commit"]["committer"]["date"]
print(f"\nplayer mapping, pinned to final commit {sha[:10]} ({when})")
secure(RAW / "worldfootballR" / "fbref-tm-player-mapping" / "fbref_to_tm_mapping.csv",
       f"https://raw.githubusercontent.com/{GH_REPO}/{sha}/{MAPPING_PATH}",
       role="source", origin=f"github:{GH_REPO}", version=f"commit {sha}", source_updated_at=when, notes=NOTES["mapping"])

# 3. Kaggle, a specific version.
meta = api(f"https://www.kaggle.com/api/v1/datasets/view/{KAGGLE_REF}")
if meta.get("currentVersionNumber") != KAGGLE_VERSION:
    print(f"  note: Kaggle's current version is {meta.get('currentVersionNumber')}; pinning version {KAGGLE_VERSION}")
print(f"\nKaggle {KAGGLE_REF}, version {KAGGLE_VERSION}")
kzip = RAW / "kaggle" / f"fbref-2017-2024-v{KAGGLE_VERSION}.zip"
secure(kzip, f"https://www.kaggle.com/api/v1/datasets/download/{KAGGLE_REF}?datasetVersionNumber={KAGGLE_VERSION}",
       role="source", origin=f"kaggle:{KAGGLE_REF}", version=f"version {KAGGLE_VERSION}",
       source_updated_at=meta.get("lastUpdated", "") if meta.get("currentVersionNumber") == KAGGLE_VERSION else "",
       notes=NOTES["kaggle"])
kdir = kzip.with_suffix("")
with zipfile.ZipFile(kzip) as z:
    z.extractall(kdir)
    members = sorted(z.namelist())
for m in members:
    p = kdir / m
    n, first, last = csv_stats(p, season_col="season")
    rows[rel(p)] = {**{f: "" for f in FIELDS}, "path": rel(p), "role": "extracted", "origin": rel(kzip),
                    "version": f"version {KAGGLE_VERSION}", "size_bytes": p.stat().st_size, "sha256": (d := sha256(p)),
                    "retrieved_at": first_seen(rel(p), d), "rows": n, "first_season": first, "last_season": last}
mp = RAW / "worldfootballR" / "fbref-tm-player-mapping" / "fbref_to_tm_mapping.csv"
rows[rel(mp)]["rows"] = csv_stats(mp)[0]

# 4. Convert every .rds to CSV with R, then record rows, seasons and checksums.
print(f"\nconverting .rds to CSV with {RSCRIPT}")
subprocess.run([RSCRIPT, "--vanilla", str(R_SCRIPT), str(RAW)], check=True)
with (RAW / "csv" / "_conversion_log.csv").open(newline="", encoding="utf-8") as fh:
    for c in csv.DictReader(fh):
        src_key, out = rel(RAW / c["rds"]), RAW / c["csv"]
        rows[src_key].update(rows=c["rows"], first_season=c["first_season"], last_season=c["last_season"])
        n = csv_stats(out)[0]
        if n != int(c["rows"]):
            sys.exit(f"STOP: {rel(out)} has {n} rows but R wrote {c['rows']}.")
        rows[rel(out)] = {**{f: "" for f in FIELDS}, "path": rel(out), "role": "derived", "origin": src_key,
                          "version": "11_rds_to_csv.R", "size_bytes": out.stat().st_size, "sha256": (d := sha256(out)),
                          "retrieved_at": first_seen(rel(out), d), "rows": c["rows"], "first_season": c["first_season"],
                          "last_season": c["last_season"], "notes": "CSV conversion of origin; all seasons kept"}

MANIFEST.parent.mkdir(parents=True, exist_ok=True)
with MANIFEST.open("w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=FIELDS)
    w.writeheader()
    for key in sorted(rows):
        w.writerow(rows[key])

roles = {}
for r in rows.values():
    roles[r["role"]] = roles.get(r["role"], 0) + 1
total = sum(int(r["size_bytes"]) for r in rows.values() if r["role"] == "source")
print(f"\nmanifest: {rel(MANIFEST)}  ({', '.join(f'{v} {k}' for k, v in sorted(roles.items()))}; "
      f"sources total {total / 1e6:.1f} MB)")
