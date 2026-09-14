-- =============================================================================
-- Transfer Market Efficiency -- checks for score.player_quality
--
-- Recomputes every player quality value from fact_player_season in plain SQL,
-- independently of R, and compares. Run by R/10_player_quality.R after it
-- writes, or on its own:
--
--   psql -d transfer_market -f sql/11_check_player_quality.sql
--
-- The metric map below must say what R/10_player_quality.R says; a check
-- compares the two, so they cannot drift apart silently.
-- =============================================================================

WITH metric_map (position_group, metric) AS (VALUES
    ('GK', 'psxg_minus_ga_p90'), ('GK', 'save_pct'),
    ('CB', 'tackles_won_p90'), ('CB', 'interceptions_p90'), ('CB', 'clearances_p90'),
    ('CB', 'aerials_won_p90'), ('CB', 'aerial_win_pct'), ('CB', 'progressive_passes_p90'),
    ('FB', 'tackles_won_p90'), ('FB', 'interceptions_p90'), ('FB', 'progressive_passes_p90'),
    ('FB', 'progressive_carries_p90'), ('FB', 'sca_p90'),
    ('CM', 'tackles_won_p90'), ('CM', 'interceptions_p90'), ('CM', 'progressive_passes_p90'),
    ('CM', 'passes_into_final_third_p90'), ('CM', 'progressive_carries_p90'), ('CM', 'sca_p90'),
    ('AM/W', 'sca_p90'), ('AM/W', 'xag_p90'), ('AM/W', 'npxg_p90'),
    ('AM/W', 'take_ons_won_p90'), ('AM/W', 'progressive_carries_p90'),
    ('FW', 'npxg_p90'), ('FW', 'goals_p90'), ('FW', 'xag_p90'), ('FW', 'sca_p90')
),
-- Centre-back defensive volume: z-scored on the residual after regressing on team possession, within season.
possession_adjusted (position_group, metric) AS (VALUES
    ('CB', 'tackles_won_p90'), ('CB', 'interceptions_p90'), ('CB', 'clearances_p90'), ('CB', 'aerials_won_p90')
),
over_floor AS (
    SELECT f.*, g.position_group, f.minutes / 90.0::float8 AS n90, cs.possession_pct::float8 AS possession_pct
    FROM fact_player_season f
    JOIN dim_position_group g USING (position_group_key)
    JOIN fact_club_season cs USING (club_key, season_key)
    WHERE f.minutes >= 900
),
metric_value_all AS (
    SELECT s.player_season_key, s.season_key, s.position_group, s.possession_pct, v.metric, v.value
    FROM over_floor s
    CROSS JOIN LATERAL (VALUES
        ('goals_p90',                   s.goals / s.n90),
        ('npxg_p90',                    s.npxg / s.n90),
        ('xag_p90',                     s.xag / s.n90),
        ('sca_p90',                     s.sca / s.n90),
        ('tackles_won_p90',             s.tackles_won / s.n90),
        ('interceptions_p90',           s.interceptions / s.n90),
        ('clearances_p90',              s.clearances / s.n90),
        ('aerials_won_p90',             s.aerials_won / s.n90),
        ('aerial_win_pct',              s.aerials_won::float8 / nullif(s.aerials_won + s.aerials_lost, 0)),
        ('progressive_passes_p90',      s.progressive_passes / s.n90),
        ('progressive_carries_p90',     s.progressive_carries / s.n90),
        ('passes_into_final_third_p90', s.passes_into_final_third / s.n90),
        ('take_ons_won_p90',            s.take_ons_won / s.n90),
        ('save_pct',                    s.gk_saves::float8 / nullif(s.gk_saves + s.gk_goals_against, 0)),
        ('psxg_minus_ga_p90',           (s.gk_psxg - s.gk_goals_against) / s.n90)
    ) AS v (metric, value)
    JOIN metric_map m ON m.position_group = s.position_group AND m.metric = v.metric
),
-- A missing input is a gap, never a zero: score on the rest if at most one is missing and two remain.
coverage AS (
    SELECT player_season_key, count(value) AS n_have, count(*) AS n_need,
           count(value) >= greatest(count(*) - 1, 2) AS enough_data
    FROM metric_value_all GROUP BY 1
),
metric_value AS (
    SELECT mv.* FROM metric_value_all mv JOIN coverage c USING (player_season_key)
    WHERE c.enough_data AND mv.value IS NOT NULL
),
metric_used AS (
    SELECT mv.*, (pa.metric IS NOT NULL) AS is_adjusted,
           CASE WHEN pa.metric IS NULL THEN mv.value
                ELSE mv.value - (regr_intercept(mv.value, mv.possession_pct) OVER w
                                 + regr_slope(mv.value, mv.possession_pct) OVER w * mv.possession_pct)
           END AS value_used
    FROM metric_value mv
    LEFT JOIN possession_adjusted pa USING (position_group, metric)
    WINDOW w AS (PARTITION BY mv.season_key, mv.position_group, mv.metric)
),
metric_z AS (
    SELECT *, (value_used - avg(value_used) OVER w) / stddev_samp(value_used) OVER w AS z
    FROM metric_used
    WINDOW w AS (PARTITION BY season_key, position_group, metric)
),
composite AS (
    SELECT player_season_key, season_key, position_group, avg(z) AS composite, count(z) AS n_metrics
    FROM metric_z GROUP BY 1, 2, 3
),
quality AS (
    SELECT *, (composite - avg(composite) OVER w) / stddev_samp(composite) OVER w AS quality_z
    FROM composite
    WINDOW w AS (PARTITION BY season_key, position_group)
),
metric_cmp AS (
    SELECT count(*) AS both_sides,
           count(*) FILTER (WHERE r.player_season_key IS NULL) AS only_in_sql,
           count(*) FILTER (WHERE q.player_season_key IS NULL) AS only_in_r,
           max(abs(r.value - q.value))           AS max_value_gap,
           max(abs(r.value_used - q.value_used)) AS max_value_used_gap,
           count(*) FILTER (WHERE r.possession_adjusted <> q.is_adjusted) AS flag_differs,
           max(abs(r.z - q.z))                   AS max_z_gap
    FROM metric_z q
    FULL JOIN score.player_quality_metric r USING (player_season_key, metric)
),
quality_cmp AS (
    SELECT count(*) FILTER (WHERE r.is_scored AND q.player_season_key IS NULL)     AS only_in_r,
           count(*) FILTER (WHERE r.player_season_key IS NULL)                     AS only_in_sql,
           max(abs(r.quality_z - q.quality_z))                                     AS max_quality_gap,
           count(*) FILTER (WHERE r.n_metrics <> q.n_metrics)                      AS n_metrics_differ
    FROM quality q
    FULL JOIN (SELECT * FROM score.player_quality WHERE is_scored) r USING (player_season_key)
),
checks AS (
SELECT 'rows: one per fact_player_season row' AS check_name,
       (SELECT count(*) FROM fact_player_season)::text AS expected,
       (SELECT count(*) FROM score.player_quality)::text AS actual
UNION ALL
SELECT 'rows: scored = 900+ minutes with enough metric inputs',
       (SELECT count(*) FROM coverage WHERE enough_data)::text,
       (SELECT count(*) FROM score.player_quality WHERE is_scored)::text
UNION ALL
SELECT 'rows: unscored under the floor are exactly the under-900 rows',
       (SELECT count(*) FROM fact_player_season WHERE minutes < 900)::text,
       count(*)::text FROM score.player_quality WHERE unscored_reason = 'under 900 minutes' AND minutes < 900
UNION ALL
SELECT 'rows: unscored for a data gap are exactly the rows short of inputs',
       (SELECT count(*) FROM coverage WHERE NOT enough_data)::text,
       count(*)::text FROM score.player_quality p JOIN coverage c USING (player_season_key)
       WHERE NOT c.enough_data AND p.unscored_reason LIKE 'advanced stats missing%'
UNION ALL
SELECT 'metric map: R wrote the same group x metric pairs as this file', '0',
       count(*)::text FROM (
           (SELECT DISTINCT g.position_group, r.metric FROM score.player_quality_metric r
            JOIN score.player_quality p USING (player_season_key) JOIN dim_position_group g USING (position_group_key)
            EXCEPT SELECT * FROM metric_map)
           UNION ALL
           (SELECT * FROM metric_map EXCEPT
            SELECT DISTINCT g.position_group, r.metric FROM score.player_quality_metric r
            JOIN score.player_quality p USING (player_season_key) JOIN dim_position_group g USING (position_group_key))
       ) d
UNION ALL SELECT 'metrics: rows only in SQL', '0', only_in_sql::text FROM metric_cmp
UNION ALL SELECT 'metrics: rows only in R', '0', only_in_r::text FROM metric_cmp
UNION ALL SELECT 'metrics: no missing input values', '0',
       count(*)::text FROM score.player_quality_metric WHERE value IS NULL OR z IS NULL
UNION ALL SELECT 'metrics: per-90 values match SQL (largest gap < 1e-9)', 'match',
       CASE WHEN max_value_gap < 1e-9 THEN 'match' ELSE max_value_gap::text END FROM metric_cmp
UNION ALL SELECT 'metrics: possession-adjusted values match SQL (largest gap < 1e-9)', 'match',
       CASE WHEN max_value_used_gap < 1e-9 THEN 'match' ELSE max_value_used_gap::text END FROM metric_cmp
UNION ALL SELECT 'metrics: the same metrics are flagged possession-adjusted in R and SQL', '0', flag_differs::text FROM metric_cmp
UNION ALL
SELECT 'metrics: adjusted CB stats carry no possession signal within any season (|r| < 1e-9)', '0',
       count(*)::text FROM (
           SELECT p.season_key, r.metric FROM score.player_quality_metric r
           JOIN score.player_quality p USING (player_season_key)
           JOIN fact_club_season cs USING (club_key, season_key)
           WHERE r.possession_adjusted
           GROUP BY 1, 2 HAVING abs(corr(r.value_used, cs.possession_pct::float8)) >= 1e-9) c
UNION ALL
SELECT 'metrics: only CB tackles won, interceptions, clearances and aerials won are adjusted', '4',
       count(DISTINCT g.position_group || ':' || r.metric)::text FROM score.player_quality_metric r
       JOIN score.player_quality p USING (player_season_key) JOIN dim_position_group g USING (position_group_key)
       WHERE r.possession_adjusted
UNION ALL SELECT 'metrics: z-scores match SQL (largest gap < 1e-9)', 'match',
       CASE WHEN max_z_gap < 1e-9 THEN 'match' ELSE max_z_gap::text END FROM metric_cmp
UNION ALL SELECT 'quality: scored rows only in R', '0', only_in_r::text FROM quality_cmp
UNION ALL SELECT 'quality: scored rows only in SQL', '0', only_in_sql::text FROM quality_cmp
UNION ALL SELECT 'quality: metric counts match SQL', '0', n_metrics_differ::text FROM quality_cmp
UNION ALL SELECT 'quality: quality_z matches SQL (largest gap < 1e-9)', 'match',
       CASE WHEN max_quality_gap < 1e-9 THEN 'match' ELSE max_quality_gap::text END FROM quality_cmp
UNION ALL
SELECT 'quality: every season x group has mean 0 and sd 1', '42',
       count(*)::text FROM (
           SELECT season_key, position_group_key FROM score.player_quality WHERE is_scored
           GROUP BY 1, 2 HAVING abs(avg(quality_z)) < 1e-9 AND abs(stddev_samp(quality_z) - 1) < 1e-9) c
UNION ALL
SELECT 'quality: percentiles run 0 to 100 in every season x group', '42',
       count(*)::text FROM (
           SELECT season_key, position_group_key FROM score.player_quality WHERE is_scored
           GROUP BY 1, 2 HAVING min(quality_pctile) = 0 AND max(quality_pctile) = 100) c
),
judged AS (
    SELECT check_name, expected, actual,
           CASE WHEN expected = actual THEN 'PASS' ELSE 'FAIL' END AS status
    FROM checks
)
SELECT check_name, expected, actual, status
FROM judged
ORDER BY CASE status WHEN 'FAIL' THEN 0 ELSE 1 END, check_name;
