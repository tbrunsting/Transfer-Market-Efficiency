# Phase 4 — presentation layer for Power BI

Power BI reads six views in the `presentation` schema, each shaped for one part
of the dashboard mockups (`docs/mockup-league-overview.png`,
`docs/mockup-club-detail.png`). The business rules — the fee rule, spend
definitions, the manager snap, market-value lookups — are applied once, in SQL,
where they are already verified. Power BI should not need to join the warehouse
tables or re-derive any of them.

| View | Rows | Grain | Dashboard element |
|---|---|---|---|
| `presentation.vw_league_overview` | 684 | club-season | Page 1: scatter, KPIs, best/worst rankings |
| `presentation.vw_club_spend_by_league` | 35 | league-season | Page 1: spend-by-league donut |
| `presentation.vw_current_squad` | 19,562 | player-club-season | Page 2: squad table |
| `presentation.vw_transfers_detail` | 32,150 | transfer per club involved | Page 2: largest transfers, spending breakdown |
| `presentation.vw_cashflow_and_tenure` | 684 | club-season | Page 2: cash flow and manager tenure panel |
| `presentation.vw_club_trophies_honors` | 145 | club | Page 2: honours |

## Build and verify

```
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\70_presentation_views.R
```

Run it **last**, after the scoring scripts (10 to 60). The score tables are
rebuilt with `DROP ... CASCADE`, which removes any view that depends on them;
R/70 recreates the whole schema. This was tested end to end: rerunning the
composite dropped the three views that read it, and R/70 restored all six.

`sql/71_check_presentation_views.sql` passes 40 of 40. Every view is reconciled
back to what it summarises, computed independently:

- gross spend and gross sales reconcile to the euro with Pillars 1 and 2, both
  through the overview and separately through the individual transfer rows;
- the efficiency index is the composite, unchanged;
- one squad row per player-season, no duplication at the player-club-season
  grain, exactly one primary club per player-season, every market value dated
  within the year before that season ended, and values present for 99.9% of
  minutes;
- one transfer row per in-scope club involved, a NULL fee exactly when
  undisclosed, free transfers a real €0;
- every club-season has a snapped manager, and trophies reconcile to the 96 in
  the window;
- football anchors: Bellingham's 2023/24 move appears as Bought for Real Madrid
  and Sold for Dortmund; Chelsea 2022/23 is Potter, Manchester United 2021/22 is
  Rangnick; Real Madrid won three European trophies in the window.

### The headline metric: rank by `efficiency_index`

`efficiency_index` is the composite z-score from the scoring layer (scoping doc
section 5: four pillars, each z-scored, combined with weights). It is the field
to rank by, everywhere.

`efficiency_score_0_100` is that same number rescaled **linearly** onto 0-100:

```
100 x (efficiency_index - min) / (max - min)     -- min -3.38, max +4.95 across the 684 club-seasons
```

**This replaced a percentile rank (changed 2026-09-27).** The percentile was
monotone at club-season level but not linear, so averaging it across seasons
reordered clubs: Lille averaged 1.448 on the index but 81.6 on the percentile,
while Atalanta averaged 1.127 and 85.0 — opposite winners from the same data.
Rank compresses the top and stretches the bottom, so a volatile club (a −1.34
season alongside a +2.70 one) is punished by the average of ranks. It affected
307 of the 4,465 club pairs; Brighton moved 5th to 18th and Liverpool 7th to
35th.

An affine rescale cannot do this: averages of a linear transform preserve order
at any level of aggregation. Three checks enforce it — identical ranking at
club-season level, identical ranking once averaged per club, and that the score
really is an affine function of the index. The value is deliberately not
rounded, because rounding makes the scale a step function that can still flip
near-ties; format it in Power BI instead.

The practical effect: scores now cluster roughly 30-58 for most clubs rather
than spreading across the full 0-100, because a z-distribution is bunched
around its mean. Lille tops it at 58.0.

## Conventions

- **Money is in euros**, not millions, so Power BI can format it freely.
- **NULL money means undisclosed** and is never zero-filled. Free transfers and
  fee-less loans are a real 0; the `fee_status` column says which is which.
- **Season** is the label (`2021/22`); `season_end_year` (2022) sorts and filters.
- **Provisional** seasons (2022/23, 2023/24) are flagged on every club-season
  view, as `is_provisional` and as a `season_status` text for legends.

## The views

### 1. `vw_league_overview` — one row per club-season

