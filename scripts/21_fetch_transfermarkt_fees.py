r"""
Phase 2: fetch complete transfer fees from Transfermarkt's club transfer pages.

WHY THIS EXISTS
---------------
The frozen transfers table has a systematic, time-correlated hole: it covers
52% of player-club-seasons in 2017/18 rising to 98% in 2023/24, because the
upstream project keeps only each player's most recent extraction. Spot-checks
found Villarreal 2019/20 missing EUR 32.5m of fee-bearing arrivals (Paco
Alcácer, Javi Ontiveros -- both with hundreds of appearances and zero transfer
rows). Understating early-window spend more than late-window spend would
manufacture a "clubs got smarter" trend out of a collection artefact, which is
the project's headline finding. See docs/phase-2-schema.md section 2.

These pages also carry what the frozen table discards: every row is labelled
(permanent fee, "Loan fee: EUR x", "loan transfer", "End of loan",
"free transfer", "?"), which fixes the loan/free distinction and loan fees too.

WHAT IT DOES
  145 clubs x 7 seasons = 1,015 pages, one plain HTTP request each (no browser).
  Politeness: 5s spacing plus jitter, exponential backoff on 429/403, and it
  stops rather than hammering if refusals persist. The machine shares its IP
  with normal browsing, so this stays gentle on purpose.
  Every page is cached, so re-running costs nothing and resumes where it left
  off. Parsing always re-runs from cache.

OUTPUTS
  data/raw/transfermarkt_pages/<club>_<season>_transfers.html  (cached source)
  data/processed/tm_club_transfers.csv                          (parsed rows)
  reference/transfermarkt_pages_manifest.csv                    (per-page provenance)
  reference/transfer_coverage_check.csv                         (page vs frozen table, per club-season)

NOTE ON DATES. These pages give the season, not the transfer date, except for
"End of loan" rows which carry one. Rows matched to the frozen table inherit
its date; unmatched rows get the season's nominal start with
date_is_estimated = true.

Run:  .venv\Scripts\python scripts\21_fetch_transfermarkt_fees.py
      .venv\Scripts\python scripts\21_fetch_transfermarkt_fees.py --parse-only
"""

import csv
import hashlib
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
from lxml import html

REPO_ROOT = Path(__file__).resolve().parents[1]
TM = REPO_ROOT / "data" / "raw" / "transfermarkt"
CACHE = REPO_ROOT / "data" / "raw" / "transfermarkt_pages"
PROCESSED = REPO_ROOT / "data" / "processed"
MAPPING = REPO_ROOT / "reference" / "club_id_mapping.csv"
MANIFEST = REPO_ROOT / "reference" / "transfermarkt_pages_manifest.csv"
COVERAGE = REPO_ROOT / "reference" / "transfer_coverage_check.csv"
OUT = PROCESSED / "tm_club_transfers.csv"

SEASONS = range(2017, 2024)
PAUSE, JITTER = 5.0, 2.0
MAX_RETRIES, MAX_CONSECUTIVE_FAILURES = 4, 5
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/153.0 Safari/537.36",
      "Accept-Language": "en-GB,en;q=0.9"}

parse_only = "--parse-only" in sys.argv
mapping = pd.read_csv(MAPPING)
clubs = pd.read_csv(TM / "clubs.csv").set_index("club_id")
CACHE.mkdir(parents=True, exist_ok=True)
PROCESSED.mkdir(parents=True, exist_ok=True)
session = requests.Session()
session.headers.update(UA)


def fee_eur(text):
    """Transfermarkt's fee text -> (euros or None, kind). Loan fees are kept, unlike the frozen table."""
    t = " ".join(text.split())
    low = t.lower()
    if low.startswith("end of loan"):
        return 0.0, "end_of_loan"
    if low.startswith("loan fee"):
        m = re.search(r"([\d.]+)\s*([mk])?", t.split(":")[-1])
        mult = {"m": 1e6, "k": 1e3}.get((m.group(2) or "").lower(), 1) if m else 1
        return (float(m.group(1)) * mult if m else 0.0), "loan_with_fee"
    if low.startswith("loan"):
        return 0.0, "loan"
    if "free" in low:
        return 0.0, "free"
    if t in ("?", "-", ""):
        return None, "undisclosed"
    if t.startswith("€"):
        m = re.match(r"€([\d.]+)\s*([mk])?", t)
        if m:
            return float(m.group(1)) * {"m": 1e6, "k": 1e3}.get((m.group(2) or "").lower(), 1), "fee"
    return None, "unparsed"


