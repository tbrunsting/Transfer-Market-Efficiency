# Phase 2 — warehouse schema

The PostgreSQL star schema the Phase 1 tables and frozen snapshots load into.
Written 2026-09-12. Nothing has been created in Postgres yet; this is for review.

## 1. The grain decision: one row per transfer event

The plan asked whether the core fact should be one row per transfer or one row
per player-club spell. **Transfer event**, with spells derived on top. The
measurements that decided it:

| Measurement | Result |
|---|---|
| Transfers touching the 145 clubs, in window | 17,336 |
| Zero-fee reversal pairs (A→B then B→A, both zero, ≤400 days) | 4,652 |
| Zero-fee arrivals by month | June 2,991, July 1,390 — the June spike is end-of-loan returns |
| Our players present in the transfers table | 4,168 of 6,464 (64%) |
| Player-club-seasons inside a reconstructable spell | 27% |

A spell is **inferred** (arrival event, then the next departure), so every hole
in the transfer history produces a wrong or missing spell, and an academy
graduate is indistinguishable from a player whose history is simply absent. The
transfer event is what the source actually records, and it is what the money
questions need:

- **Gross and net stay separable.** Scoping doc 4.9 requires fees paid and
  received shown separately, not netted. One row per event with `from_club` and
  `to_club` keys gives both by direction.
- **Undisclosed fees stay NULL** rather than being absorbed into a spell's
  arithmetic (scoping doc 7: excluded and documented, not guessed).
- **Loan returns are classifiable** at the event level, which is where the
  evidence is.

Recruitment ROI does not need spells: a signing *is* a transfer row, and its
output is the player-club-seasons at that club after that date — a direct join.
Trading profit does need pairing, which is what the bridge is for, with explicit
flags when a side is unknown rather than an assumed pairing. A club selling an
academy player at pure profit is a real finding; recording it as "purchase price
0" would be fabrication.

**Three grains:** transfer events (money), player-club-seasons (performance),
and a derived spell bridge (attribution).

## 2. The completeness problem, measured and fixed

The frozen transfers table covers 23,379 of Transfermarkt's 50,149 players. The
gap is **not** confined to academy and free moves, and it is **not** random.

Checked against Transfermarkt's own club pages (`scripts/20_validate_transfer_coverage.py`):

| Club-season | Fees on the page | Fees in the table | Missing |
|---|---|---|---|
| Brighton 2022/23 | €55.7m | €55.7m | none |
| RB Leipzig 2021/22 | €126.2m | €126.2m | none |
| Olympique Lyon 2022/23 | €32.5m | €32.5m | none |
| **Villarreal 2019/20** | **€45.8m** | **€13.3m** | **€32.5m (Paco Alcácer €25m, Javi Ontiveros €7.5m)** |

Both missing players are in the players table with hundreds of appearances and
**zero transfer rows**. And coverage rises with recency:

| Season | 2017/18 | 2018/19 | 2019/20 | 2020/21 | 2021/22 | 2022/23 | 2023/24 |
|---|---|---|---|---|---|---|---|
| % of player-club-seasons with transfer history | 52.3 | 59.6 | 67.3 | 72.6 | 79.4 | 87.2 | 98.0 |

Worst-covered clubs are those only in the top flight early in the window
(Cardiff 20%, Stoke 27%, Málaga 30%); best are recent arrivals (Le Havre 100%,
Nottingham Forest 97%). The upstream project keeps only each player's most
recent extraction, so players who left the big five years ago were never
re-fetched.

**Consequence:** early-window spend is understated more than late-window spend,
which would manufacture a "clubs got smarter" trend out of a collection
artefact — the project's headline finding.

**Resolved (2026-09-12).** `scripts/21_fetch_transfermarkt_fees.py` fetched all
145 clubs x 7 seasons = 1,015 club transfer pages directly from Transfermarkt
(plain HTTP, 5s spacing, zero backoffs, zero failures, every page cached and
checksummed). 44,045 transfer rows parsed. Restricted to the 684 club-seasons
actually played in the big five — which reconciles exactly with
`club_season_points` — the measured bias is:

