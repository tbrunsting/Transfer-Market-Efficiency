r"""
Phase 1, step 2: consistency checks on the frozen FBref data, as code.

WHY
---
docs/phase-1-data-coverage.md sets rules for using the FBref data (a metric is
only used if complete for every scored season; progressive passes come from the
standard file; team figures are player sums; joins use FBref IDs; a second
source must match the snapshot where they overlap). Those rules were first
checked by hand. This script re-checks them every time, so a mistake or a
changed file can't slip through silently.

WHAT IT DOES
  0. Re-hashes every file it reads and compares with the manifest: the checks
     only mean something if they run on the frozen data.
  1. Completeness (rule 1) for every stat type the scoring uses, 2017/18-2023/24.
  2. Progressive passes (rule 2): standard file = team files; the passing file's
     older version before 2022/23 is reported as a known issue.
  3. Team = sum of players (rule 3) for counting stats. xG is a known exception:
     FBref's team xG runs about 2% below the sum of its own player xG.
  4. Kaggle (rule 4): builds and STORES the Kaggle-to-FBref crosswalk, then checks
     the overlap and that Kaggle's SCA reproduces the snapshot's team totals.
  5. IDs (rule 5): every player row resolves to an FBref team ID; 145 clubs.

OUTPUT (both tracked in git, both identical on every re-run of unchanged data)
  reference/fbref_consistency_report.md
  reference/kaggle_fbref_crosswalk.csv
Exit code 1 if any check fails.

Run:  .venv\Scripts\python scripts\12_check_fbref_consistency.py
"""

import csv
import hashlib
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW = REPO_ROOT / "data" / "raw" / "fbref"
CSV_DIR = RAW / "csv" / "fb_big5_advanced_season_stats"
KAGGLE_DIR = RAW / "kaggle" / "fbref-2017-2024-v2"
MANIFEST = REPO_ROOT / "reference" / "fbref_snapshot_manifest.csv"
REPORT = REPO_ROOT / "reference" / "fbref_consistency_report.md"
CROSSWALK = REPO_ROOT / "reference" / "kaggle_fbref_crosswalk.csv"

WINDOW = range(2018, 2025)  # FBref's Season_End_Year: 2018 = 2017/18 ... 2024 = 2023/24
CLUBS_PER_SEASON = {y: (96 if y == 2024 else 98) for y in WINDOW}  # Ligue 1 went to 18 clubs in 2023/24
EXPECTED_CLUBS = 145

# Thresholds, each set from the measured data (2026-09-11), with some headroom.
MIN_FULL_SEASON_90S = 30   # a complete season shows 34-38; a partial one shows 5-9
MIN_FILLED_PCT = 99.0      # lowest key-column fill seen in the window is 99.5%
MIN_FILLED_PCT_GK_ADV = 98.0  # PSxG is 98.6% filled in 2022/23
MIN_EXACT_TEAMS_PCT = 97.0  # counting stats: 98-100% of teams match player sums exactly
XG_GAP_RANGE = (1.0, 3.0)   # known: player xG sums are ~2% above FBref team xG, every season
MIN_MINUTES_MATCHED_PCT = 99.0  # Kaggle crosswalk matched 99.5-99.9% of minutes
MIN_SAME_VALUE_PCT = 97.0       # Kaggle vs snapshot, lowest seen: 97.6% (xG, 2022/23)
SCA_TEAM_TOLERANCE = 0.01       # Kaggle SCA rebuilt to club totals vs team GCA file
MIN_SCA_TEAMS_WITHIN_PCT = 90.0
MAX_SCA_MEAN_GAP_PCT = 1.0
BLANK_MINUTES = 450  # 5 full matches: enough that a blank row is a real player-season, not noise
# FBref IDs known to stand for more than one person (documented in docs/phase-1-data-coverage.md).
# Any NEW impossible season total fails the check.
KNOWN_MERGED_IDS = {("4acd733a", 2023): "Valery Fernandez (Girona) and Yan Valery (Southampton, Angers)"}
MAX_LEAGUE_MATCHES = 38

