-- =============================================================================
-- Transfer Market Efficiency -- post-load checks
--
-- Run after scripts/30_load_warehouse.py. Every row is one check with what was
-- expected, what is there, and a verdict. Failures sort to the top.
--
--   psql -d transfer_market -f sql/03_checks.sql
--
-- These mirror the Phase 1 validations, so a load that silently drops or
-- duplicates rows is caught here rather than in a dashboard months later.
-- Constraints in 01_schema.sql already make the impossible impossible; this
-- file checks the things a constraint cannot: counts, totals and known values.
-- =============================================================================

WITH checks AS (

-- ---------- row counts, against the Phase 1 figures -------------------------
SELECT 'dim_club: in-scope clubs'                AS check_name, '145'   AS expected,
       count(*)::text AS actual FROM dim_club WHERE is_in_scope
UNION ALL SELECT 'fact_player_season rows', '19563', count(*)::text FROM fact_player_season
UNION ALL SELECT 'fact_club_season rows', '684', count(*)::text FROM fact_club_season
UNION ALL SELECT 'fact_club_trophy rows', '96', count(*)::text FROM fact_club_trophy
UNION ALL SELECT 'fact_manager_tenure rows', '906', count(*)::text FROM fact_manager_tenure
UNION ALL SELECT 'dim_season scored seasons', '7', count(*)::text FROM dim_season WHERE is_scored
UNION ALL SELECT 'meta.decision rows (human calls)', '80', count(*)::text FROM meta.decision
UNION ALL SELECT 'meta.source_manifest rows', '1104', count(*)::text FROM meta.source_manifest

-- ---------- the fee rule ----------------------------------------------------
-- NULL means undisclosed. If a load ever zero-fills, undisclosed drops to 0 and
-- the count of zero fees jumps: both numbers are checked, not just one.
UNION ALL
SELECT 'fee rule: undisclosed fees are NULL, not 0',
       'more than 4000 undisclosed',
       count(*) FILTER (WHERE fee_eur IS NULL)::text || ' NULL, '
       || count(*) FILTER (WHERE fee_eur = 0)::text || ' zero'
FROM fact_transfer
UNION ALL
SELECT 'fee rule: no row claims disclosed with a NULL fee', '0',
       count(*)::text FROM fact_transfer WHERE is_fee_disclosed AND fee_eur IS NULL
UNION ALL
SELECT 'fee rule: undisclosed rows never carry a deflator', '0',
       count(*)::text FROM fact_transfer
       WHERE fee_eur IS NULL AND (fee_share_of_season IS NOT NULL OR fee_vs_season_median IS NOT NULL)

-- ---------- transfer types are read, not inferred ---------------------------
UNION ALL
SELECT 'transfer types: page-sourced rows are not heuristic', '0',
       count(*)::text FROM fact_transfer
       WHERE source_system = 'transfermarkt-pages' AND is_type_heuristic
UNION ALL
SELECT 'transfer types: loan returns are excluded from signings', 'false',
       DISTINCT_counts.v
FROM (SELECT DISTINCT counts_as_signing::text AS v FROM dim_transfer_type
      WHERE transfer_type = 'loan_return') AS DISTINCT_counts
UNION ALL
SELECT 'transfer types: every type is used at least once', '6',
       count(DISTINCT transfer_type_key)::text FROM fact_transfer

-- ---------- one event, one row ----------------------------------------------
UNION ALL
SELECT 'no transfer is double-counted across sources', '0',
       count(*)::text FROM (
           SELECT player_key, transfer_date_key, from_club_key, to_club_key
           FROM fact_transfer GROUP BY 1,2,3,4 HAVING count(DISTINCT source_system) > 1) d

-- ---------- the deflator, against the measured spend ------------------------
UNION ALL
SELECT 'deflator: 2017/18 in-scope spend is about EUR 5.5bn', '5400-5700',
       round(total_fees_eur / 1e6)::text FROM dim_season WHERE season_label = '2017/18'
UNION ALL
SELECT 'deflator: 2020/21 shows the COVID dip', '3700-3900',
       round(total_fees_eur / 1e6)::text FROM dim_season WHERE season_label = '2020/21'
UNION ALL
SELECT 'deflator: every scored season has a total and a median', '7',
       count(*)::text FROM dim_season
       WHERE is_scored AND total_fees_eur IS NOT NULL AND median_fee_eur IS NOT NULL

-- ---------- sporting return --------------------------------------------------
UNION ALL
SELECT 'league champions: one per league-season', '35',
       count(*)::text FROM fact_club_season WHERE position_computed = 1
UNION ALL
SELECT 'points: Manchester City 2017/18 scored 100', '100',
       coalesce(max(cs.points_from_results)::text, 'not found')
FROM fact_club_season cs
JOIN dim_club c USING (club_key) JOIN dim_season s USING (season_key)
WHERE c.club_name = 'Manchester City' AND s.season_label = '2017/18'
UNION ALL
SELECT 'points: deductions are flagged, not applied', '2',
       count(*)::text FROM fact_club_season WHERE has_known_deduction

-- ---------- performance ------------------------------------------------------
UNION ALL
SELECT 'player-seasons: every row has a position group', '0 missing',
       count(*)::text || ' missing' FROM fact_player_season WHERE position_group_key IS NULL
UNION ALL
SELECT 'player-seasons: SCA present for the vast majority', 'over 95%',
       round(100.0 * count(*) FILTER (WHERE sca IS NOT NULL) / count(*), 1)::text || '%'
FROM fact_player_season
UNION ALL
SELECT 'player-seasons: availability denominator is populated', 'over 99%',
       round(100.0 * count(*) FILTER (WHERE team_matches_available IS NOT NULL) / count(*), 1)::text || '%'
FROM fact_player_season
UNION ALL
SELECT 'player-seasons: minutes never exceed the season',
       '0 over 4000',
       count(*)::text || ' over 4000' FROM fact_player_season WHERE minutes > 4000

-- ---------- the spell bridge -------------------------------------------------
UNION ALL
SELECT 'spells: no purchase fee without a known arrival', '0',
       count(*)::text FROM bridge_player_club_spell
       WHERE purchase_fee_eur IS NOT NULL AND NOT arrival_known
UNION ALL
SELECT 'spells: flags agree with the transfer keys', '0',
       count(*)::text FROM bridge_player_club_spell
       WHERE arrival_known <> (arrival_transfer_key IS NOT NULL)
          OR departure_known <> (departure_transfer_key IS NOT NULL)

-- ---------- provenance --------------------------------------------------------
UNION ALL
SELECT 'coverage: in-scope club-seasons measured', '684',
       count(*)::text FROM meta.transfer_coverage WHERE is_in_scope
UNION ALL
SELECT 'coverage: the frozen table was worse early than late', 'true',
       (
         (SELECT sum(frozen_fees_eur) / nullif(sum(page_fees_eur), 0) FROM meta.transfer_coverage tc
          JOIN dim_season s USING (season_key) WHERE tc.is_in_scope AND s.season_label = '2017/18')
         <
         (SELECT sum(frozen_fees_eur) / nullif(sum(page_fees_eur), 0) FROM meta.transfer_coverage tc
          JOIN dim_season s USING (season_key) WHERE tc.is_in_scope AND s.season_label = '2023/24')
       )::text
UNION ALL
SELECT 'manager tenures: boundaries flagged as match-based', 'true',
       bool_and(boundaries_are_match_based)::text FROM fact_manager_tenure

),
judged AS (
    SELECT check_name,
           expected,
           actual,
           CASE
               WHEN expected ~ '^\d+$' AND actual ~ '^\d+$' AND expected = actual THEN 'PASS'
               WHEN expected ~ '^\d+$' AND actual ~ '^\d+$'                        THEN 'FAIL'
               WHEN expected = actual                                                 THEN 'PASS'
               ELSE 'REVIEW'      -- ranges and percentages: read the numbers
           END AS status
    FROM checks
)
SELECT check_name, expected, actual, status
FROM judged
ORDER BY CASE status WHEN 'FAIL' THEN 0 WHEN 'REVIEW' THEN 1 ELSE 2 END, check_name;