def fetch(url, dest):
    """Fetch one page politely. Returns 'cached', 'fetched', or raises."""
    if dest.exists():
        return "cached"
    for attempt in range(MAX_RETRIES):
        r = session.get(url, timeout=60)
        if r.status_code == 200:
            dest.write_bytes(r.content)
            time.sleep(PAUSE + random.random() * JITTER)
            return "fetched"
        if r.status_code in (403, 429, 503):
            wait = PAUSE * (3 ** attempt) + random.random() * 5
            print(f"    HTTP {r.status_code}; backing off {wait:.0f}s (attempt {attempt + 1}/{MAX_RETRIES})", flush=True)
            time.sleep(wait)
            continue
        r.raise_for_status()
    raise RuntimeError(f"gave up on {url}")


# ---------------------------------------------------------------- fetch
targets = [(int(r.transfermarkt_club_id), r.transfermarkt_name, s) for r in mapping.itertuples() for s in SEASONS]
print(f"{len(targets)} club-seasons; {sum(1 for c, _, s in targets if (CACHE / f'{c}_{s}_transfers.html').exists())} already cached")
fetched = failures = 0
if not parse_only:
    for i, (club_id, name, season) in enumerate(targets, start=1):
        dest = CACHE / f"{club_id}_{season}_transfers.html"
        if dest.exists():
            continue
        code = clubs.loc[club_id, "club_code"]
        url = f"https://www.transfermarkt.com/{code}/transfers/verein/{club_id}/saison_id/{season}"
        try:
            fetch(url, dest)
            fetched += 1
            failures = 0
            if fetched % 25 == 0:
                print(f"  [{i}/{len(targets)}] fetched {fetched} pages (latest: {name} {season})", flush=True)
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"  FAILED {name} {season}: {type(e).__name__} {e}", flush=True)
            if failures >= MAX_CONSECUTIVE_FAILURES:
                sys.exit(f"STOP: {failures} consecutive failures. Re-run later; cached pages are kept.")
    print(f"fetched {fetched} new page(s)")

