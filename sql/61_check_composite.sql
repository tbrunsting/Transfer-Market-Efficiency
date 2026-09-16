-- =============================================================================
-- Transfer Market Efficiency -- checks for the composite efficiency index
--
-- Rebuilds the index from the four pillar tables in plain SQL, using the weights
-- as stored in score.composite_weight rather than repeating them, and compares
-- with what R wrote. Also checks the properties the index is supposed to have:
-- every club-season present, renormalisation only where recruitment is missing,
-- provisional flags inherited, and the index not collapsing into a league table.
--
--   psql -d transfer_market -f sql/61_check_composite.sql
-- =============================================================================

WITH w AS (
    SELECT max(weight) FILTER (WHERE pillar = 'recruitment_roi') AS w1,
           max(weight) FILTER (WHERE pillar = 'trading_profit')  AS w2,
           max(weight) FILTER (WHERE pillar = 'value_growth')    AS w3,
           max(weight) FILTER (WHERE pillar = 'sporting_return') AS w4,
           sum(weight) AS total
    FROM score.composite_weight
),
rebuilt AS (
    SELECT cs.club_season_key, r.roi_z::float8 AS p1, t.profit_z::float8 AS p2, g.growth_z::float8 AS p3,
           sp.ppm_z::float8 AS p4, r.is_provisional,
           (r.roi_z IS NULL) AS missing_recruitment,
           CASE WHEN r.roi_z IS NULL THEN w.total - w.w1 ELSE w.total END AS weight_applied,
           (coalesce(r.roi_z::float8 * w.w1, 0) + t.profit_z::float8 * w.w2 + g.growth_z::float8 * w.w3
            + sp.ppm_z::float8 * w.w4)
           / CASE WHEN r.roi_z IS NULL THEN w.total - w.w1 ELSE w.total END AS idx
    FROM fact_club_season cs
    JOIN score.club_season_recruitment r USING (club_season_key)
    JOIN score.club_season_trading t USING (club_season_key)
    JOIN score.club_season_value_growth g USING (club_season_key)
    JOIN score.club_season_sporting sp USING (club_season_key)
    CROSS JOIN w
),
rebuilt_z AS (
    SELECT *, (idx - avg(idx) OVER ()) / stddev_samp(idx) OVER () AS idx_z FROM rebuilt
),
cmp AS (
    SELECT count(*) FILTER (WHERE r.club_season_key IS NULL OR q.club_season_key IS NULL) AS unmatched,
           max(abs(r.efficiency_index - q.idx)) AS max_index_gap,
           max(abs(r.efficiency_z - q.idx_z)) AS max_z_gap,
           max(abs(r.weight_applied - q.weight_applied)) AS max_weight_gap,
           count(*) FILTER (WHERE r.is_recruitment_missing <> q.missing_recruitment) AS missing_flag_differs,
           count(*) FILTER (WHERE r.is_provisional <> q.is_provisional) AS provisional_differs
    FROM rebuilt_z q FULL JOIN score.club_season_efficiency r USING (club_season_key)
),
tol AS (SELECT 1e-9::float8 AS t),
checks AS (
SELECT 'weights: the four pillars are recorded and sum to 1' AS check_name, '4 pillars, sum 1' AS expected,
       count(*) || ' pillars, sum ' || CASE WHEN abs(sum(weight) - 1) < 1e-12 THEN '1' ELSE sum(weight)::text END AS actual
FROM score.composite_weight
UNION ALL
SELECT 'weights: money pillars 0.30 each, sporting return 0.10', '0.30/0.30/0.30/0.10',
       (SELECT round(w1::numeric,2) || '/' || round(w2::numeric,2) || '/' || round(w3::numeric,2) || '/' || round(w4::numeric,2) FROM w)
UNION ALL SELECT 'rows: one per club-season, none missing', '0', unmatched::text FROM cmp
UNION ALL SELECT 'index matches SQL (gap < 1e-9)', 'match',
       CASE WHEN max_index_gap < (SELECT t FROM tol) THEN 'match' ELSE max_index_gap::text END FROM cmp
UNION ALL SELECT 'standardised index matches SQL (gap < 1e-9)', 'match',
       CASE WHEN max_z_gap < (SELECT t FROM tol) THEN 'match' ELSE max_z_gap::text END FROM cmp
UNION ALL SELECT 'renormalisation: weight applied matches SQL', 'match',
       CASE WHEN max_weight_gap < (SELECT t FROM tol) THEN 'match' ELSE max_weight_gap::text END FROM cmp
UNION ALL SELECT 'renormalisation: flagged for exactly the club-seasons with no recruitment score', '0',
       missing_flag_differs::text FROM cmp
UNION ALL
SELECT 'renormalisation: those are the 69 below the recruitment spend floor',
       (SELECT count(*)::text FROM score.club_season_recruitment WHERE is_insufficient_spend),
       (SELECT count(*)::text FROM score.club_season_efficiency WHERE is_recruitment_missing)
UNION ALL
SELECT 'renormalisation: the weight applied there is 0.70', '0',
       count(*)::text FROM score.club_season_efficiency
       WHERE is_recruitment_missing AND abs(weight_applied - 0.70) > 1e-12
UNION ALL SELECT 'provisional: inherited from Pillar 1 (rows that disagree)', '0', provisional_differs::text FROM cmp
UNION ALL
SELECT 'provisional: the 2022/23 and 2023/24 cohorts', '194',
       count(*)::text FROM score.club_season_efficiency WHERE is_provisional
UNION ALL
SELECT 'property: the index is not a disguised league table (|corr with points| < 0.5)', 'yes',
       CASE WHEN abs(corr(e.efficiency_z, sp.ppm_z)) < 0.5 THEN 'yes'
            ELSE round(corr(e.efficiency_z, sp.ppm_z)::numeric, 2)::text END
FROM score.club_season_efficiency e JOIN score.club_season_sporting sp USING (club_season_key)
UNION ALL
SELECT 'property: the index is not a disguised size ranking (|corr with log squad value| < 0.5)', 'yes',
       CASE WHEN abs(corr(e.efficiency_z, ln(cs.squad_value_start_eur::float8))) < 0.5 THEN 'yes'
            ELSE round(corr(e.efficiency_z, ln(cs.squad_value_start_eur::float8))::numeric, 2)::text END
FROM score.club_season_efficiency e JOIN fact_club_season cs USING (club_season_key)
UNION ALL
SELECT 'property: every pillar still moves the index (weakest |corr| > 0.1)', 'yes',
       CASE WHEN least(abs(corr(efficiency_z, recruitment_z)), abs(corr(efficiency_z, trading_z)),
                       abs(corr(efficiency_z, value_growth_z)), abs(corr(efficiency_z, sporting_z))) > 0.1
            THEN 'yes' ELSE 'a pillar is inert' END
FROM score.club_season_efficiency
),
judged AS (
    SELECT check_name, expected, actual, CASE WHEN expected = actual THEN 'PASS' ELSE 'FAIL' END AS status FROM checks
)
SELECT check_name, expected, actual, status
FROM judged ORDER BY CASE status WHEN 'FAIL' THEN 0 ELSE 1 END, check_name;