| Season | True spend | Present in the frozen table | Missing |
|---|---|---|---|
| 2017/18 | EUR 5,547m | 79.1% | EUR 1,157m |
| 2018/19 | EUR 5,219m | 82.9% | EUR 891m |
| 2019/20 | EUR 6,509m | 90.3% | EUR 631m |
| 2020/21 | EUR 3,775m | 92.7% | EUR 277m |
| 2021/22 | EUR 3,868m | 96.6% | EUR 132m |
| 2022/23 | EUR 5,932m | 98.5% | EUR 88m |
| 2023/24 | EUR 6,563m | 99.6% | EUR 29m |

In 2017/18 one euro in five was invisible; by 2023/24, one in 250. Left
unfixed, that 40x gradient runs along the exact axis the analysis measures.
EUR 3.2bn of real spending was missing in total.

The pull also recovered **EUR 2,189m of loan fees across 1,617 loans** that the
frozen table discards entirely, and it labels every row, so loans and free
transfers stop being indistinguishable.

Sanity checks on the parse: the largest fees are Neymar EUR 222m, Mbappe
EUR 180m, Dembele EUR 148m and Coutinho EUR 135m, all correct; season totals
show the COVID dip (EUR 3.8bn in 2020/21 against EUR 5.5-6.8bn either side)
that scoping doc 4.2 describes; and the four independently spot-checked
club-seasons reproduce to the euro.

`fact_transfer` still carries `source_system`, and `meta.transfer_coverage`
still records per-club-season completeness, so the two sources stay
distinguishable and the fix is auditable rather than assumed.

## 3. Tables

### Facts