-- =============================================================================
-- Informational: things to read rather than assert
-- =============================================================================

-- Where the money is, by season, and how much of it was invisible before the pull.
SELECT s.season_label,
       round(sum(tc.page_fees_eur) / 1e6)                                    AS true_spend_eur_m,
       round(sum(tc.frozen_fees_eur) / 1e6)                                  AS frozen_dataset_eur_m,
       round(100.0 * sum(tc.frozen_fees_eur) / nullif(sum(tc.page_fees_eur), 0), 1) AS frozen_pct
FROM meta.transfer_coverage tc
JOIN dim_season s USING (season_key)
WHERE tc.is_in_scope
GROUP BY s.season_label
ORDER BY s.season_label;

-- The transfer mix, which the frozen dataset could not distinguish at all.
SELECT tt.transfer_type,
       count(*)                                          AS rows,
       round(sum(t.fee_eur) / 1e6)                       AS fees_eur_m,
       count(*) FILTER (WHERE t.fee_eur IS NULL)         AS undisclosed_rows
FROM fact_transfer t JOIN dim_transfer_type tt USING (transfer_type_key)
GROUP BY tt.transfer_type ORDER BY rows DESC;

-- Spend and sporting return per club-season: the raw material of the efficiency score.
-- Note SUM(spend_eur) ignores undisclosed fees; the count says how many were left out.
SELECT c.club_name, s.season_label,
       round(sum(m.spend_eur) / 1e6)                          AS spend_eur_m,
       count(*) FILTER (WHERE m.fee_is_undisclosed)           AS undisclosed_moves,
       max(cs.points_from_results)                            AS points
FROM vw_transfer_money m
JOIN dim_club c ON c.club_key = m.to_club_key AND c.is_in_scope
JOIN dim_season s ON s.season_key = m.season_key AND s.is_scored
LEFT JOIN fact_club_season cs ON cs.club_key = c.club_key AND cs.season_key = s.season_key
GROUP BY c.club_name, s.season_label
ORDER BY spend_eur_m DESC NULLS LAST
LIMIT 15;
