# Phase 1 — FBref data coverage map

What the FBref data actually covers, season by season, and the rules for using
it. Scoring (Phase 3) is checked against this document, so a gap should never
be discovered halfway through.

Verified 2026-09-11 by reading the source files directly (base R) and, for the
live site, the raw HTML of pages fetched with `scripts/04_fetch_fbref_cdp.py`.

## Scored window: 2017/18–2023/24, seven seasons (final)

Decided 2026-09-11. This is the final window, not a fallback.

- **Start, 2017/18:** the first season with advanced data for the Big 5
  (scoping doc 4.1).
- **End, 2023/24:** the last season for which every stat type the scoring needs
  exists in complete form. On **20 January 2026**, Opta (Stats Perform)
  terminated FBref's data licence and FBref removed all Opta-sourced advanced
  stats from the live site, past seasons included. The only complete copy is
  the worldfootballR snapshot, frozen on 2025-09-18, in which defense,
  possession, misc and keeper data end with 2023/24 (2024/25 has only 9
  matchweeks). Details and sources are in the scoping doc (4.1) and
  [`phase-1-scraper-diagnostics.md`](phase-1-scraper-diagnostics.md).
- **Outside the window:** 2024/25, 2025/26 and 2026/27 transfer activity can
  still be shown on the club page as unscored recent activity (scoping doc
  4.8). Their on-field output isn't scored.

## Sources

| Source | What it supplies | Status |
|---|---|---|
| **worldfootballR snapshot**: GitHub `JaseZiv/worldfootballR_data`, release `fb_big5_advanced_season_stats` | Every player and team stat type except player GCA | Archived 2025-09-18, frozen. **Effectively the last public copy of FBref's Opta-era data.** |
| Same repo, release `old_fb_big5_advanced_season_stats` | Player GCA, 2017/18–2021/22 complete, 2022/23 to matchweek 23 | Frozen 2023-02-16. An older version (see SCA section) |
| **Kaggle**: "FBref 2017-2024 for Europe's Top 5 leagues" (`akshankrithick/fbref-2017-2024-for-europes-top-5-leagues`, version 2, updated 2026-05-07, MIT licence) | SCA and GCA **per 90** for all seven seasons, plus 60-odd other columns | Validated against the snapshot (SCA section). No FBref IDs. The MIT licence covers the uploader's work; the underlying data belongs to Sports Reference/Opta |
| `fbref_to_tm_mapping.csv` (same repo) | FBref player ↔ Transfermarkt player | Frozen, last updated 2025-06-21 |
| Live FBref | Basic stats only | **Not a source for advanced data** since 20 Jan 2026 |

Not used: the match-level release (`fb_advanced_match_stats`) stops at
2025-02-03 and can't complete any season, and live FBref's remaining basic
columns are a different data version (Int/TklW match the snapshot for only
about 61% of players).

## Coverage within the window

"Complete" means every club played a full season (38 matches, or 34 in the
Bundesliga and in Ligue 1 from 2023/24). Row counts alone hide partial seasons,
so this was checked with the maximum matches or 90s played.

| Stat type | Used for (scoping doc 4.4 / 4.5) | 2017/18–2023/24 | Source |
|---|---|---|---|
| standard | minutes, xG, npxG, xAG, progressive passes/carries | Complete | Snapshot |
| shooting | forwards | Complete | Snapshot |
| passing | *see rule 2* | Complete | Snapshot |
| playing_time | availability | Complete | Snapshot |
| defense | centre-backs, full-backs | Complete | Snapshot |
| possession | carries, take-ons | Complete | Snapshot |
| misc | aerial duels | Complete | Snapshot |
| keepers, keepers_adv | goalkeepers | Complete | Snapshot |
| team files (all types) | club-level figures | Complete | Snapshot |
| **SCA / GCA** (player) | shot-creating actions | Complete **only via Kaggle** (per 90) | See SCA section |

Other facts checked:

- **2017/18 is fully populated.** Every file has 99.5–100% non-null values on
  its key columns (xG, xAG, progressive passes and carries, tackles plus
  interceptions, clearances, aerials won, touches, take-ons, saves, PSxG).
- **98 squads per season, then 96 in 2023/24**, when Ligue 1 dropped to 18
  clubs. That's a league change, not missing data.
- **145 distinct clubs** in the window (the scoping doc's "~98" is the count
  per season). The club mapping table, metadata and trophies cover 145.
- **Every snapshot file has a `Url` column**: the FBref player link in player
  files, the FBref team link in team files. These carry the IDs used for
  joining (next section).
- **Player mapping coverage** (`fbref_to_tm_mapping.csv`): 98–100% of players
  and about 100% of minutes in every season of the window.

## Club and player identity: join on FBref IDs, never names

Names are display labels only. Every join, within a source or across sources,
uses FBref's stable 8-character IDs: the team ID in team URLs
(`/en/squads/8d6fd021/...`) and the player ID in player URLs
(`/en/players/355c883a/...`).