results = []


def record(rule, check, ok, detail, info=False):
    results.append({"rule": rule, "check": check, "status": "INFO" if info else ("PASS" if ok else "FAIL"), "detail": detail})
    print(f"  [{results[-1]['status']}] {check}: {detail}")


def words(x):
    """Lower-case ASCII words of a name, for a loose same-person check."""
    return set(re.findall(r"[a-z]+", unicodedata.normalize("NFKD", str(x)).encode("ascii", "ignore").decode().lower()))


def label(y):
    return f"{y - 1}/{y % 100:02d}"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read(name, team=False):
    d = pd.read_csv(CSV_DIR / f"{name}.csv", low_memory=False)
    d = d[d.Season_End_Year.isin(WINDOW)]
    return d[d.Team_or_Opponent == "team"].copy() if team else d.copy()


# ------------------------------------------------------------------ 0. frozen data?
print("0. Files match the manifest")
with open(MANIFEST, newline="", encoding="utf-8") as fh:
    manifest = {r["path"]: r for r in csv.DictReader(fh)}
checked, bad = 0, []
for key, row in manifest.items():
    if row["role"] in ("derived", "extracted") and (key.startswith(CSV_DIR.relative_to(REPO_ROOT).as_posix())
                                                    or key.startswith(KAGGLE_DIR.relative_to(REPO_ROOT).as_posix())):
        checked += 1
        if sha256(REPO_ROOT / key) != row["sha256"]:
            bad.append(key)
record("frozen data", "checksums of every file read", not bad and checked > 0,
       f"{checked} files hashed, {len(bad)} mismatched" + (f": {bad}" if bad else ""))
if bad:
    sys.exit("STOP: the data on disk isn't the frozen snapshot. Re-run 10_freeze_fbref_snapshot.py first.")

# ------------------------------------------------------------------ 1. completeness
print("\n1. Completeness in every scored season (rule 1)")
PLAYER_FILES = {  # file: (90s column, key columns, minimum filled %)
    "big5_player_standard": ("Mins_Per_90_Playing", ["xG_Expected", "xAG_Expected", "PrgP_Progression", "PrgC_Progression"], MIN_FILLED_PCT),
    "big5_player_shooting": ("Mins_Per_90", ["xG_Expected", "npxG_Expected"], MIN_FILLED_PCT),
    "big5_player_passing": ("Mins_Per_90", ["Cmp_Total", "KP"], MIN_FILLED_PCT),
    "big5_player_playing_time": ("Mins_Per_90_Playing.Time", [], None),  # unused substitutes have blank minutes
    "big5_player_defense": ("Mins_Per_90", ["Tkl+Int", "Int", "Clr"], MIN_FILLED_PCT),
    "big5_player_possession": ("Mins_Per_90", ["PrgC_Carries", "Touches_Touches", "Succ_Take"], MIN_FILLED_PCT),
    "big5_player_misc": ("Mins_Per_90", ["Won_Aerial"], MIN_FILLED_PCT),
    "big5_player_keepers": ("Mins_Per_90", ["Saves"], MIN_FILLED_PCT),
    "big5_player_keepers_adv": ("Mins_Per_90", ["PSxG_Expected"], MIN_FILLED_PCT_GK_ADV),
}
P = {name: read(name) for name in PLAYER_FILES}
for name, (col90, keys, need) in PLAYER_FILES.items():
    d, problems, worst_fill, worst_90s = P[name], [], 100.0, 99.0
    for y in WINDOW:
        s = d[d.Season_End_Year == y]
        top = s[col90].max() if len(s) else 0
        fill = min([100 * s[c].notna().mean() for c in keys] or [100.0]) if len(s) else 0
        worst_fill, worst_90s = min(worst_fill, fill), min(worst_90s, top)
        if len(s) == 0 or top < MIN_FULL_SEASON_90S or (need and fill < need):
            problems.append(f"{label(y)} (max 90s {top:.1f}, fill {fill:.1f}%)")
    record("1", name, not problems, f"lowest season max-90s {worst_90s:.1f}, lowest key-column fill {worst_fill:.1f}%"
           + (f"; FAILING: {problems}" if problems else ""))

