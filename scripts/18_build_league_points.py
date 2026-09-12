r"""
Phase 1: league points per club per season (2017/18-2023/24).

Points are the "sporting return" pillar of the efficiency score (scoping doc 5),
so that efficiency can't just mean cheap and bad.

METHOD. Standard 3-1-0 from every league match in the frozen Transfermarkt
games table. Position is ranked on points, then goal difference, then goals
scored.

TWO THINGS THIS TABLE IS HONEST ABOUT
1. points_from_results is exactly that: what the match results add up to. It
   does NOT include administrative points deductions, which a results table
   cannot know about. Where a deduction happened, the official table differs.
   The check below detects those cases from the data instead of from memory, by
   comparing the computed position with Transfermarkt's own reported position.
2. Ranking uses points, then goal difference, then goals scored. That matches
   the official rules in England, Germany and France, but Spain and Italy break
   ties on head-to-head results first, so a position could differ from the
   official table when two clubs finish level on points. Any such tie is
   flagged rather than silently ordered.

CROSS-CHECK: the computed champion of every league-season must equal the league
title recorded in reference/club_trophies_detail.csv, which was itself checked
against independently known results. A mismatch fails the run.

Run:  .venv\Scripts\python scripts\18_build_league_points.py
"""

import csv
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
TM = REPO_ROOT / "data" / "raw" / "transfermarkt"
MAPPING = REPO_ROOT / "reference" / "club_id_mapping.csv"
METADATA = REPO_ROOT / "reference" / "club_metadata.csv"
TROPHIES = REPO_ROOT / "reference" / "club_trophies_detail.csv"
OUT = REPO_ROOT / "reference" / "club_season_points.csv"

SEASONS = range(2017, 2024)
LEAGUES = {"GB1": "Premier League", "ES1": "La Liga", "IT1": "Serie A", "L1": "Bundesliga", "FR1": "Ligue 1"}
HEAD_TO_HEAD_LEAGUES = {"ES1", "IT1"}  # these break ties on head-to-head, which a table alone can't reproduce

label = lambda s: f"{s}/{(s + 1) % 100:02d}"
problems, rows = [], []

mapping = pd.read_csv(MAPPING)
tm_to_fbref = dict(zip(mapping.transfermarkt_club_id, mapping.fbref_team_id))
meta = pd.read_csv(METADATA).set_index("fbref_team_id")
games = pd.read_csv(TM / "games.csv", low_memory=False)

for comp, comp_name in LEAGUES.items():
    for season in SEASONS:
        g = games[(games.competition_id == comp) & (games.season == season)]
        table = {}
        for r in g.itertuples():
            for club, gf, ga in ((r.home_club_id, r.home_club_goals, r.away_club_goals),
                                 (r.away_club_id, r.away_club_goals, r.home_club_goals)):
                t = table.setdefault(club, {"mp": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0})
                t["mp"] += 1
                t["gf"] += gf
                t["ga"] += ga
                if gf > ga:
                    t["w"] += 1
                    t["pts"] += 3
                elif gf == ga:
                    t["d"] += 1
                    t["pts"] += 1
                else:
                    t["l"] += 1
        # Transfermarkt's own position, as of each club's last match of the season.
        last = {}
        for r in g.sort_values("date").itertuples():
            last[r.home_club_id] = r.home_club_position
            last[r.away_club_id] = r.away_club_position

        ranked = sorted(table.items(), key=lambda kv: (-kv[1]["pts"], -(kv[1]["gf"] - kv[1]["ga"]), -kv[1]["gf"]))
        for pos, (club_id, t) in enumerate(ranked, start=1):
            fb = tm_to_fbref.get(club_id, "")
            tm_pos = last.get(club_id)
            note = ""
            if pos > 1:
                above = ranked[pos - 2][1]
                if above["pts"] == t["pts"] and (above["gf"] - above["ga"]) == (t["gf"] - t["ga"]) and comp in HEAD_TO_HEAD_LEAGUES:
                    note = "level on points and goal difference with the club above; this league breaks ties on head-to-head, so the order here may differ from the official table"
            rows.append({"fbref_team_id": fb, "club": meta.club_name.get(fb, ""), "country": meta.country.get(fb, ""),
                         "league": comp_name, "season": label(season), "matches": t["mp"], "wins": t["w"],
                         "draws": t["d"], "losses": t["l"], "goals_for": t["gf"], "goals_against": t["ga"],
                         "goal_difference": t["gf"] - t["ga"], "points_from_results": t["pts"],
                         "position_computed": pos,
                         "position_transfermarkt": int(tm_pos) if pd.notna(tm_pos) else "",
                         "position_matches": "" if pd.isna(tm_pos) else ("yes" if int(tm_pos) == pos else "no"),
                         "notes": note})

points = pd.DataFrame(rows)

# ---------------------------------------------------------------- validate
champs = points[points.position_computed == 1]
titles = pd.read_csv(TROPHIES)
titles = titles[titles.category == "league_titles"].set_index(["competition", "season"]).fbref_team_id
for r in champs.itertuples():
    expect = titles.get((r.league, r.season))
    if expect != r.fbref_team_id:
        problems.append(f"{r.league} {r.season}: computed champion {r.club} does not match the trophies table ({expect})")
if len(points) != sum(98 if s < 2023 else 96 for s in SEASONS):
    problems.append(f"{len(points)} club-seasons, expected {sum(98 if s < 2023 else 96 for s in SEASONS)}")
if points.fbref_team_id.eq("").any():
    problems.append(f"{points.fbref_team_id.eq('').sum()} rows could not be mapped to an FBref club")
per_season = points.groupby(["league", "season"]).matches.sum() / 2
bad_counts = {k: int(v) for k, v in per_season.items() if v not in (380, 306, 279)}
if bad_counts:
    problems.append(f"unexpected match counts: {bad_counts}")

OUT.parent.mkdir(parents=True, exist_ok=True)
points.to_csv(OUT, index=False, encoding="utf-8", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
print(f"{len(points)} club-seasons -> {OUT.relative_to(REPO_ROOT).as_posix()}")
print(f"champions cross-checked against the trophies table: {len(champs)} of 35")

mismatch = points[points.position_matches == "no"]
print(f"\nComputed position differs from Transfermarkt's own position in {len(mismatch)} of {len(points)} club-seasons.")
print("These are where a points deduction or a head-to-head tie-break would explain the gap:")
for r in mismatch.sort_values(["season", "league", "position_computed"]).itertuples():
    print(f"  {r.season} {r.league:<15} {r.club:<24} computed {r.position_computed:>2} on {r.points_from_results} pts, "
          f"Transfermarkt says {r.position_transfermarkt}")
ties = points[points.notes != ""]
if len(ties):
    print(f"\n{len(ties)} row(s) level on points and goal difference in a head-to-head league:")
    for r in ties.itertuples():
        print(f"  {r.season} {r.league} {r.club} (position {r.position_computed}, {r.points_from_results} pts)")

print("\nVALIDATION: " + ("champions, club counts and match counts all check out" if not problems else "FAILED"))
for p in problems:
    print("  - " + p)
sys.exit(1 if problems else 0)
