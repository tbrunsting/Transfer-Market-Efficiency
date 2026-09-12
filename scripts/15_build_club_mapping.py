r"""
Phase 1: build the club mapping table -- 145 FBref team IDs to Transfermarkt club IDs.

This is the item the scoping doc calls the highest risk in the project (section 7):
"Man Utd" / "Manchester United" / "Manchester Utd" all mean one club, and projects
like this commonly stall here. Both sides are local frozen data, so no scraping.

HOW IT MATCHES, in order
  1. Name match, inside the same country only. Names are normalised (accents
     stripped, lower-cased, legal-form words like FC/CF/AFC/1. and trailing
     founding numbers removed). Exact normalised match first, then fuzzy.
     The country constraint is hard: a fuzzy match may never cross countries.
  2. Squad overlap, which does not use names at all. For every FBref club-season
     its players are mapped to Transfermarkt player IDs (frozen worldfootballR
     player dictionary), and we look up which Transfermarkt club those players
     actually appeared for that season. A club's own squad is the strongest
     evidence there is that two IDs are the same club, and it catches the kind
     of error a name-only match makes.
  Both run for every club, so they can agree, disagree, or one can be missing.

NOTHING IS AUTO-ACCEPTED ON FUZZY NAMES. needs_manual_review is "no" only when
the names match exactly AND the squads agree AND no second candidate is close --
or when that exact pair appears in the review ledger.

THE REVIEW LEDGER (reference/club_mapping_review.csv)
Human decisions live in that file, not in this code: one row per decision, with
who decided, when and why. "accepted" clears a flagged pair; "resolved" supplies
the club for a conflict this script refuses to guess. Decisions are recorded per
(FBref id, Transfermarkt id) PAIR, so if a re-run ever produces a different
pairing than the one that was approved, it is flagged again instead of quietly
inheriting the approval.

Output: reference/club_id_mapping.csv (tracked in git).
Run:    .venv\Scripts\python scripts\15_build_club_mapping.py
"""

import csv
import difflib
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
FB = REPO_ROOT / "data" / "raw" / "fbref"
TM = REPO_ROOT / "data" / "raw" / "transfermarkt"
FB_CSV = FB / "csv" / "fb_big5_advanced_season_stats"
PLAYER_MAP = FB / "worldfootballR" / "fbref-tm-player-mapping" / "fbref_to_tm_mapping.csv"
OUT = REPO_ROOT / "reference" / "club_id_mapping.csv"
LEDGER = REPO_ROOT / "reference" / "club_mapping_review.csv"

WINDOW = range(2018, 2025)          # FBref Season_End_Year: 2018 = 2017/18 ... 2024 = 2023/24
TM_SEASONS = range(2017, 2024)      # Transfermarkt labels a season by its starting year
BIG5 = {"GB1": "Premier League", "ES1": "La Liga", "IT1": "Serie A", "L1": "Bundesliga", "FR1": "Ligue 1"}
EXPECTED_CLUBS = 145

FUZZY_FLOOR = 0.60        # below this, a name pair isn't worth proposing
AMBIGUOUS_MARGIN = 0.07   # runner-up this close to the best = ambiguous, flag it
AMBIGUOUS_FLOOR = 0.80    # any other candidate this similar is also worth flagging
MIN_SQUAD_SHARE = 0.80    # share of a club's mapped players appearing for one TM club

# Legal forms and founding years, not identity. "Real", "Athletic", "Borussia" are identity: kept.
NOISE = {"fc", "cf", "cfc", "afc", "ac", "as", "ass", "ss", "ssc", "sc", "us", "usc", "ud", "cd", "rc", "rcd",
         "sd", "sv", "tsv", "tsg", "vfl", "vfb", "fsv", "bsc", "spvgg", "spal", "calcio", "club", "football",
         "futbol", "futebol", "balompie", "cp", "ca", "aj", "sm", "og", "esp", "kgaa", "co", "1846", "1893",
         "1899", "1900", "1904", "1907", "1909", "04", "05", "96", "98", "1846", "eV"}
ABBREV = {"utd": "united", "st": "saint", "nott ham": "nottingham", "m gladbach": "monchengladbach",
          "s g": "saint germain", "gladbach": "monchengladbach", "dep": "deportivo", "atl": "atletico"}


def norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    for a, b in ABBREV.items():
        s = re.sub(rf"\b{a}\b", b, s)
    toks = [t for t in s.split() if t not in NOISE and not (t.isdigit() and len(t) <= 4)]
    return " ".join(toks) or s


def read_player_map() -> pd.DataFrame:
    """The frozen mapping CSV is UTF-8 except three Windows-1252 lines (see the FBref manifest)."""
    text = []
    with PLAYER_MAP.open("rb") as fh:
        for raw in fh:
            try:
                text.append(raw.decode("utf-8"))
            except UnicodeDecodeError:
                text.append(raw.decode("cp1252"))
    import io
    return pd.read_csv(io.StringIO("".join(text)))


# ---------------------------------------------------------------- FBref side
ts = pd.read_csv(FB_CSV / "big5_team_standard.csv", low_memory=False)
ts = ts[(ts.Team_or_Opponent == "team") & ts.Season_End_Year.isin(WINDOW)].copy()
ts["team_id"] = ts.Url.str.extract(r"/squads/([0-9a-f]{8})/")[0]
fb_clubs = {}
for tid, grp in ts.sort_values("Season_End_Year").groupby("team_id"):
    fb_clubs[tid] = {"aliases": list(dict.fromkeys(grp.Squad)), "league": grp.Comp.iloc[-1],
                     "seasons": sorted(grp.Season_End_Year)}
print(f"FBref: {len(fb_clubs)} team IDs, {sum(len(v['aliases']) > 1 for v in fb_clubs.values())} with more than one name")

# ---------------------------------------------------------------- Transfermarkt side
comp = pd.read_csv(TM / "competitions.csv")
comp = comp[comp.competition_id.isin(BIG5)].set_index("competition_id")
games = pd.read_csv(TM / "games.csv", low_memory=False)
gw = games[games.competition_id.isin(BIG5) & games.season.isin(TM_SEASONS)]
pairs = pd.concat([gw[["competition_id", "season", "home_club_id"]].rename(columns={"home_club_id": "club_id"}),
                   gw[["competition_id", "season", "away_club_id"]].rename(columns={"away_club_id": "club_id"})]).drop_duplicates()
clubs = pd.read_csv(TM / "clubs.csv").set_index("club_id")
tm_clubs = {}
for cid, grp in pairs.groupby("club_id"):
    league_id = grp.competition_id.mode().iloc[0]
    tm_clubs[int(cid)] = {"name": clubs.loc[cid, "name"], "country": comp.loc[league_id, "country_name"],
                          "league": BIG5[league_id], "seasons": sorted(grp.season)}
print(f"Transfermarkt: {len(tm_clubs)} club IDs in the five leagues, {min(TM_SEASONS)}-{max(TM_SEASONS)}")
FB_COUNTRY = {"Premier League": "England", "La Liga": "Spain", "Serie A": "Italy",
              "Bundesliga": "Germany", "Ligue 1": "France"}
by_country = {}
for cid, info in tm_clubs.items():
    by_country.setdefault(info["country"], []).append(cid)

# ---------------------------------------------------------------- squad overlap (names not used)
pl = pd.read_csv(FB_CSV / "big5_player_standard.csv", low_memory=False)
pl = pl[pl.Season_End_Year.isin(WINDOW)].copy()
team_key = ts.set_index(["Season_End_Year", "Squad"]).team_id
pl["team_id"] = [team_key.get((y, s)) for y, s in zip(pl.Season_End_Year, pl.Squad)]
pl["fbref_player"] = pl.Url.str.extract(r"/players/([0-9a-f]{8})/")[0]
pmap = read_player_map()
pmap["fbref_player"] = pmap.UrlFBref.str.extract(r"/players/([0-9a-f]{8})/")[0]
pmap["tm_player"] = pmap.UrlTmarkt.str.extract(r"/spieler/(\d+)")[0].astype("Int64")
pl = pl.merge(pmap[["fbref_player", "tm_player"]].dropna().drop_duplicates("fbref_player"), on="fbref_player", how="left")

app = pd.read_csv(TM / "appearances.csv", low_memory=False, usecols=["game_id", "player_id", "player_club_id", "competition_id"])
app = app[app.competition_id.isin(BIG5)].merge(games[["game_id", "season"]], on="game_id")
app = app[app.season.isin(TM_SEASONS)]
tm_player_club = app.groupby(["season", "player_id"]).player_club_id.agg(lambda s: s.mode().iloc[0])