gap = P["big5_player_standard"]
gap = gap[gap.PrgP_Progression.isna() & (gap.Min_Playing >= BLANK_MINUTES)].sort_values("Min_Playing", ascending=False)
record("1", f"players with {BLANK_MINUTES}+ minutes but blank advanced stats (known gap)", True,
       f"{len(gap)} player-seasons, {int(gap.Min_Playing.sum()):,} minutes: "
       + "; ".join(f"{label(y)} {sq} {pl} ({int(m)} min)" for y, sq, pl, m in
                   zip(gap.Season_End_Year, gap.Squad, gap.Player, gap.Min_Playing))
       + ". Blank in every advanced file; filled from Kaggle where columns validate (13_fill_blank_players_from_kaggle.py)", info=True)

TEAM_FILES = ["big5_team_standard", "big5_team_passing", "big5_team_gca", "big5_team_defense", "big5_team_possession"]
T = {name: read(name, team=True) for name in TEAM_FILES}
for name in TEAM_FILES:
    counts = T[name].groupby("Season_End_Year").size()
    wrong = {label(y): int(counts.get(y, 0)) for y in WINDOW if counts.get(y, 0) != CLUBS_PER_SEASON[y]}
    record("1", name, not wrong, "one row per club in every season" if not wrong else f"wrong club counts: {wrong}")
mp = T["big5_team_standard"].groupby("Season_End_Year").MP_Playing.max()
record("1", "team standard: full seasons", all(mp.get(y, 0) >= 34 for y in WINDOW),
       ", ".join(f"{label(y)}: {int(mp.get(y, 0))}" for y in WINDOW) + " matches (max per club)")

K = pd.concat([pd.read_csv(f, encoding="utf-8") for f in sorted(KAGGLE_DIR.glob("cleaned_*.csv"))], ignore_index=True)
K["Season_End_Year"] = K.season.str[:4].astype(int) + 1
kfill = K.groupby("Season_End_Year")["Shot creating actions p 90"].apply(lambda s: 100 * s.notna().mean())
record("1", "Kaggle SCA per 90", set(kfill.index) == set(WINDOW) and kfill.min() >= MIN_FILLED_PCT,
       f"{K.Season_End_Year.nunique()} seasons, lowest fill {kfill.min():.1f}%")

# ------------------------------------------------------------------ 2 & 3. player sums vs team files
print("\n2-3. Team figures vs player sums (rules 2 and 3)")


def exact_share(player_df, pcol, team_df, tcol, y):
    """% of teams whose player sum equals the team figure. Teams with a blank player value are left out:
    those are a separate, known problem (see the blank-players check), and a blank sums as zero."""
    p = player_df[player_df.Season_End_Year == y]
    blank_teams = set(p.loc[p[pcol].isna(), "Squad"])
    a = p[~p.Squad.isin(blank_teams)].groupby("Squad")[pcol].sum()
    b = team_df[team_df.Season_End_Year == y].set_index("Squad")[tcol]
    both = pd.concat([a, b], axis=1, join="inner")
    return 100 * ((both.iloc[:, 0] - both.iloc[:, 1]).abs() < 1e-6).mean(), len(blank_teams)


def exact_detail(shares):
    return (f"teams matching exactly, lowest season {min(v[0] for v in shares.values()):.0f}% "
            f"(teams with a blank player value left out: {sum(v[1] for v in shares.values())} team-seasons)")


