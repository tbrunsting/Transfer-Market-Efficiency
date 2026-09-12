# Phase 1 — data ingest, start to finish

What Phase 1 set out to do, what it found, and what it produced. Written 2026-09-12,
when Phase 1's inputs closed. The next phase is schema design.

## What changed about the project along the way

Phase 1 began expecting a nine-season FBref scrape. Three findings reshaped it.

1. **FBref's advanced statistics are gone.** On 20 January 2026, Opta (Stats
   Perform) terminated FBref's data licence and Sports Reference removed every
   Opta-sourced column, for past seasons as well as new ones. Verified directly
   in the saved HTML: the live 2023/24 pages return complete tables with every
   xG, progressive, advanced-defensive and shot-creating cell **blank**, while
   minutes and goals remain. Details and sources in the scoping doc, section 4.1.
2. **The scraper problem was real, and then irrelevant.** soccerdata failed on
   every run because seleniumbase makes a plain HTTP pre-check before opening a
   page, FBref answers 403 to any non-browser client, so its chromedriver
   restart path ran on *every* page — and that path discards its own restart
   errors before declaring itself connected. Pure CDP mode (no chromedriver)
   fixed it in about 6 seconds, just in time to prove the data was blank.
   Full trail in [`phase-1-scraper-diagnostics.md`](phase-1-scraper-diagnostics.md).
3. **The scored window is seven seasons, 2017/18–2023/24**, because the last
   complete copy of the Opta-era data stops there. Coverage map in
   [`phase-1-data-coverage.md`](phase-1-data-coverage.md).

## The data, all frozen and checksummed

Nothing in the pipeline scrapes FBref. Every source is a local, checksummed
copy, because two of the three upstream projects have stopped updating.

| Source | Contents | Provenance |
|---|---|---|
| worldfootballR snapshot | 34 files, 187 MB. Effectively the last public copy of FBref's Opta-era data; that repository was archived 2025-09-18 | `reference/fbref_snapshot_manifest.csv` (73 rows) |
| Kaggle "FBref 2017-2024" | Shot-creating actions, which the snapshot lacks at player level | same manifest |
| transfermarkt-datasets | 8 tables, 70 MB: transfers, valuations, games, per-match managers. Updates paused July 2026 | `reference/transfermarkt_snapshot_manifest.csv` |
| Wikidata | Club cities, plus 17 cup editions missing from Transfermarkt | cached queries; QIDs recorded per row |

About 650 MB of raw data sits under `data/raw/`, which is gitignored. Only the
manifests and the curated tables are tracked.

## The thirteen reference tables

All keyed on `fbref_team_id`, never on names.

| Table | Rows | What it is |
|---|---|---|
| `club_id_mapping.csv` | 145 | FBref team ID ↔ Transfermarkt club ID, one-to-one |
| `club_metadata.csv` | 145 | League, leagues in window, country, city, crest URL, Wikidata QID |
| `club_trophies.csv` | 145 | Trophies since 2017/18 in three categories |
| `club_trophies_detail.csv` | 97 | One row per trophy, each traceable to a match, a league table, or a Wikidata edition |
| `club_season_points.csv` | 684 | Points, record, goals, position per club-season |
| `manager_tenures.csv` | 906 | Match-based tenures, all competitions |
| `kaggle_fbref_crosswalk.csv` | 18,224 | Kaggle ↔ FBref player links |
| `fbref_blank_fill.csv` | 384 | Kaggle values filling the snapshot's blank player-seasons |
| `club_mapping_review.csv` | 44 | Human decisions on club matching |
| `club_metadata_review.csv` | 25 | Human decisions on cities and countries |
| `manager_name_review.csv` | 11 | Human decisions on shared manager names |
| `fbref_snapshot_manifest.csv` | 73 | FBref + Kaggle provenance |
| `transfermarkt_snapshot_manifest.csv` | 16 | Transfermarkt provenance |

Plus two generated reports, `fbref_consistency_report.md` and
`fbref_blank_fill_report.md`.

**The review-ledger pattern.** Wherever a judgement was needed, the decision
lives in a `*_review.csv` beside the table, with who decided, when and why, and
the build script reads it. Decisions are keyed to the specific pair or value
approved, so if a re-run ever produces something different it is flagged again
rather than inheriting the approval.

## The scripts

