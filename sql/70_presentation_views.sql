-- =============================================================================
-- Transfer Market Efficiency -- presentation layer for Power BI
--
-- Six views, each shaped for one part of the dashboard mockups
-- (docs/mockup-league-overview.png, docs/mockup-club-detail.png). Power BI
-- should read these, not join the warehouse tables itself: every business rule
-- (the fee rule, spend definitions, the manager snap, market-value lookups) is
-- applied here, once, where it is already proven.
--
-- Owned by R/70_presentation_views.R, which runs this file and then
-- sql/71_check_presentation_views.sql. Run it LAST, after the scoring scripts:
-- they rebuild the score tables with DROP ... CASCADE, which removes these views.
--
-- Money columns are euros (not millions) so Power BI can format them freely.
-- A NULL money value always means "undisclosed"; it is never zero-filled.
-- =============================================================================

DROP SCHEMA IF EXISTS presentation CASCADE;
CREATE SCHEMA presentation;

COMMENT ON SCHEMA presentation IS
    'Dashboard-shaped views over the warehouse (public) and the scores (score). Rebuilt by R/70_presentation_views.R.';

-- -----------------------------------------------------------------------------
-- 1. League overview: one row per club-season (page 1 scatter, rankings, KPIs)
-- -----------------------------------------------------------------------------
CREATE VIEW presentation.vw_league_overview AS
SELECT
    cs.club_season_key,
    c.club_key,
    c.club_name,
    c.country                                        AS club_country,
    c.city                                           AS club_city,
    c.crest_url,
    co.competition_name                              AS league,
    co.source_code                                   AS league_code,
    s.season_label                                   AS season,
    s.season_end_year,
    r.spend_eur                                      AS gross_spend_eur,
    t.income_eur                                     AS gross_sales_eur,
    r.spend_eur - t.income_eur                       AS net_spend_eur,
    r.n_undisclosed                                  AS undisclosed_signings,
    t.n_undisclosed_sales                            AS undisclosed_sales,
    cs.squad_value_start_eur,
    cs.matches,
    cs.points_from_results                           AS points,
    sp.points_per_match,
    cs.has_known_deduction,
    e.efficiency_z                                   AS efficiency_index,
    round((100 * percent_rank() OVER (ORDER BY e.efficiency_z))::numeric, 1) AS efficiency_score_0_100,
    e.recruitment_z,
    e.trading_z,
    e.value_growth_z,
    e.sporting_z,
    NOT e.is_recruitment_missing                     AS is_recruitment_scored,
    e.is_provisional,
    CASE WHEN e.is_provisional THEN 'Provisional' ELSE 'Scored' END AS season_status
FROM fact_club_season cs
JOIN dim_club c                            ON c.club_key = cs.club_key
JOIN dim_competition co                    ON co.competition_key = cs.competition_key
JOIN dim_season s                          ON s.season_key = cs.season_key
JOIN score.club_season_efficiency e        ON e.club_season_key = cs.club_season_key
JOIN score.club_season_recruitment r       ON r.club_season_key = cs.club_season_key
JOIN score.club_season_trading t           ON t.club_season_key = cs.club_season_key
JOIN score.club_season_sporting sp         ON sp.club_season_key = cs.club_season_key;

COMMENT ON VIEW presentation.vw_league_overview IS
    'One row per club-season in the big five, 2017/18-2023/24. gross_spend_eur = disclosed permanent and loan fees '
    'paid; gross_sales_eur = disclosed permanent fees and loan fees received; undisclosed moves are counted, never '
    'valued. efficiency_index is the composite z-score; efficiency_score_0_100 is its percentile across all 684 '
    'club-seasons (fixed, not filter-dependent). recruitment_z is NULL below the spend floor '
    '(is_recruitment_scored = false). Provisional = the 2022/23 and 2023/24 signing cohorts.';

-- -----------------------------------------------------------------------------
-- 2. Spend by league: one row per league-season (page 1 donut)
-- -----------------------------------------------------------------------------
CREATE VIEW presentation.vw_club_spend_by_league AS
SELECT
    league,
    league_code,
    season,
    season_end_year,
    count(*)                    AS clubs,
    sum(gross_spend_eur)        AS gross_spend_eur,
    sum(gross_sales_eur)        AS gross_sales_eur,
    sum(net_spend_eur)          AS net_spend_eur,
    sum(undisclosed_signings)   AS undisclosed_signings