```sql
-- One row per transfer event. The financial atom; never netted, never inferred.
CREATE TABLE fact_transfer (
    transfer_key            bigserial PRIMARY KEY,
    player_key              int         NOT NULL REFERENCES dim_player,
    from_club_key           int         NOT NULL REFERENCES dim_club,   -- includes out-of-scope clubs
    to_club_key             int         NOT NULL REFERENCES dim_club,
    transfer_date_key       int         NOT NULL REFERENCES dim_date,
    season_key              int         NOT NULL REFERENCES dim_season, -- the season the window belongs to
    transfer_type_key       int         NOT NULL REFERENCES dim_transfer_type,
    fee_eur                 numeric(14,2),        -- NULL = undisclosed, never 0-filled
    is_fee_disclosed        boolean     NOT NULL,
    market_value_eur        numeric(14,2),        -- player's value at the transfer date
    fee_share_of_season     numeric(10,8),        -- fee / total top-five spend that season (scoping doc 4.6)
    fee_vs_season_median    numeric(10,4),        -- fee / median disclosed fee that season
    is_type_heuristic       boolean     NOT NULL,  -- false for page-sourced rows: the label is stated
    date_is_estimated       boolean     NOT NULL,  -- page rows give a season, not a date (see below)
    source_system           text        NOT NULL,  -- 'transfermarkt-pages' (primary) | 'transfermarkt-datasets'
    source_ref              text        NOT NULL,  -- row identity in the source
    UNIQUE (player_key, transfer_date_key, from_club_key, to_club_key, source_system)
);

-- One row per player per club per season. The performance atom (scoping doc 4.4, 4.5).
CREATE TABLE fact_player_season (
    player_season_key       bigserial PRIMARY KEY,
    player_key              int NOT NULL REFERENCES dim_player,
    club_key                int NOT NULL REFERENCES dim_club,
    season_key              int NOT NULL REFERENCES dim_season,
    position_group_key      int NOT NULL REFERENCES dim_position_group,
    age                     smallint,
    matches_played          smallint,
    starts                  smallint,
    minutes                 int,
    nineties                numeric(6,2),
    team_matches_available  smallint,      -- availability denominator (4.5)
    goals                   smallint,  assists smallint,
    xg                      numeric(7,2),  npxg numeric(7,2),  xag numeric(7,2),
    shots                   smallint,  shots_on_target smallint,
    progressive_passes      smallint,  progressive_carries smallint,  progressive_received smallint,
    key_passes              smallint,  passes_into_final_third smallint, passes_into_penalty_area smallint,
    tackles                 smallint,  tackles_won smallint,  interceptions smallint,
    blocks                  smallint,  clearances smallint,  errors smallint,
    aerials_won             smallint,  aerials_lost smallint,
    touches                 int,       take_ons_attempted smallint, take_ons_won smallint,
    carries                 int,       carries_into_final_third smallint,
    sca                     smallint,  gca smallint,          -- SCA from Kaggle (4.4)
    gk_saves                smallint,  gk_goals_against smallint, gk_psxg numeric(7,2),
    sca_source              text,      -- 'kaggle' where filled
    is_value_filled         boolean NOT NULL DEFAULT false,  -- from reference/fbref_blank_fill.csv
    is_old_vintage          boolean NOT NULL DEFAULT false,  -- passing-file columns before 2022/23
    UNIQUE (player_key, club_key, season_key)
);

-- One row per club per season. Sporting return (scoping doc 5) plus season context.
CREATE TABLE fact_club_season (
    club_season_key         bigserial PRIMARY KEY,
    club_key                int NOT NULL REFERENCES dim_club,
    season_key              int NOT NULL REFERENCES dim_season,
    competition_key         int NOT NULL REFERENCES dim_competition,  -- which league that season
    matches                 smallint, wins smallint, draws smallint, losses smallint,
    goals_for               smallint, goals_against smallint, goal_difference smallint,
    points_from_results     smallint NOT NULL,
    position_computed       smallint,
    position_source         smallint,          -- Transfermarkt's own reported position
    has_known_deduction     boolean NOT NULL DEFAULT false,
    deduction_note          text,
    UNIQUE (club_key, season_key)
);

-- Market value history: the squad-value-growth pillar.
CREATE TABLE fact_player_valuation (
    valuation_key           bigserial PRIMARY KEY,
    player_key              int NOT NULL REFERENCES dim_player,
    valuation_date_key      int NOT NULL REFERENCES dim_date,
    club_key                int REFERENCES dim_club,
    market_value_eur        numeric(14,2) NOT NULL,
    UNIQUE (player_key, valuation_date_key)
);

-- One row per trophy won, each traceable to its source.
CREATE TABLE fact_club_trophy (
    trophy_key              bigserial PRIMARY KEY,
    club_key                int NOT NULL REFERENCES dim_club,
    season_key              int NOT NULL REFERENCES dim_season,
    competition_key         int NOT NULL REFERENCES dim_competition,
    trophy_category         text NOT NULL,   -- league_titles | domestic_cups | european_trophies
    source_system           text NOT NULL,   -- games | league table | wikidata
    source_ref              text NOT NULL,   -- game_id, table reference, or Q-item
    UNIQUE (club_key, season_key, competition_key)
);

-- Manager stints. MATCH-BASED boundaries: this source has no appointment dates.
CREATE TABLE fact_manager_tenure (
    tenure_key              bigserial PRIMARY KEY,
    club_key                int NOT NULL REFERENCES dim_club,
    manager_key             int NOT NULL REFERENCES dim_manager,
    stint_seq               smallint NOT NULL,
    first_match_date_key    int NOT NULL REFERENCES dim_date,
    last_match_date_key     int NOT NULL REFERENCES dim_date,
    first_season_key        int NOT NULL REFERENCES dim_season,
    last_season_key         int NOT NULL REFERENCES dim_season,
    matches                 smallint NOT NULL,
    league_matches          smallint NOT NULL,
    is_likely_caretaker     boolean NOT NULL,
    boundaries_are_match_based boolean NOT NULL DEFAULT true,  -- never present these as official dates
    UNIQUE (club_key, manager_key, stint_seq)
);

-- Derived bridge: a player's continuous time at one club, for ROI and trading profit.
-- Built from fact_transfer; flags say what is actually known rather than assuming a pairing.
CREATE TABLE bridge_player_club_spell (
    spell_key               bigserial PRIMARY KEY,
    player_key              int NOT NULL REFERENCES dim_player,
    club_key                int NOT NULL REFERENCES dim_club,
    arrival_transfer_key    bigint REFERENCES fact_transfer,   -- NULL when unknown
    departure_transfer_key  bigint REFERENCES fact_transfer,   -- NULL when still there or unknown
    start_date_key          int REFERENCES dim_date,
    end_date_key            int REFERENCES dim_date,
    purchase_fee_eur        numeric(14,2),
    sale_fee_eur            numeric(14,2),
    market_value_at_arrival numeric(14,2),
    market_value_at_exit    numeric(14,2),
    seasons_at_club         smallint,
    minutes_at_club         int,
    arrival_known           boolean NOT NULL,  -- false = academy, pre-2012, or missing history
    departure_known         boolean NOT NULL,
    arrival_is_academy      boolean,           -- distinguishable only where youth rows exist
    UNIQUE (player_key, club_key, start_date_key)
);
```