std = P["big5_player_standard"]
for tname in ("big5_team_passing", "big5_team_standard"):
    tcol = "PrgP" if tname == "big5_team_passing" else "PrgP_Progression"
    shares = {y: exact_share(std, "PrgP_Progression", T[tname], tcol, y) for y in WINDOW}
    record("2", f"progressive passes: standard file vs {tname}", min(v[0] for v in shares.values()) >= MIN_EXACT_TEAMS_PCT,
           exact_detail(shares))
pas = P["big5_player_passing"]
old_version = [label(y) for y in WINDOW
               if exact_share(pas, "Prog" if y < 2023 else "PrgP", T["big5_team_passing"], "PrgP", y)[0] < MIN_EXACT_TEAMS_PCT]
record("2", "passing file progressive passes (known issue)", True,
       f"older data version in {', '.join(old_version) or 'no seasons'}; never use it for progressive passes", info=True)

COUNTING = [("goals", std, "Gls", "big5_team_standard", "Gls"), ("penalties attempted", std, "PKatt", "big5_team_standard", "PKatt"),
            ("progressive carries", P["big5_player_possession"], "PrgC_Carries", "big5_team_possession", "PrgC_Carries"),
            ("tackles + interceptions", P["big5_player_defense"], "Tkl+Int", "big5_team_defense", "Tkl+Int")]
for label_, pdf, pcol, tname, tcol in COUNTING:
    shares = {y: exact_share(pdf, pcol, T[tname], tcol, y) for y in WINDOW}
    record("3", f"{label_}: team = sum of players", min(v[0] for v in shares.values()) >= MIN_EXACT_TEAMS_PCT,
           exact_detail(shares))
gaps = {}
for y in WINDOW:
    a = std[std.Season_End_Year == y].xG_Expected.sum()
    b = T["big5_team_standard"][T["big5_team_standard"].Season_End_Year == y].xG_Expected.sum()
    gaps[y] = 100 * (a / b - 1)
in_range = all(XG_GAP_RANGE[0] <= g <= XG_GAP_RANGE[1] for g in gaps.values())
record("3", "xG: player sum vs FBref team xG (known discrepancy)", in_range,
       f"player sums above team figures by {min(gaps.values()):+.2f}% to {max(gaps.values()):+.2f}% per season. "
       "Team xG is built from player sums; never mix with FBref's team xG")

# ------------------------------------------------------------------ 5. IDs
print("\n5. FBref IDs (rule 5)")
ts = T["big5_team_standard"].copy()
ts["team_id"] = ts.Url.str.extract(r"/squads/([0-9a-f]{8})/")[0]
team_key = ts.set_index(["Season_End_Year", "Squad"]).team_id
unique_key = not ts.duplicated(["Season_End_Year", "Squad"]).any()
std = std.copy()
std["team_id"] = [team_key.get((y, s)) for y, s in zip(std.Season_End_Year, std.Squad)]
std["player_id"] = std.Url.str.extract(r"/players/([0-9a-f]{8})/")[0]
resolved = 100 * std.team_id.notna().mean()
record("5", "player rows resolve to a team ID via (season, club name)", unique_key and resolved == 100.0,
       f"{std.team_id.notna().sum():,} of {len(std):,} ({resolved:.2f}%); (season, club name) unique: {unique_key}")
n_ids = ts.team_id.nunique()
record("5", "distinct clubs in the window", n_ids == EXPECTED_CLUBS, f"{n_ids} team IDs (expected {EXPECTED_CLUBS})")
tot = std.groupby(["player_id", "Season_End_Year"]).agg(mp=("MP_Playing", "sum"), mins=("Min_Playing", "sum"),
                                                        where=("Squad", lambda x: "/".join(x)))