| Column | Type | Meaning |
|---|---|---|
| club_season_key, club_key | int | keys |
| club_name, club_country, club_city, crest_url | text | club identity (country is the club's own: Monaco, Wales) |
| league, league_code | text | e.g. Premier League, GB1 |
| season, season_end_year | text, int | 2021/22, 2022 |
| gross_spend_eur | float | disclosed permanent and loan fees paid |
| gross_sales_eur | float | disclosed permanent fees and loan fees received |
| net_spend_eur | float | spend − sales (positive = spent more) |
| undisclosed_signings, undisclosed_sales | int | moves counted but never valued |
| squad_value_start_eur | numeric | squad value on 1 July (the scale control) |
| matches, points, points_per_match | int, int, float | results points; deductions not applied |
| has_known_deduction | bool | Juventus 2022/23, Everton 2023/24 |
| efficiency_index | float | the composite z-score |
| efficiency_score_0_100 | numeric | the same index rescaled linearly onto 0-100 for display; ranks identically to `efficiency_index` at every level |
| recruitment_z, trading_z, value_growth_z, sporting_z | float | the four pillars; recruitment_z is NULL below the spend floor |
| is_recruitment_scored | bool | false for the 69 club-seasons below the spend floor |
| is_provisional, season_status | bool, text | provisional cohorts |

### 2. `vw_club_spend_by_league` — one row per league-season

league, league_code, season, season_end_year, clubs, gross_spend_eur,
gross_sales_eur, net_spend_eur, undisclosed_signings.

Donut **shares are not stored**: they must be a Power BI measure, because a
share has to be of the *selected* seasons and leagues. To drill from a league
into its clubs, use `vw_league_overview` with a league > club hierarchy.

### 3. `vw_current_squad` — one row per player per club per season

| Column | Type | Meaning |
|---|---|---|
| player_season_key, player_key, club_key | int | keys — group players by `player_key`, never by name |
| club_name, season, season_end_year | text, text, int | |
| is_latest_season_for_club | bool | the club's most recent season in the window |
| clubs_in_season | int | how many clubs this player played for that season (2 for a mid-season move) |
| is_primary_club_for_season | bool | true for the club where he played the most minutes that season |
| player_name | text | |
| nationality_code | text | 3-letter code (all but 6 players who played) |
| position_group | text | the six scoring groups |
| position_detail, position_short | text | Transfermarkt position, and its abbreviation (GK, CB, LB, RB, DM, CM, AM, LM, RM, LW, RW, SS, CF) |
| age, matches_played, starts, minutes | int | |
| quality_percentile | float | player quality within position group and season; NULL under 900 minutes |
| market_value_eur, market_value_date | numeric, date | latest valuation on or before the season's end, within a year |

**A player can appear twice in one season, and both rows are correct.** The
grain is player per *club* per season, so a mid-season transfer gives one row
per club: Mbappé 2017/18 is AS Monaco (74 minutes) and PSG (2,094). 895
player-seasons are split this way. Both rows carry the same market value,
because the value is looked up by player and date, not per club.

Two consequences for Power BI:

- **A league-wide total would count a mover twice.** Filter
  `is_primary_club_for_season` to count each player once: that is about €2–3bn
  per season on total squad value. The club-level squad table needs no filter,
  since it is already one club.
- **Never group by `player_name`.** 49 names belong to more than one player
  (Rodri, Marcelo, Danilo, Nacho, Adama Traoré, Raúl García). Group by
  `player_key`.

A duplicate check must therefore include the club:

```sql
SELECT player_key, club_key, season, count(*)
FROM presentation.vw_current_squad
GROUP BY 1, 2, 3 HAVING count(*) > 1;   -- returns zero rows
```

Grouping only by `player_name, season` returns 946 groups, which are the 895
mid-season moves plus shared names, not duplication.

"Current squad" for a multi-season selection is a Power BI filter:
`season_end_year = MAX(selected season_end_year)`. `is_latest_season_for_club`
is the default when nothing is selected. Market values are matched by player
and date only, never by the valuation's unreliable club field.

### 4. `vw_transfers_detail` — one row per transfer per in-scope club involved

| Column | Type | Meaning |
|---|---|---|
| transfer_key, club_key | int | a move between two in-scope clubs appears twice, once from each side |
| club_name, season, season_end_year | text, text, int | |
| transfer_date, date_is_estimated | date, bool | most page dates are estimated season starts |
| player_name | text | |
| direction | text | Bought, Sold, Loan in, Loan out |
| direction_filter | text | Bought, Sold, Loan — the mockup's buttons |
| transfer_type | text | raw type |
| transfer_category | text | Permanent, Free, Loan |
| fee_status | text | Fee, Loan fee, Free, Loan (no fee), Undisclosed |
| fee_eur, is_fee_disclosed | numeric, bool | NULL only when undisclosed |
| other_club_name, other_club_in_scope | text, bool | the counterparty |
| is_big_five_season | bool | whether the club was in the big five that season |

Loan returns are excluded. The page-2 spending breakdown comes from here:
permanent fees and loan fees as euros, and free signings as a **count** (they
cost €0).

### 5. `vw_cashflow_and_tenure` — one row per club-season

| Column | Type | Meaning |
|---|---|---|
| club_season_key, club_key, club_name, league, season, season_end_year | | |
| fees_paid_eur, fees_received_eur | float | disclosed only |
| net_spend_eur | float | paid − received |
| net_transfer_balance_eur | float | received − paid, for the figure printed above each bar pair |
| undisclosed_signings, undisclosed_sales | int | |
| efficiency_index, efficiency_score_0_100 | float, numeric | the line above the bars |
| is_provisional, season_status | bool, text | |
| manager_name, manager_is_caretaker | text, bool | the manager the season is snapped to |
| managers_in_season, managers_count | text, int | everyone who took a match, in order, e.g. "Thomas Tuchel > Graham Potter > Bruno Saltor (caretaker) > Frank Lampard" |
| manager_band_seq | int | consecutive seasons under one manager share a number: one colour band each |

**The manager snap (scoping doc 4.9).** This source has match-based tenures but
no appointment dates. A season is assigned to the non-caretaker whose matches
spanned most of it, by date overlap; a caretaker is chosen only if nobody else
managed (never, in practice). 259 of 684 club-seasons had more than one
manager. The rule is approximate by nature: Chelsea 2020/21 snaps to Lampard
(September to January) rather than Tuchel (January to May), whose spans are
almost equal. That is why `managers_in_season` is exposed for tooltips. A band
also restarts when a club returns to the big five after a season away.

### 6. `vw_club_trophies_honors` — one row per in-scope club

club_key, club_name, club_country, window_label ("Trophies since 2017/18"),
league_titles, domestic_cups, european_trophies, total_trophies.

Zero-filled for clubs that won nothing; counts cover the scored window only.

## Where the mockups and the data differ — decided (Tyler, 2026-09-16)

The mockups were drawn before the build. Four points needed a dashboard
decision rather than a view change.

1. **Scatter axes: points per match against gross spend, with the efficiency
   index as colour or bubble size.** Not the mockup's "points per €100M spent":
   dividing by spend makes the cheapest clubs look best by construction, which
   is exactly the bias the scoring layer's log transform and spend floor were
   built to remove. Columns: `points_per_match`, `gross_spend_eur`,
   `efficiency_index` in `vw_league_overview`.

2. **The 2024/25–2025/26 recency layer is deferred.** `fact_transfer` holds
   2017/18–2023/24 only, because the club-page pull covered the scored window.
   The club page shows the scored window and nothing after it for now.
   *Note:* scoping doc section 4.8 still describes this layer as shown on the
   club page, flagged unscored, so this is a departure from the plan rather than
   part of it. *Revisit criterion:* if time allows, fetch the 2024/25–2026/27
   club pages (plain HTTP, the same pull as before), load them, and add the layer
   with its unscored flag; otherwise amend 4.8 to say the layer was deferred.

3. **Spending breakdown: permanent fees and loan fees in real euros, free
   transfers as a count.** The mockup's €26.0M for free transfers is not a
   simplification but an error: a free transfer costs nothing. From
   `vw_transfers_detail`: euros are `sum(fee_eur)` where `fee_status` is Fee or
   Loan fee; free signings are the count where `fee_status = 'Free'`.

4. **Club score labels: "Transfer Efficiency Score"**, with the four pillars
   under their real names as sub-scores: **Recruitment ROI**, **Trading
   Profit**, **Squad Value Growth**, **Sporting Return**. Not the mockup's
   "Transfer Activity Score": name it what it measures. Columns:
   `efficiency_score_0_100` (headline), `recruitment_z`, `trading_z`,
   `value_growth_z`, `sporting_z`.
