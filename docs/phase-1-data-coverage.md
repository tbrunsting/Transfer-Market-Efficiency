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
| **transfermarkt-datasets** (GitHub `dcaribou/transfermarkt-datasets`, CC0) | Clubs, fixtures and league points, transfer fees, market values, per-match manager names | **Adopted** 2026-09-11. Frozen: updates stopped in July 2026, so transfers after 11 July 2026 are missing and the 2026/27 recency layer is incomplete. Evaluation: [`phase-1-transfermarkt-evaluation.md`](phase-1-transfermarkt-evaluation.md) |

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
3. **Team figures are built by summing player figures.** For counting stats
   (goals, penalties, progressive passes and carries, tackles plus
   interceptions) player sums match the team files exactly for 99–100% of
   teams, once teams with a blank player row are set aside (see "Known gaps").
   **xG is the exception:** FBref's team xG runs 1.45–2.10% *below* the sum of
   its own player xG in every season. It isn't penalties (non-penalty xG shows
   the same gap) or a data revision (player xG is identical to the pre-2023
   release). Team xG is therefore always the sum of player xG, and is never
   mixed with FBref's team figure.
4. **Nothing from a second source is combined with the snapshot until it has
   matched the snapshot where they overlap**, as Kaggle was tested in the SCA
   section below.
5. **Joins use FBref IDs, never names** (previous section), with one guard:
   an ID must stand for one person (see "Known gaps").

## Known gaps in the snapshot

Found by `scripts/12_check_fbref_consistency.py`; each is re-checked on every
run.

- **13 player-seasons with 450+ minutes had every advanced column blank**
  (21,091 minutes), in all advanced files, while their minutes and goals are
  there. Most are in 2022/23: Lee Kang-in (Mallorca, 2,823 min), Yan Valery
  (Angers), Gabriel Strefezza (Lecce), Thijs Dallinga (Toulouse), Robert
  Sánchez (Brighton), Hugo Guillamón (Valencia), Omar Marmoush (Wolfsburg),
  plus a few in other seasons.
  **Filled from Kaggle** (decision 2026-09-11) by
  `scripts/13_fill_blank_players_from_kaggle.py`: 384 values across 19 blank
  rows, **all 13 of the 450+ minute player-seasons included**. Only Kaggle
  columns that agree with the snapshot for at least 97% of players are used
  (25 of 44 candidate pairs). Proof: clubs whose player sums equal their team
  totals rise from 669 to 681 of 684 team-seasons for progressive passes, 670
  to 683 for progressive carries, and 665 to 682 for clearances. The fill is a
  correction table (`reference/fbref_blank_fill.csv`, report alongside)
  applied on load; the snapshot itself is never modified.
  **Still blank for those rows (known gap):** xAG, aerials won (the count; the
  win rate is filled), touches, all passing detail (Kaggle's passing columns
  are a different data version, 29–62% agreement), and all goalkeeping stats
  (86–88% agreement). So two goalkeepers, **Robert Sánchez (Brighton 2022/23)
  and Léo Jardim (Lille 2021/22 and 2022/23)**, can't be scored on
  goalkeeping for those seasons, and no filled player has xAG for the filled
  season. 12 more blank rows are low-minute players with no Kaggle link.
- **One FBref player ID stands for two people:** `4acd733a` ("Valery", born
  1999) covers Valery Fernández (Girona) and Yan Valery (Southampton, then
  Angers) in 2022/23, 56 league matches in one season. It's the only
  impossible season total in the window. The crosswalk links each club's row
  to the right Kaggle player (Girona to Valery Fernández, Angers and
  Southampton to Yan Valery). Any join to Transfermarkt for this ID must still
  be resolved by hand, and the check fails if a new case appears.
- **The passing file's older version** covers 2017/18–2021/22 (rule 2). The
  check found no difference in 2022/23 once blank rows are set aside.

## Resolved: loan fees, and loans vs free transfers

**This was a known limitation. It is no longer one** (resolved 2026-09-12).

It was accepted because the Transfermarkt build first used records every loan,
loan return and free transfer as a fee of 0 and discards loan fees: its parser
only reads fee text beginning with "€", and Transfermarkt writes loan fees as
"Loan fee:€5.80m". Recovering them meant about 1,015 live page fetches, which
looked like a fragile dependency for a small share of the money.

**What changed.** A different, larger problem forced the same fetch anyway: the
frozen transfers table turned out to be missing real fee-bearing signings, and
missing them unevenly across the window (79% of true spend present in 2017/18,
99.6% in 2023/24 — see [`phase-2-schema.md`](phase-2-schema.md) section 2).
Fixing that required pulling fees from Transfermarkt's club pages, and those
pages label every row, so the loan data came with it.

