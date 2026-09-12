r"""
Phase 1: club metadata for the 145 clubs -- league, country, city and crest URL.

Keyed to reference/club_id_mapping.csv, so every row hangs off an FBref team ID
and a Transfermarkt club ID, never a name.

WHERE EACH FIELD COMES FROM
  league            frozen Transfermarkt games: which league the club actually
                    played in, per season. "league" is its most recent season in
                    the window; leagues_in_window lists all of them (clubs get
                    promoted and relegated).
  country           the CLUB's own country, not its league's. It starts from the
                    league's country and is corrected in the review ledger where
                    they differ: Cardiff and Swansea are Welsh clubs in the
                    English league, Monaco is Monegasque in the French one. The
                    league field already says which competition they play in.
  city              Wikidata, joined on P7223 (Transfermarkt team ID). Not in
                    the frozen data: clubs.csv has a stadium but no city.
  crest_url         Transfermarkt's per-club-ID pattern, which covers all 145.
                    Wikidata's logo property only covers about half (non-free
                    trademarks are missing). Note these are club trademarks.

CITY NEEDS CARE, AND THE AUTOMATION IS NOT ENOUGH. A club's Wikidata
"headquarters location" is sometimes a building, not a place: Stade Rennais' is
its training centre. So a headquarters value is accepted only when that value is
itself a settlement; otherwise we fall back to the territory containing it, then
to the stadium's city. city_source records which rule produced the value.

The flag list is NOT exhaustive, and it is important to know why: a training
ground's commune usually IS a real town (Le Haillan, Avion, Formello), so it
passes the settlement test while still being the wrong answer for "the club's
city". All 145 values were therefore reviewed by eye once, and 16 were corrected
in reference/club_metadata_review.csv. If this is ever re-derived from changed
data, re-read all 145 rather than trusting the flags.

Run:  .venv\Scripts\python scripts\17_build_club_metadata.py
"""

import csv
import json
import sys
from pathlib import Path

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
TM = REPO_ROOT / "data" / "raw" / "transfermarkt"
WD_CACHE = REPO_ROOT / "data" / "raw" / "wikidata" / "club_metadata.json"
MAPPING = REPO_ROOT / "reference" / "club_id_mapping.csv"
LEDGER = REPO_ROOT / "reference" / "club_metadata_review.csv"
OUT = REPO_ROOT / "reference" / "club_metadata.csv"

SEASONS = range(2017, 2024)
LEAGUES = {"GB1": "Premier League", "ES1": "La Liga", "IT1": "Serie A", "L1": "Bundesliga", "FR1": "Ligue 1"}
CREST = "https://tmssl.akamaized.net/images/wappen/head/{}.png"
# Wikidata classes that mean "a place people live", so a headquarters value can be used as the city.
SETTLEMENT_WORDS = ("city", "town", "municipality", "commune", "village", "human settlement", "borough",
                    "urban", "metropolis", "comune", "big city", "capital")
UA = {"User-Agent": "Transfer-Market-Efficiency/1.0 (github.com/tbrunsting)"}

mapping = pd.read_csv(MAPPING)
tm_ids = [int(x) for x in mapping.transfermarkt_club_id]

# ---------------------------------------------------------------- leagues actually played, per season
games = pd.read_csv(TM / "games.csv", low_memory=False)
g = games[games.competition_id.isin(LEAGUES) & games.season.isin(SEASONS)]
played = pd.concat([g[["competition_id", "season", "home_club_id"]].rename(columns={"home_club_id": "club_id"}),
                    g[["competition_id", "season", "away_club_id"]].rename(columns={"away_club_id": "club_id"})]).drop_duplicates()
comp = pd.read_csv(TM / "competitions.csv").set_index("competition_id")

# ---------------------------------------------------------------- Wikidata: city
SPARQL = """
SELECT ?tmId ?club ?clubLabel ?hqLabel ?hqClassLabel ?hqAdminLabel ?venueCityLabel WHERE {
  VALUES ?tmId { %s }
  ?club wdt:P7223 ?tmId .
  OPTIONAL { ?club wdt:P159 ?hq .
             OPTIONAL { ?hq wdt:P31 ?hqClass }
             OPTIONAL { ?hq wdt:P131 ?hqAdmin } }
  OPTIONAL { ?club wdt:P115 ?venue . ?venue wdt:P131 ?venueCity }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
}
""" % " ".join(f'"{i}"' for i in tm_ids)