merged = tot[(tot.mp > MAX_LEAGUE_MATCHES) | (tot.mins > MAX_LEAGUE_MATCHES * 90)]
new = [k for k in merged.index if k not in KNOWN_MERGED_IDS]
record("5", "player IDs standing for more than one person", not new,
       "; ".join(f"{pid} in {label(y)}: {int(merged.loc[(pid, y), 'mp'])} league matches at {merged.loc[(pid, y), 'where']}"
                 + (f" (known: {KNOWN_MERGED_IDS[(pid, y)]})" if (pid, y) in KNOWN_MERGED_IDS else " (NEW)")
                 for pid, y in merged.index) or "none")
renamed = ts.groupby("team_id").Squad.unique()
renamed = {tid: " -> ".join(names) for tid, names in renamed.items() if len(names) > 1}
record("5", "clubs whose name changes inside the snapshot", True,
       "; ".join(f"{tid}: {v}" for tid, v in renamed.items()) or "none", info=True)

# ------------------------------------------------------------------ 4. Kaggle
print("\n4. Kaggle vs snapshot, and the crosswalk (rule 4)")
dfn = P["big5_player_defense"][["Season_End_Year", "Url", "Squad", "Int"]]
S = std.merge(dfn, on=["Season_End_Year", "Url", "Squad"], how="left")


def num(x):
    """Whole numbers as '38', never '38.0': pandas reads integer columns with blanks as floats."""
    if pd.isna(x):
        return ""
    return str(int(x)) if float(x).is_integer() else str(x)


born = num


def fingerprint(b, mp, g, a, xg, prgp, intc):
    return "|".join([num(b), num(mp), num(g), num(a), "" if pd.isna(xg) else f"{xg:.1f}", num(prgp), num(intc)])


S["fp"] = [fingerprint(*v) for v in zip(S.Born, S.MP_Playing, S.Gls, S.Ast, S.xG_Expected, S.PrgP_Progression, S.Int)]
S["name_key"] = [f"{s}|{p}|{born(b)}" for s, p, b in zip(S.Squad, S.Player, S.Born)]
S["nb"] = [f"{p}|{born(b)}" for p, b in zip(S.Player, S.Born)]
K["kid"] = range(len(K))
K["fp"] = [fingerprint(*v) for v in zip(K.born, K["Matches Played"], K.Goals, K.Assists, K["Expected Goals"],
                                         K["Progressive Passes"], K.Interceptions)]
K["nb"] = [f"{p}|{born(b)}" for p, b in zip(K.player, K.born)]
MIN_PLAYERS_PER_CLUB_PAIR = 3  # a club-name pair needs 3+ distinct players agreeing, so one coincidence can't create it

