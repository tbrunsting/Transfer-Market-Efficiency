r"""
Phase 1: manager tenures per club (2017/18-2023/24), for scoping doc 4.9.

TENURE BOUNDARIES ARE MATCH-BASED, NOT DATE-BASED. This source has no
appointment or departure dates: what it has is the manager's name on every
match. A tenure here is therefore a contiguous run of a club's matches under
one manager name, and its edges are the first and last match that manager took,
not the day they signed or were sacked. A manager appointed during an
international break appears to start at the next match; one sacked after a
match appears to end at that match. Section 4.9 snaps the bands to season
boundaries anyway, so this is fine for that panel -- but nothing downstream
should present these as official appointment dates.

All competitions are used (league, domestic cups, Europe), because more matches
means tighter boundaries than the league alone would give.

A handful of matches (16 in this window) have no manager recorded. A gap like
that would otherwise split one tenure in two. Where the matches either side of
the gap are the same manager, the gap is absorbed into that tenure; where they
differ, it stays its own row named "(not recorded)".

NO MANAGER IDs. Names are the only identifier in this source, which creates two
risks, both flagged rather than guessed at:
  - the same person spelled two ways ("Jose"/"José"), which would split one
    tenure into two people;
  - two different people sharing a name, which would merge them.
A name appearing at two clubs at the same time is proof of the second, so that
is reported as a contradiction, not a warning.

Resolutions live in reference/manager_name_review.csv, keyed on (name, club),
the same club-context method the club mapping used for its conflicts: a shared
name is separated by which club's matches each stint belongs to. "split" renames
that name at that club; "confirmed_distinct" records that two similar names are
genuinely two people, so the similarity check stops asking. A stint whose
(name, club) is not in the ledger is still flagged.

Run:  .venv\Scripts\python scripts\19_build_manager_tenures.py
"""

import csv
import difflib
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
TM = REPO_ROOT / "data" / "raw" / "transfermarkt"
MAPPING = REPO_ROOT / "reference" / "club_id_mapping.csv"
METADATA = REPO_ROOT / "reference" / "club_metadata.csv"
OUT = REPO_ROOT / "reference" / "manager_tenures.csv"

SEASONS = range(2017, 2024)
CARETAKER_MATCHES = 3      # a run this short is usually a caretaker; noted, not flagged
SPELLING_SIMILARITY = 0.88  # two names this close are probably the same person spelled differently

label = lambda s: f"{s}/{(s + 1) % 100:02d}"
strip = lambda s: unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower().strip()

mapping = pd.read_csv(MAPPING)
tm_to_fbref = dict(zip(mapping.transfermarkt_club_id, mapping.fbref_team_id))
meta = pd.read_csv(METADATA).set_index("fbref_team_id")
games = pd.read_csv(TM / "games.csv", low_memory=False)
cg = pd.read_csv(TM / "club_games.csv", low_memory=False)

# Every match played by one of our 145 clubs in the window, with who managed it.
g = games[games.season.isin(SEASONS)][["game_id", "season", "date", "competition_id", "competition_type"]]
m = cg[["game_id", "club_id", "own_manager_name"]].merge(g, on="game_id")
m = m[m.club_id.isin(tm_to_fbref)].sort_values(["club_id", "date"]).reset_index(drop=True)
print(f"{len(m):,} club-matches in the window for the 145 clubs, "
      f"{m.own_manager_name.isna().sum()} with no manager name")
print(f"  competitions: {', '.join(f'{k} {v:,}' for k, v in m.competition_type.value_counts().items())}")

# ---------------------------------------------------------------- apply the name ledger
LEDGER = REPO_ROOT / "reference" / "manager_name_review.csv"
splits, distinct, used = {}, set(), set()
if LEDGER.exists():
    with LEDGER.open(newline="", encoding="utf-8") as fh:
        for d in csv.DictReader(fh):
            if d["decision"] == "split":
                splits[(d["manager_name"], d["club"])] = d
            else:
                distinct.add(d["manager_name"])
    club_name_of = {cid: meta.club_name.get(fb, "") for cid, fb in tm_to_fbref.items()}
    renamed = 0
    for (name, club), d in splits.items():
        hit = (m.own_manager_name == name) & (m.club_id.map(club_name_of) == club)
        if hit.any():
            m.loc[hit, "own_manager_name"] = d["resolved_name"]
            used.add((name, club))
            renamed += int(hit.sum())
    print(f"  name ledger: {len(splits)} split rule(s) renamed {renamed} club-matches, "
          f"{len(distinct)} name(s) confirmed as distinct people")

# ---------------------------------------------------------------- fill unrecorded managers where it is unambiguous
absorbed = 0
for club_id, grp in m.groupby("club_id"):
    idx = grp.sort_values("date").index
    names = m.loc[idx, "own_manager_name"]
    before, after = names.ffill(), names.bfill()
    fill = names.isna() & (before == after) & before.notna()
    m.loc[idx[fill], "own_manager_name"] = before[fill]
    absorbed += int(fill.sum())
still_unknown = m.own_manager_name.isna()
m.loc[still_unknown, "own_manager_name"] = "(not recorded)"
print(f"  {absorbed} unrecorded match(es) absorbed into the surrounding tenure, "
      f"{int(still_unknown.sum())} left as '(not recorded)'")

