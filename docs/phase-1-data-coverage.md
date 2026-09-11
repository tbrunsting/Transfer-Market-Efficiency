# Phase 1 — FBref data coverage map

What the FBref data actually covers, season by season, and the rules for using
it. Scoring (Phase 3) is checked against this document, so a gap should never
be discovered halfway through.

Verified 2026-09-11 by reading the release files directly with base R.

## Source

- **worldfootballR's pre-scraped data**, the files behind
  `load_fb_big5_advanced_season_stats()`: GitHub repo
  `JaseZiv/worldfootballR_data`, release `fb_big5_advanced_season_stats`.
  These are plain downloads from GitHub. Live FBref requests without a browser
  get `403` (see [`phase-1-scraper-diagnostics.md`](phase-1-scraper-diagnostics.md)).
- **The source is frozen.** The data repo and the worldfootballR package were
  both archived on 2025-09-18 and will never update. That makes the snapshot
  fully reproducible, but also means the gaps below are permanent unless we
  fill them another way.
- Player GCA comes from a second release, `old_fb_big5_advanced_season_stats`,
  frozen on 2023-02-16. The current release has no player GCA file.

## Coverage by stat type

"Complete" means every club played a full season (38 matches, or 34 in the
Bundesliga and in Ligue 1 from 2023/24). Row counts alone hide partial seasons,
so this was checked with the maximum matches or 90s played.

| Stat type | Used for (scoping doc 4.4 / 4.5) | Complete | Partial | Missing |
|---|---|---|---|---|
| standard | minutes, xG, npxG, xAG, progressive passes/carries | 2017/18–2024/25 | 2025/26 (5 matchweeks) | — |
| shooting | forwards | 2017/18–2024/25 | 2025/26 (5 matchweeks) | — |
| passing | *see rule 2* | 2017/18–2024/25 | 2025/26 (5 matchweeks) | — |
| playing_time | availability | 2017/18–2024/25 | 2025/26 (5 matchweeks) | — |
| defense | centre-backs, full-backs | 2017/18–2023/24 | 2024/25 (9 matchweeks) | 2025/26 |
| possession | carries, take-ons | 2017/18–2023/24 | 2024/25 (9 matchweeks) | 2025/26 |
| misc | aerial duels | 2017/18–2023/24 | 2024/25 (9 matchweeks) | 2025/26 |
| keepers, keepers_adv | goalkeepers | 2017/18–2023/24 | 2024/25 (9 matchweeks) | 2025/26 |
| **gca** (player) | shot-creating actions | 2017/18–2021/22 | 2022/23 (23 of 38 matchweeks) | **2023/24**, 2024/25, 2025/26 |
| team files (all types) | club-level figures | 2017/18–2023/24 | 2024/25 (9 matchweeks) | 2025/26 |

Other facts checked:

- **2017/18 is fully populated.** Every file has 99.5–100% non-null values on
  its key columns (xG, xAG, progressive passes and carries, tackles plus
  interceptions, clearances, aerials won, touches, take-ons, saves, PSxG).
- **98 squads per season, then 96 from 2023/24**, when Ligue 1 dropped to 18
  clubs. That's a league change, not missing data.
- **154 distinct clubs** across the window (30–32 per league). The scoping doc's
  "~98" is the count per season. The club mapping table, metadata and trophies
  all cover 154.
- **Every file has a `Url` column** (the FBref player or team link), which is
  the key `player_dictionary_mapping()` joins on.
- **Player mapping coverage** (`fbref_to_tm_mapping.csv`, also frozen, last
  updated 2025-06-21): 98–100% of players and about 100% of minutes for
  2017/18–2024/25; 89% of players and 92% of minutes for the partial 2025/26.
  The gaps are summer-2025 signings.
- **Match-level files** (release `fb_advanced_match_stats`) stop at 2025-02-03
  for every stat type, and the summary files, the only ones with SCA, start at
  2024/25. They can't complete any season, so they aren't used.

## Rules