links, renamed_pairs = [], set()
for y in WINDOW:
    s, k = S[S.Season_End_Year == y].copy(), K[K.Season_End_Year == y].copy()
    # 1. Which Kaggle club name is which FBref club name? Learned from players whose name + birth year is unique
    #    on both sides, whatever the club (e.g. Kaggle "Gladbach" = FBref "M'Gladbach").
    j = (s[~s.nb.duplicated(keep=False)][["nb", "Squad"]]
         .merge(k[~k.nb.duplicated(keep=False)][["nb", "squad"]], on="nb"))
    votes = j.groupby(["squad", "Squad"]).size()
    votes = votes[votes >= MIN_PLAYERS_PER_CLUB_PAIR].sort_values(ascending=False)
    club_map = {}
    for (ks, fs), _ in votes.items():
        club_map.setdefault(ks, fs)  # strongest pair wins; each Kaggle club maps to one FBref club
    renamed_pairs |= {(ks, fs) for ks, fs in club_map.items() if ks != fs}
    k["fb_squad"] = k.squad.map(club_map)
    # 2. Exact name + birth year, inside the matched club.
    kkey = dict(zip([f"{c}|{nb}" for c, nb in zip(k.fb_squad, k.nb)], k.kid))
    s["kid"] = pd.Series([kkey.get(f"{c}|{nb}", float("nan")) for c, nb in zip(s.Squad, s.nb)], index=s.index, dtype="float64")
    s["method"] = ["name" if pd.notna(x) else "" for x in s.kid]
    # 3. Statistical fingerprint for the rest, only inside the matched club (without this, one false link
    #    crossed clubs: Getafe -> Granada), and only where the fingerprint is unique on both sides.
    free_k = k[~k.kid.isin(s.kid.dropna()) & ~k.fp.duplicated(keep=False)]
    fkey = dict(zip([f"{c}|{fp}" for c, fp in zip(free_k.fb_squad, free_k.fp)], free_k.kid))
    todo = s.kid.isna() & ~s.fp.duplicated(keep=False)
    s.loc[todo, "kid"] = [fkey.get(f"{c}|{fp}", float("nan")) for c, fp in zip(s.loc[todo, "Squad"], s.loc[todo, "fp"])]
    s.loc[todo & s.kid.notna(), "method"] = "fingerprint"
    # 4. Rows whose advanced stats are blank can't carry a stats fingerprint. For those only, match on what they
    #    do have (club, birth year, matches, goals, assists), unique on both sides. These are the rows the Kaggle
    #    fill (13_fill_blank_players_from_kaggle.py) exists for, so each link is listed for review below.
    blank = s.kid.isna() & s.PrgP_Progression.isna() & (s.Min_Playing > 0)
    lite_s = [f"{c}|{num(b)}|{num(m)}|{num(g)}|{num(a)}" for c, b, m, g, a in zip(s.Squad, s.Born, s.MP_Playing, s.Gls, s.Ast)]
    s["lite"] = lite_s
    free_k = k[~k.kid.isin(s.kid.dropna())].copy()
    free_k["lite"] = [f"{c}|{num(b)}|{num(m)}|{num(g)}|{num(a)}" for c, b, m, g, a in
                      zip(free_k.fb_squad, free_k.born, free_k["Matches Played"], free_k.Goals, free_k.Assists)]
    free_k = free_k[~free_k.lite.duplicated(keep=False)]
    ok = blank & ~s.lite.duplicated(keep=False)
    s.loc[ok, "kid"] = [dict(zip(free_k.lite, free_k.kid)).get(x, float("nan")) for x in s.loc[ok, "lite"]]
    s.loc[ok & s.kid.notna(), "method"] = "blank-row match"
    links.append(s)
L = pd.concat(links)
L["kid"] = pd.to_numeric(L.kid)  # mixed ints/None would otherwise refuse to merge with K.kid
L = L.merge(K, on="kid", how="left", suffixes=("", "_k"))

for y in WINDOW:
    x = L[L.Season_End_Year == y]
    mins = 100 * x.loc[x.kid.notna(), "Min_Playing"].sum() / x.Min_Playing.sum()
    m = x[x.kid.notna()]
    v = m[m.method != "blank-row match"]  # blank-row links have no snapshot values to compare
    same_xg = 100 * ((v.xG_Expected - v["Expected Goals"]).abs() <= 0.051).mean()
    same_prgp = 100 * (v.PrgP_Progression == v["Progressive Passes"]).mean()
    same_int = 100 * (v.Int == v.Interceptions).dropna().mean() if v.Int.notna().any() else 0
    m2 = m.assign(sca=m["Shot creating actions p 90"] * m.Mins_Per_90_Playing).groupby("Squad").sca.sum()
    tg = T["big5_team_gca"][T["big5_team_gca"].Season_End_Year == y].set_index("Squad").SCA_SCA
    gap = ((m2 - tg) / tg).dropna()
    within = 100 * (gap.abs() <= SCA_TEAM_TOLERANCE).mean()
    ok = (mins >= MIN_MINUTES_MATCHED_PCT and min(same_xg, same_prgp, same_int) >= MIN_SAME_VALUE_PCT
          and within >= MIN_SCA_TEAMS_WITHIN_PCT and abs(100 * gap.mean()) <= MAX_SCA_MEAN_GAP_PCT)
    record("4", f"Kaggle {label(y)}", ok,
           f"minutes matched {mins:.2f}%; same xG/PrgP/Int {same_xg:.1f}/{same_prgp:.1f}/{same_int:.1f}%; "
           f"team SCA gap {100 * gap.mean():+.2f}%, clubs within 1% {within:.0f}%")