### Dimensions

```sql
CREATE TABLE dim_club (            -- from club_id_mapping + club_metadata
    club_key            serial PRIMARY KEY,
    fbref_team_id       char(8) UNIQUE,      -- natural key; NULL for out-of-scope clubs
    transfermarkt_id    int NOT NULL UNIQUE,
    club_name           text NOT NULL,
    country             text,                -- the CLUB's country: Cardiff = Wales, Monaco = Monaco
    city                text,
    crest_url           text,
    wikidata_qid        text,
    is_in_scope         boolean NOT NULL     -- false for youth sides, "Without Club", non-big-five clubs
);                                            -- league is NOT here: it changes by season

CREATE TABLE dim_player (
    player_key          serial PRIMARY KEY,
    fbref_player_id     char(8) UNIQUE,
    transfermarkt_id    int UNIQUE,
    player_name         text NOT NULL,
    nationality         text,
    birth_year          smallint,
    primary_position    text,
    foot                text
);

CREATE TABLE dim_season (
    season_key          serial PRIMARY KEY,
    season_label        text NOT NULL UNIQUE,   -- '2017/18'
    season_end_year     smallint NOT NULL,
    transfermarkt_year  smallint NOT NULL,      -- 2017 for 2017/18
    is_scored           boolean NOT NULL,       -- false for 2024/25, 2025/26, 2026/27
    total_fees_eur      numeric(16,2),          -- the 4.6 deflator inputs
    median_fee_eur      numeric(14,2),
    transfer_coverage_pct numeric(5,2)          -- see meta.transfer_coverage
);

CREATE TABLE dim_date (date_key int PRIMARY KEY, full_date date NOT NULL UNIQUE,
    year smallint, month smallint, day smallint, season_key int REFERENCES dim_season);

CREATE TABLE dim_competition (
    competition_key     serial PRIMARY KEY,
    source_code         text UNIQUE,        -- 'GB1', 'CL', 'FAC'
    competition_name    text NOT NULL,
    competition_type    text NOT NULL,      -- domestic_league | domestic_cup | european
    country             text
);

CREATE TABLE dim_manager (
    manager_key         serial PRIMARY KEY,
    manager_name        text NOT NULL UNIQUE,   -- resolved name, after manager_name_review.csv
    source_name         text NOT NULL,          -- as recorded, e.g. 'Luis García'
    was_name_split      boolean NOT NULL DEFAULT false
);

CREATE TABLE dim_position_group (   -- scoping doc 4.4
    position_group_key  serial PRIMARY KEY,
    position_group      text NOT NULL UNIQUE,   -- GK, CB, FB, CM, AM/W, FW
    fbref_positions     text NOT NULL           -- which FBref Pos values map here
);

CREATE TABLE dim_transfer_type (
    transfer_type_key   serial PRIMARY KEY,
    transfer_type       text NOT NULL UNIQUE,   -- see the rule below
    counts_as_signing   boolean NOT NULL,
    counts_as_spend     boolean NOT NULL
);
```