FROM presentation.vw_league_overview
GROUP BY league, league_code, season, season_end_year;

COMMENT ON VIEW presentation.vw_club_spend_by_league IS
    'One row per league-season. Donut shares must be a Power BI measure (share of the SELECTED total), so they are '
    'not stored here. To drill from a league into its clubs, use vw_league_overview with a league > club hierarchy.';

-- -----------------------------------------------------------------------------
-- 3. Squad: one row per player per club per season (page 2 squad table)
-- -----------------------------------------------------------------------------
CREATE VIEW presentation.vw_current_squad AS
SELECT
    f.player_season_key,
    c.club_key,
    c.club_name,
    s.season                                        AS season,
    s.season_end_year,
    (s.season_end_year = max(s.season_end_year) OVER (PARTITION BY c.club_key)) AS is_latest_season_for_club,
    p.player_name,
    p.nationality                                   AS nationality_code,
    g.position_group,
    coalesce(f.tm_sub_position, 'Unknown')          AS position_detail,
    CASE f.tm_sub_position
        WHEN 'Goalkeeper'         THEN 'GK'
        WHEN 'Centre-Back'        THEN 'CB'
        WHEN 'Left-Back'          THEN 'LB'
        WHEN 'Right-Back'         THEN 'RB'
        WHEN 'Defensive Midfield' THEN 'DM'
        WHEN 'Central Midfield'   THEN 'CM'
        WHEN 'Attacking Midfield' THEN 'AM'
        WHEN 'Left Midfield'      THEN 'LM'
        WHEN 'Right Midfield'     THEN 'RM'
        WHEN 'Left Winger'        THEN 'LW'
        WHEN 'Right Winger'       THEN 'RW'
        WHEN 'Second Striker'     THEN 'SS'
        WHEN 'Centre-Forward'     THEN 'CF'
        ELSE g.position_group
    END                                             AS position_short,
    f.age,
    f.matches_played,
    f.starts,
    f.minutes,
    q.quality_pctile                                AS quality_percentile,
    mv.market_value_eur,
    mv.market_value_date
FROM fact_player_season f
JOIN dim_player p           ON p.player_key = f.player_key
JOIN dim_club c             ON c.club_key = f.club_key
JOIN dim_position_group g   ON g.position_group_key = f.position_group_key
JOIN (SELECT season_key, season_label AS season, season_end_year,
             (to_char(season_end_date, 'YYYYMMDD'))::int AS end_key,
             (to_char(season_end_date - 365, 'YYYYMMDD'))::int AS floor_key
      FROM dim_season) s    ON s.season_key = f.season_key
LEFT JOIN score.player_quality q ON q.player_season_key = f.player_season_key
LEFT JOIN LATERAL (
    -- latest valuation on or before the season's end, within a year; matched by player and date only,
    -- never by the valuation's club field (see docs/phase-3-scoring.md, known limitations)
    SELECT v.market_value_eur, d.full_date AS market_value_date
    FROM fact_player_valuation v
    JOIN dim_date d ON d.date_key = v.valuation_date_key
    WHERE v.player_key = f.player_key
      AND v.valuation_date_key <= s.end_key
      AND v.valuation_date_key >= s.floor_key
    ORDER BY v.valuation_date_key DESC
    LIMIT 1) mv ON true;

COMMENT ON VIEW presentation.vw_current_squad IS
    'One row per player who played for a club in a season (FBref appearances). market_value_eur = latest '
    'Transfermarkt valuation on or before that season''s end (30 June), within 365 days; NULL if none. The '
    '"current squad" for a multi-season selection is a Power BI filter (season_end_year = the latest selected); '
    'is_latest_season_for_club marks the default. quality_percentile is NULL for player-seasons under 900 minutes.';

