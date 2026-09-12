r"""
Phase 2 pre-check: is the frozen transfers table complete enough for recruitment ROI?

WHY
---
The frozen transfers table covers only 23,379 of Transfermarkt's 50,149 players,
so 36% of our players have no transfer history in it at all. A Chelsea 2023/24
spot-check found every real transfer present, suggesting the gap sits in academy
and free moves rather than fee-bearing ones -- but that was one club-season.
ROI is built on fees paid, so this checks a few club-seasons across the five
leagues against Transfermarkt's own club transfer pages before that logic exists.

WHAT IT COMPARES, per club-season
  fees paid on the page  vs  fees paid in the frozen table
  every arrival row on the page, matched to the table by player id
Missing rows are reported with the fee Transfermarkt shows, because a missing
free transfer or loan costs ROI nothing while a missing EUR 40m signing is fatal.

Makes one plain HTTP request per club-season (no browser). Not part of the
pipeline; run when the question comes up.

Usage: .venv\Scripts\python scripts\20_validate_transfer_coverage.py
"""

import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests
from lxml import html

REPO_ROOT = Path(__file__).resolve().parents[1]
TM = REPO_ROOT / "data" / "raw" / "transfermarkt"
MAPPING = REPO_ROOT / "reference" / "club_id_mapping.csv"
CACHE = REPO_ROOT / "data" / "raw" / "transfermarkt_pages"

# One club per league, picked for different spending profiles: a trading club, a heavy
# spender, a mid-table Spanish side, and a big French club.
CHECKS = [("Brighton & Hove Albion", 2022), ("RB Leipzig", 2021), ("Villarreal CF", 2019), ("Olympique Lyon", 2022)]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/153.0 Safari/537.36"}
PAUSE = 8  # polite spacing between page requests


def fee_eur(text):
    """Parse Transfermarkt's fee text. Returns (euros, kind)."""
    t = " ".join(text.split())
    low = t.lower()
    if low.startswith("end of loan"):
        return 0.0, "end of loan"
    if low.startswith("loan fee"):
        m = re.search(r"([\d.]+)\s*([mk])?", t.split(":")[-1])
        mult = {"m": 1e6, "k": 1e3}.get((m.group(2) or "").lower(), 1) if m else 1
        return (float(m.group(1)) * mult if m else 0.0), "loan with fee"
    if low.startswith("loan"):
        return 0.0, "loan"
    if "free" in low:
        return 0.0, "free"
    if t in ("?", "-", ""):
        return None, "undisclosed"
    if t.startswith("€"):
        m = re.match(r"€([\d.]+)\s*([mk])?", t)
        if m:
            mult = {"m": 1e6, "k": 1e3}.get((m.group(2) or "").lower(), 1)
            return float(m.group(1)) * mult, "fee"
    return None, f"unparsed({t})"


mapping = pd.read_csv(MAPPING)
clubs = pd.read_csv(TM / "clubs.csv")
tr = pd.read_csv(TM / "transfers.csv", low_memory=False)
session = requests.Session()
session.headers.update(UA)
CACHE.mkdir(parents=True, exist_ok=True)

print(f"{'club':<26} {'season':<8} {'page fees':>12} {'table fees':>12} {'diff':>10}  arrivals page/table")
summary = []
for club_name, season in CHECKS:
    row = mapping[mapping.transfermarkt_name == club_name]
    if row.empty:
        sys.exit(f"{club_name} is not in the club mapping")
    club_id = int(row.transfermarkt_club_id.iloc[0])
    code = clubs.loc[clubs.club_id == club_id, "club_code"].iloc[0]
    url = f"https://www.transfermarkt.com/{code}/transfers/verein/{club_id}/saison_id/{season}"
    cached = CACHE / f"{club_id}_{season}_transfers.html"
    if not cached.exists():
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        cached.write_bytes(resp.content)
        time.sleep(PAUSE)
    tree = html.parse(str(cached))

    page = []
    for box in tree.xpath("//div[contains(@class,'box')][.//h2[contains(.,'Arrivals')]]"):
        for trow in box.xpath(".//table[contains(@class,'items')]/tbody/tr[td]"):
            href = trow.xpath(".//a[contains(@href,'/profil/spieler/')]/@href")
            if not href:
                continue
            pid = int(re.search(r"/spieler/(\d+)", href[0]).group(1))
            amount, kind = fee_eur(trow.xpath("./td")[-1].text_content())
            page.append({"player_id": pid, "amount": amount, "kind": kind})
    page = pd.DataFrame(page).drop_duplicates("player_id")

    label = f"{season % 100:02d}/{(season + 1) % 100:02d}"
    tbl = tr[(tr.to_club_id == club_id) & (tr.transfer_season == label)]
    page_fees = page.loc[page.kind == "fee", "amount"].sum()
    tbl_fees = tbl.transfer_fee.sum()
    merged = page.merge(tbl[["player_id", "transfer_fee"]], on="player_id", how="left", indicator=True)
    missing = merged[merged._merge == "left_only"]
    print(f"{club_name:<26} {label:<8} {page_fees/1e6:>11.1f}m {tbl_fees/1e6:>11.1f}m "
          f"{(tbl_fees-page_fees)/1e6:>9.1f}m  {len(page)}/{len(tbl)}")
    summary.append({"club": club_name, "season": label, "page_fees": page_fees, "table_fees": tbl_fees,
                    "page_rows": len(page), "table_rows": len(tbl), "missing": missing})

print("\nArrival rows on the page that are NOT in the frozen table:")
for s in summary:
    miss = s["missing"]
    if miss.empty:
        print(f"  {s['club']} {s['season']}: none")
        continue
    by_kind = miss.kind.value_counts().to_dict()
    fee_missing = miss[miss.kind == "fee"].amount.sum()
    print(f"  {s['club']} {s['season']}: {len(miss)} missing {by_kind}; "
          f"fee-bearing value missed: EUR {fee_missing/1e6:.1f}m")
    for r in miss[miss.kind == "fee"].itertuples():
        print(f"      player {r.player_id}: EUR {r.amount/1e6:.1f}m NOT in the table")

tot_page = sum(s["page_fees"] for s in summary)
tot_tbl = sum(s["table_fees"] for s in summary)
print(f"\nAcross the {len(summary)} club-seasons: page EUR {tot_page/1e6:.1f}m vs table EUR {tot_tbl/1e6:.1f}m "
      f"({100*(tot_tbl/tot_page - 1) if tot_page else 0:+.1f}%)")
print("Verdict: fee-bearing arrivals are " + ("COMPLETE in these samples" if tot_tbl >= tot_page * 0.999
      else "INCOMPLETE -- see the missing rows above"))
