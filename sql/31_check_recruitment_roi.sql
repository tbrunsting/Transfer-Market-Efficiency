-- =============================================================================
-- Transfer Market Efficiency -- checks for Pillar 1, recruitment ROI
--
-- Rebuilds the credit rule, the club-season cohorts, the spend floor and the
-- scale normalisation in plain SQL, independently of R, and compares. The
-- regression is done a different way on purpose: R fits lm() with season and
-- league dummies; SQL removes both sets of effects by alternating projections
-- (demean by season, then by league, repeated until nothing is left) and takes
-- one slope. By the Frisch-Waugh-Lovell theorem that is the same model, so
-- agreement means more than shared code. League effects are then read off the
-- fitted values, which are constant within each league-season cell.
--
--   psql -d transfer_market -f sql/31_check_recruitment_roi.sql
--
-- The parameters below must equal what R/30_recruitment_roi.R used; a check
-- compares them with score.run_parameter.
-- =============================================================================

WITH RECURSIVE p AS (
    SELECT 3 AS horizon, false AS include_undisclosed, 0.0::float8 AS under_floor_w, 0.5::float8 AS gap_w,
           0.1::float8 AS eps, 2024 AS last_year
),
tr AS (
    SELECT t.transfer_key, t.player_key, t.from_club_key, t.to_club_key, t.season_key, s.season_end_year AS yr,
           tt.transfer_type, tt.counts_as_spend, t.fee_eur::float8 AS fee,
           (t.fee_eur / s.median_fee_eur)::float8 AS fee_units, d.full_date
    FROM fact_transfer t
    JOIN dim_transfer_type tt USING (transfer_type_key)
    JOIN dim_season s USING (season_key)
    JOIN dim_date d ON d.date_key = t.transfer_date_key
),
arrivals AS (
    SELECT tr.* FROM tr
    JOIN fact_club_season cs ON cs.club_key = tr.to_club_key AND cs.season_key = tr.season_key
    WHERE tr.transfer_type <> 'loan_return'
),
arrival_end AS (
    SELECT a.transfer_key, min(d.yr) AS end_yr
    FROM arrivals a
    JOIN tr d ON d.player_key = a.player_key AND d.from_club_key = a.to_club_key AND d.full_date > a.full_date
    WHERE a.transfer_type IN ('loan', 'loan_with_fee')
       OR d.transfer_type IN ('permanent_with_fee', 'free', 'undisclosed')
    GROUP BY a.transfer_key
),
candidates AS (
    SELECT f.player_season_key, f.club_key, f.season_key, fs.season_end_year AS yr, f.minutes,
           f.team_matches_available, q.is_scored, q.unscored_reason, q.quality_pctile,
           a.transfer_key, a.transfer_type, a.season_key AS arrival_season_key, a.yr AS arrival_yr, a.full_date
    FROM fact_player_season f
    JOIN dim_season fs ON fs.season_key = f.season_key
    JOIN score.player_quality q ON q.player_season_key = f.player_season_key
    JOIN arrivals a ON a.player_key = f.player_key AND a.to_club_key = f.club_key
    LEFT JOIN arrival_end e ON e.transfer_key = a.transfer_key
    CROSS JOIN p
    WHERE fs.season_end_year >= a.yr
      AND fs.season_end_year <= coalesce(e.end_yr, p.last_year)
      AND fs.season_end_year - a.yr < p.horizon
),
credit AS (
    SELECT DISTINCT ON (c.player_season_key)
           c.*, c.yr - c.arrival_yr AS season_offset,
           c.minutes::float8 / 90 / c.team_matches_available AS season_eq,
           CASE WHEN c.is_scored THEN c.quality_pctile / 100
                WHEN c.unscored_reason LIKE 'advanced stats missing%' THEN p.gap_w
                ELSE p.under_floor_w END AS w,
           (p.include_undisclosed OR c.transfer_type <> 'undisclosed') AS counts
    FROM candidates c CROSS JOIN p
    ORDER BY c.player_season_key, c.full_date DESC, c.transfer_key DESC
),
credit_out AS (
    SELECT *, season_eq * w AS output FROM credit
),
cohort AS (
    SELECT cs.club_season_key, cs.club_key, cs.season_key, cs.competition_key, s.season_end_year AS yr,
           cs.squad_value_start_eur::float8 AS sv,
           coalesce(ca.n_arrivals, 0) AS n_arrivals, coalesce(ca.n_spend, 0) AS n_spend,
           coalesce(ca.n_und, 0) AS n_und, coalesce(ca.spend_eur, 0) AS spend_eur,
           coalesce(ca.spend_units, 0) AS spend_units,
           coalesce(co.output, 0) AS output, coalesce(co.output_und, 0) AS output_und
    FROM fact_club_season cs
    JOIN dim_season s ON s.season_key = cs.season_key
    LEFT JOIN (SELECT to_club_key AS club_key, season_key, count(*) AS n_arrivals,
                      count(*) FILTER (WHERE counts_as_spend) AS n_spend,
                      count(*) FILTER (WHERE transfer_type = 'undisclosed') AS n_und,
                      sum(fee) FILTER (WHERE counts_as_spend) AS spend_eur,
                      sum(fee_units) FILTER (WHERE counts_as_spend) AS spend_units
               FROM arrivals GROUP BY 1, 2) ca ON ca.club_key = cs.club_key AND ca.season_key = cs.season_key
    LEFT JOIN (SELECT club_key, arrival_season_key AS season_key,
                      sum(output) FILTER (WHERE counts) AS output,
                      sum(output) FILTER (WHERE transfer_type = 'undisclosed') AS output_und
               FROM credit_out GROUP BY 1, 2) co ON co.club_key = cs.club_key AND co.season_key = cs.season_key
),
spend_floor AS (
    SELECT percentile_cont(0.10) WITHIN GROUP (ORDER BY spend_units) AS v FROM cohort
),
scored AS (
    SELECT c.*, ln(c.output + p.eps) - ln(c.spend_units) AS log_roi
    FROM cohort c CROSS JOIN spend_floor f CROSS JOIN p
    WHERE c.spend_units >= f.v
),
projection (iter, club_season_key, season_key, competition_key, y, x) AS (
    SELECT 0, club_season_key, season_key, competition_key, log_roi, ln(sv) FROM scored
    UNION ALL
    SELECT iter + 1, club_season_key, season_key, competition_key,
           y - CASE WHEN iter % 2 = 0 THEN avg(y) OVER (PARTITION BY season_key)
                    ELSE avg(y) OVER (PARTITION BY competition_key) END,
           x - CASE WHEN iter % 2 = 0 THEN avg(x) OVER (PARTITION BY season_key)
                    ELSE avg(x) OVER (PARTITION BY competition_key) END
    FROM projection WHERE iter < 400
),
demeaned AS (
    SELECT s.*, pr.y AS y_d, pr.x AS x_d
    FROM scored s JOIN projection pr ON pr.club_season_key = s.club_season_key AND pr.iter = 400
),
leftover AS (
    SELECT greatest((SELECT max(abs(m)) FROM (SELECT avg(y_d) AS m FROM demeaned GROUP BY season_key) a),
                    (SELECT max(abs(m)) FROM (SELECT avg(y_d) AS m FROM demeaned GROUP BY competition_key) b),
                    (SELECT max(abs(m)) FROM (SELECT avg(x_d) AS m FROM demeaned GROUP BY season_key) c),
                    (SELECT max(abs(m)) FROM (SELECT avg(x_d) AS m FROM demeaned GROUP BY competition_key) d)) AS v
),
slope AS (
    SELECT sum(x_d * y_d) / sum(x_d * x_d) AS b FROM demeaned
),
resid AS (
    SELECT d.*, d.y_d - s.b * d.x_d AS resid,
           d.log_roi - s.b * ln(d.sv) - (d.y_d - s.b * d.x_d) AS fitted_effects
    FROM demeaned d CROSS JOIN slope s
),
cell_effect AS (
    SELECT competition_key, season_key, avg(fitted_effects) AS f, coalesce(stddev_pop(fitted_effects), 0) AS spread
    FROM resid GROUP BY 1, 2
),
league_sql AS (
    SELECT cl.competition_key, avg(cl.f - cb.f) AS log_effect,
           (SELECT count(*) FROM scored sc WHERE sc.competition_key = cl.competition_key) AS n,
           (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY sc.output / sc.spend_units)
            FROM scored sc WHERE sc.competition_key = cl.competition_key) AS median_ratio
    FROM cell_effect cl
    JOIN cell_effect cb ON cb.season_key = cl.season_key
    JOIN dim_competition bc ON bc.competition_key = cb.competition_key AND bc.competition_name = 'Bundesliga'
    GROUP BY cl.competition_key
),
league_cmp AS (
    SELECT count(*) FILTER (WHERE r.competition_key IS NULL OR q.competition_key IS NULL) AS unmatched,
           max(abs(r.log_effect_vs_reference - q.log_effect)) AS max_effect_gap,
           max(abs(r.output_per_euro_vs_reference - exp(q.log_effect))) AS max_ratio_gap,
           count(*) FILTER (WHERE r.n_club_seasons <> q.n) AS n_differs,
           max(abs(r.median_output_per_spend - q.median_ratio)) AS max_median_gap
    FROM league_sql q FULL JOIN score.league_premium r USING (competition_key)
),
resid_z AS (
    SELECT *, resid / stddev_samp(resid) OVER () AS z FROM resid
),
credit_cmp AS (
    SELECT count(*) FILTER (WHERE r.player_season_key IS NULL) AS only_in_sql,
           count(*) FILTER (WHERE q.player_season_key IS NULL) AS only_in_r,
           count(*) FILTER (WHERE r.transfer_key <> q.transfer_key) AS different_arrival,
           count(*) FILTER (WHERE r.counts_toward_output <> q.counts) AS different_counting,
           max(abs(r.output - q.output)) AS max_output_gap
    FROM credit_out q
    FULL JOIN score.signing_credit r USING (player_season_key)
),
rec_cmp AS (
    SELECT count(*) FILTER (WHERE r.club_season_key IS NULL OR q.club_season_key IS NULL) AS unmatched,
           count(*) FILTER (WHERE r.n_arrivals <> q.n_arrivals OR r.n_spend_signings <> q.n_spend
                               OR r.n_undisclosed <> q.n_und) AS count_differs,
           max(abs(r.spend_eur - q.spend_eur) / greatest(q.spend_eur, 1)) AS max_spend_rel_gap,
           max(abs(r.spend_median_fees - q.spend_units)) AS max_units_gap,
           max(abs(r.output - q.output)) AS max_output_gap,
           max(abs(r.output_undisclosed - q.output_und)) AS max_output_und_gap,
           count(*) FILTER (WHERE r.is_insufficient_spend <> (s.club_season_key IS NULL)) AS floor_differs,
           count(*) FILTER (WHERE r.is_provisional <> (q.yr + (SELECT horizon FROM p) - 1 > (SELECT last_year FROM p))) AS provisional_differs,
           max(abs(r.log_roi - s.log_roi)) AS max_log_roi_gap,
           max(abs(r.roi_normalised - s.resid)) AS max_resid_gap,
           max(abs(r.roi_z - s.z)) AS max_z_gap
    FROM cohort q
    FULL JOIN score.club_season_recruitment r USING (club_season_key)
    LEFT JOIN resid_z s USING (club_season_key)
),
param AS (
    SELECT max(value) FILTER (WHERE name = 'horizon_seasons') AS horizon,
           max(value) FILTER (WHERE name = 'include_undisclosed_output') AS include_undisclosed,
           max(value) FILTER (WHERE name = 'under_floor_weight') AS under_floor_w,
           max(value) FILTER (WHERE name = 'data_gap_weight') AS gap_w,
           max(value) FILTER (WHERE name = 'eps') AS eps,
           max(value) FILTER (WHERE name = 'spend_floor_median_fees') AS spend_floor,
           max(value) FILTER (WHERE name = 'squad_value_log_slope') AS slope
    FROM score.run_parameter WHERE pillar = 'recruitment_roi'
),
tol AS (SELECT 1e-9::float8 AS t),
checks AS (
SELECT 'parameters: R used the same horizon, undisclosed rule, weights and eps as this file' AS check_name,
       '0' AS expected,
       (CASE WHEN r.horizon = p.horizon AND r.include_undisclosed = p.include_undisclosed::int
                  AND r.under_floor_w = p.under_floor_w AND r.gap_w = p.gap_w AND r.eps = p.eps
             THEN 0 ELSE 1 END)::text AS actual
FROM param r CROSS JOIN p
UNION ALL SELECT 'credit: player-seasons only in SQL', '0', only_in_sql::text FROM credit_cmp
UNION ALL SELECT 'credit: player-seasons only in R', '0', only_in_r::text FROM credit_cmp
UNION ALL SELECT 'credit: same arrival credited', '0', different_arrival::text FROM credit_cmp
UNION ALL SELECT 'credit: same counting rule', '0', different_counting::text FROM credit_cmp
UNION ALL SELECT 'credit: output matches SQL (largest gap < 1e-9)', 'match',
       CASE WHEN max_output_gap < (SELECT t FROM tol) THEN 'match' ELSE max_output_gap::text END FROM credit_cmp
UNION ALL
SELECT 'credit: Julian Alvarez''s 2022 fee to Manchester City is credited despite the loan back to River Plate', '>=1',
       count(*)::text
FROM score.signing_credit sc JOIN fact_transfer t USING (transfer_key)
JOIN dim_player pl ON pl.player_key = t.player_key JOIN dim_club c ON c.club_key = sc.club_key
WHERE pl.player_name = 'Julián Álvarez' AND c.club_name = 'Manchester City' AND sc.transfer_type = 'permanent_with_fee'
UNION ALL SELECT 'club-seasons: all 684 on both sides', '0', unmatched::text FROM rec_cmp
UNION ALL SELECT 'club-seasons: arrival and signing counts match', '0', count_differs::text FROM rec_cmp
UNION ALL SELECT 'club-seasons: spend in euros matches (relative gap < 1e-9)', 'match',
       CASE WHEN max_spend_rel_gap < (SELECT t FROM tol) THEN 'match' ELSE max_spend_rel_gap::text END FROM rec_cmp
UNION ALL SELECT 'club-seasons: deflated spend matches (gap < 1e-9)', 'match',
       CASE WHEN max_units_gap < (SELECT t FROM tol) THEN 'match' ELSE max_units_gap::text END FROM rec_cmp
UNION ALL SELECT 'club-seasons: output matches (gap < 1e-9)', 'match',
       CASE WHEN max_output_gap < (SELECT t FROM tol) THEN 'match' ELSE max_output_gap::text END FROM rec_cmp
UNION ALL SELECT 'club-seasons: undisclosed output matches (gap < 1e-9)', 'match',
       CASE WHEN max_output_und_gap < (SELECT t FROM tol) THEN 'match' ELSE max_output_und_gap::text END FROM rec_cmp
UNION ALL SELECT 'guardrail: same club-seasons below the spend floor', '0', floor_differs::text FROM rec_cmp
UNION ALL SELECT 'guardrail: spend floor value matches (gap < 1e-9)', 'match',
       CASE WHEN abs(r.spend_floor - f.v) < (SELECT t FROM tol) THEN 'match' ELSE (r.spend_floor - f.v)::text END
FROM param r CROSS JOIN spend_floor f
UNION ALL SELECT 'guardrail: same cohorts flagged provisional', '0', provisional_differs::text FROM rec_cmp
UNION ALL SELECT 'roi: log ROI matches (gap < 1e-9)', 'match',
       CASE WHEN max_log_roi_gap < (SELECT t FROM tol) THEN 'match' ELSE max_log_roi_gap::text END FROM rec_cmp
UNION ALL SELECT 'roi: alternating projections converged (largest leftover season or league mean < 1e-12)', 'yes',
       CASE WHEN v < 1e-12 THEN 'yes' ELSE v::text END FROM leftover
UNION ALL SELECT 'roi: fitted effects are constant within every league-season cell (spread < 1e-9)', 'yes',
       CASE WHEN max(spread) < 1e-9 THEN 'yes' ELSE max(spread)::text END FROM cell_effect
UNION ALL SELECT 'roi: squad-value slope matches (lm with dummies vs alternating projections)', 'match',
       CASE WHEN abs(r.slope - s.b) < (SELECT t FROM tol) THEN 'match' ELSE (r.slope - s.b)::text END
FROM param r CROSS JOIN slope s
UNION ALL SELECT 'roi: normalised residuals match (gap < 1e-9)', 'match',
       CASE WHEN max_resid_gap < (SELECT t FROM tol) THEN 'match' ELSE max_resid_gap::text END FROM rec_cmp
UNION ALL SELECT 'roi: z-scores match (gap < 1e-9)', 'match',
       CASE WHEN max_z_gap < (SELECT t FROM tol) THEN 'match' ELSE max_z_gap::text END FROM rec_cmp
UNION ALL
SELECT 'roi: normalised ROI carries no squad-value signal (|r| < 1e-9)', '0',
       (CASE WHEN abs(corr(r.roi_normalised, ln(cs.squad_value_start_eur::float8))) < 1e-9 THEN 0 ELSE 1 END)::text
FROM score.club_season_recruitment r JOIN fact_club_season cs USING (club_season_key)
WHERE NOT r.is_insufficient_spend
UNION ALL
SELECT 'roi: normalised ROI averages zero in every league (leagues that do not)', '0',
       count(*)::text FROM (
           SELECT cs.competition_key FROM score.club_season_recruitment r JOIN fact_club_season cs USING (club_season_key)
           WHERE NOT r.is_insufficient_spend GROUP BY 1 HAVING abs(avg(r.roi_normalised)) >= 1e-9) l
UNION ALL SELECT 'league premium: one row per league', '0', unmatched::text FROM league_cmp
UNION ALL SELECT 'league premium: log effects match SQL (gap < 1e-9)', 'match',
       CASE WHEN max_effect_gap < (SELECT t FROM tol) THEN 'match' ELSE max_effect_gap::text END FROM league_cmp
UNION ALL SELECT 'league premium: output-per-euro ratios match SQL (gap < 1e-9)', 'match',
       CASE WHEN max_ratio_gap < (SELECT t FROM tol) THEN 'match' ELSE max_ratio_gap::text END FROM league_cmp
UNION ALL SELECT 'league premium: club-season counts and raw medians match', 'match',
       CASE WHEN n_differs = 0 AND max_median_gap < (SELECT t FROM tol) THEN 'match'
            ELSE n_differs || ' / ' || max_median_gap END FROM league_cmp
UNION ALL SELECT 'league premium: the reference league is Bundesliga at exactly 1', '1',
       output_per_euro_vs_reference::text FROM score.league_premium WHERE competition_name = 'Bundesliga'
UNION ALL
SELECT 'provisional: exactly the 2022/23 and 2023/24 cohorts (three-season horizon)', '0',
       count(*)::text FROM score.club_season_recruitment r JOIN dim_season s USING (season_key)
       WHERE r.is_provisional <> (s.season_label IN ('2022/23', '2023/24'))
UNION ALL
SELECT 'undisclosed: no undisclosed-fee output counts toward the score', '0',
       count(*)::text FROM score.signing_credit WHERE transfer_type = 'undisclosed' AND counts_toward_output
UNION ALL
SELECT 'spend: permanent fees by season reconcile to dim_season.total_fees_eur (seasons that differ)', '0',
       count(*)::text FROM (
           SELECT a.season_key FROM arrivals a JOIN dim_season s USING (season_key)
           WHERE a.transfer_type = 'permanent_with_fee'
           GROUP BY a.season_key, s.total_fees_eur
           HAVING abs(sum(a.fee) - s.total_fees_eur::float8) > 1) d
),
judged AS (
    SELECT check_name, expected, actual,
           CASE WHEN expected = actual THEN 'PASS'
                WHEN expected ~ '^>=\d+$' AND actual ~ '^\d+$' AND actual::int >= substr(expected, 3)::int THEN 'PASS'
                ELSE 'FAIL' END AS status
    FROM checks
)
SELECT check_name, expected, actual, status
FROM judged
ORDER BY CASE status WHEN 'FAIL' THEN 0 ELSE 1 END, check_name;