-- -----------------------------------------------------------------------------
-- 4. Transfers: one row per transfer per in-scope club involved (page 2 largest transfers)
-- -----------------------------------------------------------------------------
CREATE VIEW presentation.vw_transfers_detail AS
WITH sides AS (
    SELECT t.transfer_key, t.to_club_key AS club_key, t.from_club_key AS other_club_key, 'in' AS side
    FROM fact_transfer t
    UNION ALL
    SELECT t.transfer_key, t.from_club_key, t.to_club_key, 'out'
    FROM fact_transfer t
)
SELECT
    t.transfer_key,
    c.club_key,
    c.club_name,
    s.season_label                                      AS season,
    s.season_end_year,
    d.full_date                                         AS transfer_date,
    t.date_is_estimated,
    p.player_name,
    CASE WHEN x.side = 'in'  AND tt.transfer_type IN ('permanent_with_fee', 'free', 'undisclosed') THEN 'Bought'
         WHEN x.side = 'out' AND tt.transfer_type IN ('permanent_with_fee', 'free', 'undisclosed') THEN 'Sold'
         WHEN x.side = 'in'  THEN 'Loan in'
         ELSE 'Loan out' END                            AS direction,
    CASE WHEN tt.transfer_type IN ('loan', 'loan_with_fee') THEN 'Loan'
         WHEN x.side = 'in' THEN 'Bought' ELSE 'Sold' END AS direction_filter,
    tt.transfer_type,
    CASE tt.transfer_type
        WHEN 'permanent_with_fee' THEN 'Permanent'
        WHEN 'undisclosed'        THEN 'Permanent'
        WHEN 'free'               THEN 'Free'
        ELSE 'Loan' END                                 AS transfer_category,
    CASE tt.transfer_type
        WHEN 'permanent_with_fee' THEN 'Fee'
        WHEN 'loan_with_fee'      THEN 'Loan fee'
        WHEN 'free'               THEN 'Free'
        WHEN 'loan'               THEN 'Loan (no fee)'
        WHEN 'undisclosed'        THEN 'Undisclosed'
    END                                                 AS fee_status,
    t.fee_eur,
    t.is_fee_disclosed,
    oc.club_name                                        AS other_club_name,
    oc.is_in_scope                                      AS other_club_in_scope,
    EXISTS (SELECT 1 FROM fact_club_season cs
            WHERE cs.club_key = c.club_key AND cs.season_key = t.season_key) AS is_big_five_season
FROM sides x
JOIN fact_transfer t         ON t.transfer_key = x.transfer_key
JOIN dim_transfer_type tt    ON tt.transfer_type_key = t.transfer_type_key
JOIN dim_club c              ON c.club_key = x.club_key AND c.is_in_scope
JOIN dim_club oc             ON oc.club_key = x.other_club_key
JOIN dim_player p            ON p.player_key = t.player_key
JOIN dim_season s            ON s.season_key = t.season_key
JOIN dim_date d              ON d.date_key = t.transfer_date_key
WHERE tt.transfer_type <> 'loan_return';

COMMENT ON VIEW presentation.vw_transfers_detail IS
    'One row per transfer per in-scope club involved, seen from that club: a move between two in-scope clubs '
    'appears twice (Bought for one, Sold for the other). Loan returns are excluded. fee_eur is NULL only when '
    'undisclosed (fee_status = Undisclosed); free transfers and fee-less loans are a real 0. direction_filter '
    'drives the All / Bought / Sold / Loan buttons. Seasons 2017/18-2023/24 only: later windows are not loaded.';