overlap = {}
for tid, grp in pl.dropna(subset=["team_id", "tm_player"]).groupby("team_id"):
    votes = Counter()
    for season, tmp in zip(grp.Season_End_Year, grp.tm_player):
        club = tm_player_club.get((season - 1, int(tmp)))
        if pd.notna(club):
            votes[int(club)] += 1
    if votes:
        top, n = votes.most_common(1)[0]
        overlap[tid] = {"club_id": top, "share": n / sum(votes.values()), "players": sum(votes.values()),
                        "runner_up": (votes.most_common(2)[1] if len(votes) > 1 else None)}
print(f"squad overlap computed for {len(overlap)} of {len(fb_clubs)} FBref clubs "
      f"({pl.tm_player.notna().mean()*100:.1f}% of player-seasons had a mapped Transfermarkt player)")

# ---------------------------------------------------------------- human decisions
accepted, resolved = {}, {}
if LEDGER.exists():
    with LEDGER.open(newline="", encoding="utf-8") as fh:
        for d in csv.DictReader(fh):
            if d["decision"] == "resolved":
                resolved[d["fbref_team_id"]] = d
            else:
                accepted[(d["fbref_team_id"], int(d["transfermarkt_club_id"]))] = d
    print(f"review ledger: {len(accepted)} accepted pairs, {len(resolved)} resolved conflicts")
else:
    print("review ledger: none yet, so every non-exact match will be flagged")
used_decisions = set()

# ---------------------------------------------------------------- match
rows = []
for tid, info in fb_clubs.items():
    country = FB_COUNTRY[info["league"]]
    cands = by_country[country]
    scored = []
    for cid in cands:
        target = norm(tm_clubs[cid]["name"])
        best = max((difflib.SequenceMatcher(None, norm(a), target).ratio(), a) for a in info["aliases"])
        exact = any(norm(a) == target for a in info["aliases"])
        scored.append({"cid": cid, "ratio": 1.0 if exact else best[0], "exact": exact, "alias": best[1]})
    scored.sort(key=lambda x: -x["ratio"])
    top, second = scored[0], (scored[1] if len(scored) > 1 else None)
    ov = overlap.get(tid)

    ratio_of = {c["cid"]: c["ratio"] for c in scored}
    notes, review = [], False
    if top["exact"]:
        method, chosen = "exact", top["cid"]
    elif top["ratio"] >= FUZZY_FLOOR:
        method, chosen = "fuzzy", top["cid"]
        notes.append(f"name similarity {top['ratio']:.2f} ('{norm(top['alias'])}' vs '{norm(tm_clubs[top['cid']]['name'])}')")
        review = True
    else:
        method, chosen = "unmatched", None
        notes.append(f"no name candidate above {FUZZY_FLOOR:.2f} (best {top['ratio']:.2f}: {tm_clubs[top['cid']]['name']})")
        review = True

    name_choice = chosen
    if ov:
        notes.append(f"squad overlap: {ov['share']*100:.0f}% of {ov['players']} mapped players played for "
                     f"{tm_clubs.get(ov['club_id'], {}).get('name', ov['club_id'])}")
        if chosen is None and ov["share"] >= MIN_SQUAD_SHARE:
            method, chosen = "squad-overlap", ov["club_id"]
            notes.append("identified by squad overlap only, names too different")
            review = True
        elif chosen is not None and ov["club_id"] != chosen:
            # Two independent signals contradict each other. Choosing either one here would be a guess,
            # so the row is left empty for a human decision (validation then reports it as missing).
            notes.append(f"CONFLICT: names say {tm_clubs[chosen]['name']} ({chosen}), squads say "
                         f"{tm_clubs.get(ov['club_id'], {}).get('name', ov['club_id'])} ({ov['club_id']})")
            decision = resolved.get(tid)
            if decision and int(decision["transfermarkt_club_id"]) == ov["club_id"]:
                method, chosen, review = "manual", ov["club_id"], False
                notes.append(f"resolved by {decision['decided_by']} on {decision['decided_on']}: {decision['reason']}")
                used_decisions.add(tid)
            else:
                notes.append("left blank deliberately -- needs a human decision in the review ledger")
                method, chosen, review = "conflict", None, True
    else:
        notes.append("no squad-overlap evidence")
        review = True

    # Ambiguity, only where it could actually mislead: a rival that is also an exact/near-exact name match
    # when the choice came from names, or a rival with a real share of the squad when it came from squads.
    if second and name_choice is not None and method in ("exact", "fuzzy"):
        rival_close = second["ratio"] >= (0.95 if top["exact"] else top["ratio"] - AMBIGUOUS_MARGIN)
        if rival_close:
            notes.append(f"AMBIGUOUS: {tm_clubs[second['cid']]['name']} scores {second['ratio']:.2f} vs "
                         f"{top['ratio']:.2f} for {tm_clubs[top['cid']]['name']}")
            review = True
    if ov and ov["runner_up"] and chosen is not None:
        rival_id, rival_votes = ov["runner_up"]
        if rival_votes / ov["players"] >= 0.15:
            notes.append(f"AMBIGUOUS: {rival_votes / ov['players'] * 100:.0f}% of mapped players also played for "
                         f"{tm_clubs.get(rival_id, {}).get('name', rival_id)} ({rival_id})")
            review = True

    if review and chosen is not None and (tid, chosen) in accepted:
        d = accepted[(tid, chosen)]
        notes.append(f"accepted by {d['decided_by']} on {d['decided_on']}: {d['reason']}")
        used_decisions.add((tid, chosen))
        review = False

    rows.append({"fbref_team_id": tid, "fbref_name_aliases": " | ".join(info["aliases"]),
                 "transfermarkt_club_id": chosen if chosen is not None else "",
                 "transfermarkt_name": tm_clubs[chosen]["name"] if chosen else "",
                 "country": country, "league": info["league"], "match_method": method,
                 "squad_share": round(ov["share"], 3) if ov and chosen is not None and ov["club_id"] == chosen else "",
                 "name_score": round(ratio_of.get(chosen, 0.0), 3) if chosen is not None else "",
                 "needs_manual_review": "yes" if review else "no", "notes": "; ".join(notes)})