### Provenance (schema `meta`)

```sql
CREATE TABLE meta.source_manifest (      -- both Phase 1 manifests
    path text PRIMARY KEY, role text, origin text, source_url text,
    source_updated_at text, size_bytes bigint, sha256 char(64), rows bigint, notes text);

CREATE TABLE meta.decision (             -- the three review ledgers
    decision_key serial PRIMARY KEY, table_name text, entity_id text, field text,
    value text, decision text, decided_by text, decided_on date, reason text);

CREATE TABLE meta.transfer_coverage (    -- how complete the fee data is, per club-season
    club_key int REFERENCES dim_club, season_key int REFERENCES dim_season,
    players_with_history int, players_total int, coverage_pct numeric(5,2),
    page_checked boolean DEFAULT false, page_fees_eur numeric(14,2), table_fees_eur numeric(14,2),
    PRIMARY KEY (club_key, season_key));
```

## 4. The transfer-type rule

No longer a heuristic. Transfermarkt labels every row, and the pull keeps the
label, so `transfer_type` is read rather than inferred:

| Page label | Type | Signing? | Spend? | Rows in window |
|---|---|---|---|---|
| `EUR 12.00m` | `permanent_with_fee` | yes | yes | 8,245 |
| `Loan fee: EUR 3.00m` | `loan_with_fee` | no | yes (loan fee only) | 1,617 |
| `loan transfer` | `loan` | no | no | 9,648 |
| `End of loan` | `loan_return` | no | no | 11,596 |
| `free transfer` | `free` | yes | no | 7,460 |
| `?` or `-` | `undisclosed` | yes | no (counted, never valued) | 5,479 |

`is_type_heuristic` is therefore false for every page-sourced row, and the
reversal-pair rule survives only as a fallback for any row that exists solely in
the frozen table. This matters twice over: without separating them, the 11,596
end-of-loan rows would count as signings and make every club that ran a loan
army look like a heavy recruiter, and 14.4% of loans carry a fee (median
EUR 0.6m, up to EUR 20m) that would otherwise vanish.

## 5. How the Phase 1 tables map

| Reference table | Destination |
|---|---|
| `tm_club_transfers.csv` (the fee pull) | `fact_transfer` — the primary fee source |
| `transfermarkt_pages_manifest.csv` | `meta.source_manifest` — SHA-256 per page |
| `transfer_coverage_check.csv` | `meta.transfer_coverage` |
| `club_id_mapping` + `club_metadata` | `dim_club` |
| `club_season_points` | `fact_club_season` |
| `club_trophies_detail` | `fact_club_trophy` (the summary is a view) |
| `manager_tenures` + `manager_name_review` | `fact_manager_tenure`, `dim_manager` |
| `kaggle_fbref_crosswalk`, `fbref_blank_fill` | load-time lookups into `fact_player_season` |
| `club_mapping_review`, `club_metadata_review`, `manager_name_review` | `meta.decision` |
| both manifests | `meta.source_manifest` |
| frozen FBref CSVs | `fact_player_season` |
| frozen Transfermarkt CSVs | `fact_transfer`, `fact_player_valuation` |

## 6. Load order

1. `dim_season`, `dim_date`, `dim_competition`, `dim_position_group`, `dim_transfer_type`
2. `dim_club` (mapping + metadata, then out-of-scope clubs referenced by transfers)
3. `dim_player` (FBref players, then Transfermarkt-only players), `dim_manager`
4. `fact_player_season`, `fact_club_season`, `fact_club_trophy`, `fact_manager_tenure`
5. `fact_transfer`, then `fact_player_valuation`
6. `bridge_player_club_spell` (derived from `fact_transfer`)
7. `meta.*`, then `dim_season` deflators and `meta.transfer_coverage` recomputed