-- -----------------------------------------------------------------------------
-- 5. Cash flow and manager tenure: one row per club-season (page 2 combined panel)
-- -----------------------------------------------------------------------------
CREATE VIEW presentation.vw_cashflow_and_tenure AS
WITH overlap AS (
    -- every manager who took a match in the season, with how much of the season their matches spanned
    SELECT cs.club_season_key, m.manager_name, t.is_likely_caretaker,
           fd.full_date AS first_match, ld.full_date AS last_match,
           (least(ld.full_date, s.season_end_date) - greatest(fd.full_date, s.season_start_date)) AS days_in_charge
    FROM fact_club_season cs
    JOIN dim_season s          ON s.season_key = cs.season_key
    JOIN fact_manager_tenure t ON t.club_key = cs.club_key
    JOIN dim_manager m         ON m.manager_key = t.manager_key
    JOIN dim_date fd           ON fd.date_key = t.first_match_date_key
    JOIN dim_date ld           ON ld.date_key = t.last_match_date_key
    WHERE fd.full_date <= s.season_end_date AND ld.full_date >= s.season_start_date
),
snapped AS (
    -- the season belongs to the manager whose matches spanned most of it; caretakers only if nobody else
    SELECT DISTINCT ON (club_season_key) club_season_key, manager_name, is_likely_caretaker
    FROM overlap
    ORDER BY club_season_key, is_likely_caretaker, days_in_charge DESC, first_match DESC
),
everyone AS (
    SELECT club_season_key,
           string_agg(manager_name || CASE WHEN is_likely_caretaker THEN ' (caretaker)' ELSE '' END, ' > '
                      ORDER BY first_match) AS managers_in_season,
           count(*) AS managers_count
    FROM overlap GROUP BY club_season_key
),
base AS (
    SELECT o.club_season_key, o.club_key, o.club_name, o.league, o.season, o.season_end_year,
           o.gross_spend_eur AS fees_paid_eur, o.gross_sales_eur AS fees_received_eur,
           o.net_spend_eur, o.gross_sales_eur - o.gross_spend_eur AS net_transfer_balance_eur,
           o.undisclosed_signings, o.undisclosed_sales,
           o.efficiency_index, o.efficiency_score_0_100, o.is_provisional, o.season_status,
           sn.manager_name, sn.is_likely_caretaker AS manager_is_caretaker,
           ev.managers_in_season, ev.managers_count,
           lag(sn.manager_name) OVER w AS previous_manager,
           lag(o.season_end_year) OVER w AS previous_season_end_year
    FROM presentation.vw_league_overview o
    JOIN snapped sn  ON sn.club_season_key = o.club_season_key
    JOIN everyone ev ON ev.club_season_key = o.club_season_key
    WINDOW w AS (PARTITION BY o.club_key ORDER BY o.season_end_year)
)
SELECT club_season_key, club_key, club_name, league, season, season_end_year,
       fees_paid_eur, fees_received_eur, net_spend_eur, net_transfer_balance_eur,
       undisclosed_signings, undisclosed_sales,
       efficiency_index, efficiency_score_0_100, is_provisional, season_status,
       manager_name, manager_is_caretaker, managers_in_season, managers_count,
       -- a new band starts when the manager changes or the club returns after a season outside the big five
       sum(CASE WHEN previous_manager IS DISTINCT FROM manager_name
                  OR previous_season_end_year IS DISTINCT FROM season_end_year - 1 THEN 1 ELSE 0 END)
           OVER (PARTITION BY club_key ORDER BY season_end_year)   AS manager_band_seq
FROM base;

COMMENT ON VIEW presentation.vw_cashflow_and_tenure IS
    'One row per club-season. Fees are disclosed only. net_spend_eur = paid - received (positive = spent more); '
    'net_transfer_balance_eur = received - paid, for the figure printed above each bar pair. manager_name is '
    'snapped to the season (scoping doc 4.9): the non-caretaker whose matches spanned most of the season, by date '
    'overlap, since this source has no appointment dates. managers_in_season lists everyone in order, for tooltips. '
    'manager_band_seq groups consecutive seasons under one manager into a single tenure band.';

-- -----------------------------------------------------------------------------
-- 6. Honours: one row per in-scope club (page 2 honours panel)
-- -----------------------------------------------------------------------------
CREATE VIEW presentation.vw_club_trophies_honors AS
SELECT
    c.club_key,
    c.club_name,
    c.country                                                                    AS club_country,
    'Trophies since 2017/18'                                                     AS window_label,
    count(ft.trophy_key) FILTER (WHERE ft.trophy_category = 'league_titles')     AS league_titles,
    count(ft.trophy_key) FILTER (WHERE ft.trophy_category = 'domestic_cups')     AS domestic_cups,
    count(ft.trophy_key) FILTER (WHERE ft.trophy_category = 'european_trophies') AS european_trophies,
    count(ft.trophy_key)                                                         AS total_trophies
FROM dim_club c
LEFT JOIN (fact_club_trophy ft
           JOIN dim_season s ON s.season_key = ft.season_key AND s.is_scored)  -- window filter inside the join,
       ON ft.club_key = c.club_key                                             -- so a club with none still appears
WHERE c.is_in_scope
GROUP BY c.club_key, c.club_name, c.country;

COMMENT ON VIEW presentation.vw_club_trophies_honors IS
    'One row per in-scope club, zero-filled. Counts cover the scored window only (2017/18-2023/24), matching the '
    '"Trophies since 2017/18" label. Categories: league titles, domestic cups (both cups where a country has two), '
    'European trophies (Champions League, Europa League, Conference League).';
