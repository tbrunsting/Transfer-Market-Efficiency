r"""
Phase 1, step 2b: fill the snapshot's blank player-seasons from Kaggle.

WHY
---
A handful of real player-seasons in the worldfootballR snapshot have every
advanced column blank while their minutes and goals are present (e.g. Lee
Kang-in, Mallorca 2022/23, 2,823 minutes). Decision (Tyler, 2026-09-11): fill
them from the Kaggle "FBref 2017-2024" copy, and document whatever it can't fill.

HOW, AND WHY IT'S SAFE
  1. Each Kaggle column is paired with its FBref counterpart. A pair is only used
     if the two agree for at least 97% of the players where both have a value
     (rule 4 of docs/phase-1-data-coverage.md). The data decides; nothing is
     assumed to mean the same thing because of its name.
  2. Blank rows are found in the snapshot and linked to Kaggle through the stored
     crosswalk (reference/kaggle_fbref_crosswalk.csv, built by script 12).
  3. Proof: clubs' player sums are compared with the snapshot's team files before
     and after filling. A correct fill closes the gaps the blank rows caused.

OUTPUT (tracked in git, identical on every re-run of unchanged data)
  reference/fbref_blank_fill.csv         one row per filled value, in long form
  reference/fbref_blank_fill_report.md   column agreement, what was filled, proof, what's left

The fill is a correction table, not an edited copy of the data: the frozen snapshot
is never modified, and the warehouse applies these values on load.

Run:  .venv\Scripts\python scripts\13_fill_blank_players_from_kaggle.py
"""

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
CSV_DIR = REPO_ROOT / "data" / "raw" / "fbref" / "csv" / "fb_big5_advanced_season_stats"
KAGGLE_DIR = REPO_ROOT / "data" / "raw" / "fbref" / "kaggle" / "fbref-2017-2024-v2"
CROSSWALK = REPO_ROOT / "reference" / "kaggle_fbref_crosswalk.csv"
OUT = REPO_ROOT / "reference" / "fbref_blank_fill.csv"
REPORT = REPO_ROOT / "reference" / "fbref_blank_fill_report.md"

WINDOW = range(2018, 2025)
MIN_AGREEMENT_PCT = 97.0
REPORT_MINUTES = 450  # the player-seasons Tyler's decision refers to; smaller blank rows are filled too

