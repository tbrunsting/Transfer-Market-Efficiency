r"""
Phase 1, Step 3: Fetch exactly ONE page, slowly, and dump what came back.

Deliberately fetches one page per process run. The Cloudflare-triggered
teardown in seleniumbase (browser_launcher.py:516 -- a bare `with driver:`
whose __exit__ restarts the whole chromedriver service) means a long-lived
driver reused across several pages is the fragile case. One page per process
sidesteps it entirely: fresh driver, one request, exit.

RATE_LIMIT is raised well above soccerdata's 7s default. That default is what
actually triggered the failures -- Cloudflare rate-limits, seleniumbase takes
its evasion path, and the reconnect on that path is broken. Slowing down means
the evasion path never runs.

If the page is already cached this makes NO network request at all, so it is
safe to re-run to re-inspect a page you already have.

Usage:
  .venv\Scripts\python scripts\03_probe_one_page.py player standard
  .venv\Scripts\python scripts\03_probe_one_page.py team shooting

Args: <grain: team|player> <stat_type> [season, default 1718]
"""

import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from fbref_extended import FBrefExtended  # noqa: E402

RATE_LIMIT = 25  # seconds; soccerdata's default of 7 is what tripped Cloudflare

grain = sys.argv[1] if len(sys.argv) > 1 else "player"
stat_type = sys.argv[2] if len(sys.argv) > 2 else "standard"
season = sys.argv[3] if len(sys.argv) > 3 else "1718"

if grain not in ("team", "player"):
    sys.exit(f"grain must be 'team' or 'player', got {grain!r}")

fbref = FBrefExtended(leagues="Big 5 European Leagues Combined", seasons=season)
fbref.rate_limit = RATE_LIMIT

print(f"grain={grain}  stat_type={stat_type}  season={season}  rate_limit={fbref.rate_limit}s")
print(f"cache={fbref.data_dir}\n")

t0 = time.perf_counter()
if grain == "team":
    df = fbref.read_team_season_stats(stat_type=stat_type)
else:
    df = fbref.read_player_season_stats(stat_type=stat_type)
print(f"fetched/loaded in {time.perf_counter() - t0:.1f}s")
print(f"rows: {len(df)}\n")


def flatten(cols) -> list[str]:
    if isinstance(cols, pd.MultiIndex):
        return ["_".join(str(p) for p in tup if "Unnamed" not in str(p)).strip("_") for tup in cols]
    return [str(c) for c in cols]


flat = flatten(df.columns)
print(f"--- ALL {len(flat)} COLUMNS ---")
for i, c in enumerate(flat):
    print(f"  [{i:>2}] {c}")

# The columns that prove this page carries the advanced data we need.
# Each stat type has its own; checking xG on a GCA page would report a false failure.
MARKERS = {
    "standard": ["xg", "xag", "prgp"],
    "shooting": ["xg", "npxg"],
    "passing": ["prgp", "xag", "kp"],
    "playing_time": ["min", "mp"],
    "defense": ["tkl", "int", "clr"],
    "possession": ["prgc", "touches", "succ"],
    "misc": ["aerial", "won"],
    "gca": ["sca", "gca"],
    "keeper": ["saves", "save%"],
    "keeper_adv": ["psxg"],
}

print(f"\n--- KEY COLUMNS FOR '{stat_type}' (present AND populated?) ---")
all_ok = True
for marker in MARKERS.get(stat_type, []):
    hits = [(i, c) for i, c in enumerate(flat) if marker in c.lower()]
    if not hits:
        print(f"  [MISSING] no column matching {marker!r}")
        all_ok = False
        continue
    i, c = hits[0]
    s = pd.to_numeric(df.iloc[:, i], errors="coerce")
    nn = s.notna().sum()
    pct = nn / len(df) * 100 if len(df) else 0
    print(f"  [{'OK   ' if nn else 'EMPTY'}] {marker!r} -> {c}: {nn}/{len(df)} populated ({pct:.1f}%)")
    all_ok = all_ok and nn > 0

# Row counts hide partial seasons, so check how much of the season is in the table.
print("\n--- IS THIS A COMPLETE SEASON? ---")
nineties = [(i, c) for i, c in enumerate(flat) if c.endswith("90s")]
if nineties:
    i, c = nineties[0]
    top = pd.to_numeric(df.iloc[:, i], errors="coerce").max()
    print(f"  most 90s played by any player ({c}): {top}  (a complete season is about 34-38)")
else:
    print("  no 90s column in this table -- judge completeness from the row count")

print(f"\nVERDICT: key columns {'all present and populated' if all_ok else 'NOT all usable -- see above'}")

print("\n--- FIRST ROW ---")
print(df.head(1).T.to_string())