# ---------------------------------------------------------------- parse
rows, manifest, missing_pages = [], [], []
for club_id, name, season in targets:
    path = CACHE / f"{club_id}_{season}_transfers.html"
    if not path.exists():
        missing_pages.append((name, season))
        continue
    tree = html.parse(str(path))
    n_rows = 0
    for box in tree.xpath("//div[contains(@class,'box')][.//h2[contains(.,'Arrivals') or contains(.,'Departures')]]"):
        direction = "in" if "Arrivals" in box.xpath(".//h2")[0].text_content() else "out"
        for tr in box.xpath(".//table[contains(@class,'items')]/tbody/tr[td]"):
            href = tr.xpath(".//a[contains(@href,'/profil/spieler/')]/@href")
            if not href:
                continue
            cells = tr.xpath("./td")
            other = tr.xpath(".//a[contains(@href,'/verein/')]/@href")
            other_id = int(re.search(r"/verein/(\d+)", other[-1]).group(1)) if other else None
            amount, kind = fee_eur(cells[-1].text_content())
            end_date = re.search(r"(\d{2}/\d{2}/\d{4})", " ".join(cells[-1].text_content().split()))
            rows.append({"club_id": club_id, "club_name": name, "season": season,
                         "season_label": f"{season % 100:02d}/{(season + 1) % 100:02d}",
                         "direction": direction,
                         "player_id": int(re.search(r"/spieler/(\d+)", href[0]).group(1)),
                         "player_name": " ".join(tr.xpath(".//a[contains(@href,'/profil/spieler/')]")[0]
                                                 .text_content().split()),
                         "other_club_id": other_id, "fee_eur": amount, "fee_kind": kind,
                         "fee_text": " ".join(cells[-1].text_content().split()),
                         "stated_date": end_date.group(1) if end_date else ""})
            n_rows += 1
    manifest.append({"path": path.relative_to(REPO_ROOT).as_posix(), "club_id": club_id, "club_name": name,
                     "season": season, "url": f"https://www.transfermarkt.com/-/transfers/verein/{club_id}/saison_id/{season}",
                     "size_bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "parsed_rows": n_rows,
                     "retrieved_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})

df = pd.DataFrame(rows).drop_duplicates(["club_id", "season", "direction", "player_id"])
df.to_csv(OUT, index=False, encoding="utf-8", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
pd.DataFrame(manifest).to_csv(MANIFEST, index=False, encoding="utf-8", lineterminator="\n")
print(f"\nparsed {len(df):,} transfer rows from {len(manifest)} pages -> {OUT.relative_to(REPO_ROOT).as_posix()}")
if missing_pages:
    print(f"  {len(missing_pages)} page(s) still missing: {missing_pages[:5]}")
print("  fee kinds: " + ", ".join(f"{k} {v:,}" for k, v in df.fee_kind.value_counts().items()))

# ---------------------------------------------------------------- coverage: pages vs the frozen table
tr_frozen = pd.read_csv(TM / "transfers.csv", low_memory=False)
# Only club-seasons actually played in the big five count towards spend: we fetched all 145 x 7
# combinations, so a relegated club's second-tier season is in the cache but must not be summed.
points = pd.read_csv(REPO_ROOT / "reference" / "club_season_points.csv")
fb_of_tm = dict(zip(mapping.transfermarkt_club_id, mapping.fbref_team_id))
in_scope = {(r.fbref_team_id, r.season) for r in points.itertuples()}
cov = []
for (club_id, season), grp in df[df.direction == "in"].groupby(["club_id", "season"]):
    label = f"{season % 100:02d}/{(season + 1) % 100:02d}"
    frozen = tr_frozen[(tr_frozen.to_club_id == club_id) & (tr_frozen.transfer_season == label)]
    page_fees = grp.loc[grp.fee_kind == "fee", "fee_eur"].sum()
    frozen_fees = frozen.transfer_fee.sum()
    matched = grp.player_id.isin(frozen.player_id).sum()
    season_full = f"20{label[:2]}/{label[-2:]}"
    cov.append({"club_id": club_id, "club_name": grp.club_name.iloc[0], "season_label": label,
                "in_scope": (fb_of_tm.get(club_id), season_full) in in_scope,
                "page_arrivals": len(grp), "frozen_arrivals": len(frozen), "page_arrivals_matched": int(matched),
                "page_fees_eur": page_fees, "frozen_fees_eur": frozen_fees,
                "fees_missing_from_frozen_eur": page_fees - frozen_fees,
                "page_loan_fees_eur": grp.loc[grp.fee_kind == "loan_with_fee", "fee_eur"].sum()})
cv = pd.DataFrame(cov).sort_values(["season_label", "club_name"])
cv.to_csv(COVERAGE, index=False, encoding="utf-8", lineterminator="\n")
print(f"\ncoverage check -> {COVERAGE.relative_to(REPO_ROOT).as_posix()}")
sc = cv[cv.in_scope]
print(f"  club-seasons: {len(cv)} fetched, {len(sc)} in scope (played in the big five)")
print(f"  IN SCOPE fees on pages   EUR {sc.page_fees_eur.sum()/1e6:>9,.0f}m")
print(f"  IN SCOPE fees in frozen  EUR {sc.frozen_fees_eur.sum()/1e6:>9,.0f}m "
      f"({100*(sc.frozen_fees_eur.sum()/sc.page_fees_eur.sum()-1):+.1f}%)")
loan_all = df[df.fee_kind == "loan_with_fee"]
print(f"  loan fees recovered, both directions (absent from the frozen table entirely): "
      f"EUR {loan_all.fee_eur.sum()/1e6:,.1f}m across {len(loan_all):,} loans")
by_season = sc.groupby("season_label").agg(page=("page_fees_eur", "sum"), frozen=("frozen_fees_eur", "sum"))
by_season["frozen_pct_of_page"] = (100 * by_season.frozen / by_season.page).round(1)
print("\nby season (this is the bias the pull fixes):")
print(by_season.assign(page=(by_season.page / 1e6).round(0), frozen=(by_season.frozen / 1e6).round(0)).to_string())