if WD_CACHE.exists():
    wd = json.loads(WD_CACHE.read_text(encoding="utf-8"))
    print(f"Wikidata: cached ({WD_CACHE.relative_to(REPO_ROOT).as_posix()})")
else:
    print("Wikidata: querying query.wikidata.org for all 145 clubs")
    r = requests.get("https://query.wikidata.org/sparql", params={"query": SPARQL},
                     headers={**UA, "Accept": "application/sparql-results+json"}, timeout=180)
    r.raise_for_status()
    wd = r.json()
    WD_CACHE.parent.mkdir(parents=True, exist_ok=True)
    WD_CACHE.write_text(json.dumps(wd, indent=1, ensure_ascii=False), encoding="utf-8")

val = lambda b, k: b.get(k, {}).get("value", "")
w = pd.DataFrame([{"tm_id": int(val(b, "tmId")), "qid": val(b, "club").split("/")[-1], "label": val(b, "clubLabel"),
                   "hq": val(b, "hqLabel"), "hq_class": val(b, "hqClassLabel"),
                   "hq_admin": val(b, "hqAdminLabel"), "venue_city": val(b, "venueCityLabel")}
                  for b in wd["results"]["bindings"]])
print(f"  {w.tm_id.nunique()} of {len(tm_ids)} clubs found on Wikidata ({len(w)} rows before aggregating)")


def choose_city(rows):
    """Headquarters if it's a settlement, else the territory containing it, else the stadium's city.

    Classes are grouped PER headquarters value. Pooling them across values let a settlement class
    on one headquarters validate a different, non-place value (Marseille's training centre).
    """
    per_hq = {}
    for r in rows.itertuples():
        if r.hq:
            e = per_hq.setdefault(r.hq, {"classes": set(), "admin": ""})
            if r.hq_class:
                e["classes"].add(r.hq_class.lower())
            e["admin"] = e["admin"] or r.hq_admin
    hq = next(iter(per_hq), "")
    classes = sorted(per_hq.get(hq, {}).get("classes", set()))
    admin = per_hq.get(hq, {}).get("admin", "")
    venue_city = next((x for x in rows.venue_city if x), "")
    settlement = next((h for h, e in per_hq.items()
                       if any(word in c for c in e["classes"] for word in SETTLEMENT_WORDS)), "")
    if settlement:
        return settlement, "wikidata P159 headquarters (a settlement)", ""
    if hq and admin:
        return admin, "wikidata P159 -> P131 (headquarters is not a settlement)", f"headquarters is '{hq}' ({'; '.join(sorted(set(classes))) or 'no class'})"
    if venue_city:
        return venue_city, "wikidata P115 stadium -> P131 city", f"headquarters unusable ('{hq}')" if hq else "no headquarters set"
    if hq:
        return hq, "wikidata P159 headquarters (class unknown)", f"could not confirm '{hq}' is a settlement"
    return "", "none", "no city found on Wikidata"


# ---------------------------------------------------------------- build
decisions = {}
if LEDGER.exists():
    with LEDGER.open(newline="", encoding="utf-8") as fh:
        decisions = {(d["fbref_team_id"], d["field"]): d for d in csv.DictReader(fh)}
    print(f"review ledger: {len(decisions)} decision(s)")