| Script | Purpose |
|---|---|
| `01`–`03` | Early FBref connection and coverage probes (kept as the record) |
| `04_fetch_fbref_cdp.py` | Pure CDP fetcher: the only thing that ever fetched FBref successfully |
| `fbref_extended.py` | Adds the stat types soccerdata blocks, plus a cache-only reader |
| `10_freeze_fbref_snapshot.py` + `11_rds_to_csv.R` | Freeze and convert the FBref snapshot |
| `12_check_fbref_consistency.py` | The data rules, as runnable checks |
| `13_fill_blank_players_from_kaggle.py` | Fill blank player-seasons from Kaggle |
| `14_freeze_transfermarkt.py` | Freeze the Transfermarkt tables |
| `15_build_club_mapping.py` | Club mapping, by name and by squad overlap |
| `16_build_club_trophies.py` | Trophies |
| `17_build_club_metadata.py` | Metadata |
| `18_build_league_points.py` | League points |
| `19_build_manager_tenures.py` | Manager tenures |

## What the checks actually caught

Verification was not ceremony. Every item below was a real error that would
have reached the dashboard:

- **Roma mapped to Parma**, and Rennes to Nantes, by name similarity alone —
  both same-country, so the country constraint did not help. Squad overlap
  (which club a club's own players actually played for) caught all three
  conflicts, and the clubs it named were exactly the ones the name matcher had
  left unused.
- **Progressive passes 15% low** before 2022/23 in the passing file, an
  unrefreshed older version that would have manufactured a fake mid-window
  trend in the "who got smarter" question.
- **Wikidata property P2446 returns people, not clubs.** The first join attempt
  mapped Transfermarkt 12 to "Harry Koch" instead of AS Roma. The club property
  is P7223.
- **16 wrong cities**, of which the automated flags found only 6: a training
  ground's commune usually *is* a real town, so it passes a settlement test
  while still being the wrong answer.
- **A class-pooling bug** that let a settlement class on one headquarters value
  validate a different, non-place value (Marseille's training centre).
- **Two managers sharing a name at two clubs simultaneously**, which is proof
  of two different people rather than a warning.
- **FBref's team xG is about 2% below the sum of its own player xG**, in every
  season. Unexplained, so team xG is built from player sums and never mixed
  with FBref's team figure.

Checks that passed: all 45 cup finals against known results, all 35 league
champions (twice, independently), all 17 Wikidata cup editions, known manager
histories (Chelsea's four managers in 2022/23, in order), and spot values such
as Manchester City's 100 points in 2017/18.

## Decisions and limitations, with revisit criteria

| Decision | Reasoning | Revisit when |
|---|---|---|
| Seven seasons, 2017/18–2023/24 | The advanced data stops there | Sports Reference has said some advanced data may return "at a dramatically smaller scale"; anything returning must pass the overlap test first |
| SCA from Kaggle | Absent from the snapshot at player level; validated against the snapshot where they overlap | — |
| ~~Loan fees not counted~~ **Resolved 2026-09-12** | The frozen build discards them, but the club-page pull that fixed the fee-completeness bias recovered €2,189m of loan fees across 1,617 loans and labels every row | Satisfied: breakdown is three-way again (permanent / loan / free) |
| `points_from_results` is the primary figure | Match results cannot know about administrative deductions | If the sporting-return pillar looks wrong in a way deductions would explain; the fix is 35 Transfermarkt league-table pages |
| Manager tenures are match-based | This source has no appointment or departure dates | If official dates are ever needed for something finer than section 4.9's season-snapped bands |
| `country` is the club's own | Cardiff and Swansea are Welsh, Monaco Monegasque; the league field carries the competition | — |

**On points and positions.** `position_computed` disagrees with Transfermarkt's
own reported position in 49 of 684 club-seasons. 42 are one-place swaps between
clubs level on points, where Spain and Italy break ties on head-to-head (which
a table alone cannot reproduce) or where Transfermarkt's figure reflects a
rescheduled final match. Both three-place gaps are the two flagged deductions
(Juventus 2022/23, Everton 2023/24). Both positions are kept in the table so
the difference is always visible.

## Phase 1 is closed

Every input the scoring needs exists, is checksummed, and is traceable to a
source: player performance for seven seasons, transfer fees and market values,
league points, club identity and metadata, trophies, and manager tenures.

Next: schema design — the star schema in PostgreSQL that these tables and the
frozen snapshots load into.
