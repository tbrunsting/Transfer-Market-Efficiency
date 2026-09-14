-- =============================================================================
-- Transfer Market Efficiency -- checks for Pillar 2, trading profit
--
-- Rebuilds every sale's owning arrival, basis and profit, the club-season
-- totals, the winsorising and the normalisation in plain SQL, independently of
-- R, and compares. As for Pillar 1, the regression is solved differently on
-- purpose: R fits lm() with season and league dummies; SQL removes both by
-- alternating projections, then reads league effects off the fitted values.
--
--   psql -d transfer_market -f sql/41_check_trading_profit.sql
-- =============================================================================

WITH RECURSIVE p AS (
    SELECT DATE '2017-07-01' AS window_open, 365 AS backward_days, 180 AS forward_days
),
tr AS (
    SELECT t.transfer_key, t.player_key, t.from_club_key, t.to_club_key, t.season_key, tt.transfer_type,
           t.fee_eur::float8 AS fee, d.full_date
    FROM fact_transfer t
    JOIN dim_transfer_type tt USING (transfer_type_key)
    JOIN dim_date d ON d.date_key = t.transfer_date_key
),
sales AS (
    SELECT tr.* FROM tr
    JOIN fact_club_season cs ON cs.club_key = tr.from_club_key AND cs.season_key = tr.season_key
    WHERE tr.transfer_type IN ('permanent_with_fee', 'loan_with_fee', 'undisclosed')
),
owning AS (
    SELECT DISTINCT ON (s.transfer_key) s.transfer_key, a.transfer_key AS owning_arrival_key, a.full_date AS arrival_date
    FROM sales s
    JOIN tr a ON a.player_key = s.player_key AND a.to_club_key = s.from_club_key
             AND a.transfer_type <> 'loan_return' AND a.full_date < s.full_date
    WHERE s.transfer_type = 'permanent_with_fee'
    ORDER BY s.transfer_key, a.full_date DESC, a.transfer_key DESC
),
sourced AS (
    SELECT s.*, o.owning_arrival_key,
           CASE WHEN s.transfer_type = 'undisclosed' THEN 'undisclosed_excluded'
                WHEN s.transfer_type = 'loan_with_fee' THEN 'loan_fee_received'
                WHEN o.owning_arrival_key IS NOT NULL THEN 'window_arrival'
                ELSE 'at_club_on_2017_07_01' END AS basis_source,
           CASE WHEN s.transfer_type = 'permanent_with_fee'
                THEN coalesce(o.arrival_date, (SELECT window_open FROM p)) END AS basis_date
    FROM sales s LEFT JOIN owning o USING (transfer_key)
),
valued AS (
    SELECT so.*,
           bw.valuation_date AS bw_date, bw.mv AS bw_value, fw.valuation_date AS fw_date, fw.mv AS fw_value
    FROM sourced so
    LEFT JOIN LATERAL (
        SELECT d.full_date AS valuation_date, v.market_value_eur::float8 AS mv
        FROM fact_player_valuation v JOIN dim_date d ON d.date_key = v.valuation_date_key
        WHERE v.player_key = so.player_key AND d.full_date <= so.basis_date
          AND d.full_date >= so.basis_date - (SELECT backward_days FROM p)
        ORDER BY d.full_date DESC LIMIT 1) bw ON so.basis_date IS NOT NULL
    LEFT JOIN LATERAL (
        SELECT d.full_date AS valuation_date, v.market_value_eur::float8 AS mv
        FROM fact_player_valuation v JOIN dim_date d ON d.date_key = v.valuation_date_key
        WHERE v.player_key = so.player_key AND d.full_date > so.basis_date
          AND d.full_date <= so.basis_date + (SELECT forward_days FROM p)
        ORDER BY d.full_date ASC LIMIT 1) fw ON so.basis_date IS NOT NULL
),
profit AS (
    SELECT transfer_key, from_club_key AS club_key, season_key, transfer_type, owning_arrival_key, basis_source,
           basis_date,
           CASE WHEN basis_date IS NULL THEN NULL WHEN bw_value IS NOT NULL THEN 'backward'
                WHEN fw_value IS NOT NULL THEN 'forward' ELSE 'none' END AS basis_lookup,
           CASE WHEN bw_value IS NOT NULL THEN bw_date ELSE fw_date END AS basis_valuation_date,
           CASE WHEN transfer_type = 'undisclosed' THEN NULL ELSE fee END AS income,
           CASE WHEN transfer_type = 'loan_with_fee' THEN 0
                WHEN transfer_type = 'permanent_with_fee' THEN coalesce(bw_value, fw_value, 0) END AS basis,
           transfer_type <> 'undisclosed' AS counts
    FROM valued
),
club AS (
    SELECT cs.club_season_key, cs.club_key, cs.season_key, cs.competition_key,
           cs.squad_value_start_eur::float8 AS sv, s.median_fee_eur::float8 AS median_fee,
           count(pr.*) FILTER (WHERE pr.transfer_type = 'permanent_with_fee') AS n_fee,
           count(pr.*) FILTER (WHERE pr.transfer_type = 'loan_with_fee') AS n_loan,
           count(pr.*) FILTER (WHERE pr.transfer_type = 'undisclosed') AS n_und,
           count(pr.*) FILTER (WHERE pr.basis_source = 'at_club_on_2017_07_01') AS n_legacy,
           count(pr.*) FILTER (WHERE pr.basis_lookup = 'none') AS n_zero,
           coalesce(sum(pr.income) FILTER (WHERE pr.counts), 0) AS income,
           coalesce(sum(pr.basis) FILTER (WHERE pr.counts), 0) AS basis
    FROM fact_club_season cs
    JOIN dim_season s ON s.season_key = cs.season_key
    LEFT JOIN profit pr ON pr.club_key = cs.club_key AND pr.season_key = cs.season_key
    GROUP BY cs.club_season_key, cs.club_key, cs.season_key, cs.competition_key, cs.squad_value_start_eur, s.median_fee_eur
),
club_units AS (
    SELECT *, income - basis AS profit, (income - basis) / median_fee AS units FROM club
),
bounds AS (
    SELECT percentile_cont(0.01) WITHIN GROUP (ORDER BY units) AS lo,
           percentile_cont(0.99) WITHIN GROUP (ORDER BY units) AS hi
    FROM club_units
),
capped AS (
    SELECT c.*, least(greatest(c.units, b.lo), b.hi) AS y, (c.units < b.lo OR c.units > b.hi) AS is_capped
    FROM club_units c CROSS JOIN bounds b
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
leftover AS (
    SELECT greatest((SELECT max(abs(m)) FROM (SELECT avg(y_d) AS m FROM demeaned GROUP BY season_key) a),
                    (SELECT max(abs(m)) FROM (SELECT avg(y_d) AS m FROM demeaned GROUP BY competition_key) b),
                    (SELECT max(abs(m)) FROM (SELECT avg(x_d) AS m FROM demeaned GROUP BY season_key) c),
                    (SELECT max(abs(m)) FROM (SELECT avg(x_d) AS m FROM demeaned GROUP BY competition_key) d)) AS v
),
slope AS (SELECT sum(x_d * y_d) / sum(x_d * x_d) AS b FROM demeaned),
resid AS (
    SELECT d.*, d.y_d - s.b * d.x_d AS resid, d.y - s.b * ln(d.sv) - (d.y_d - s.b * d.x_d) AS fitted_effects
    FROM demeaned d CROSS JOIN slope s
),
resid_z AS (SELECT *, resid / stddev_samp(resid) OVER () AS z FROM resid),
cell_effect AS (
    SELECT competition_key, season_key, avg(fitted_effects) AS f, coalesce(stddev_pop(fitted_effects), 0) AS spread
    FROM resid GROUP BY 1, 2
),
league_sql AS (
    SELECT co.competition_name, avg(cl.f - cb.f) AS effect
    FROM cell_effect cl
    JOIN cell_effect cb ON cb.season_key = cl.season_key
    JOIN dim_competition bc ON bc.competition_key = cb.competition_key AND bc.competition_name = 'Bundesliga'
    JOIN dim_competition co ON co.competition_key = cl.competition_key
    GROUP BY co.competition_name
),
sale_cmp AS (
    SELECT count(*) FILTER (WHERE r.transfer_key IS NULL) AS only_in_sql,
           count(*) FILTER (WHERE q.transfer_key IS NULL) AS only_in_r,
           count(*) FILTER (WHERE r.owning_arrival_key IS DISTINCT FROM q.owning_arrival_key) AS arrival_differs,
           count(*) FILTER (WHERE r.basis_source <> q.basis_source OR r.basis_lookup IS DISTINCT FROM q.basis_lookup
                               OR r.basis_date IS DISTINCT FROM q.basis_date
                               OR r.basis_valuation_date IS DISTINCT FROM q.basis_valuation_date) AS basis_route_differs,
           count(*) FILTER (WHERE r.counts_toward_profit <> q.counts OR (r.income_eur IS NULL) <> (q.income IS NULL)) AS counting_differs,
           max(abs(r.basis_eur - q.basis)) AS max_basis_gap,
           max(abs(r.profit_eur - (q.income - q.basis))) AS max_profit_gap
    FROM profit q FULL JOIN score.sale_basis r USING (transfer_key)
),
club_cmp AS (
    SELECT count(*) FILTER (WHERE r.club_season_key IS NULL OR q.club_season_key IS NULL) AS unmatched,
           count(*) FILTER (WHERE r.n_fee_sales <> q.n_fee OR r.n_loan_fee_income <> q.n_loan OR r.n_undisclosed_sales <> q.n_und
                               OR r.n_legacy_basis <> q.n_legacy OR r.n_zero_basis <> q.n_zero) AS counts_differ,
           max(abs(r.income_eur - q.income) / greatest(q.income, 1)) AS max_income_rel_gap,
           max(abs(r.basis_eur - q.basis) / greatest(q.basis, 1)) AS max_basis_rel_gap,
           max(abs(r.profit_median_fees - q.units)) AS max_units_gap,
           max(abs(r.profit_capped - q.y)) AS max_capped_gap,
           count(*) FILTER (WHERE r.is_capped <> q.is_capped) AS cap_flag_differs,
           max(abs(r.profit_normalised - q.resid)) AS max_resid_gap,
           max(abs(r.profit_z - q.z)) AS max_z_gap
    FROM resid_z q FULL JOIN score.club_season_trading r USING (club_season_key)
),
param AS (
    SELECT max(value) FILTER (WHERE name = 'winsor_low_median_fees') AS lo,
           max(value) FILTER (WHERE name = 'winsor_high_median_fees') AS hi,
           max(value) FILTER (WHERE name = 'squad_value_log_slope') AS slope,
           max(value) FILTER (WHERE name = 'backward_days') AS bw,
           max(value) FILTER (WHERE name = 'forward_days') AS fw
    FROM score.run_parameter WHERE pillar = 'trading_profit'
),
tol AS (SELECT 1e-9::float8 AS t),
checks AS (
SELECT 'parameters: R used the same 365-day backward and 180-day forward valuation windows' AS check_name, '0' AS expected,
       (CASE WHEN r.bw = p.backward_days AND r.fw = p.forward_days THEN 0 ELSE 1 END)::text AS actual
FROM param r CROSS JOIN p
UNION ALL SELECT 'sales: only in SQL', '0', only_in_sql::text FROM sale_cmp
UNION ALL SELECT 'sales: only in R', '0', only_in_r::text FROM sale_cmp
UNION ALL SELECT 'sales: same owning arrival', '0', arrival_differs::text FROM sale_cmp
UNION ALL SELECT 'sales: same basis source, date, lookup and valuation date', '0', basis_route_differs::text FROM sale_cmp
UNION ALL SELECT 'sales: same counting (undisclosed excluded, no income)', '0', counting_differs::text FROM sale_cmp
UNION ALL SELECT 'sales: basis matches (gap < 1e-9)', 'match',
       CASE WHEN max_basis_gap < (SELECT t FROM tol) THEN 'match' ELSE max_basis_gap::text END FROM sale_cmp
UNION ALL SELECT 'sales: profit matches (gap < 1e-9)', 'match',
       CASE WHEN max_profit_gap < (SELECT t FROM tol) THEN 'match' ELSE max_profit_gap::text END FROM sale_cmp
UNION ALL
SELECT 'D2: Hazard''s 2019/20 sale by Chelsea is priced at his 1 July 2017 value, not as pure income', 'at_club_on_2017_07_01',
       max(r.basis_source) || CASE WHEN max(r.basis_eur) > 0 THEN '' ELSE ' (zero basis)' END
FROM score.sale_basis r JOIN dim_player pl USING (player_key) JOIN dim_club c USING (club_key) JOIN dim_season s USING (season_key)
WHERE pl.player_name = 'Eden Hazard' AND c.club_name = 'Chelsea FC' AND s.season_label = '2019/20'
UNION ALL
SELECT 'D2: Morata''s 2020/21 sale by Chelsea links to the 2017 purchase through his loan to Atletico', 'permanent_with_fee',
       max(a.transfer_type)
FROM score.sale_basis r JOIN dim_player pl USING (player_key) JOIN dim_club c ON c.club_key = r.club_key
JOIN dim_season s ON s.season_key = r.season_key
JOIN fact_transfer ft ON ft.transfer_key = r.owning_arrival_key JOIN dim_transfer_type a ON a.transfer_type_key = ft.transfer_type_key
WHERE pl.player_name = 'Álvaro Morata' AND c.club_name = 'Chelsea FC' AND s.season_label = '2020/21'
UNION ALL
SELECT 'D1: realised only (no valuation of players still held enters Pillar 2)', '0',
       count(*)::text FROM score.sale_basis WHERE transfer_type NOT IN ('permanent_with_fee', 'loan_with_fee', 'undisclosed')
UNION ALL
SELECT 'defaults: loan fees received carry zero basis', '0',
       count(*)::text FROM score.sale_basis WHERE transfer_type = 'loan_with_fee' AND basis_eur <> 0
UNION ALL SELECT 'club-seasons: all 684 on both sides', '0', unmatched::text FROM club_cmp
UNION ALL SELECT 'club-seasons: sale counts match', '0', counts_differ::text FROM club_cmp
UNION ALL SELECT 'club-seasons: income and basis match (relative gap < 1e-9)', 'match',
       CASE WHEN max_income_rel_gap < (SELECT t FROM tol) AND max_basis_rel_gap < (SELECT t FROM tol) THEN 'match'
            ELSE greatest(max_income_rel_gap, max_basis_rel_gap)::text END FROM club_cmp
UNION ALL SELECT 'club-seasons: profit in median fees matches (gap < 1e-9)', 'match',
       CASE WHEN max_units_gap < (SELECT t FROM tol) THEN 'match' ELSE max_units_gap::text END FROM club_cmp
UNION ALL SELECT 'D3: winsor bounds match (gap < 1e-9)', 'match',
       CASE WHEN abs(r.lo - b.lo) < (SELECT t FROM tol) AND abs(r.hi - b.hi) < (SELECT t FROM tol) THEN 'match'
            ELSE (r.lo - b.lo) || ' / ' || (r.hi - b.hi) END
FROM param r CROSS JOIN bounds b
UNION ALL SELECT 'D3: same club-seasons capped, capped values match', 'match',
       CASE WHEN cap_flag_differs = 0 AND max_capped_gap < (SELECT t FROM tol) THEN 'match'
            ELSE cap_flag_differs || ' / ' || max_capped_gap END FROM club_cmp
UNION ALL SELECT 'normalisation: alternating projections converged (leftover < 1e-12)', 'yes',
       CASE WHEN v < 1e-12 THEN 'yes' ELSE v::text END FROM leftover
UNION ALL SELECT 'normalisation: fitted effects constant within every league-season cell', 'yes',
       CASE WHEN max(spread) < 1e-9 THEN 'yes' ELSE max(spread)::text END FROM cell_effect
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
       JOIN score.run_parameter r ON r.pillar = 'trading_profit' AND r.name = 'league_effect_vs_Bundesliga:' || l.competition_name
       WHERE abs(r.value - l.effect) >= 1e-9
UNION ALL
SELECT 'normalisation: a league effect recorded for all five leagues', '5',
       count(*)::text FROM score.run_parameter WHERE pillar = 'trading_profit' AND name LIKE 'league_effect_vs_Bundesliga:%'
UNION ALL
SELECT 'normalisation: profit carries no squad-value signal (|r| < 1e-9)', '0',
       (CASE WHEN abs(corr(r.profit_normalised, ln(cs.squad_value_start_eur::float8))) < 1e-9 THEN 0 ELSE 1 END)::text
FROM score.club_season_trading r JOIN fact_club_season cs USING (club_season_key)
),
judged AS (
    SELECT check_name, expected, actual, CASE WHEN expected = actual THEN 'PASS' ELSE 'FAIL' END AS status FROM checks
)
SELECT check_name, expected, actual, status
FROM judged
ORDER BY CASE status WHEN 'FAIL' THEN 0 ELSE 1 END, check_name;