rows, flagged = [], 0
for m in mapping.itertuples():
    tm_id = int(m.transfermarkt_club_id)
    mine = played[played.club_id == tm_id]
    by_season = mine.sort_values("season")
    leagues = list(dict.fromkeys(LEAGUES[c] for c in by_season.competition_id))
    country = comp.loc[by_season.competition_id.iloc[-1], "country_name"]
    country_source = "league country (transfermarkt-datasets competitions)"
    wrows = w[w.tm_id == tm_id]
    notes, review = [], False
    if len(wrows):
        city, city_source, note = choose_city(wrows)
        qid = wrows.qid.iloc[0]
        if note:
            notes.append(note)
        if city_source != "wikidata P159 headquarters (a settlement)":
            review = True
    else:
        city, city_source, qid = "", "none", ""
        notes.append("no Wikidata item with this Transfermarkt id")
        review = True

    decision = decisions.get((m.fbref_team_id, "city"))
    if decision:
        if decision["decision"] == "override" and decision["value"] != city:
            notes.append(f"city overridden from '{city}' by {decision['decided_by']} on {decision['decided_on']}: {decision['reason']}")
            city, city_source = decision["value"], "manual override"
        else:
            notes.append(f"city accepted by {decision['decided_by']} on {decision['decided_on']}: {decision['reason']}")
        review = False
    cdec = decisions.get((m.fbref_team_id, "country"))
    if cdec and cdec["value"] != country:
        notes.append(f"country overridden from '{country}' (its league's) by {cdec['decided_by']} on "
                     f"{cdec['decided_on']}: {cdec['reason']}")
        country, country_source = cdec["value"], "manual override"
    flagged += review

    rows.append({"fbref_team_id": m.fbref_team_id, "club_name": m.transfermarkt_name,
                 "transfermarkt_club_id": tm_id, "country": country, "country_source": country_source,
                 "league": LEAGUES[by_season.competition_id.iloc[-1]], "leagues_in_window": " | ".join(leagues),
                 "seasons_in_window": by_season.season.nunique(), "city": city, "city_source": city_source,
                 "wikidata_qid": qid, "crest_url": CREST.format(tm_id),
                 "needs_manual_review": "yes" if review else "no", "notes": "; ".join(notes)})

out = pd.DataFrame(rows).sort_values(["country", "league", "club_name"])

# ---------------------------------------------------------------- crest URLs: do they all exist?
print("\nChecking every crest URL responds (145 HEAD requests)")
session = requests.Session()
session.headers.update(UA)
bad_crest = []
for r in out.itertuples():
    try:
        resp = session.head(r.crest_url, timeout=30, allow_redirects=True)
        if resp.status_code != 200 or "image" not in resp.headers.get("Content-Type", ""):
            bad_crest.append((r.club_name, r.crest_url, resp.status_code, resp.headers.get("Content-Type", "")))
    except requests.RequestException as e:
        bad_crest.append((r.club_name, r.crest_url, "error", type(e).__name__))
print(f"  {len(out) - len(bad_crest)} of {len(out)} crests return an image")

problems = []
unused = [k for k in decisions if k[0] not in set(out.fbref_team_id)]
if unused:
    problems.append(f"{len(unused)} review-ledger row(s) refer to clubs not in the mapping: {unused}")
if flagged:
    problems.append(f"{flagged} row(s) still need review and are not in the review ledger")
if len(out) != len(mapping):
    problems.append(f"{len(out)} rows, expected {len(mapping)}")
if out.city.eq("").any():
    problems.append(f"{out.city.eq('').sum()} clubs with no city")
if bad_crest:
    problems.append(f"{len(bad_crest)} crest URL(s) did not return an image: {bad_crest[:5]}")
if out.seasons_in_window.eq(0).any():
    problems.append("some clubs played no league season in the window")

OUT.parent.mkdir(parents=True, exist_ok=True)
out.to_csv(OUT, index=False, encoding="utf-8", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
print(f"\n{len(out)} clubs -> {OUT.relative_to(REPO_ROOT).as_posix()}")
print("  city sources: " + ", ".join(f"{k} {v}" for k, v in out.city_source.value_counts().items()))
print(f"  needs manual review: {flagged}")
print("\nVALIDATION: " + ("all 145 have a league, country, city and a working crest" if not problems else "FAILED"))
for p in problems:
    print("  - " + p)
fl = out[out.needs_manual_review == "yes"]
if len(fl):
    print(f"\n--- {len(fl)} row(s) flagged for review ---")
    for r in fl.itertuples():
        print(f"  {r.country:<8} {r.club_name:<30} city='{r.city}'\n      via {r.city_source}; {r.notes}")
sys.exit(1 if problems else 0)