# Kaggle column -> (FBref file, FBref column). Candidates only; step 1 accepts or rejects each one.
CANDIDATES = [
    ("Expected Goals", "big5_player_standard", "xG_Expected"),
    ("Exp NPG", "big5_player_standard", "npxG_Expected"),
    ("Progressive Passes", "big5_player_standard", "PrgP_Progression"),
    ("Progressive Carries", "big5_player_standard", "PrgC_Progression"),
    ("Expected Goals", "big5_player_shooting", "xG_Expected"),
    ("Exp NPG", "big5_player_shooting", "npxG_Expected"),
    ("Total Shots", "big5_player_shooting", "Sh_Standard"),
    ("% Shots on target", "big5_player_shooting", "SoT_percent_Standard"),
    ("Goals per shot", "big5_player_shooting", "G_per_Sh_Standard"),
    ("Goals per shot on target", "big5_player_shooting", "G_per_SoT_Standard"),
    ("Tackles attempted", "big5_player_defense", "Tkl_Tackles"),
    ("Tackles Won", "big5_player_defense", "TklW_Tackles"),
    ("% Dribbles tackled", "big5_player_defense", "Tkl_percent_Challenges"),
    ("Shots blocked", "big5_player_defense", "Sh_Blocks"),
    ("Passes blocked", "big5_player_defense", "Pass_Blocks"),
    ("Interceptions", "big5_player_defense", "Int"),
    ("Clearances", "big5_player_defense", "Clr"),
    ("Errors made", "big5_player_defense", "Err"),
    ("touches_def_pen", "big5_player_possession", "Def Pen_Touches"),
    ("Take ons attempted", "big5_player_possession", "Att_Take"),
    ("% Successful take-ons", "big5_player_possession", "Succ_percent_Take"),
    ("Times tackled during take-on", "big5_player_possession", "Tkld_Take"),
    ("carries_prgc", "big5_player_possession", "PrgC_Carries"),
    ("carries final 3rd", "big5_player_possession", "Final_Third_Carries"),
    ("carries penalty area", "big5_player_possession", "CPA_Carries"),
    ("Possessions lost", "big5_player_possession", "Dis_Carries"),
    ("Possessions lost", "big5_player_possession", "Mis_Carries"),
    ("Passes Completed", "big5_player_passing", "Cmp_Total"),
    ("Passes Attempted", "big5_player_passing", "Att_Total"),
    ("Pass completion %", "big5_player_passing", "Cmp_percent_Total"),
    ("Progressive passes distance", "big5_player_passing", "PrgDist_Total"),
    ("% Short pass completed", "big5_player_passing", "Cmp_percent_Short"),
    ("% Medium passes completed", "big5_player_passing", "Cmp_percent_Medium"),
    ("% Long passes completed", "big5_player_passing", "Cmp_percent_Long"),
    ("Key passes", "big5_player_passing", "KP"),
    ("1/3", "big5_player_passing", "Final_Third"),
    ("Passes into penalty area", "big5_player_passing", "PPA"),
    ("% Aerial Duels won", "big5_player_misc", "Won_percent_Aerial"),
    ("Goals Against", "big5_player_keepers", "GA"),
    ("Saves", "big5_player_keepers", "Saves"),
    ("Saves %", "big5_player_keepers", "Save_percent"),
    ("Clean Sheets", "big5_player_keepers", "CS"),
    ("% Clean sheets", "big5_player_keepers", "CS_percent"),
    ("Crosses Stopped", "big5_player_keepers_adv", "Stp_Crosses"),
]
# Columns the scoring leans on that have no Kaggle counterpart at all (checked by name in the Kaggle schema).
NO_KAGGLE_EQUIVALENT = [("big5_player_standard", "xAG_Expected"), ("big5_player_misc", "Won_Aerial"),
                        ("big5_player_possession", "Touches_Touches"), ("big5_player_keepers_adv", "PSxG_Expected")]


def label(y):
    return f"{y - 1}/{y % 100:02d}"


def pid(urls):
    return urls.str.extract(r"/players/([0-9a-f]{8})/")[0]


files = sorted({f for _, f, _ in CANDIDATES} | {f for f, _ in NO_KAGGLE_EQUIVALENT})
F = {}
for f in files:
    d = pd.read_csv(CSV_DIR / f"{f}.csv", low_memory=False)
    d = d[d.Season_End_Year.isin(WINDOW)].copy()
    d["player_id"] = pid(d.Url)
    F[f] = d
K = pd.concat([pd.read_csv(p, encoding="utf-8") for p in sorted(KAGGLE_DIR.glob("cleaned_*.csv"))], ignore_index=True)
K["Season_End_Year"] = K.season.str[:4].astype(int) + 1
cw = pd.read_csv(CROSSWALK)
# crosswalk + Kaggle values, keyed the way FBref rows are: (season, FBref player ID, FBref club name)
KX = cw.merge(K, left_on=["season_end_year", "kaggle_player", "kaggle_squad", "kaggle_born"],
              right_on=["Season_End_Year", "player", "squad", "born"], how="inner")
KX = KX.rename(columns={"fbref_player_id": "player_id", "fbref_squad": "Squad"})
KEY = ["Season_End_Year", "player_id", "Squad"]
assert not KX.duplicated(KEY).any(), "crosswalk links one FBref row to two Kaggle rows"

