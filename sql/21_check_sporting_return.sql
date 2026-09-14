-- =============================================================================
-- Transfer Market Efficiency -- checks for score.club_season_sporting (Pillar 4)
--
-- Recomputes points per match and its within-league-season z-score from
-- fact_club_season in plain SQL and compares with what R wrote.
--
--   psql -d transfer_market -f sql/21_check_sporting_return.sql
-- =============================================================================

WITH recomputed AS (
    SELECT club_season_key, competition_key, season_key,
           points_from_results::float8 / matches AS ppm
    FROM fact_club_season
),
recomputed_z AS (
    SELECT *, (ppm - avg(ppm) OVER w) / stddev_samp(ppm) OVER w AS ppm_z
    FROM recomputed
    WINDOW w AS (PARTITION BY competition_key, season_key)
),
cmp AS (
    SELECT count(*) FILTER (WHERE r.club_season_key IS NULL) AS only_in_sql,
           count(*) FILTER (WHERE q.club_season_key IS NULL) AS only_in_r,
           max(abs(r.points_per_match - q.ppm)) AS max_ppm_gap,
           max(abs(r.ppm_z - q.ppm_z))          AS max_z_gap
    FROM recomputed_z q
    FULL JOIN score.club_season_sporting r USING (club_season_key)
),
checks AS (
SELECT 'rows: one per fact_club_season row' AS check_name,
       (SELECT count(*) FROM fact_club_season)::text AS expected,
       (SELECT count(*) FROM score.club_season_sporting)::text AS actual
UNION ALL SELECT 'rows only in SQL', '0', only_in_sql::text FROM cmp
UNION ALL SELECT 'rows only in R', '0', only_in_r::text FROM cmp
UNION ALL SELECT 'points per match matches SQL (largest gap < 1e-9)', 'match',
       CASE WHEN max_ppm_gap < 1e-9 THEN 'match' ELSE max_ppm_gap::text END FROM cmp
UNION ALL SELECT 'ppm_z matches SQL (largest gap < 1e-9)', 'match',
       CASE WHEN max_z_gap < 1e-9 THEN 'match' ELSE max_z_gap::text END FROM cmp
UNION ALL
SELECT 'every league-season has mean 0 and sd 1', '35',
       count(*)::text FROM (
           SELECT competition_key, season_key FROM score.club_season_sporting GROUP BY 1, 2
           HAVING abs(avg(ppm_z)) < 1e-9 AND abs(stddev_samp(ppm_z) - 1) < 1e-9) c
UNION ALL
SELECT 'Ligue 1 2019/20 (abandoned) is on its own 27-28 matches', '27-28',
       min(r.matches) || '-' || max(r.matches)
FROM score.club_season_sporting r JOIN dim_competition co USING (competition_key) JOIN dim_season s USING (season_key)
WHERE co.competition_name = 'Ligue 1' AND s.season_label = '2019/20'
UNION ALL
SELECT 'deductions not applied: Juventus 2022/23 on 72 results points', '72',
       r.points_from_results::text
FROM score.club_season_sporting r JOIN dim_club c USING (club_key) JOIN dim_season s USING (season_key)
WHERE c.club_name = 'Juventus FC' AND s.season_label = '2022/23'
UNION ALL
SELECT 'known deductions carried through', '2',
       count(*)::text FROM score.club_season_sporting WHERE has_known_deduction
),
judged AS (
    SELECT check_name, expected, actual, CASE WHEN expected = actual THEN 'PASS' ELSE 'FAIL' END AS status
    FROM checks
)
SELECT check_name, expected, actual, status
FROM judged
ORDER BY CASE status WHEN 'FAIL' THEN 0 ELSE 1 END, check_name;
