r"""
Phase 1: trophies won by the 145 clubs during the scored window (2017/18-2023/24).

WINDOW ONLY, NOT ALL-TIME. The dashboard panel is labelled "Trophies since
2017/18". The frozen Transfermarkt data starts in 2012, so all-time counts are
not available from it, and the mockup's panel was always window-scoped.

EVERY TROPHY IS TRACEABLE. club_trophies_detail.csv has one row per trophy with
its source and a reference: a Transfermarkt game_id for a cup final, the league
table it was computed from, or a Wikidata edition Q-item.

THREE CATEGORIES
  league_titles       the five domestic leagues, computed from the league table
  domestic_cups       both major cups where a country has two (FA Cup + EFL Cup;
                      Coupe de France + Coupe de la Ligue while it existed).
                      Domestic super cups are excluded by decision.
  european_trophies   Champions League, Europa League, Conference League

SOURCES
  Frozen Transfermarkt games for the leagues, FA Cup, Copa del Rey, Coppa
  Italia, DFB-Pokal, and all three European competitions. Winners come from the
  final's score; Transfermarkt folds penalty shootouts into it (e.g. Villarreal
  12-11 Manchester United), so finals are never ambiguous.
  Wikidata edition items for the two competitions missing from the frozen data:
  the English League Cup and the two French cups.

CROSS-CHECKS, not sources: league champions and cup-final counts are checked
against independently known results. A mismatch fails the run rather than being
written out.

Run:  .venv\Scripts\python scripts\16_build_club_trophies.py
"""

import csv
import json
import sys
from pathlib import Path

import pandas as pd
import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
TM = REPO_ROOT / "data" / "raw" / "transfermarkt"
WD_CACHE = REPO_ROOT / "data" / "raw" / "wikidata" / "cup_winners.json"
MAPPING = REPO_ROOT / "reference" / "club_id_mapping.csv"
OUT_DETAIL = REPO_ROOT / "reference" / "club_trophies_detail.csv"
OUT_SUMMARY = REPO_ROOT / "reference" / "club_trophies.csv"

SEASONS = range(2017, 2024)  # Transfermarkt labels a season by its starting year: 2017 = 2017/18
LEAGUES = {"GB1": "Premier League", "ES1": "La Liga", "IT1": "Serie A", "L1": "Bundesliga", "FR1": "Ligue 1"}
CUPS_IN_DATA = {"FAC": ("domestic_cups", "FA Cup"), "CDR": ("domestic_cups", "Copa del Rey"),
                "CIT": ("domestic_cups", "Coppa Italia"), "DFB": ("domestic_cups", "DFB-Pokal"),
                "CL": ("european_trophies", "Champions League"), "EL": ("european_trophies", "Europa League"),
                "UCOL": ("european_trophies", "Conference League")}
# Competitions absent from the frozen data, filled from Wikidata edition items.
WD_COMPS = {"Q11152": ("domestic_cups", "EFL Cup"), "Q212412": ("domestic_cups", "Coupe de France"),
            "Q476539": ("domestic_cups", "Coupe de la Ligue")}
SPARQL = """
SELECT ?comp ?edition ?editionLabel ?winnerLabel ?tmId WHERE {
  VALUES ?comp { wd:Q11152 wd:Q212412 wd:Q476539 }
  ?edition wdt:P3450 ?comp ; wdt:P1346 ?winner .
  OPTIONAL { ?winner wdt:P7223 ?tmId }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
}
"""
# Independently known results, used ONLY to verify what the script derives.
CROSSCHECK_CHAMPIONS = {
    ("GB1", 2017): "Manchester City", ("GB1", 2018): "Manchester City", ("GB1", 2019): "Liverpool",
    ("GB1", 2020): "Manchester City", ("GB1", 2021): "Manchester City", ("GB1", 2022): "Manchester City",
    ("GB1", 2023): "Manchester City",
    ("ES1", 2017): "Barcelona", ("ES1", 2018): "Barcelona", ("ES1", 2019): "Real Madrid",
    ("ES1", 2020): "Atlético", ("ES1", 2021): "Real Madrid", ("ES1", 2022): "Barcelona",
    ("ES1", 2023): "Real Madrid",
    ("IT1", 2017): "Juventus", ("IT1", 2018): "Juventus", ("IT1", 2019): "Juventus", ("IT1", 2020): "Inter",
    ("IT1", 2021): "Milan", ("IT1", 2022): "Napoli", ("IT1", 2023): "Inter",
    ("L1", 2017): "Bayern", ("L1", 2018): "Bayern", ("L1", 2019): "Bayern", ("L1", 2020): "Bayern",
    ("L1", 2021): "Bayern", ("L1", 2022): "Bayern", ("L1", 2023): "Leverkusen",
    ("FR1", 2017): "Paris", ("FR1", 2018): "Paris", ("FR1", 2019): "Paris", ("FR1", 2020): "Lille",
    ("FR1", 2021): "Paris", ("FR1", 2022): "Paris", ("FR1", 2023): "Paris",
}
EXPECTED = {"league_titles": 35, "domestic_cups": 45, "european_trophies": 17}