Why this is a rule, not a preference:

- **Names change inside the same source.** In the snapshot itself, Borussia
  Mönchengladbach (team ID `32f3ee20`) is "M'Gladbach" for 2017/18–2022/23 and
  "Gladbach" for 2023/24. A name join would split one club into two.
- **FBref renamed clubs on the live site.** Comparing live 2023/24 pages with
  the snapshot: "Nott'ham Forest" is now "Nottingham", "Eint Frankfurt" is
  "Frankfurt", "Paris S-G" is "Paris SG", "Sheffield Utd" is
  "Sheffield United", "Betis" is "Real Betis", "Newcastle Utd" is "Newcastle".
  198 of the 201 unmatched live rows were the same player IDs under new club
  names.
- **Other sources spell players differently too.** Kaggle writes "Gladbach"
  and uses fuller or romanised player names where the snapshot has
  "Martinelli", "Carlos" or "이강인". An exact name join missed 1.2–2.5% of
  minutes per season.

How it's applied:

- **Snapshot:** team files carry team IDs. Player files carry only a club name,
  but within one season of the snapshot (season, club name) is unique and maps
  to exactly one team ID: **100% of player rows (19,563) resolve**. So player
  rows get a team ID in the first transformation step, and names are never
  used after that.
- **Club mapping table (Phase 1 item 6):** FBref team ID ↔ Transfermarkt club
  ID, one row per club (145), reviewed by hand. Names sit alongside only for
  readability.
- **Kaggle** has no IDs, so its rows are linked to FBref player IDs through a
  crosswalk built from a statistical fingerprint (birth year, matches, goals,
  assists, xG, progressive passes, interceptions). That matched 99.5–99.9% of
  minutes in every season. The crosswalk is stored, so it's built once and can
  be reviewed.

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
   team files exactly (progressive passes above).
4. **Nothing from a second source is combined with the snapshot until it has
   matched the snapshot where they overlap**, as Kaggle was tested in the SCA
   section below.
5. **Joins use FBref IDs, never names** (previous section).

## Shot-creating actions (SCA): keep them, sourced from Kaggle (recommended)

*Awaiting Tyler's decision. If it's declined, rule 1 drops SCA and creators are
scored on xAG plus progressive passes and carries.*

No FBref-derived source has complete player SCA for the whole window: the
snapshot's old GCA file stops at matchweek 23 of 2022/23 and has nothing for
2023/24, and live FBref's GCA columns are blank. Kaggle has SCA and GCA per 90
for all seven seasons, 100% filled. Tests against the snapshot:

- **The rest of Kaggle is the same Opta-era data version.** xG, progressive
  passes and interceptions match the snapshot for 99–100% of players in every
  season, including 2023/24. Progressive passes match the standard file's
  *current* version, not the passing file's old one.
- **Kaggle's SCA reproduces the team totals.** Player SCA per 90 × 90s played,
  summed by club, compared with the snapshot's team GCA file (complete through
  2023/24):

  | Season | Minutes matched | Mean gap to team SCA | Clubs within 1% |
  |---|---|---|---|
  | 2017/18 | 99.86% | −0.06% | 99% |
  | 2018/19 | 99.87% | −0.13% | 100% |
  | 2019/20 | 99.83% | −0.17% | 98% |
  | 2020/21 | 99.76% | −0.16% | 93% |
  | 2021/22 | 99.73% | −0.16% | 95% |
  | 2022/23 | 99.49% | −0.59% | 92% |
  | 2023/24 | 99.70% | −0.30% | 92% |

  The small remaining gap fits SCA per 90 being rounded to two decimals, plus
  the 0.1–0.5% of minutes the crosswalk didn't match.
- **Against the old GCA file, 91–93% of player SCA per 90 values are
  identical** in 2017/18–2021/22. The old file predates FBref's February 2023
  data revision; Kaggle matches the current team totals, so the differences
  look like those revisions.

Recommendation: **Kaggle SCA per 90 for all seven seasons**, one source and one
data version, rather than splicing the old file (to 2021/22) onto Kaggle
(2022/23 on). Scoring uses per-90 rates (the quality axis in 4.5), so the lack
of totals doesn't matter. Kaggle's GCA per 90 wasn't validated; section 4.4
scores on SCA.

## Status

- Window 2017/18–2023/24: **final** (2026-09-11).
- SCA from Kaggle: **awaiting Tyler's decision**.
- Snapshot freeze (scripted download + manifest): planned next.
- Consistency checks as code (rules 2–5): not started.
- Browser gap pages (the earlier Tier 1/Tier 2 plan): **abandoned**. Pure CDP
  mode fetches FBref pages fine, but since 20 Jan 2026 there's no advanced data
  on them to fetch (gca, defense and standard 2023/24 checked in raw HTML; see
  the diagnostics doc).