# ---------------------------------------------------------------- 1. which pairs agree?
print("1. Kaggle vs FBref, column by column (players where both have a value)")
agree = []
for kcol, f, fcol in CANDIDATES:
    # Kaggle side renamed to kval first: some columns share a name on both sides ("Saves")
    m = F[f][KEY + [fcol]].merge(KX[KEY + [kcol, "match_method"]].rename(columns={kcol: "kval"}), on=KEY)
    m = m[(m.match_method != "blank-row match") & m[fcol].notna() & m.kval.notna()]
    whole = (m[fcol] % 1 == 0).all() and (m.kval % 1 == 0).all()
    tol = 1e-9 if whole else 0.051
    pct = 100 * ((m[fcol] - m.kval).abs() <= tol).mean() if len(m) else 0.0
    agree.append({"kaggle": kcol, "file": f, "fbref": fcol, "n": len(m), "pct": pct, "used": pct >= MIN_AGREEMENT_PCT})
    print(f"  {'USE ' if pct >= MIN_AGREEMENT_PCT else 'skip'} {kcol:<30} -> {f.replace('big5_player_', '')}.{fcol:<22} "
          f"{pct:6.2f}% of {len(m):,}")
A = pd.DataFrame(agree)
used = A[A.used]

# ---------------------------------------------------------------- 2. fill the blank rows
print("\n2. Blank rows filled")
std = F["big5_player_standard"]
blank = std[std.PrgP_Progression.isna() & (std.Min_Playing > 0)][KEY + ["Player", "Min_Playing"]]
rows = []
for _, u in used.iterrows():
    target = F[u.file][KEY + [u.fbref]]
    b = blank.merge(target, on=KEY)
    b = b[b[u.fbref].isna()].merge(KX[KEY + [u.kaggle]].rename(columns={u.kaggle: "kval"}), on=KEY)
    b = b[b.kval.notna()]
    for r in b.itertuples(index=False):
        rows.append({"season_end_year": r.Season_End_Year, "fbref_player_id": r.player_id, "fbref_squad": r.Squad,
                     "player": r.Player, "minutes": int(r.Min_Playing), "file": u.file, "column": u.fbref,
                     "value": r.kval, "kaggle_column": u.kaggle})
fill = pd.DataFrame(rows).sort_values(["season_end_year", "fbref_squad", "fbref_player_id", "file", "column"])
fill.to_csv(OUT, index=False, encoding="utf-8", lineterminator="\n")
done = fill.groupby(["season_end_year", "fbref_player_id", "fbref_squad"]).size()
big = blank[blank.Min_Playing >= REPORT_MINUTES]
big_done = big.merge(fill[["season_end_year", "fbref_player_id", "fbref_squad"]].drop_duplicates(),
                     left_on=KEY, right_on=["season_end_year", "fbref_player_id", "fbref_squad"], how="left")
not_filled = big_done[big_done.season_end_year.isna()]
print(f"  blank rows with minutes: {len(blank)} | filled: {len(done)} | values written: {len(fill)}")
print(f"  of the {len(big)} with {REPORT_MINUTES}+ minutes: filled {len(big) - len(not_filled)}, not filled {len(not_filled)}")


# ---------------------------------------------------------------- 3. proof: team = sum of players, before and after
def apply(f, col):
    d = F[f].copy()
    add = fill[(fill.file == f) & (fill.column == col)].set_index(["season_end_year", "fbref_player_id", "fbref_squad"]).value
    idx = list(zip(d.Season_End_Year, d.player_id, d.Squad))
    d[col] = [v if pd.notna(v) else add.get(i, v) for v, i in zip(d[col], idx)]
    return d


TEAM_CHECKS = [("big5_player_standard", "PrgP_Progression", "big5_team_standard", "PrgP_Progression"),
               ("big5_player_standard", "PrgC_Progression", "big5_team_standard", "PrgC_Progression"),
               ("big5_player_possession", "PrgC_Carries", "big5_team_possession", "PrgC_Carries"),
               ("big5_player_defense", "Int", "big5_team_defense", "Int"),
               ("big5_player_defense", "Clr", "big5_team_defense", "Clr"),
               ("big5_player_defense", "Tkl_Tackles", "big5_team_defense", "Tkl_Tackles")]