# ---------------------------------------------------------------- contiguous runs
rows = []
for club_id, grp in m.groupby("club_id"):
    grp = grp.sort_values("date")
    runs = (grp.own_manager_name != grp.own_manager_name.shift()).cumsum()
    for seq, (_, r) in enumerate(grp.groupby(runs), start=1):
        fb = tm_to_fbref[club_id]
        r = r.sort_values("date")
        rows.append({"fbref_team_id": fb, "club": meta.club_name.get(fb, ""), "country": meta.country.get(fb, ""),
                     "manager_name": r.own_manager_name.iloc[0], "stint_seq": seq,
                     "first_match_date": r.date.iloc[0], "last_match_date": r.date.iloc[-1],
                     "first_season": label(r.season.iloc[0]), "last_season": label(r.season.iloc[-1]),
                     "seasons_spanned": r.season.nunique(), "matches": len(r),
                     "league_matches": int((r.competition_type == "domestic_league").sum()),
                     "likely_caretaker": "yes" if len(r) <= CARETAKER_MATCHES else "no", "notes": ""})
t = pd.DataFrame(rows)

# ---------------------------------------------------------------- name risks
names = sorted(t.manager_name.dropna().unique())
spelling_pairs = []
for i, a in enumerate(names):
    for b in names[i + 1:]:
        if a in distinct and b in distinct:
            continue  # already checked by a human and confirmed to be two different people
        if strip(a) == strip(b) or difflib.SequenceMatcher(None, strip(a), strip(b)).ratio() >= SPELLING_SIMILARITY:
            spelling_pairs.append((a, b))

# A name at two clubs at overlapping times cannot be one person.
overlaps = []
for name, grp in t.groupby("manager_name"):
    spells = grp.sort_values("first_match_date")[["club", "first_match_date", "last_match_date"]].values.tolist()
    for i in range(len(spells)):
        for j in range(i + 1, len(spells)):
            c1, s1, e1 = spells[i]
            c2, s2, e2 = spells[j]
            if c1 != c2 and s2 <= e1 and s1 <= e2:
                overlaps.append((name, c1, s1, e1, c2, s2, e2))
for name, c1, s1, e1, c2, s2, e2 in overlaps:
    t.loc[t.manager_name == name, "notes"] = (f"SAME NAME AT TWO CLUBS AT ONCE ({c1} {s1}..{e1} and {c2} {s2}..{e2}): "
                                              "must be two different people; needs a manual split")
for a, b in spelling_pairs:
    for n in (a, b):
        prior = t.loc[t.manager_name == n, "notes"].iloc[0] if (t.manager_name == n).any() else ""
        t.loc[t.manager_name == n, "notes"] = (prior + "; " if prior else "") + f"POSSIBLE SPELLING VARIANT of '{b if n == a else a}'"

# ---------------------------------------------------------------- validate
problems = []
if t.matches.sum() != len(m):
    problems.append(f"tenure matches sum to {t.matches.sum()}, but there are {len(m)} club-matches")
if t.fbref_team_id.nunique() != len(mapping):
    problems.append(f"{t.fbref_team_id.nunique()} clubs have tenures, expected {len(mapping)}")
stale = [k for k in splits if k not in used]
if stale:
    problems.append(f"{len(stale)} name-ledger split rule(s) matched nothing: {stale}")
if overlaps:
    problems.append(f"{len(overlaps)} shared manager name(s) still unresolved: "
                    + "; ".join(f"{o[0]} at {o[1]} and {o[4]}" for o in overlaps))
# Independently known, used only to verify: who took at least one LEAGUE match for that club that season.
CROSSCHECK = {
    ("Chelsea FC", 2022): ["Thomas Tuchel", "Graham Potter", "Bruno Saltor", "Frank Lampard"],
    ("Bayern Munich", 2022): ["Julian Nagelsmann", "Thomas Tuchel"],
    ("Real Madrid", 2023): ["Carlo Ancelotti"],
    ("Manchester City", 2023): ["Pep Guardiola"],
}
club_of = dict(zip(mapping.transfermarkt_club_id, mapping.fbref_team_id))
league_only = m[m.competition_type == "domestic_league"]
for (club, season), expect in CROSSCHECK.items():
    ids = [k for k, v in club_of.items() if meta.club_name.get(v, "") == club]
    x = league_only[(league_only.club_id.isin(ids)) & (league_only.season == season)].sort_values("date")
    got = list(dict.fromkeys(x.own_manager_name))
    if got != expect:
        problems.append(f"{club} {label(season)}: managers {got}, expected {expect}")

t = t.sort_values(["country", "club", "first_match_date"])
OUT.parent.mkdir(parents=True, exist_ok=True)
t.to_csv(OUT, index=False, encoding="utf-8", lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
print(f"\n{len(t)} tenures across {t.fbref_team_id.nunique()} clubs -> {OUT.relative_to(REPO_ROOT).as_posix()}")
print(f"  distinct manager names: {len(names)} | likely caretakers (<= {CARETAKER_MATCHES} matches): {(t.likely_caretaker == 'yes').sum()}")
print(f"  tenures per club: min {t.groupby('fbref_team_id').size().min()}, "
      f"median {int(t.groupby('fbref_team_id').size().median())}, max {t.groupby('fbref_team_id').size().max()}")
print(f"  longest: " + ", ".join(f"{r.manager_name} ({r.club}, {r.matches} matches)"
                                 for r in t.nlargest(3, 'matches').itertuples()))
if overlaps:
    print(f"\n{len(overlaps)} name(s) appear at two clubs at the same time -- two people sharing a name:")
    for name, c1, s1, e1, c2, s2, e2 in overlaps:
        print(f"  '{name}': {c1} {s1}..{e1} AND {c2} {s2}..{e2}")
if spelling_pairs:
    print(f"\n{len(spelling_pairs)} possible spelling variant pair(s) of one person:")
    for a, b in spelling_pairs:
        print(f"  '{a}' vs '{b}'")
print("\nVALIDATION: " + ("match counts, club coverage and known tenures all check out" if not problems else "FAILED"))
for p in problems:
    print("  - " + p)
sys.exit(1 if problems else 0)