1. **A metric is used only if it's complete for every scored season.** Filling
   holes season by season from different sources is how a fake trend gets into
   the "who got smarter" question.
2. **Progressive passes and xAG come from the standard file, never the passing
   file.** For 2017/18–2021/22 the passing file holds an older version of the
   data that was never refreshed:

   | Per-team average | 2017/18 | 2019/20 | 2021/22 | 2022/23 | 2023/24 |
   |---|---|---|---|---|---|
   | Progressive passes, standard file (players summed) | 1,498 | 1,346 | 1,388 | 1,397 | 1,392 |
   | Progressive passes, passing file `Prog`/`PrgP` | **1,273** | **1,145** | **1,172** | 1,397 | 1,392 |
   | Team passing file | 1,498 | 1,347 | 1,388 | 1,402 | 1,392 |

   Key passes have the same problem on a smaller scale: 1.5–2.3% low before
   2022/23, with only 3 of 98 teams matching. Any other passing-file column must
   pass the same player-versus-team test before it's used.
3. **Team figures are built by summing player figures.** Player sums match the
   team files exactly (progressive passes above), so team data needs no
   separate source for gap seasons.
4. **Nothing from a second source is combined with the snapshot until an
   overlap page matches it.** Scraped pages go through soccerdata, whose column
   names differ from worldfootballR's (for example `Expected_xG` versus
   `xG_Expected`). The overlap page proves the column mapping and the data
   version before any gap season is added.
5. **Old GCA (2017/18–2021/22) is usable as is.** Summed to team level it
   matches the current team GCA file to within 0.06% (e.g. 816.7 vs 817.2 SCA
   per team in 2017/18), so re-scraping those seasons isn't needed.

## Closing the gaps

Every gap maps to a Big 5 combined FBref page. Each page covers all five
leagues for one stat type in one season. They're fetched through the browser
route, **one page per process, launched by Tyler**:

`.venv\Scripts\python scripts\03_probe_one_page.py player <stat_type> <season>`

**First, a single test page:** `player gca 2324`. It fills a season no other
source covers and shows whether FBref still publishes the advanced stats. Tier 1
only goes ahead if this succeeds cleanly (no retry or reconnect lines, SCA
populated, complete season). The outcome is recorded in the diagnostics doc.

**Tier 1, making 2024/25 scoreable (9 pages):**

| stat_type | season | Why |
|---|---|---|
| gca | 2324 | Test page. 2023/24 has no GCA source at all |
| gca | 2223 | Old file stops at matchweek 23 |
| gca | 2425 | — |
| defense | 2425 | Snapshot has 9 matchweeks |
| possession | 2425 | Snapshot has 9 matchweeks |
| misc | 2425 | Snapshot has 9 matchweeks |
| keeper | 2425 | Snapshot has 9 matchweeks |
| keeper_adv | 2425 | Snapshot has 9 matchweeks |
| defense | 2324 | **Overlap page.** The snapshot already has it; this proves rule 4 |

**Tier 2, making 2025/26 scoreable (10 pages):** standard, shooting, passing,
playing_time, defense, possession, misc, keeper, keeper_adv, gca for `2526`.

If Tier 1 can't be fetched, the only other lead for player GCA in
2022/23–2023/24 is the Kaggle dataset "FBref 2017-2024 for Europe's Top 5
leagues", and only if it passes the overlap test.

## What the scored window can be

| Outcome | Scored seasons | Shot-creating actions |
|---|---|---|
| No gap pages come through | 2017/18–2023/24 (7) | Dropped under rule 1 (missing for 1.6 of those seasons). Creators are scored on xAG plus progressive passes and carries. |
| Tier 1 succeeds | 2017/18–2024/25 (8) | Used |
| Tiers 1 and 2 succeed | 2017/18–2025/26 (9) | Used |

Any season outside the scored window is shown on the club page as unscored
recent activity, alongside 2026/27 (scoping doc 4.8).

## Status

- Snapshot freeze (scripted download + manifest): not started.
- Consistency checks as code (rules 2–4): not started.
- Test page `player gca 2324`: pending, Tyler runs it.
