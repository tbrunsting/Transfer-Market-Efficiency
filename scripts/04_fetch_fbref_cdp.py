r"""
Phase 1: fetch specific FBref gap pages WITHOUT chromedriver.

WHY THIS EXISTS
---------------
Every soccerdata run against FBref died in seleniumbase's reconnect step.
Reading seleniumbase's source showed why it can never be dodged:

  1. Before opening a URL, seleniumbase makes a plain `requests` call to it
     (browser_launcher.py, uc_special_open_if_cf). If that returns 4xx/5xx it
     takes a "special" path that stops and restarts chromedriver mid-session.
  2. FBref answers every plain HTTP client with 403, so that path runs on
     EVERY FBref page, not just unlucky ones.
  3. reconnect() wraps each restart step in suppress(Exception), then marks
     itself connected regardless. When the restart fails on this machine the
     error is thrown away, and the next command hits a dead port:
     "connection refused".

This script uses seleniumbase's Pure CDP mode (sb_cdp.Chrome) instead. It
drives a real Chrome over the DevTools protocol with no chromedriver process
at all, so there is nothing to restart and that code path never runs.

It only FETCHES: each page is saved into soccerdata's cache under the name
soccerdata expects, then parsed with FBrefCached (which never opens a
browser) and checked. A page that isn't a complete, populated season is
renamed *.rejected.html so it can't be used as data by accident.

Run by Tyler, not Claude (Chrome launched from Claude's sandbox dies).
Close your own Chrome first. If a Cloudflare "Verify you are human" box
appears, the script tries to click it; if it stays, click it yourself.

Usage (pages are stat_type:season):
  .venv\Scripts\python scripts\04_fetch_fbref_cdp.py gca:2324
  .venv\Scripts\python scripts\04_fetch_fbref_cdp.py gca:2223 gca:2425 defense:2425
  .venv\Scripts\python scripts\04_fetch_fbref_cdp.py --dry-run gca:2324
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fbref_extended import (  # noqa: E402
    PLAYER_STAT_TYPES,
    FBrefCached,
    check_player_table,
    player_cache_path,
    player_page_url,
)

SECONDS_BETWEEN_PAGES = 25  # FBref asks for no more than ~20 requests/minute
WAIT_FOR_TABLE = 120  # seconds to wait for the stats table, including any challenge
CLOUDFLARE_SIGNS = ("Just a moment", "Verify you are human", "challenge-platform", "cf-challenge")

args = sys.argv[1:]
dry_run = "--dry-run" in args
pages = [a.split(":") for a in args if ":" in a] or [["gca", "2324"]]
for stat_type, season in pages:
    if stat_type not in PLAYER_STAT_TYPES or len(season) != 4 or not season.isdigit():
        sys.exit(f"Bad page {stat_type}:{season}. Use stat_type:season, e.g. gca:2324")

data_dir = FBrefCached(leagues="Big 5 European Leagues Combined", seasons=pages[0][1]).data_dir


def stamp() -> str:
    return time.strftime("%H:%M:%S")


def verify(stat_type: str, season: str) -> bool:
    """Parse the cached page with the offline reader and check it's a full season."""
    reader = FBrefCached(leagues="Big 5 European Leagues Combined", seasons=season)
    ok, lines = check_player_table(reader.read_player_season_stats(stat_type=stat_type), stat_type)
    for line in lines:
        print(f"      {line}")
    return ok


todo = []
for stat_type, season in pages:
    path = player_cache_path(data_dir, stat_type, season)
    print(f"{stat_type}:{season}\n  url:   {player_page_url(stat_type, season)}\n  cache: {path.name}")
    if path.exists():
        print("  already cached -- verifying instead of fetching")
        print(f"  {'PASS' if verify(stat_type, season) else 'FAIL'}")
    else:
        todo.append((stat_type, season, path))

if dry_run or not todo:
    sys.exit(0)

from seleniumbase import sb_cdp  # noqa: E402  (imported late so --dry-run never starts Chrome)

results = {}
sb = None
try:
    for n, (stat_type, season, path) in enumerate(todo):
        if n:
            print(f"\n[{stamp()}] waiting {SECONDS_BETWEEN_PAGES}s before the next page")
            time.sleep(SECONDS_BETWEEN_PAGES)
        url = player_page_url(stat_type, season)
        print(f"\n[{stamp()}] opening {stat_type}:{season}")
        if sb is None:
            sb = sb_cdp.Chrome(url)
        else:
            sb.get(url)

        table_marker = f'id="stats_{stat_type}"'
        source, started, last_click = "", time.time(), 0.0
        while time.time() - started < WAIT_FOR_TABLE:
            source = sb.get_page_source(include_shadow_dom=False)
            if table_marker in source:
                break
            if any(sign in source for sign in CLOUDFLARE_SIGNS) and time.time() - last_click > 15:
                print(f"[{stamp()}]   Cloudflare challenge showing -- trying to click it "
                      "(click it yourself if it stays)")
                try:
                    sb.solve_captcha()
                except Exception as e:  # noqa: BLE001
                    print(f"[{stamp()}]   auto-click failed: {type(e).__name__}")
                last_click = time.time()
            sb.sleep(3)

        if table_marker not in source:
            print(f"[{stamp()}]   FAIL: stats table never appeared within {WAIT_FOR_TABLE}s. Not saved.")
            results[f"{stat_type}:{season}"] = "FETCH FAILED (no stats table on the page)"
            continue

        path.write_text(source, encoding="utf-8")
        print(f"[{stamp()}]   saved {len(source) / 1e6:.1f} MB -> checking it")
        if verify(stat_type, season):
            results[f"{stat_type}:{season}"] = "PASS"
        else:
            rejected = path.with_suffix(".rejected.html")
            path.replace(rejected)
            print(f"      moved to {rejected.name} so it can't be used as data")
            results[f"{stat_type}:{season}"] = "FETCHED OK, but failed the data check (see above)"
finally:
    if sb is not None:
        try:
            sb.quit()
        except Exception:  # noqa: BLE001
            pass

print("\n" + "=" * 50)
for page, result in results.items():
    print(f"  {page:<16} {result}")
