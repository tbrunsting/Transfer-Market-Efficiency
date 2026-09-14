r"""
Phase 3a: find the correct Transfermarkt id for players the frozen dictionary mapped wrongly.

WHY
---
Checking every FBref player-season against Transfermarkt's own appearance records
showed 99.49% of minutes confirmed, but 0.15% (153 players, 11 regulars) under a
Transfermarkt id that never played for that club at all. The dictionary pointed
them at unrelated people -- Manuel Ugarte at "Edu", Mohammed Kudus at "Iddriss
Mohammed" -- so their signings joined to no output.

METHOD (squad evidence, the same idea as the club mapping)
For each such player, take every club-season FBref says he played. Candidates are
Transfermarkt players who appeared for those same clubs in those same seasons.
A candidate is accepted only if:
  * the names are compatible (one name's words contained in the other's), and
  * the birth year matches (when both are known), and
  * it covers more of his club-seasons than any other candidate (no tie).
Anything else is left alone and listed as unresolved -- never guessed.

RESULT (2026-09-13): of 153 flagged players, only 7 had a wrong id. 140 were confirmed
correct -- 41 by their other club-seasons, 99 because the dictionary id belongs to a
player with the same name and birth year -- so the flag was mostly gaps in
Transfermarkt's appearance table (worst in 2021/22). 6 stayed unresolved: four are
name variants of the same person (Peter / Oghenekaro Etebo), two have under 40 minutes.

OUTPUT: appends the `relink` rows to reference/player_id_review.csv, which the loader
already applies, and writes every outcome to reference/tm_id_check.csv.

Run: .venv\Scripts\python scripts\22_find_tm_id_relinks.py
"""

import os
import sys
import unicodedata
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import load_dotenv

REPO = Path(__file__).resolve().parents[1]
TM = REPO / "data" / "raw" / "transfermarkt"
LEDGER = REPO / "reference" / "player_id_review.csv"
UNRESOLVED = REPO / "reference" / "tm_id_check.csv"
load_dotenv(REPO / ".env")


def words(x):
    return set(unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()
               .replace("-", " ").replace(".", " ").split())


def ordered(x):
    return unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower().replace("-", " ").split()


def compatible(n1, n2):
    """Same person's name? One contained in the other, or same surname with one first name a prefix of the other
    (Javi / Javier Ontiveros)."""
    a, b = words(n1), words(n2)
    if a and b and (a <= b or b <= a):
        return True
    o1, o2 = ordered(n1), ordered(n2)
    return bool(o1 and o2 and o1[-1] == o2[-1] and (o1[0].startswith(o2[0]) or o2[0].startswith(o1[0])))


with psycopg.connect(os.environ.get("DATABASE_URL", "")) as conn:
    cur = conn.execute("""
        SELECT p.fbref_player_id, p.player_name, p.birth_year, p.transfermarkt_id AS dict_tm_id,
               c.transfermarkt_id AS club_tm_id, c.club_name, s.season_end_year, f.minutes
        FROM fact_player_season f JOIN dim_player p USING (player_key) JOIN dim_club c USING (club_key)
        JOIN dim_season s USING (season_key)""")
    ps = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])

ap = pd.read_csv(TM / "appearances.csv", usecols=["player_id", "player_club_id", "game_id", "player_name", "minutes_played"],
                 low_memory=False)
games = pd.read_csv(TM / "games.csv", usecols=["game_id", "season"], low_memory=False)
ap = ap.merge(games, on="game_id")
ap["season_end_year"] = ap.season + 1
played = ap.groupby(["player_id", "player_club_id", "season_end_year"]).agg(
    tm_name=("player_name", "first"), tm_minutes=("minutes_played", "sum")).reset_index()
seen = set(zip(played.player_id, played.player_club_id, played.season_end_year))
any_club = set(zip(played.player_id, played.player_club_id))
born = pd.read_csv(TM / "players.csv", usecols=["player_id", "name", "date_of_birth"])
tm_name_of = dict(zip(born.player_id, born.name))
born = dict(zip(born.player_id, pd.to_datetime(born.date_of_birth, errors="coerce").dt.year))

ps["dict_ok"] = [pd.notna(t) and (int(t), int(c)) in any_club for t, c in zip(ps.dict_tm_id, ps.club_tm_id)]
wrong_players = ps[~ps.dict_ok & ps.dict_tm_id.notna()].fbref_player_id.unique()
print(f"players with a player-season under a Transfermarkt id that never played for that club: {len(wrong_players)}")

existing = pd.read_csv(LEDGER, dtype=str)
already = set(existing.loc[existing.decision.isin(["relink", "split", "merge"]), "fbref_player_id"])