**The revisit criterion has been satisfied rather than left open.** What the
pull recovered:

- **€2,189m of loan fees across 1,617 loans**, entirely absent from the frozen
  table. 14.4% of loans carry a fee (median €0.6m, up to €20m).
- **Every row's own label**, so loans, loan returns, free transfers and
  undisclosed fees are read rather than inferred: 8,245 permanent fees, 11,596
  end-of-loan, 9,648 loans, 7,460 free, 5,479 undisclosed, 1,617 loans with a
  fee.

What this now means:

- **The club page's spending breakdown is three-way again:** permanent, loan
  and free, as the original mockup had it. The two-way compromise is reverted.
- **Loan fees paid and received appear in the money flows.**
- **Loan returns are identified by their own label**, not by pattern-matching
  reversal pairs. The reversal-pair rule survives only as a documented fallback
  for rows that exist solely in the frozen table.
- Transfers with an undisclosed fee are still counted as moves but never
  valued (scoping doc 7).

## Shot-creating actions (SCA): kept, sourced from Kaggle for all seven seasons

**Decided 2026-09-11 (Tyler).** Section 4.4 of the scoping doc stands as
written.

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

Decision: **Kaggle SCA per 90 for all seven seasons**, one source and one data
version, rather than splicing the old file (to 2021/22) onto Kaggle
(2022/23 on). Scoring uses per-90 rates (the quality axis in 4.5), so the lack
of totals doesn't matter. Kaggle's GCA per 90 wasn't validated; section 4.4
scores on SCA.

## Status

- Window 2017/18–2023/24: **final** (2026-09-11).
- SCA from Kaggle for all seven seasons: **decided** (2026-09-11).
- Snapshot freeze: **done** (2026-09-11). `scripts/10_freeze_fbref_snapshot.py`
  (which calls `scripts/11_rds_to_csv.R`) holds 34 source files (187.4 MB) in
  `data/raw/fbref/`, recorded with SHA-256 checksums in
  `reference/fbref_snapshot_manifest.csv` (73 rows, reviewed by Tyler). All 32
  CSV conversions are identical to their `.rds` sources, and re-runs are
  idempotent. The player mapping CSV has mixed encoding (UTF-8 except 3 lines
  in Windows-1252), so decode it per line when loading.
- Consistency checks as code: **done** (2026-09-11).
  `scripts/12_check_fbref_consistency.py` checks rules 1–5 on the frozen data,
  after first re-hashing every file against the manifest. All checks pass. It
  writes `reference/fbref_consistency_report.md` and the Kaggle-to-FBref
  crosswalk `reference/kaggle_fbref_crosswalk.csv` (18,224 links: 18,052 by
  name, 164 by fingerprint within the same club, 8 blank-row matches). Both
  files are byte-identical on a re-run.
- Blank player-seasons: **filled from Kaggle** (2026-09-11) by
  `scripts/13_fill_blank_players_from_kaggle.py`, all 13 of the 450+ minute
  cases; what remains blank is listed in "Known gaps".
- Transfermarkt source: **`transfermarkt-datasets` adopted** (2026-09-11), with
  the loan limitation accepted and managers taken from per-match names. See
  [`phase-1-transfermarkt-evaluation.md`](phase-1-transfermarkt-evaluation.md).
- Browser gap pages (the earlier Tier 1/Tier 2 plan): **abandoned**. Pure CDP
  mode fetches FBref pages fine, but since 20 Jan 2026 there's no advanced data
  on them to fetch (gca, defense and standard 2023/24 checked in raw HTML; see
  the diagnostics doc).

## Known gaps in shot-creating actions (2026-09-13)

SCA comes from Kaggle for all seven seasons (one source per metric). Measured in
the loaded warehouse, it covers **99.79% of minutes played**. Most of the
player-seasons without it are cameos under 90 minutes, where a per-90 figure
barely exists.

Two regulars (900+ minutes) were investigated individually:

- **Gabriel Martinelli, Arsenal 2022/23: fixed.** The crosswalk missed him
  twice for legitimate reasons: FBref names him "Martinelli" that season, and
  FBref's xG (9.3) differs from Kaggle's (9.1). Linked by hand after confirming
  the Kaggle row is not a duplicate of his 2023/24 row (47 of 64 columns
  differ). Recorded in `reference/kaggle_crosswalk_review.csv`.
- **Atakan Karazor, Stuttgart 2020/21 to 2023/24: documented gap.** He has no
  row in the Kaggle dataset in any season. The old worldfootballR GCA release
  could supply two of those seasons, but that would mix SCA sources, so these
  four player-seasons have no SCA. Recorded as `gap` rows in the same ledger,
  and checked by `sql/03_checks.sql`, which fails if any other regular appears
  without SCA.
