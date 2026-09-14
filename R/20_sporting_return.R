# Pillar 4, sporting return: league points per match, standardised within league-season (scoping doc 5).
#
# Grain: one row per fact_club_season row (club x big-five season).
# Per match, not total points: Bundesliga and 18-club Ligue 1 seasons are 34 matches, the rest 38, and
# Ligue 1 2019/20 stopped after 27-28. Results points, not official: deductions are administrative, and
# this pillar measures what happened on the pitch.
#
# Verified by sql/21_check_sporting_return.sql, which recomputes every value independently in SQL.
#
# Run from the repository root:
#   "C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\20_sporting_return.R

source("R/db.R")
suppressPackageStartupMessages(library(dplyr))

con <- warehouse_connect()
on.exit(dbDisconnect(con), add = TRUE)

cs <- dbGetQuery(con, "
  SELECT club_season_key, club_key, season_key, competition_key, matches, points_from_results, has_known_deduction
  FROM fact_club_season")
cat("read", nrow(cs), "club-seasons from fact_club_season\n")

out <- cs %>%
  mutate(points_per_match = points_from_results / matches) %>%
  group_by(competition_key, season_key) %>%
  mutate(ppm_z = (points_per_match - mean(points_per_match)) / sd(points_per_match)) %>%
  ungroup()

cells <- out %>% group_by(competition_key, season_key) %>%
  summarise(n = n(), m = mean(ppm_z), s = sd(ppm_z), .groups = "drop")
stopifnot(
  "684 club-seasons"                         = nrow(out) == 684,
  "35 league-seasons (5 leagues x 7 seasons)" = nrow(cells) == 35,
  "each league-season has mean 0 and sd 1"    = all(abs(cells$m) < 1e-9 & abs(cells$s - 1) < 1e-9),
  "no missing values"                        = !anyNA(out$ppm_z)
)

invisible(dbWithTransaction(con, {
  run_sql_file(con, "sql/20_score_sporting_return.sql")
  dbWriteTable(con, Id(schema = "score", table = "club_season_sporting"),
               select(out, club_season_key, club_key, season_key, competition_key, matches, points_from_results,
                      points_per_match, ppm_z, has_known_deduction),
               append = TRUE)
}))
cat("wrote score.club_season_sporting:", nrow(out), "rows\n")

cat("\nchecking against SQL (sql/21_check_sporting_return.sql):\n")
run_checks(con, "sql/21_check_sporting_return.sql")