out = pd.DataFrame(rows).sort_values(["country", "league", "transfermarkt_name", "fbref_name_aliases"])

# ---------------------------------------------------------------- validate
problems = []
if len(out) != EXPECTED_CLUBS:
    problems.append(f"{len(out)} FBref IDs, expected {EXPECTED_CLUBS}")
missing = out[out.transfermarkt_club_id == ""]
if len(missing):
    problems.append(f"{len(missing)} FBref IDs with no Transfermarkt ID")
dups = out[out.transfermarkt_club_id != ""].transfermarkt_club_id.duplicated(keep=False)
if dups.any():
    problems.append(f"{dups.sum()} rows share a Transfermarkt ID with another row")
stale = [k for k in list(accepted) + list(resolved) if k not in used_decisions]
if stale:
    problems.append(f"{len(stale)} review-ledger decision(s) no longer match what the script produced: {stale}")
unused = set(tm_clubs) - set(pd.to_numeric(out.transfermarkt_club_id, errors="coerce").dropna().astype(int))
if unused:
    problems.append(f"{len(unused)} Transfermarkt clubs in scope were never matched: "
                    + ", ".join(f"{tm_clubs[c]['name']} ({c})" for c in sorted(unused)))

OUT.parent.mkdir(parents=True, exist_ok=True)
out.to_csv(OUT, index=False, encoding="utf-8", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
bym = out.match_method.value_counts()
print(f"\nwritten {OUT.relative_to(REPO_ROOT).as_posix()}: {len(out)} rows")
print("  by method: " + ", ".join(f"{k} {v}" for k, v in bym.items()))
print(f"  needs manual review: {(out.needs_manual_review == 'yes').sum()}")
print("\nVALIDATION: " + ("one-to-one, nothing missing" if not problems else "FAILED"))
for p in problems:
    print("  - " + p)
flagged = out[out.needs_manual_review == "yes"]
if len(flagged):
    print(f"\n--- {len(flagged)} row(s) flagged for review ---")
    for r in flagged.itertuples():
        print(f"  {r.country:<8} {r.fbref_team_id}  {r.fbref_name_aliases:<28} -> "
              f"{r.transfermarkt_name or '(none)':<26} [{r.match_method}, name {r.name_score}, "
              f"squad {r.squad_share}]\n      {r.notes}")
sys.exit(1 if problems else 0)
