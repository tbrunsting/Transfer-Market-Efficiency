r"""
Phase 1, Step 2: Are the advanced stats actually POPULATED for 2017/18?

This is the load-bearing check for the whole project. The scoping doc starts
the window at 2017/18 because that is when StatsBomb's advanced data begins.
If xG, progressive passing and defensive actions are not really there, the
position-adjusted scoring in section 4.4 cannot be built and the window moves.

WHAT THIS CHECKS, AND WHY IT IS NOT JUST A COLUMN LIST
------------------------------------------------------
FBref will happily render a column HEADER for a season it has no data for.
So "the column exists" proves nothing. For each stat type this script reports:

    - the column actually matched
    - how many of the ~98 clubs have a NON-NULL value
    - a real sample value

A column that is present but 0% populated is a FAIL, and that is exactly the
failure mode that would otherwise be discovered weeks later in the R scoring.

Uses "Big 5 European Leagues Combined" because that is the path the real
nine-season pull will use -- verifying the production code path, not a
lookalike. One request per stat type, so 5 requests at a 7s rate limit.

Run:  .venv\Scripts\python scripts\02_verify_advanced_stats_1718.py
"""

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from fbref_extended import FBrefExtended  # noqa: E402

SEASON = "1718"

# The columns that only exist if the advanced (StatsBomb-era) data is present.
# Keyed by stat type; each entry is a list of substrings to look for.
MARKERS = {
    "standard": ["xg", "npxg", "xag"],
    "possession": ["carries", "prgc", "touches", "take"],
    "defense": ["tkl", "int", "blocks"],
    "passing": ["prgp", "cmp%", "totdist"],
    "gca": ["sca", "gca"],
}


def flatten(cols) -> list[str]:
    """Turn FBref's two-level column headers into single searchable strings."""
    if isinstance(cols, pd.MultiIndex):
        return ["_".join(str(p) for p in tup if "Unnamed" not in str(p)).strip("_") for tup in cols]
    return [str(c) for c in cols]


def check(df: pd.DataFrame, stat_type: str) -> bool:
    flat = flatten(df.columns)
    print(f"  rows: {len(df)}   columns: {len(flat)}")
    print(f"  all columns: {flat}")

    ok = True
    for marker in MARKERS[stat_type]:
        hits = [(i, c) for i, c in enumerate(flat) if marker in c.lower()]
        if not hits:
            print(f"  [FAIL] no column matching {marker!r}")
            ok = False
            continue

        idx, colname = hits[0]
        series = df.iloc[:, idx]
        non_null = series.notna().sum()
        pct = (non_null / len(df) * 100) if len(df) else 0
        sample = series.dropna().iloc[0] if non_null else None

        if non_null == 0:
            print(f"  [FAIL] {marker!r} -> {colname!r}: column present but 0% populated")
            ok = False
        else:
            print(f"  [ OK ] {marker!r} -> {colname!r}: {non_null}/{len(df)} ({pct:.0f}%) e.g. {sample}")
    return ok


fbref = FBrefExtended(leagues="Big 5 European Leagues Combined", seasons=SEASON)
print(f"season {SEASON}  cache={fbref.data_dir}\n")

results = {}
for stat_type in MARKERS:
    print(f"=== {stat_type} ===")
    t0 = time.perf_counter()
    try:
        df = fbref.read_team_season_stats(stat_type=stat_type)
        print(f"  fetched in {time.perf_counter() - t0:.1f}s")
        results[stat_type] = check(df, stat_type)
        if stat_type == "possession":
            print("\n  --- one club, real values (sanity check) ---")
            print(df.head(1).T.head(25).to_string())
    except Exception as e:
        print(f"  [ERROR] {type(e).__name__}: {e}")
        results[stat_type] = False
    print()

print("=" * 60)
for stat_type, ok in results.items():
    print(f"  {stat_type:<12} {'PASS' if ok else 'FAIL'}")
verdict = all(results.values())
print("=" * 60)
print("2017/18 advanced stats usable:", "YES" if verdict else "NO -- window start needs review")
