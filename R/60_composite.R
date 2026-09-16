# The composite efficiency index: the four pillars combined into one number per club-season.
#
# Weights (decided by Tyler, 2026-09-16, after the evidence below):
#   recruitment ROI 0.30, trading profit 0.30, value growth 0.30, sporting return 0.10.
#
# The scoping doc originally said the weights would be derived by regressing each pillar on sporting
# success. Measured, that method fails three ways:
#   - wrong signs: trading profit takes a negative coefficient against every success target, and at club
#     level so does recruitment ROI (-0.198 against points, -0.167 against trophies);
#   - no signal: R2 between 0.006 (next-season points) and 0.129 (points above squad-value expectation);
#   - unstable: leave-one-season-out coefficients move as much as the coefficients themselves.
# It is structural, not a fixable choice of target: the pillars are residualised against squad value by
# design, while league points correlate 0.67 with squad value, so efficiency and success are partly
# opposed. Clipping the negative weights would collapse the index onto value growth alone (0.81 of the
# weight), which defeats having four pillars.
#
# Sporting return therefore carries a deliberate 0.10: enough to serve the purpose the scoping doc gives
# Pillar 4 (stopping "efficient" from meaning cheap and bad) without turning the index into a success
# ranking. At 0.10 the club ranking still correlates 0.96 with the money-only version, while the average
# league-points z of the top ten rises from +0.23 to +0.72.
#
# Verified by sql/61_check_composite.sql.
#
# Run from the repository root, after the four pillar scripts:
#   "C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\60_composite.R

source("R/db.R")
suppressPackageStartupMessages(library(dplyr))

WEIGHTS <- tibble::tribble(
  ~pillar,           ~source_table,                    ~source_column, ~weight, ~rationale,
  "recruitment_roi", "score.club_season_recruitment",  "roi_z",          0.30,
  "Output delivered by signings per deflated euro. Equal weight: the money pillars are near independent (|r| <= 0.20) and already standardised, and the evidence does not support ranking one above another.",
  "trading_profit",  "score.club_season_trading",      "profit_z",       0.30,
  "Realised profit on sales against value at acquisition. Equal weight, for the same reason.",
  "value_growth",    "score.club_season_value_growth", "growth_z",       0.30,
  "Unrealised growth of players still held, net of value written off. Equal weight, for the same reason.",
  "sporting_return", "score.club_season_sporting",     "ppm_z",          0.10,
  "League points per match. A deliberate guard rail rather than a derived weight: it stops a cheap and bad club topping the index, and at 0.10 it does that while leaving the ranking 0.96 correlated with the money-only version."
)
stopifnot("weights sum to 1" = abs(sum(WEIGHTS$weight) - 1) < 1e-12)

con <- warehouse_connect()
on.exit(dbDisconnect(con), add = TRUE)

pillars <- dbGetQuery(con, "
  SELECT cs.club_season_key, cs.club_key, cs.season_key,
         r.roi_z::float8      AS recruitment_z,
         r.is_provisional,
         t.profit_z::float8   AS trading_z,
         g.growth_z::float8   AS value_growth_z,
         sp.ppm_z::float8     AS sporting_z
  FROM fact_club_season cs
  JOIN score.club_season_recruitment r USING (club_season_key)
  JOIN score.club_season_trading t USING (club_season_key)
  JOIN score.club_season_value_growth g USING (club_season_key)
  JOIN score.club_season_sporting sp USING (club_season_key)")
cat("read", nrow(pillars), "club-seasons with all four pillars joined\n")

w <- setNames(WEIGHTS$weight, c("recruitment_z", "trading_z", "value_growth_z", "sporting_z"))

# Below the recruitment spend floor there is no Pillar 1 score, so its weight is dropped and the rest
# renormalised: the club-season is still scored on what it has, and flagged.
eff <- pillars %>%
  mutate(is_recruitment_missing = is.na(recruitment_z),
         weight_applied = if_else(is_recruitment_missing, sum(w) - w[["recruitment_z"]], sum(w)),
         # if_else, not arithmetic with a negated flag: in R `!` binds looser than `*` and `+`, so
         # `z * weight * !missing + ...` silently negates the whole sum instead of the flag.
         recruitment_term = if_else(is_recruitment_missing, 0, recruitment_z * w[["recruitment_z"]]),
         efficiency_index = (recruitment_term +
                             trading_z      * w[["trading_z"]] +
                             value_growth_z * w[["value_growth_z"]] +
                             sporting_z     * w[["sporting_z"]]) / weight_applied,
         efficiency_z = (efficiency_index - mean(efficiency_index)) / sd(efficiency_index))

stopifnot(
  "684 club-seasons"                      = nrow(eff) == 684,
  "no missing index"                      = !anyNA(eff$efficiency_index),
  "only recruitment is ever missing"      = !anyNA(eff[c("trading_z", "value_growth_z", "sporting_z")]),
  "renormalised rows match the floor"     = sum(eff$is_recruitment_missing) ==
    dbGetQuery(con, "SELECT count(*) AS n FROM score.club_season_recruitment WHERE is_insufficient_spend")$n,
  "weights applied are 1 or 0.7"          = all(abs(eff$weight_applied - ifelse(eff$is_recruitment_missing, 0.7, 1)) < 1e-12)
)

invisible(dbWithTransaction(con, {
  run_sql_file(con, "sql/60_score_composite.sql")
  dbWriteTable(con, Id(schema = "score", table = "composite_weight"), as.data.frame(WEIGHTS), append = TRUE)
  dbWriteTable(con, Id(schema = "score", table = "club_season_efficiency"),
               select(eff, club_season_key, club_key, season_key, recruitment_z, trading_z, value_growth_z,
                      sporting_z, weight_applied, is_recruitment_missing, is_provisional, efficiency_index,
                      efficiency_z),
               append = TRUE)
}))

cat(sprintf("wrote score.club_season_efficiency: %d club-seasons (%d renormalised without recruitment, %d provisional)\n",
            nrow(eff), sum(eff$is_recruitment_missing), sum(eff$is_provisional)))

cat("\nchecking against SQL (sql/61_check_composite.sql):\n")
run_checks(con, "sql/61_check_composite.sql")