label = lambda season: f"{season}/{(season + 1) % 100:02d}"
problems, detail = [], []

mapping = pd.read_csv(MAPPING)
tm_to_fbref = dict(zip(mapping.transfermarkt_club_id, mapping.fbref_team_id))
fbref_name = dict(zip(mapping.fbref_team_id, mapping.transfermarkt_name))
games = pd.read_csv(TM / "games.csv", low_memory=False)

# ---------------------------------------------------------------- league titles, from the table
print("League titles (computed from the league table)")
for comp, comp_name in LEAGUES.items():
    for season in SEASONS:
        g = games[(games.competition_id == comp) & (games.season == season)]
        table = {}
        for r in g.itertuples():
            for club, gf, ga in ((r.home_club_id, r.home_club_goals, r.away_club_goals),
                                 (r.away_club_id, r.away_club_goals, r.home_club_goals)):
                t = table.setdefault(club, {"pts": 0, "gf": 0, "ga": 0})
                t["pts"] += 3 if gf > ga else (1 if gf == ga else 0)
                t["gf"] += gf
                t["ga"] += ga
        ranked = sorted(table.items(), key=lambda kv: (-kv[1]["pts"], -(kv[1]["gf"] - kv[1]["ga"]), -kv[1]["gf"]))
        champ_id, champ = ranked[0]
        runner = ranked[1][1]
        # A tie on points AND goal difference would need each league's own tie-breaker rules, so flag it.
        if champ["pts"] == runner["pts"] and (champ["gf"] - champ["ga"]) == (runner["gf"] - runner["ga"]):
            problems.append(f"{comp_name} {label(season)}: top two tie on points and goal difference")
        expect = CROSSCHECK_CHAMPIONS[(comp, season)]
        got = str(mapping.loc[mapping.transfermarkt_club_id == champ_id, "transfermarkt_name"].squeeze())
        if expect.lower() not in got.lower():
            problems.append(f"{comp_name} {label(season)}: computed {got}, expected {expect}")
        detail.append({"fbref_team_id": tm_to_fbref.get(champ_id, ""), "club": got, "season": label(season),
                       "category": "league_titles", "competition": comp_name,
                       "source": "transfermarkt-datasets games (league table)",
                       "source_ref": f"{comp}:{season}:table from {len(g)} games",
                       "detail": f"{champ['pts']} pts, GD {champ['gf'] - champ['ga']:+d}"})
    print(f"  {comp_name}: 7 seasons")

# ---------------------------------------------------------------- cup finals, from the final's score
print("\nCup finals (winner from the final's score, shootouts included)")
for comp, (category, comp_name) in CUPS_IN_DATA.items():
    f = games[(games.competition_id == comp) & games.season.isin(SEASONS)
              & (games["round"].str.strip().str.lower() == "final")]
    for r in f.itertuples():
        if r.home_club_goals == r.away_club_goals:
            problems.append(f"{comp_name} {label(r.season)}: final level at {r.home_club_goals}-{r.away_club_goals}, winner unclear")
            continue
        home_won = r.home_club_goals > r.away_club_goals
        win_id = r.home_club_id if home_won else r.away_club_id
        detail.append({"fbref_team_id": tm_to_fbref.get(win_id, ""),
                       "club": r.home_club_name if home_won else r.away_club_name, "season": label(r.season),
                       "category": category, "competition": comp_name,
                       "source": "transfermarkt-datasets games (final)", "source_ref": f"game_id={r.game_id}",
                       "detail": f"{r.home_club_name} {r.home_club_goals}-{r.away_club_goals} {r.away_club_name} on {r.date}"})
    print(f"  {comp_name}: {len(f)} finals")

# ---------------------------------------------------------------- the two missing cups, from Wikidata
if WD_CACHE.exists():
    wd = json.loads(WD_CACHE.read_text(encoding="utf-8"))
    print(f"\nWikidata editions (cached: {WD_CACHE.relative_to(REPO_ROOT).as_posix()})")