## 7. Notes carried into the load

- **Dates.** The pages give a season, not a transfer date, except for
  "End of loan" rows which carry one. Rows matched to the frozen table inherit
  its date; unmatched rows take the season's nominal start and set
  `date_is_estimated`. Nothing in the scoring depends on within-season dates.
- **Scope.** All 145 x 7 club-seasons were fetched, but only the 684 played in
  the big five count towards spend or the deflator. A relegated club's
  second-tier season is in the cache and must be filtered out.
- **The deflator** (scoping doc 4.6) now has real inputs per season: total
  in-scope spend and the median disclosed fee, ranging from EUR 4.50m in
  2017/18 to EUR 6.00m in 2019/20.
- **Loan fees are now available**, so the club page's spending breakdown can go
  back to three ways (permanent / loan / free) rather than the two-way split
  accepted when loan fees looked unrecoverable. Worth revisiting that decision.
- **461 rows have no counterpart club id** (moves to retirement, unknown or
  non-Transfermarkt clubs). They load with a null counterpart rather than being
  dropped.

## 8. Phase 3a: warehouse fixes before scoring (2026-09-13)

Four fixes landed before any scoring was written, and each has a check in
`sql/03_checks.sql`. The DDL sketches in section 3 were the design and are
not updated column by column. `sql/01_schema.sql` is the authority. After the
rebuild (`scripts/31_rebuild_warehouse.py`) and reload, all 44 checks pass.

### 8.1 A bug found on the way: transfer `source_ref` was not unique

The spell bridge links a spell to its arrival and departure transfers through
`source_ref`. For page-sourced transfers that reference was built as
`page <club>/<season>: player <id>`, which is the same for a player's move in
and move out on one club page. **7,801 references were shared by 15,602
transfers.** In the warehouse loaded at the Phase 2 commit, 7,781 spells
pointed their arrival key at a move into a *different* club, and 4,340
departure keys at a move out of one.

The fee amounts on each spell were computed from the correct events, so
purchase and sale fees were right. What was wrong were the keys, and anything
that joined through them, such as the season of a sale. The old check only
tested that a key was present, not that it pointed to the right move.

Fix: the reference now includes the direction (`... from>to`), the loader
stops if any reference repeats, `fact_transfer` has `UNIQUE (source_system,
source_ref)`, and three checks test correctness:

- the arrival goes into this club, for this player;
- the departure goes out of this club, for this player;
- no sale is counted in more than one spell.

A figure reported earlier, 3,429 departures claimed by several arrivals, came
from the same bug. The true count is **638**. In each case the latest arrival
keeps the sale, and earlier arrivals lose their departure link.

### 8.2 Transfermarkt ID relinks: 7 wrong, not 153

153 FBref players had a Transfermarkt ID that never appeared for that club and
season in Transfermarkt's appearances table.
`scripts/22_find_tm_id_relinks.py` sorted every one of them
(`reference/tm_id_check.csv`):

| Outcome | Players | How |
|---|---|---|
| Dictionary ID confirmed | 140 | 41 by the ID's other club-seasons; 99 by the ID having the same name and birth year. These are gaps in the appearances table (worst in 2021/22), not wrong IDs |
| Relinked | 7 | Kudus, Ugarte, Iliman Ndiaye, Giuliano Simeone, Antoine Valerio, Aliou Baldé, Jorge Moreno. Added to `reference/player_id_review.csv` |
| Unresolved | 6 | 4 name variants that could not be confirmed (Màrmol, Etebo, Jesús Santiago, Peter González) and 2 with 37 and 1 minutes |