print("\n3. Proof: clubs whose player sum equals the team file, before -> after the fill")
proof = []
for pf, pcol, tf, tcol in TEAM_CHECKS:
    t = pd.read_csv(CSV_DIR / f"{tf}.csv", low_memory=False)
    t = t[(t.Team_or_Opponent == "team") & t.Season_End_Year.isin(WINDOW)].set_index(["Season_End_Year", "Squad"])[tcol]
    res = []
    for d in (F[pf], apply(pf, pcol)):
        s = d.groupby(["Season_End_Year", "Squad"])[pcol].sum()
        j = pd.concat([s, t], axis=1, join="inner")
        res.append(int(((j.iloc[:, 0] - j.iloc[:, 1]).abs() < 1e-6).sum()))
    proof.append((f"{pf.replace('big5_player_', '')}.{pcol}", res[0], res[1], len(t)))
    print(f"  {proof[-1][0]:<32} {res[0]} -> {res[1]} of {len(t)} team-seasons")

# ---------------------------------------------------------------- report
gaps = [f"{f.replace('big5_player_', '')}.{c}" for f, c in NO_KAGGLE_EQUIVALENT] + \
       [f"{r.file.replace('big5_player_', '')}.{r.fbref} ({r.pct:.1f}% agreement)" for r in A[~A.used].itertuples()]
lines = ["# Blank player-seasons filled from Kaggle", "",
         "Written by `scripts/13_fill_blank_players_from_kaggle.py`. The frozen snapshot is never modified; these values",
         "are a correction table applied on load (`reference/fbref_blank_fill.csv`).", "",
         f"Blank rows with minutes in 2017/18-2023/24: {len(blank)}. Filled: {len(done)}. Values written: {len(fill)}.",
         f"Of the {len(big)} player-seasons with {REPORT_MINUTES}+ minutes: {len(big) - len(not_filled)} filled, "
         f"{len(not_filled)} not filled.", "",
         "## Proof: clubs whose player sum equals the team file", "", "| Metric | Before | After | Team-seasons |", "|---|---|---|---|"]
lines += [f"| {m} | {a} | {b} | {n} |" for m, a, b, n in proof]
lines += ["", f"## Column pairs (used if agreement >= {MIN_AGREEMENT_PCT:.0f}%)", "",
          "| Kaggle column | FBref file.column | Agreement | Players compared | Used |", "|---|---|---|---|---|"]
lines += [f"| {r.kaggle} | {r.file.replace('big5_player_', '')}.{r.fbref} | {r.pct:.2f}% | {r.n:,} | {'yes' if r.used else 'no'} |"
          for r in A.itertuples()]
lines += ["", f"## Player-seasons with {REPORT_MINUTES}+ minutes", "", "| Season | Club | Player | Minutes | Filled values |",
          "|---|---|---|---|---|"]
for r in big.sort_values("Min_Playing", ascending=False).itertuples():
    n = len(fill[(fill.season_end_year == r.Season_End_Year) & (fill.fbref_player_id == r.player_id) & (fill.fbref_squad == r.Squad)])
    lines.append(f"| {label(r.Season_End_Year)} | {r.Squad} | {r.Player} | {int(r.Min_Playing):,} | {n} |")
lines += ["", "## Still blank for these rows (known gap)", "",
          "Columns with no Kaggle counterpart, or whose counterpart failed the agreement test, stay blank for filled rows:", ""]
lines += [f"- {g}" for g in gaps]
REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
print(f"\n-> {OUT.relative_to(REPO_ROOT).as_posix()}, {REPORT.relative_to(REPO_ROOT).as_posix()}")
sys.exit(1 if len(not_filled) else 0)
