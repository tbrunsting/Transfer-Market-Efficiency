-- =============================================================================
-- Transfer Market Efficiency -- checks for the presentation views
--
-- Every view is reconciled back to the tables it summarises, computed
-- independently here, so a dashboard total can always be traced to a verified
-- score or warehouse figure. Also a few known football facts as anchors.
--
--   psql -d transfer_market -f sql/71_check_presentation_views.sql
-- =============================================================================

WITH checks AS (

-- ---------- 1. league overview ----------------------------------------------
SELECT 'overview: one row per big-five club-season' AS check_name,
       (SELECT count(*) FROM fact_club_season)::text AS expected,
       (SELECT count(*) FROM presentation.vw_league_overview)::text AS actual
UNION ALL
SELECT 'overview: gross spend reconciles to Pillar 1 spend (EUR)',
       (SELECT round(sum(spend_eur))::text FROM score.club_season_recruitment),
       (SELECT round(sum(gross_spend_eur))::text FROM presentation.vw_league_overview)
UNION ALL
SELECT 'overview: gross sales reconcile to Pillar 2 income (EUR)',
       (SELECT round(sum(income_eur))::text FROM score.club_season_trading),
       (SELECT round(sum(gross_sales_eur))::text FROM presentation.vw_league_overview)
UNION ALL
SELECT 'overview: efficiency index is the composite, unchanged (rows that differ)', '0',
       count(*)::text FROM presentation.vw_league_overview o
       JOIN score.club_season_efficiency e USING (club_season_key)
       WHERE o.efficiency_index IS DISTINCT FROM e.efficiency_z
UNION ALL
SELECT 'overview: 0-100 score spans 0 to 100', '0-100',
       min(efficiency_score_0_100)::int || '-' || max(efficiency_score_0_100)::int FROM presentation.vw_league_overview
UNION ALL
SELECT 'overview: no NULLs outside recruitment_z below the spend floor', '0',
       count(*)::text FROM presentation.vw_league_overview
       WHERE club_name IS NULL OR league IS NULL OR gross_spend_eur IS NULL OR gross_sales_eur IS NULL
          OR efficiency_index IS NULL OR points_per_match IS NULL
          OR (recruitment_z IS NULL) <> (NOT is_recruitment_scored)
UNION ALL
SELECT 'overview: provisional seasons are 2022/23 and 2023/24 only', '0',
       count(*)::text FROM presentation.vw_league_overview
       WHERE is_provisional <> (season IN ('2022/23', '2023/24'))

-- ---------- 2. spend by league ----------------------------------------------
UNION ALL
SELECT 'spend by league: 35 league-seasons', '35', count(*)::text FROM presentation.vw_club_spend_by_league
UNION ALL
SELECT 'spend by league: totals equal the overview', 'match',
       CASE WHEN abs((SELECT sum(gross_spend_eur) FROM presentation.vw_club_spend_by_league)
                   - (SELECT sum(gross_spend_eur) FROM presentation.vw_league_overview)) < 1
             AND (SELECT sum(clubs) FROM presentation.vw_club_spend_by_league) = 684
            THEN 'match' ELSE 'differs' END

-- ---------- 3. squad --------------------------------------------------------
UNION ALL
SELECT 'squad: one row per player-season',
       (SELECT count(*) FROM fact_player_season)::text,
       (SELECT count(*) FROM presentation.vw_current_squad)::text
UNION ALL
SELECT 'squad: every market value dated within the year before that season ended', '0',
       count(*)::text FROM presentation.vw_current_squad q JOIN dim_season s ON s.season_label = q.season
       WHERE q.market_value_date IS NOT NULL
         AND (q.market_value_date > s.season_end_date OR q.market_value_date < s.season_end_date - 365)
UNION ALL
SELECT 'squad: market value present for at least 95% of minutes played', '>=95',
       round(100.0 * sum(minutes) FILTER (WHERE market_value_eur IS NOT NULL) / sum(minutes), 1)::text
FROM presentation.vw_current_squad
UNION ALL
SELECT 'squad: exactly one latest season flagged per club (clubs that differ)', '0',
       count(*)::text FROM (
           SELECT club_key FROM presentation.vw_current_squad GROUP BY club_key
           HAVING count(DISTINCT season_end_year) FILTER (WHERE is_latest_season_for_club) <> 1) x

-- ---------- 4. transfers ----------------------------------------------------
UNION ALL
SELECT 'transfers: one row per in-scope club side, loan returns excluded',
       (SELECT (count(*) FILTER (WHERE tc.is_in_scope) + count(*) FILTER (WHERE fc.is_in_scope))::text
        FROM fact_transfer t JOIN dim_transfer_type tt USING (transfer_type_key)
        JOIN dim_club tc ON tc.club_key = t.to_club_key JOIN dim_club fc ON fc.club_key = t.from_club_key
        WHERE tt.transfer_type <> 'loan_return'),
       (SELECT count(*)::text FROM presentation.vw_transfers_detail)
UNION ALL
SELECT 'transfers: fees paid in big-five seasons reconcile to Pillar 1 spend (EUR)',
       (SELECT round(sum(spend_eur))::text FROM score.club_season_recruitment),
       (SELECT round(sum(fee_eur))::text FROM presentation.vw_transfers_detail
        WHERE direction IN ('Bought', 'Loan in') AND is_big_five_season
          AND transfer_type IN ('permanent_with_fee', 'loan_with_fee'))
UNION ALL
SELECT 'transfers: fees received in big-five seasons reconcile to Pillar 2 income (EUR)',
       (SELECT round(sum(income_eur))::text FROM score.club_season_trading),
       (SELECT round(sum(fee_eur))::text FROM presentation.vw_transfers_detail
        WHERE direction IN ('Sold', 'Loan out') AND is_big_five_season
          AND transfer_type IN ('permanent_with_fee', 'loan_with_fee'))
UNION ALL
SELECT 'transfers: fee is NULL exactly when undisclosed (rows that break the rule)', '0',
       count(*)::text FROM presentation.vw_transfers_detail
       WHERE (fee_eur IS NULL) <> (fee_status = 'Undisclosed') OR (fee_eur IS NULL) = is_fee_disclosed
UNION ALL
SELECT 'transfers: free transfers are a real zero, never NULL', '0',
       count(*)::text FROM presentation.vw_transfers_detail WHERE fee_status = 'Free' AND fee_eur IS DISTINCT FROM 0
UNION ALL
SELECT 'transfers: every row has a direction and a filter bucket', '0',
       count(*)::text FROM presentation.vw_transfers_detail
       WHERE direction IS NULL OR direction_filter NOT IN ('Bought', 'Sold', 'Loan') OR fee_status IS NULL
UNION ALL
SELECT 'transfers: a move between two in-scope clubs appears once per club (Bellingham to Real Madrid)', 'Bought/Sold',
       string_agg(direction, '/' ORDER BY direction) FROM presentation.vw_transfers_detail
       WHERE player_name = 'Jude Bellingham' AND season = '2023/24' AND other_club_in_scope

-- ---------- 5. cash flow and tenure -----------------------------------------
UNION ALL
SELECT 'cash flow: one row per club-season', '684', count(*)::text FROM presentation.vw_cashflow_and_tenure
UNION ALL
SELECT 'cash flow: every season has a manager', '0',
       count(*)::text FROM presentation.vw_cashflow_and_tenure WHERE manager_name IS NULL OR managers_count < 1
UNION ALL
SELECT 'cash flow: fees and index match the overview (rows that differ)', '0',
       count(*)::text FROM presentation.vw_cashflow_and_tenure c
       JOIN presentation.vw_league_overview o USING (club_season_key)
       WHERE c.fees_paid_eur IS DISTINCT FROM o.gross_spend_eur OR c.fees_received_eur IS DISTINCT FROM o.gross_sales_eur
          OR c.efficiency_index IS DISTINCT FROM o.efficiency_index
          OR c.net_transfer_balance_eur IS DISTINCT FROM -c.net_spend_eur
UNION ALL
SELECT 'cash flow: a caretaker is snapped only where nobody else managed (rows that break it)', '0',
       count(*)::text FROM presentation.vw_cashflow_and_tenure
       WHERE manager_is_caretaker
         AND EXISTS (SELECT 1 FROM unnest(string_to_array(managers_in_season, ' > ')) AS m(name)
                     WHERE m.name NOT LIKE '%(caretaker)')
UNION ALL
SELECT 'cash flow: snap anchor - Chelsea 2022/23 is Potter (after Tuchel, before Lampard)', 'Graham Potter',
       max(manager_name) FROM presentation.vw_cashflow_and_tenure WHERE club_name = 'Chelsea FC' AND season = '2022/23'
UNION ALL
SELECT 'cash flow: snap anchor - Manchester United 2021/22 is Rangnick, not Solskjaer', 'Ralf Rangnick',
       max(manager_name) FROM presentation.vw_cashflow_and_tenure WHERE club_name = 'Manchester United' AND season = '2021/22'
UNION ALL
SELECT 'cash flow: tenure bands start at 1 for every club', '0',
       count(*)::text FROM (SELECT club_key FROM presentation.vw_cashflow_and_tenure GROUP BY club_key
                            HAVING min(manager_band_seq) <> 1) x

-- ---------- 6. honours ------------------------------------------------------
UNION ALL
SELECT 'honours: one row per in-scope club, zero-filled',
       (SELECT count(*) FROM dim_club WHERE is_in_scope)::text,
       (SELECT count(*) FROM presentation.vw_club_trophies_honors)::text
UNION ALL
SELECT 'honours: trophy total reconciles to fact_club_trophy in the window',
       (SELECT count(*)::text FROM fact_club_trophy ft JOIN dim_season s USING (season_key) WHERE s.is_scored),
       (SELECT sum(total_trophies)::text FROM presentation.vw_club_trophies_honors)
UNION ALL
SELECT 'honours: categories add up to the total (clubs that do not)', '0',
       count(*)::text FROM presentation.vw_club_trophies_honors
       WHERE league_titles + domestic_cups + european_trophies <> total_trophies
UNION ALL
SELECT 'honours: anchor - Real Madrid won 3 European trophies in the window (Champions League 2017/18, 2021/22, 2023/24)', '3',
       european_trophies::text FROM presentation.vw_club_trophies_honors WHERE club_name = 'Real Madrid'
),
judged AS (
    SELECT check_name, expected, actual,
           CASE WHEN expected = actual THEN 'PASS'
                WHEN expected ~ '^>=[\d.]+$' AND actual ~ '^[\d.]+$'
                     AND actual::numeric >= substr(expected, 3)::numeric THEN 'PASS'
                ELSE 'FAIL' END AS status
    FROM checks
)
SELECT check_name, expected, actual, status
FROM judged ORDER BY CASE status WHEN 'FAIL' THEN 0 ELSE 1 END, check_name;