Before the relinks, Ugarte's move to PSG and Kudus's move to West Ham did not
join to anything those players then did. A check now confirms both join.

### 8.3 Position groups: Transfermarkt detail, FBref season override

This replaces the Phase 2 rule of taking the first FBref position listed.
FBref only gives four broad positions (GK/DF/MF/FW), so that rule could not
separate full-backs from centre-backs or wingers from central midfielders.

1. **Base group** from Transfermarkt `sub_position`, mapped to the six groups
   (Left-/Right-Back to FB, Defensive/Central Midfield to CM, wingers, wide
   and attacking midfield to AM/W, Centre-Forward/Second Striker to FW).
2. **FBref override**, per season. Transfermarkt gives one position per
   player; FBref says where they played that season. If the two broad
   positions disagree, the season wins, and Transfermarkt's detail picks the
   nearest group. For example, a winger listed as DF becomes FB, and a
   centre-back listed as MF becomes CM.
3. **FBref fallback** where Transfermarkt has no position.

| `position_group_source` | Player-seasons |
|---|---|
| `transfermarkt` | 18,320 |
| `fbref_override` | 1,131 |
| `fbref_fallback` | 111 |

`tm_sub_position` and `fbref_position_raw` are both kept on every row, so any
assignment can be audited. All six groups exist in every season, and 99.92% of
minutes have a Transfermarkt-based group.

**Limitation.** `sub_position` is Transfermarkt's *current* position for the
player, not a position history. A player who moved from winger to full-back
is only caught where FBref's broad position disagrees too.

### 8.4 The spell bridge: departure-only spells and arrival sources

A sale with no matching arrival in the window used to have no spell. That hid
most academy graduates, and players bought before 2017/18, from any
trading-profit figure. Every spell now records where its arrival came from:

| `arrival_source` | Meaning | Spells |
|---|---|---|
| `window_transfer` | Arrival is a 2017/18+ transfer in `fact_transfer` | 34,664 |
| `pre_window_transfer` | Departure matched to a pre-2017 arrival in the frozen dataset (dates and value only, no fee key) | 1,404 |
| `none_recorded` | No arrival anywhere: academy graduates and missing history | 6,081 |

`arrival_known` is true only for `window_transfer`, and the DDL enforces this.
Departure-only spells carry no purchase fee, so the sale counts as pure
income. Where several departures claimed one pre-window arrival, the latest
kept it (137 cases). When two arrivals share an estimated start date, the copy
that carries the sale is the one kept; before that fix, 7 sales vanished.
Every fee sale from an in-scope club now has exactly one spell.

`market_value_at_arrival` and `market_value_at_exit` come from
`fact_player_valuation`: the nearest valuation on or before the date, within
365 days. They feed Pillar 3 (appreciation).

### 8.5 Squad value at season start (revenue proxy)

There is no revenue source, so scale is measured by squad market value.
`fact_club_season.squad_value_start_eur` is, for each club-season, the sum of
each player's latest Transfermarkt valuation on or before 1 July of the
season's start year, within the prior 365 days. A player counts for the club
recorded on that valuation, which is the club they were at on that date.
`squad_players_valued` gives the head count. The value is present for all 684
club-seasons.

This is measured at the start of the season on purpose. An end-of-season value
would already include the window's signings and results, so it could not
serve as a neutral scale control for spend.

### 8.6 Known limitation: undisclosed fees are excluded from spend

**2,988 arrivals** into in-scope clubs in the scored seasons have an
undisclosed fee. `fee_eur` stays NULL, and they count as signings but not as
spend. This is the rule from section 2, not an estimate, and it has a known
direction: **it understates spend most for the clubs that disclose least.**
Those clubs' ROI and cost-per-point figures therefore look better than they
are. The dashboard should show undisclosed moves alongside spend (the
`undisclosed_moves` count is already in the model) rather than hide them.
Imputing a fee from market value was considered and rejected: it would put
invented money into a table whose rule is that money is never inferred.