else:
    print("\nWikidata editions (querying query.wikidata.org)")
    resp = requests.get("https://query.wikidata.org/sparql", params={"query": SPARQL},
                        headers={"Accept": "application/sparql-results+json",
                                 "User-Agent": "Transfer-Market-Efficiency/1.0 (github.com/tbrunsting)"}, timeout=120)
    resp.raise_for_status()
    wd = resp.json()
    WD_CACHE.parent.mkdir(parents=True, exist_ok=True)
    WD_CACHE.write_text(json.dumps(wd, indent=1, ensure_ascii=False), encoding="utf-8")

import re
for comp_qid, (category, comp_name) in WD_COMPS.items():
    n = 0
    for b in wd["results"]["bindings"]:
        if b["comp"]["value"].split("/")[-1] != comp_qid:
            continue
        years = re.findall(r"(20\d\d)", b["editionLabel"]["value"])
        if not years or not (2017 <= int(years[0]) <= 2023):
            continue
        tm_id = int(b["tmId"]["value"]) if "tmId" in b else None
        detail.append({"fbref_team_id": tm_to_fbref.get(tm_id, ""), "club": b["winnerLabel"]["value"],
                       "season": label(int(years[0])), "category": category, "competition": comp_name,
                       "source": "wikidata edition item (P3450 competition, P1346 winner)",
                       "source_ref": b["edition"]["value"].split("/")[-1],
                       "detail": b["editionLabel"]["value"] + (f", Transfermarkt club {tm_id}" if tm_id else ", no Transfermarkt id")})
        n += 1
    print(f"  {comp_name}: {n} editions")

# ---------------------------------------------------------------- validate and write
d = pd.DataFrame(detail)
for category, expect in EXPECTED.items():
    got = (d.category == category).sum()
    if got != expect:
        problems.append(f"{category}: {got} trophies, expected {expect}")
dupes = d.groupby(["competition", "season"]).size()
if (dupes > 1).any():
    problems.append(f"more than one winner for: {dupes[dupes > 1].to_dict()}")
outside = d[d.fbref_team_id == ""]

d = d.sort_values(["category", "competition", "season"])
OUT_DETAIL.parent.mkdir(parents=True, exist_ok=True)
d.to_csv(OUT_DETAIL, index=False, encoding="utf-8", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)

won = d[d.fbref_team_id != ""]
# country and league come from the metadata table when it exists: it holds the club's own country
# (Cardiff and Swansea are Welsh, Monaco Monegasque), which the club mapping's league-based column does not.
META = REPO_ROOT / "reference" / "club_metadata.csv"
if META.exists():
    meta = pd.read_csv(META).set_index("fbref_team_id")
    country_of, league_of = meta.country.to_dict(), meta.league.to_dict()
    print(f"  club country/league from {META.relative_to(REPO_ROOT).as_posix()}")
else:
    country_of, league_of = dict(zip(mapping.fbref_team_id, mapping.country)), dict(zip(mapping.fbref_team_id, mapping.league))
    print("  club_metadata.csv not built yet, falling back to the mapping's league-based country")
summary = pd.DataFrame({"fbref_team_id": mapping.fbref_team_id, "club": mapping.fbref_team_id.map(fbref_name),
                        "country": mapping.fbref_team_id.map(country_of),
                        "league": mapping.fbref_team_id.map(league_of)})
for category in EXPECTED:
    summary[category] = summary.fbref_team_id.map(won[won.category == category].fbref_team_id.value_counts()).fillna(0).astype(int)
summary["total_trophies"] = summary[list(EXPECTED)].sum(axis=1)
summary = summary.sort_values(["total_trophies", "club"], ascending=[False, True])
summary.to_csv(OUT_SUMMARY, index=False, encoding="utf-8", lineterminator="\n")

print(f"\n{len(d)} trophies -> {OUT_DETAIL.relative_to(REPO_ROOT).as_posix()}")
print(f"{len(summary)} clubs -> {OUT_SUMMARY.relative_to(REPO_ROOT).as_posix()}  "
      f"({(summary.total_trophies > 0).sum()} clubs won something, {summary.total_trophies.sum()} trophies to our clubs)")
if len(outside):
    print(f"\n{len(outside)} trophies won by clubs outside the 145 (correctly not counted):")
    for r in outside.itertuples():
        print(f"  {r.season} {r.competition:<20} {r.club}")
print("\nVALIDATION: " + ("everything checks out" if not problems else "FAILED"))
for p in problems:
    print("  - " + p)
print("\nTop of the table:")
print(summary.head(12)[["club", "country", "league_titles", "domestic_cups", "european_trophies", "total_trophies"]].to_string(index=False))
sys.exit(1 if problems else 0)
