-- =============================================================================
-- Transfer Market Efficiency -- checks for Pillar 3, squad value growth
--
-- Rebuilds ownership from transfer events, the end values, which spells are
-- scored, the season marks, the club-season totals, the winsorising and the
-- normalisation in plain SQL, and compares with what R wrote. The regression is
-- again solved by alternating projections rather than lm().
--
--   psql -d transfer_market -f sql/51_check_value_growth.sql
-- =============================================================================

WITH RECURSIVE p AS (
    SELECT DATE '2017-07-01' AS window_open, DATE '2024-07-01' AS window_close, 365 AS backward_days, 180 AS forward_days
),
ev AS (
    SELECT t.transfer_key, t.player_key, t.from_club_key, t.to_club_key, t.season_key, tt.transfer_type,
           d.full_date AS dt,
           CASE tt.transfer_type WHEN 'loan_return' THEN 0 WHEN 'loan' THEN 2 WHEN 'loan_with_fee' THEN 2 ELSE 1 END AS rnk
    FROM fact_transfer t
    JOIN dim_transfer_type tt USING (transfer_type_key)
    JOIN dim_date d ON d.date_key = t.transfer_date_key
),
first_ev AS (
    SELECT DISTINCT ON (player_key) player_key,
           CASE WHEN transfer_type = 'loan_return' THEN to_club_key ELSE from_club_key END AS owner
    FROM ev ORDER BY player_key, dt, rnk, transfer_key
),
steps AS (
    SELECT f.player_key, (SELECT window_open FROM p) AS dt, -1 AS rnk, 0 AS transfer_key, f.owner,
           'at_club_when_the_window_opened' AS via
    FROM first_ev f
    UNION ALL
    SELECT player_key, dt, rnk, transfer_key, to_club_key, transfer_type
    FROM ev WHERE transfer_type IN ('permanent_with_fee', 'free', 'undisclosed', 'loan_return')
),
kept AS (
    SELECT * FROM (
        SELECT s.*, lag(owner) OVER (PARTITION BY player_key ORDER BY dt, rnk, transfer_key) AS prev_owner
        FROM steps s) t
    WHERE prev_owner IS NULL OR owner <> prev_owner
),
spans AS (
    SELECT player_key, owner AS club_key, dt AS start_date, via AS start_via,
           coalesce(lead(dt) OVER w, (SELECT window_close FROM p)) AS end_date,
           coalesce(lead(via) OVER w, 'held_at_close') AS end_via
    FROM kept
    WINDOW w AS (PARTITION BY player_key ORDER BY dt, rnk, transfer_key)
),
no_event_players AS (
    SELECT player_key, min(club_key) AS club_key
    FROM (SELECT DISTINCT player_key, club_key FROM fact_player_season) f
    WHERE NOT EXISTS (SELECT 1 FROM ev WHERE ev.player_key = f.player_key)
    GROUP BY player_key HAVING count(*) = 1
),
holding AS (
    SELECT h.* FROM (
        SELECT player_key, club_key, start_date, end_date, start_via, end_via FROM spans WHERE end_date > start_date
        UNION ALL
        SELECT player_key, club_key, (SELECT window_open FROM p), (SELECT window_close FROM p),
               'at_club_when_the_window_opened', 'held_at_close'
        FROM no_event_players) h
    JOIN dim_club c USING (club_key)
    WHERE c.is_in_scope
),
valued AS (
    SELECT h.*, sv.v AS start_value,
           CASE WHEN h.end_via = 'free' THEN 0 ELSE evv.v END AS end_value
    FROM holding h
    LEFT JOIN LATERAL (
        SELECT coalesce(
            (SELECT v.market_value_eur::float8 FROM fact_player_valuation v JOIN dim_date d ON d.date_key = v.valuation_date_key
             WHERE v.player_key = h.player_key AND d.full_date <= h.start_date
               AND d.full_date >= h.start_date - (SELECT backward_days FROM p) ORDER BY d.full_date DESC LIMIT 1),
            (SELECT v.market_value_eur::float8 FROM fact_player_valuation v JOIN dim_date d ON d.date_key = v.valuation_date_key
             WHERE v.player_key = h.player_key AND d.full_date > h.start_date
               AND d.full_date <= h.start_date + (SELECT forward_days FROM p) ORDER BY d.full_date LIMIT 1)) AS v) sv ON true
    LEFT JOIN LATERAL (
        SELECT coalesce(
            (SELECT v.market_value_eur::float8 FROM fact_player_valuation v JOIN dim_date d ON d.date_key = v.valuation_date_key
             WHERE v.player_key = h.player_key AND d.full_date <= h.end_date
               AND d.full_date >= h.end_date - (SELECT backward_days FROM p) ORDER BY d.full_date DESC LIMIT 1),
            (SELECT v.market_value_eur::float8 FROM fact_player_valuation v JOIN dim_date d ON d.date_key = v.valuation_date_key
             WHERE v.player_key = h.player_key AND d.full_date > h.end_date
               AND d.full_date <= h.end_date + (SELECT forward_days FROM p) ORDER BY d.full_date LIMIT 1)) AS v) evv ON true
),
-- The sales Pillar 2 counted: a fee sale by a club in a season it played in the big five.
pillar2_sales AS (
    SELECT s.player_key, s.from_club_key AS club_key, s.dt
    FROM ev s
    JOIN fact_club_season cs ON cs.club_key = s.from_club_key AND cs.season_key = s.season_key
    WHERE s.transfer_type = 'permanent_with_fee'
),
-- A spell is excluded if such a sale falls anywhere inside it, not only at its end: same-day estimated
-- dates can place another club's loan return after the sale and end the spell with the wrong reason.
sold_spells AS (
    SELECT DISTINCT v.player_key, v.club_key, v.start_date
    FROM valued v
    JOIN pillar2_sales q ON q.player_key = v.player_key AND q.club_key = v.club_key
                        AND q.dt >= v.start_date AND q.dt <= v.end_date
),
scored AS (
    SELECT v.*,
           CASE WHEN s.player_key IS NOT NULL THEN 'fee sale scored in Pillar 2'
                WHEN v.end_via = 'undisclosed' THEN 'undisclosed departure'
                WHEN v.end_value IS NULL THEN 'no end valuation'
           END AS excluded_because
    FROM valued v
    LEFT JOIN sold_spells s USING (player_key, club_key, start_date)
),
marks AS (
    SELECT s.player_key, s.club_key, s.start_date, cs.season_key, cs.club_season_key,
           (s.start_date >= ds.season_start_date) AS starts_here,
           (s.end_date <= ds.season_end_date + 1) AS ends_here,
           CASE WHEN s.start_date >= ds.season_start_date THEN coalesce(s.start_value, 0)
                ELSE coalesce((SELECT v.market_value_eur::float8 FROM fact_player_valuation v JOIN dim_date d ON d.date_key = v.valuation_date_key
                               WHERE v.player_key = s.player_key AND d.full_date <= ds.season_start_date
                               ORDER BY d.full_date DESC LIMIT 1),
                              (SELECT v.market_value_eur::float8 FROM fact_player_valuation v JOIN dim_date d ON d.date_key = v.valuation_date_key
                               WHERE v.player_key = s.player_key AND d.full_date > ds.season_start_date
                                 AND d.full_date <= ds.season_start_date + (SELECT forward_days FROM p)
                               ORDER BY d.full_date LIMIT 1), 0) END AS value_start,
           CASE WHEN s.end_date <= ds.season_end_date + 1 THEN coalesce(s.end_value, 0)
                ELSE coalesce((SELECT v.market_value_eur::float8 FROM fact_player_valuation v JOIN dim_date d ON d.date_key = v.valuation_date_key
                               WHERE v.player_key = s.player_key AND d.full_date <= ds.season_end_date + 1
                               ORDER BY d.full_date DESC LIMIT 1),
                              (SELECT v.market_value_eur::float8 FROM fact_player_valuation v JOIN dim_date d ON d.date_key = v.valuation_date_key
                               WHERE v.player_key = s.player_key AND d.full_date > ds.season_end_date + 1
                                 AND d.full_date <= ds.season_end_date + 1 + (SELECT forward_days FROM p)
                               ORDER BY d.full_date LIMIT 1), 0) END AS value_end,
           (s.end_date <= ds.season_end_date + 1 AND s.end_via = 'free') AS is_write_off
    FROM scored s
    JOIN fact_club_season cs ON cs.club_key = s.club_key
    JOIN dim_season ds ON ds.season_key = cs.season_key
    WHERE s.excluded_because IS NULL
      AND s.start_date < ds.season_end_date + 1 AND s.end_date > ds.season_start_date
),
club AS (
    SELECT cs.club_season_key, cs.club_key, cs.season_key, cs.competition_key,
           cs.squad_value_start_eur::float8 AS sv, ds.median_fee_eur::float8 AS median_fee,
           count(m.*) AS n_holdings,
           count(m.*) FILTER (WHERE m.is_write_off) AS n_write_offs,
           coalesce(sum(m.value_start) FILTER (WHERE m.is_write_off), 0) AS write_off,
           coalesce(sum(m.value_end - m.value_start), 0) AS growth
    FROM fact_club_season cs
    JOIN dim_season ds ON ds.season_key = cs.season_key
    LEFT JOIN marks m ON m.club_season_key = cs.club_season_key
    GROUP BY cs.club_season_key, cs.club_key, cs.season_key, cs.competition_key, cs.squad_value_start_eur, ds.median_fee_eur
),
units AS (SELECT *, growth / median_fee AS u FROM club),
bounds AS (
    SELECT percentile_cont(0.01) WITHIN GROUP (ORDER BY u) AS lo,
           percentile_cont(0.99) WITHIN GROUP (ORDER BY u) AS hi FROM units
),
capped AS (
    SELECT c.*, least(greatest(c.u, b.lo), b.hi) AS y, (c.u < b.lo OR c.u > b.hi) AS is_capped
    FROM units c CROSS JOIN bounds b
),
projection (iter, club_season_key, season_key, competition_key, y, x) AS (
    SELECT 0, club_season_key, season_key, competition_key, y, ln(sv) FROM capped
    UNION ALL
    SELECT iter + 1, club_season_key, season_key, competition_key,
           y - CASE WHEN iter % 2 = 0 THEN avg(y) OVER (PARTITION BY season_key)
                    ELSE avg(y) OVER (PARTITION BY competition_key) END,
           x - CASE WHEN iter % 2 = 0 THEN avg(x) OVER (PARTITION BY season_key)
                    ELSE avg(x) OVER (PARTITION BY competition_key) END
    FROM projection WHERE iter < 400
),
demeaned AS (
    SELECT c.*, pr.y AS y_d, pr.x AS x_d
    FROM capped c JOIN projection pr ON pr.club_season_key = c.club_season_key AND pr.iter = 400
),
slope AS (SELECT sum(x_d * y_d) / sum(x_d * x_d) AS b FROM demeaned),
resid AS (
    SELECT d.*, d.y_d - s.b * d.x_d AS resid, d.y - s.b * ln(d.sv) - (d.y_d - s.b * d.x_d) AS fitted_effects
    FROM demeaned d CROSS JOIN slope s
),
resid_z AS (SELECT *, resid / stddev_samp(resid) OVER () AS z FROM resid),
cell_effect AS (SELECT competition_key, season_key, avg(fitted_effects) AS f FROM resid GROUP BY 1, 2),
league_sql AS (
    SELECT co.competition_name, avg(cl.f - cb.f) AS effect
    FROM cell_effect cl
    JOIN cell_effect cb ON cb.season_key = cl.season_key
    JOIN dim_competition bc ON bc.competition_key = cb.competition_key AND bc.competition_name = 'Bundesliga'
    JOIN dim_competition co ON co.competition_key = cl.competition_key
    GROUP BY co.competition_name
),
holding_cmp AS (
    SELECT count(*) FILTER (WHERE r.player_key IS NULL) AS only_in_sql,
           count(*) FILTER (WHERE q.player_key IS NULL) AS only_in_r,
           count(*) FILTER (WHERE r.end_date <> q.end_date OR r.start_via <> q.start_via OR r.end_via <> q.end_via) AS shape_differs,
           count(*) FILTER (WHERE r.is_scored <> (q.excluded_because IS NULL)
                               OR r.excluded_because IS DISTINCT FROM q.excluded_because) AS scoring_differs,
           max(abs(coalesce(r.start_value_eur, -1) - coalesce(q.start_value, -1))) AS max_start_gap,
           max(abs(coalesce(r.end_value_eur, -1) - coalesce(q.end_value, -1))) AS max_end_gap
    FROM scored q FULL JOIN score.player_holding r USING (player_key, club_key, start_date)
),
mark_cmp AS (
    SELECT count(*) FILTER (WHERE r.holding_key IS NULL) AS only_in_sql,
           count(*) FILTER (WHERE q.player_key IS NULL) AS only_in_r,
           max(abs(r.value_start_eur - q.value_start)) AS max_start_gap,
           max(abs(r.value_end_eur - q.value_end)) AS max_end_gap,
           count(*) FILTER (WHERE r.is_write_off <> q.is_write_off) AS write_off_differs
    FROM marks q
    FULL JOIN (SELECT m.holding_key, m.season_key, m.value_start_eur, m.value_end_eur, m.is_write_off,
                      h.player_key, h.club_key, h.start_date
               FROM score.holding_season_growth m JOIN score.player_holding h USING (holding_key)) r
      ON r.player_key = q.player_key AND r.club_key = q.club_key AND r.start_date = q.start_date AND r.season_key = q.season_key
),
club_cmp AS (
    SELECT count(*) FILTER (WHERE r.club_season_key IS NULL OR q.club_season_key IS NULL) AS unmatched,
           count(*) FILTER (WHERE r.n_holdings <> q.n_holdings OR r.n_write_offs <> q.n_write_offs) AS counts_differ,
           max(abs(r.write_off_eur - q.write_off)) AS max_write_off_gap,
           max(abs(r.growth_eur - q.growth)) AS max_growth_gap,
           max(abs(r.growth_median_fees - q.u)) AS max_units_gap,
           max(abs(r.growth_capped - q.y)) AS max_capped_gap,
           count(*) FILTER (WHERE r.is_capped <> q.is_capped) AS cap_differs,
           max(abs(r.growth_normalised - q.resid)) AS max_resid_gap,
           max(abs(r.growth_z - q.z)) AS max_z_gap
    FROM resid_z q FULL JOIN score.club_season_value_growth r USING (club_season_key)
),
param AS (
    SELECT max(value) FILTER (WHERE name = 'winsor_low_median_fees') AS lo,
           max(value) FILTER (WHERE name = 'winsor_high_median_fees') AS hi,
           max(value) FILTER (WHERE name = 'squad_value_log_slope') AS slope
    FROM score.run_parameter WHERE pillar = 'value_growth'
),
tol AS (SELECT 1e-9::float8 AS t),
checks AS (
SELECT 'ownership: spells only in SQL' AS check_name, '0' AS expected, only_in_sql::text AS actual FROM holding_cmp
UNION ALL SELECT 'ownership: spells only in R', '0', only_in_r::text FROM holding_cmp
UNION ALL SELECT 'ownership: same end date, start and end reason', '0', shape_differs::text FROM holding_cmp
UNION ALL SELECT 'ownership: same spells scored, same exclusion reasons', '0', scoring_differs::text FROM holding_cmp
UNION ALL SELECT 'ownership: start and end values match (gap < 1e-9)', 'match',
       CASE WHEN max_start_gap < (SELECT t FROM tol) AND max_end_gap < (SELECT t FROM tol) THEN 'match'
            ELSE greatest(max_start_gap, max_end_gap)::text END FROM holding_cmp
UNION ALL
SELECT 'ownership: a loan out does not end a spell (Julian Alvarez is owned by Manchester City from 2022)', '1',
       count(*)::text FROM score.player_holding h JOIN dim_player pl USING (player_key) JOIN dim_club c USING (club_key)
       WHERE pl.player_name = 'Julián Álvarez' AND c.club_name = 'Manchester City'
         AND h.start_date <= DATE '2022-01-31' AND h.end_date >= DATE '2024-07-01'
UNION ALL
SELECT 'write-off: Messi leaving Barcelona in 2021 is charged at his value that season', '80000000',
       max(g.value_start_eur)::bigint::text
       FROM score.holding_season_growth g JOIN score.player_holding h USING (holding_key)
       JOIN dim_player pl ON pl.player_key = h.player_key JOIN dim_club c ON c.club_key = h.club_key
       WHERE pl.player_name = 'Lionel Messi' AND c.club_name = 'FC Barcelona' AND g.is_write_off
UNION ALL
SELECT 'no double counting: no scored spell ends in a sale that Pillar 2 counted', '0',
       count(*)::text FROM score.player_holding h
       JOIN score.sale_basis b ON b.player_key = h.player_key AND b.club_key = h.club_key
                              AND b.transfer_type = 'permanent_with_fee' AND b.counts_toward_profit
       JOIN fact_transfer t ON t.transfer_key = b.transfer_key
       JOIN dim_date d ON d.date_key = t.transfer_date_key AND d.full_date = h.end_date
       WHERE h.is_scored
UNION ALL SELECT 'marks: season rows only in SQL', '0', only_in_sql::text FROM mark_cmp
UNION ALL SELECT 'marks: season rows only in R', '0', only_in_r::text FROM mark_cmp
UNION ALL SELECT 'marks: season start and end values match (gap < 1e-9)', 'match',
       CASE WHEN max_start_gap < (SELECT t FROM tol) AND max_end_gap < (SELECT t FROM tol) THEN 'match'
            ELSE greatest(max_start_gap, max_end_gap)::text END FROM mark_cmp
UNION ALL SELECT 'marks: same write-off flags', '0', write_off_differs::text FROM mark_cmp
UNION ALL SELECT 'club-seasons: all 684 on both sides', '0', unmatched::text FROM club_cmp
UNION ALL SELECT 'club-seasons: holding and write-off counts match', '0', counts_differ::text FROM club_cmp
UNION ALL SELECT 'club-seasons: growth and write-off value match (gap < 1e-9)', 'match',
       CASE WHEN max_growth_gap < (SELECT t FROM tol) AND max_write_off_gap < (SELECT t FROM tol) THEN 'match'
            ELSE greatest(max_growth_gap, max_write_off_gap)::text END FROM club_cmp
UNION ALL SELECT 'club-seasons: growth in median fees matches (gap < 1e-9)', 'match',
       CASE WHEN max_units_gap < (SELECT t FROM tol) THEN 'match' ELSE max_units_gap::text END FROM club_cmp
UNION ALL SELECT 'winsor: bounds match and the same club-seasons are capped', 'match',
       CASE WHEN abs(r.lo - b.lo) < (SELECT t FROM tol) AND abs(r.hi - b.hi) < (SELECT t FROM tol)
                 AND (SELECT cap_differs FROM club_cmp) = 0 AND (SELECT max_capped_gap FROM club_cmp) < (SELECT t FROM tol)
            THEN 'match' ELSE 'differs' END
FROM param r CROSS JOIN bounds b
UNION ALL SELECT 'normalisation: squad-value slope matches (lm vs alternating projections)', 'match',
       CASE WHEN abs(r.slope - s.b) < (SELECT t FROM tol) THEN 'match' ELSE (r.slope - s.b)::text END
FROM param r CROSS JOIN slope s
UNION ALL SELECT 'normalisation: residuals match (gap < 1e-9)', 'match',
       CASE WHEN max_resid_gap < (SELECT t FROM tol) THEN 'match' ELSE max_resid_gap::text END FROM club_cmp
UNION ALL SELECT 'normalisation: z-scores match (gap < 1e-9)', 'match',
       CASE WHEN max_z_gap < (SELECT t FROM tol) THEN 'match' ELSE max_z_gap::text END FROM club_cmp
UNION ALL
SELECT 'normalisation: league effects match (leagues with gap >= 1e-9)', '0',
       count(*)::text FROM league_sql l
       JOIN score.run_parameter r ON r.pillar = 'value_growth' AND r.name = 'league_effect_vs_Bundesliga:' || l.competition_name
       WHERE abs(r.value - l.effect) >= 1e-9
UNION ALL
SELECT 'normalisation: growth carries no squad-value signal (|r| < 1e-9)', '0',
       (CASE WHEN abs(corr(r.growth_normalised, ln(cs.squad_value_start_eur::float8))) < 1e-9 THEN 0 ELSE 1 END)::text
FROM score.club_season_value_growth r JOIN fact_club_season cs USING (club_season_key)
),
judged AS (
    SELECT check_name, expected, actual, CASE WHEN expected = actual THEN 'PASS' ELSE 'FAIL' END AS status FROM checks
)
SELECT check_name, expected, actual, status
FROM judged ORDER BY CASE status WHEN 'FAIL' THEN 0 ELSE 1 END, check_name;