resolved, unresolved, confirmed = [], [], []
for fid in wrong_players:
    mine = ps[ps.fbref_player_id == fid]
    name, by, dict_id = mine.player_name.iloc[0], mine.birth_year.iloc[0], mine.dict_tm_id.iloc[0]
    if fid in already:
        continue
    keys = list(zip(mine.club_tm_id, mine.season_end_year))
    pool = played[[(c, s) in set(keys) for c, s in zip(played.player_club_id, played.season_end_year)]]
    cands = []
    for tm_id, grp in pool.groupby("player_id"):
        if not compatible(name, grp.tm_name.iloc[0]):
            continue
        tby = born.get(tm_id)
        if pd.notna(by) and pd.notna(tby) and int(by) != int(tby):
            continue
        covered = len({(c, s) for c, s in zip(grp.player_club_id, grp.season_end_year)} & set(keys))
        cands.append((covered, int(tm_id), grp.tm_name.iloc[0], tby))
    cands.sort(reverse=True)
    minutes = float(mine.minutes.fillna(0).sum())
    clubs = "; ".join(sorted({f"{c} {s-1}/{s % 100:02d}" for c, s in zip(mine.club_name, mine.season_end_year)}))
    dict_covers = next((c[0] for c in cands if c[1] == dict_id), 0)
    if dict_covers > 0 and (not cands or cands[0][1] == dict_id or cands[0][0] <= dict_covers):
        confirmed.append({"fbref_player_id": fid, "fbref_name": name, "dictionary_tm_id": int(dict_id),
                          "club_seasons_confirmed": dict_covers, "club_seasons": len(keys), "minutes": minutes})
        continue
    if cands and (len(cands) == 1 or cands[0][0] > cands[1][0]) and cands[0][1] != dict_id:
        cov, tm_id, tm_name, tby = cands[0]
        resolved.append({"fbref_player_id": fid, "fbref_name": name, "decision": "relink", "club": "",
                         "canonical_fbref_player_id": fid, "transfermarkt_id": tm_id,
                         "decided_by": "Tyler (batch approval 2026-09-13)", "decided_on": "2026-09-13",
                         "reason": (f"dictionary id {int(dict_id)} never appeared for his clubs; Transfermarkt "
                                    f"{tm_id} '{tm_name}' (born {tby}) appeared for {cov} of his {len(keys)} "
                                    f"club-seasons ({clubs})")})
    elif not cands and compatible(name, tm_name_of.get(int(dict_id), "")) and (
            pd.isna(by) or pd.isna(born.get(int(dict_id))) or int(by) == int(born.get(int(dict_id)))):
        # No appearance rows for ANYONE matching him at those club-seasons (a gap in the appearances table),
        # but the dictionary's id belongs to a player with his name and birth year: identity confirmed another way.
        confirmed.append({"fbref_player_id": fid, "fbref_name": name, "dictionary_tm_id": int(dict_id),
                          "club_seasons_confirmed": 0, "club_seasons": len(keys), "minutes": minutes,
                          "confirmed_by": f"name and birth year ('{tm_name_of.get(int(dict_id))}')"})
        continue
    else:
        why = ("no Transfermarkt player with a compatible name and birth year appeared for his clubs, and the "
               f"dictionary id belongs to '{tm_name_of.get(int(dict_id), '(not in players table)')}'" if not cands
               else f"tie between {[(c[1], c[2]) for c in cands[:3]]}")
        unresolved.append({"fbref_player_id": fid, "fbref_name": name, "birth_year": by,
                           "dictionary_tm_id": int(dict_id), "minutes": minutes, "club_seasons": clubs, "reason": why})

res, unr, con = pd.DataFrame(resolved), pd.DataFrame(unresolved), pd.DataFrame(confirmed)
n_by_seasons = int((con.club_seasons_confirmed > 0).sum()) if len(con) else 0
print(f"  dictionary id CONFIRMED -- flag was a gap in Transfermarkt's appearance data: {len(con)}")
print(f"      by his other club-seasons: {n_by_seasons} | by name and birth year of the dictionary id: {len(con) - n_by_seasons}")
print(f"  resolved to a different, name- and birth-year-compatible Transfermarkt id: {len(res)}")
print(f"  unresolved (left as they are, listed for review): {len(unr)}")

if len(res):
    taken = set(existing.transfermarkt_id.dropna().astype(int))
    clash = res[res.transfermarkt_id.isin(taken)]
    if len(clash):
        sys.exit(f"STOP: proposed ids already used in the ledger: {clash[['fbref_name', 'transfermarkt_id']].to_dict('records')}")
    dup = res[res.transfermarkt_id.duplicated(keep=False)]
    if len(dup):
        sys.exit(f"STOP: two players resolved to the same id: {dup[['fbref_name', 'transfermarkt_id']].to_dict('records')}")

if "--write" in sys.argv:
    out = pd.concat([existing, res.astype(str)], ignore_index=True)
    out.to_csv(LEDGER, index=False, encoding="utf-8", lineterminator="\n")
    # Every flagged player and what the check concluded, so the outcome is auditable.
    check = pd.concat([
        con.assign(outcome="dictionary id confirmed (the flag was a gap in Transfermarkt's appearance data)"),
        res.rename(columns={"transfermarkt_id": "new_tm_id"})[["fbref_player_id", "fbref_name", "new_tm_id", "reason"]]
           .assign(outcome="relinked to the id that actually played for his clubs"),
        unr.assign(outcome="unresolved: dictionary id kept (see reason)"),
    ], ignore_index=True)
    check.to_csv(UNRESOLVED, index=False, encoding="utf-8", lineterminator="\n")
    print(f"wrote {len(res)} relink rows to {LEDGER.name}; all {len(check)} outcomes to tm_id_check.csv")
else:
    pd.set_option("display.width", 220)
    print("\nlargest resolved (by minutes):")
    res["minutes"] = res.fbref_player_id.map(ps.groupby("fbref_player_id").minutes.sum())
    print(res.sort_values("minutes", ascending=False).head(15)[["fbref_name", "transfermarkt_id", "minutes", "reason"]]
          .to_string(index=False, max_colwidth=110))
    print("\nunresolved:")
    print(unr.sort_values("minutes", ascending=False).head(20).to_string(index=False, max_colwidth=80))
    print("\n(dry run: nothing written; pass --write to append to the ledger)")