cw = L[L.kid.notna()][["Season_End_Year", "player", "squad", "born", "player_id", "Squad", "team_id", "method"]].copy()
cw.columns = ["season_end_year", "kaggle_player", "kaggle_squad", "kaggle_born", "fbref_player_id", "fbref_squad",
              "fbref_team_id", "match_method"]
cw = cw.sort_values(["season_end_year", "fbref_team_id", "fbref_player_id", "kaggle_player"])
CROSSWALK.parent.mkdir(parents=True, exist_ok=True)
cw.to_csv(CROSSWALK, index=False, encoding="utf-8", lineterminator="\n")
fbname = dict(zip(zip(L.Season_End_Year, L.player_id, L.Squad), L.Player))
fpl = cw[cw.match_method == "fingerprint"]
review = [(y, fbname.get((y, p, sq)), kp, sq) for y, p, sq, kp in
          zip(fpl.season_end_year, fpl.fbref_player_id, fpl.fbref_squad, fpl.kaggle_player)
          if not words(kp) & words(fbname.get((y, p, sq)))]
record("4", "club names that differ between Kaggle and FBref (learned, not assumed)", True,
       "; ".join(f"Kaggle '{a}' = FBref '{b}'" for a, b in sorted(renamed_pairs)) or "none", info=True)
record("4", "fingerprint links whose names share no word (review by eye)", True,
       f"{len(review)} of {len(fpl)}: " + "; ".join(f"{label(y)} {sq}: FBref '{a}' = Kaggle '{b}'" for y, a, b, sq in review),
       info=True)
br = cw[cw.match_method == "blank-row match"]
record("4", "links for rows with blank advanced stats (review by eye)", True,
       f"{len(br)}: " + "; ".join(f"{label(y)} {sq}: FBref '{fbname.get((y, p, sq))}' = Kaggle '{kp}'" for y, p, sq, kp in
                              zip(br.season_end_year, br.fbref_player_id, br.fbref_squad, br.kaggle_player)), info=True)
by = cw.match_method.value_counts()
record("4", "crosswalk stored", True, f"{len(cw):,} links ({by.get('name', 0):,} by name, {by.get('fingerprint', 0):,} by "
       f"fingerprint, {by.get('blank-row match', 0):,} blank-row) -> {CROSSWALK.relative_to(REPO_ROOT).as_posix()}", info=True)

# ------------------------------------------------------------------ report
fails = [r for r in results if r["status"] == "FAIL"]
lines = ["# FBref consistency report", "",
         "Written by `scripts/12_check_fbref_consistency.py`. Checks the rules in",
         "`docs/phase-1-data-coverage.md` against the frozen snapshot for the scored window",
         "(2017/18-2023/24). Re-running on unchanged data produces this file unchanged.", "",
         f"Manifest SHA-256: `{sha256(MANIFEST)}`", "",
         f"**Result: {'ALL CHECKS PASSED' if not fails else f'{len(fails)} CHECK(S) FAILED'}** "
         f"({sum(r['status'] == 'PASS' for r in results)} passed, {len(fails)} failed, "
         f"{sum(r['status'] == 'INFO' for r in results)} informational)", "",
         "| Rule | Check | Status | Detail |", "|---|---|---|---|"]
lines += [f"| {r['rule']} | {r['check']} | {r['status']} | {r['detail']} |" for r in results]
REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
print(f"\n{'ALL CHECKS PASSED' if not fails else f'{len(fails)} CHECK(S) FAILED'} -> {REPORT.relative_to(REPO_ROOT).as_posix()}")
sys.exit(1 if fails else 0)
