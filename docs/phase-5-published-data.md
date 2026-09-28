# What the public site republishes

Written before the first public deploy, so the decision to publish is made with
the actual contents in view rather than a guess at them. Every field below was
read out of the committed files under `web/public/`, not from the SQL that
produced them.

Snapshot of 2026-09-27: **148 JSON files + 145 crest images, 7.96 MB on disk**
(page 1 is 113 KB; a club shard is a median of 36 KB).

## 1. Volumes

| What | Rows published |
|---|---|
| Club-seasons (page 1) | 684 |
| Clubs | 145 |
| Player-club-season squad rows | 19,562 |
| Distinct players named | 6,575 |
| Market values (one per squad row) | 19,215 (347 null) |
| Player quality percentiles (derived) | 11,265 |
| Transfer rows (each club's side of a move) | 32,150 |
| …with a disclosed fee | 27,132 |
| …fee null because undisclosed | 5,018 |
| Club crest images | 145 |

## 2. Field-by-field provenance

`D` marks a field **derived by this project** — no source publishes it.

### `data/page1.json` → `overview` (684 rows)

| Field | Source |
|---|---|
| `club_key`, `club_name`, `club_country` | Transfermarkt club pages + `reference/club_metadata.csv`; `club_key` is this warehouse's own surrogate key |
| `league`, `league_code`, `season`, `season_end_year` | Transfermarkt competition codes (`GB1`, `ES1`, …) |
| `gross_spend_eur`, `gross_sales_eur`, `net_spend_eur` | **Transfermarkt** transfer fees, summed per club-season |
| `undisclosed_signings`, `undisclosed_sales` | counts of Transfermarkt moves with no fee stated |
| `squad_value_start_eur` | **Transfermarkt** player market valuations, summed at season start |
| `matches`, `points`, `points_per_match` | computed 3-1-0 from the frozen **Transfermarkt** games table |
| `has_known_deduction` | this project's flag where the computed table disagrees with Transfermarkt's reported position |
| `efficiency_index`, `efficiency_score_0_100` | **D** — the composite score |
| `recruitment_z`, `trading_z`, `value_growth_z`, `sporting_z` | **D** — the four pillars |
| `is_recruitment_scored`, `is_provisional` | **D** — scoring-coverage flags |

`spend_by_league` (35 rows) is the same money aggregated per league-season.
`weights` (4 rows) and `league_premium` (5 rows) are **D**: the composite
weights with their written rationale, and the per-league output-per-euro
premium from the scoring layer.

### `data/clubs/index.json` (145 rows)

`club_key`, `club_name`, `seasons` as above; `country` and `city` from
**Wikidata** (reviewed by hand in `reference/club_metadata_review.csv`);
`leagues` derived from the club's seasons in scope.

### `data/clubs/<club_key>.json`

**`squad`** — 19,562 rows, the biggest player-level surface:

| Field | Source |
|---|---|
| `player_key` | this warehouse's surrogate key |
| `player_name`, `age` | **FBref** (worldfootballR snapshot) |
| `nationality_code` | **FBref**, falling back to **Transfermarkt** for players who appear only there |
| `matches_played`, `starts`, `minutes` | **FBref** |
| `position_group`, `position_short` | **D** — this project's mapping of FBref/Transfermarkt positions |
| `position_detail` | **Transfermarkt**'s own reported position |
| `market_value_eur` | **Transfermarkt** player valuations |
| `quality_percentile` | **D** — the player-quality model (FBref per-90 output + **Kaggle** shot-creating actions) |
| `season`, `season_end_year`, `clubs_in_season`, `is_primary_club_for_season`, `is_latest_season_for_club` | derived grain flags |

No birth dates, no contract terms, no wages, no injury or disciplinary data,
no player photographs — age is the integer FBref publishes.

**`transfers`** — 32,150 rows:

| Field | Source |
|---|---|
| `player_name`, `transfer_date`, `other_club_name` | **Transfermarkt** club transfer pages |
| `fee_eur`, `fee_status`, `is_fee_disclosed` | **Transfermarkt** fees, exactly as stated; undisclosed stays `null`, never zero |
| `direction`, `direction_filter`, `transfer_category` | Transfermarkt's own labels, regrouped |
| `date_is_estimated` | this project's flag where the page gives only a window |
| `other_club_in_scope`, `is_big_five_season` | derived scope flags |

**`cashflow`** — 7 rows per club: the same Transfermarkt money per season, plus
`efficiency_index` / `efficiency_score_0_100` (**D**) and manager fields.
`manager_name`, `manager_is_caretaker`, `managers_in_season`, `managers_count`
come from the per-match managers in the **transfermarkt-datasets** games table;
tenure boundaries are match-based, not appointment dates.

**`honours`** — `league_titles`, `domestic_cups`, `european_trophies` computed
from the frozen **Transfermarkt** games and league tables, plus 17 cup editions
from **Wikidata**. Window-scoped ("since 2017/18"), never all-time.

**`pillars`** — the four pillar z-scores per season (**D**).

### `crests/<club_key>.png`

145 club crests, 3.5 MB, downloaded from Transfermarkt's image CDN
(`tmssl.akamaized.net/images/wappen/head/<tm_id>.png`) and committed to the
repository rather than hotlinked. **These are the one genuinely third-party
asset the site re-hosts**: club badges are the clubs' own trademarks, served by
Transfermarkt. Re-hosting them is ordinary editorial use in a non-commercial
portfolio project, and they are credited in the site footer — but it is the one
item on this page that is a copy of someone else's file rather than a number
derived from data.

## 3. What is *not* published

- No raw source files. `data/` stays gitignored; the site carries aggregates
  and the per-player rows listed above, not the 650 MB of inputs.
- No FBref per-90 statistics, xG, or the underlying per-match data — only the
  derived `quality_percentile`.
- No wages, contracts, agents, birth dates, addresses or any personal data
  beyond name, nationality code, age and on-pitch appearances.
- No Transfermarkt page HTML, and no Kaggle or worldfootballR files.
- No credentials, connection strings or warehouse internals; `manifest.json`
  carries file hashes and row counts only.

## 4. Attribution on the site

Both pages carry a **Data & sources** footer (in `web/src/layouts/Base.astro`,
so it also survives the fetch-failure state) crediting Transfermarkt,
transfermarkt-datasets, FBref, the worldfootballR_data snapshot, the Kaggle
FBref 2017–2024 dataset and Wikidata; stating plainly that the scores are
derived by this project and endorsed by none of those sources; noting that the
data is a frozen snapshot rather than a live feed; and linking to the
repository README for method and limitations.
