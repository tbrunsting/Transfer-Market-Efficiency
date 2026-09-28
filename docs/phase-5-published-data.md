# What the public site republishes

Written before the first public deploy, so the decision to publish is made with
the actual contents in view rather than a guess at them. Every field below was
read out of the committed files under `web/public/`, not from the SQL that
produced them.

**The export carries only what the pages read.** Each field was traced to a
runtime read in `web/src` (type declarations do not count as use); 34 fields
and three whole tables had none and were dropped — see §5. `scripts/81_check_web_export.py`
pins the surviving column list, so a field cannot drift back in by being added
to a view.

Snapshot of 2026-09-28: **148 JSON files + 145 crest images, 5.77 MB on disk**
(page 1 is 51 KB, 16 KB gzipped; the median club shard is 17 KB; the crests are
3.11 MB of the total).

## 1. Volumes

| What | Rows published |
|---|---|
| Club-seasons (page 1) | 684 |
| Clubs | 145 |
| Player-club-season squad rows | 19,562 |
| Distinct player names | 6,526 |
| Market values (one per squad row) | 19,215 (347 null) |
| Transfer rows (each club's side of a move) | 32,150 |
| …with a disclosed fee | 27,132 |
| …fee null because undisclosed | 5,018 |
| Club crest images | 145 |

## 2. Field-by-field provenance

`D` marks a field **derived by this project** — no source publishes it.

### `data/page1.json` → `overview` (684 rows, 13 fields)

| Field | Source |
|---|---|
| `club_key` | this warehouse's own surrogate key |
| `club_name` | Transfermarkt club pages + `reference/club_metadata.csv` |
| `league`, `season`, `season_end_year` | Transfermarkt competitions and this project's season labels |
| `gross_spend_eur`, `gross_sales_eur`, `net_spend_eur` | **Transfermarkt** transfer fees, summed per club-season |
| `matches`, `points` | computed 3-1-0 from the frozen **Transfermarkt** games table |
| `efficiency_index`, `efficiency_score_0_100` | **D** — the composite score |
| `is_provisional` | **D** — scoring-coverage flag |

Points per match is **not** published: the page divides summed points by summed
matches over the current filter, which is not a mean of the per-season rates.

### `data/clubs/index.json` (145 rows, 6 fields)

`club_key`, `club_name`, `seasons` as above; `country` and `city` from
**Wikidata** (reviewed by hand in `reference/club_metadata_review.csv`);
`leagues` derived from the club's seasons in scope.

### `data/clubs/<club_key>.json`

**`squad`** — 19,562 rows, 6 fields, the only player-level surface:

| Field | Source |
|---|---|
| `player_name` | **FBref** (worldfootballR snapshot) |
| `nationality_code` | **FBref**, falling back to **Transfermarkt** for players who appear only there |
| `position_short` | **D** — this project's mapping of Transfermarkt and FBref positions |
| `minutes` | **FBref** |
| `market_value_eur` | **Transfermarkt** player valuations |
| `season_end_year` | the season the row belongs to |

No player ids, no birth dates, no age, no contract terms, no wages, no injury
or disciplinary data, no photographs — and, since the audit, no per-player
quality percentile, because no page showed one.

**`transfers`** — 32,150 rows, 8 fields:

| Field | Source |
|---|---|
| `player_name`, `other_club_name` | **Transfermarkt** club transfer pages |
| `fee_eur`, `fee_status` | **Transfermarkt** fees, exactly as stated; undisclosed stays `null`, never zero |
| `direction`, `direction_filter` | Transfermarkt's own labels, regrouped |
| `season`, `season_end_year` | the season the move belongs to |

Transfer *dates* are no longer published: the table shows the season, and
nothing read the date.

**`cashflow`** — 7 rows per club, 11 fields: Transfermarkt money per season
(`fees_paid_eur`, `fees_received_eur`, `undisclosed_signings`,
`undisclosed_sales`), the scores (**D**), `is_provisional` (**D**), and
`manager_name` / `manager_is_caretaker` from the per-match managers in the
**transfermarkt-datasets** games table — match-based boundaries, not
appointment dates.

**`honours`** — `league_titles`, `domestic_cups`, `european_trophies` computed
from the frozen **Transfermarkt** games and league tables, plus 17 cup editions
from **Wikidata**, and the `window_label` the panel prints. Window-scoped
("since 2017/18"), never all-time.

**`pillars`** — 6 fields: the four pillar z-scores (**D**),
`is_recruitment_scored` (**D**) and the season they belong to.

### `crests/<club_key>.png`

145 club crests, 3.11 MB, downloaded from Transfermarkt's image CDN
(`tmssl.akamaized.net/images/wappen/head/<tm_id>.png`) and committed to the
repository rather than hotlinked. **These are the one genuinely third-party
asset the site re-hosts**: club badges are the clubs' own trademarks, served by
Transfermarkt. The footer says so — "Club crests are trademarks of their
respective owners, shown for identification only" — and a missing crest file
degrades to the club's initials rather than a broken-image icon.

## 3. What is *not* published

- No raw source files. `data/` stays gitignored.
- No FBref per-90 statistics, xG, or per-match data — and no longer the derived
  quality percentile either.
- No wages, contracts, agents, birth dates, addresses or any personal data
  beyond a player's name, nationality code, minutes and market value.
- No Transfermarkt page HTML, and no Kaggle or worldfootballR files.
- No credentials, connection strings or warehouse internals; `manifest.json`
  carries file hashes and row counts only.

## 4. Attribution on the site

Both pages carry a **Data & sources** footer (in `web/src/layouts/Base.astro`,
so it also survives the fetch-failure state) crediting Transfermarkt,
transfermarkt-datasets, FBref, the worldfootballR_data snapshot, the Kaggle
FBref 2017–2024 dataset and Wikidata; stating that the scores are derived by
this project and endorsed by none of those sources; noting the crest
trademarks; noting that the data is a frozen snapshot rather than a live feed;
and linking to the repository README.

## 5. What the audit dropped

Traced on 2026-09-27; removed from the export on 2026-09-28. Everything below
still exists in the `presentation.*` views — it is simply no longer published.

| Table | Dropped |
|---|---|
| `page1.overview` | `club_country`, `league_code`, `points_per_match`, `undisclosed_signings`, `undisclosed_sales`, `squad_value_start_eur`, `has_known_deduction`, `recruitment_z`, `trading_z`, `value_growth_z`, `sporting_z`, `is_recruitment_scored` |
| `page1.spend_by_league` | whole table (9 fields × 35 rows) — the donut sums the same money from the overview rows |
| `page1.weights` | whole table (4 fields × 4 rows) — the pillar weights are in the page's own footer text and in the docs |
| `page1.league_premium` | whole table (4 fields × 5 rows) — a Phase 3 finding no page shows |
| `shard.squad` | `player_key`, `season`, `is_latest_season_for_club`, `clubs_in_season`, `is_primary_club_for_season`, `position_group`, `position_detail`, `age`, `matches_played`, `starts`, `quality_percentile` |
| `shard.transfers` | `transfer_key`, `transfer_date`, `date_is_estimated`, `transfer_category`, `is_fee_disclosed`, `other_club_in_scope`, `is_big_five_season` |
| `shard.cashflow` | `net_spend_eur`, `net_transfer_balance_eur`, `season_status`, `managers_in_season`, `managers_count`, `manager_band_seq` |
| `shard.honours` | `total_trophies` |
| `shard.pillars` | `season`, `efficiency_index`, `efficiency_score_0_100`, `is_provisional` |

Effect: **112 published fields → 63**, and the payload fell from 7.96 MB to
5.77 MB on disk, page 1 from 113 KB to 51 KB (29 KB → 16 KB gzipped). The 20
export checks still pass, including the two new ones that compare the published
column list with the list the site reads, in both directions.

Two of these were judgement calls rather than dead weight:
`quality_percentile` is the project's own player-quality model and was the most
sensitive field published, which is why it went first; `transfer_date` is
genuine provenance, but the UI shows only the season, and a date nothing
displays is data released for nothing. Both are one line in
`scripts/80_export_web_data.py` if a later page needs them.
