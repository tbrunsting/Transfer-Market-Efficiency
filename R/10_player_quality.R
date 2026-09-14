# Player quality: output per 90, scored against the player's own position group (scoping doc 4.4, 4.5).
#
# Grain: one row per fact_player_season row (player x club x season).
# Floor: 900 minutes for that club that season. Below it the row is kept but unscored, with the reason:
#        a per-90 rate from a handful of appearances is noise, not quality.
# Gaps:  a row over the floor missing more than one of its group's metrics is unscored too, for that
#        reason; a missing input is a gap in the data, never a zero.
# Method: 1. each metric is a per-90 rate or a ratio;
#         2. each is z-scored within season x position group, over scored rows only;
#         3. a player's composite is the mean of their metric z-scores;
#         4. the composite is re-standardised within season x group, so 0 is that group's average that
#            season and 1 is one standard deviation, whatever the group.
# Within-season standardisation also absorbs the pre-2022/23 passing-file vintage (is_old_vintage).
#
# Possession (centre-backs only): a centre-back's defensive volume depends on how much the team defends.
# Raw, elite centre-backs at dominant teams ranked low (Dias 25th percentile, Skriniar 10th). A full
# StatsBomb-style adjustment overcorrected: CB quality then tracked team possession at r = 0.75, one
# club-size effect swapped for another. So those four stats take the residual of a within-season
# regression on team possession instead: the part possession explains is removed, nothing more
# (decided 2026-09-14). CM and FB defensive stats showed no possession link (r -0.10 to 0.00) and stay
# raw. Centre-backs remain the worst-measured group; see docs/phase-3-scoring.md.
#
# Verified by sql/11_check_player_quality.sql, which recomputes every value independently in SQL.
#
# Run from the repository root:
#   "C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\10_player_quality.R

source("R/db.R")
suppressPackageStartupMessages({
  library(dplyr)
  library(tidyr)
})

MINUTES_FLOOR <- 900

# Which metrics judge which job. Defenders on defending and progressive passing, creators on
# shot-creating actions (scoping doc 4.4). Kept here, in one table, so the choice is easy to read and change.
METRICS <- tibble::tribble(
  ~position_group, ~metric,
  "GK",   "psxg_minus_ga_p90",       # shot-stopping: goals prevented beyond what the shots faced deserved
  "GK",   "save_pct",
  "CB",   "tackles_won_p90",
  "CB",   "interceptions_p90",
  "CB",   "clearances_p90",
  "CB",   "aerials_won_p90",
  "CB",   "aerial_win_pct",
  "CB",   "progressive_passes_p90",
  "FB",   "tackles_won_p90",
  "FB",   "interceptions_p90",
  "FB",   "progressive_passes_p90",
  "FB",   "progressive_carries_p90",
  "FB",   "sca_p90",
  "CM",   "tackles_won_p90",
  "CM",   "interceptions_p90",
  "CM",   "progressive_passes_p90",
  "CM",   "passes_into_final_third_p90",
  "CM",   "progressive_carries_p90",
  "CM",   "sca_p90",
  "AM/W", "sca_p90",
  "AM/W", "xag_p90",
  "AM/W", "npxg_p90",
  "AM/W", "take_ons_won_p90",
  "AM/W", "progressive_carries_p90",
  "FW",   "npxg_p90",
  "FW",   "goals_p90",
  "FW",   "xag_p90",
  "FW",   "sca_p90"
)

# Metrics whose value is the residual on team possession before z-scoring (see the header).
POSSESSION_ADJUSTED <- tibble::tibble(
  position_group = "CB",
  metric = c("tackles_won_p90", "interceptions_p90", "clearances_p90", "aerials_won_p90")
)

zscore <- function(x) (x - mean(x, na.rm = TRUE)) / sd(x, na.rm = TRUE)
# y minus its least-squares fit on x: what x does not explain.
residual_on <- function(y, x) {
  slope <- cov(y, x) / var(x)
  y - (mean(y) + slope * (x - mean(x)))
}
ratio  <- function(num, den) ifelse(den > 0, num / den, NA_real_)

con <- warehouse_connect()
on.exit(dbDisconnect(con), add = TRUE)

ps <- dbGetQuery(con, "
  SELECT f.player_season_key, f.player_key, f.club_key, f.season_key, f.position_group_key,
         g.position_group, f.minutes,
         f.goals, f.npxg, f.xag, f.sca, f.tackles_won, f.interceptions, f.clearances,
         f.aerials_won, f.aerials_lost, f.progressive_passes, f.progressive_carries,
         f.passes_into_final_third, f.take_ons_won, f.gk_saves, f.gk_goals_against, f.gk_psxg,
         cs.possession_pct::float8 AS possession_pct
  FROM fact_player_season f
  JOIN dim_position_group g USING (position_group_key)
  JOIN fact_club_season cs USING (club_key, season_key)")
cat("read", nrow(ps), "player-seasons from fact_player_season\n")

unknown <- setdiff(unique(ps$position_group), METRICS$position_group)
if (length(unknown)) stop("position groups with no metric set: ", paste(unknown, collapse = ", "))

rates <- ps %>%
  mutate(
    is_scored                   = minutes >= MINUTES_FLOOR,
    n90                         = minutes / 90,
    goals_p90                   = goals / n90,
    npxg_p90                    = npxg / n90,
    xag_p90                     = xag / n90,
    sca_p90                     = sca / n90,
    tackles_won_p90             = tackles_won / n90,
    interceptions_p90           = interceptions / n90,
    clearances_p90              = clearances / n90,
    aerials_won_p90             = aerials_won / n90,
    aerial_win_pct              = ratio(aerials_won, aerials_won + aerials_lost),
    progressive_passes_p90      = progressive_passes / n90,
    progressive_carries_p90     = progressive_carries / n90,
    passes_into_final_third_p90 = passes_into_final_third / n90,
    take_ons_won_p90            = take_ons_won / n90,
    save_pct                    = ratio(gk_saves, gk_saves + gk_goals_against),
    psxg_minus_ga_p90           = (gk_psxg - gk_goals_against) / n90
  )

# A missing input is a data gap, not low output, so it is never treated as zero. A row missing at most one
# of its group's metrics is scored on the rest (n_metrics says so); a row missing more, or left with fewer
# than two, is unscored with the gap as its reason. In the current load that is Karazor's three
# Stuttgart seasons (no SCA; scored on 5 of 6) and the blank-filled rows with no advanced stats (unscored).
inputs <- rates %>%
  filter(is_scored) %>%
  select(player_season_key, season_key, position_group, possession_pct, all_of(unique(METRICS$metric))) %>%
  pivot_longer(all_of(unique(METRICS$metric)), names_to = "metric", values_to = "value") %>%
  semi_join(METRICS, by = c("position_group", "metric"))

coverage <- inputs %>%
  group_by(player_season_key, position_group) %>%
  summarise(n_have = sum(!is.na(value)), n_need = n(), .groups = "drop") %>%
  mutate(enough_data = n_have >= pmax(n_need - 1, 2))

gaps <- filter(coverage, n_have < n_need)
if (nrow(gaps)) {
  cat("\nrows over the minutes floor with a missing metric input:\n")
  print(count(gaps, position_group, n_have, n_need, enough_data), n = Inf)
}

rates <- rates %>%
  left_join(select(coverage, player_season_key, n_have, n_need, enough_data), by = "player_season_key") %>%
  mutate(unscored_reason = case_when(
           !is_scored   ~ paste("under", MINUTES_FLOOR, "minutes"),
           !enough_data ~ sprintf("advanced stats missing from the FBref snapshot (%d of %d metrics)", n_have, n_need),
           TRUE         ~ NA_character_),
         is_scored = is.na(unscored_reason))

metric_long <- inputs %>%
  semi_join(filter(rates, is_scored), by = "player_season_key") %>%
  filter(!is.na(value)) %>%
  left_join(mutate(POSSESSION_ADJUSTED, possession_adjusted = TRUE), by = c("position_group", "metric")) %>%
  mutate(possession_adjusted = coalesce(possession_adjusted, FALSE)) %>%
  group_by(season_key, position_group, metric) %>%
  mutate(value_used = if (first(possession_adjusted)) residual_on(value, possession_pct) else value,
         z = zscore(value_used)) %>%
  ungroup()

# By construction an adjusted metric is uncorrelated with possession within each season.
leftover <- metric_long %>% filter(possession_adjusted) %>%
  group_by(season_key, metric) %>% summarise(r = cor(value_used, possession_pct), .groups = "drop")
stopifnot("adjusted metrics carry no possession signal" = all(abs(leftover$r) < 1e-9))

quality <- metric_long %>%
  group_by(player_season_key, season_key, position_group) %>%
  summarise(composite = mean(z, na.rm = TRUE), n_metrics = sum(!is.na(z)), .groups = "drop") %>%
  group_by(season_key, position_group) %>%
  mutate(quality_z = zscore(composite),
         quality_pctile = 100 * percent_rank(quality_z)) %>%
  ungroup()

out <- rates %>%
  select(player_season_key, player_key, club_key, season_key, position_group_key, minutes, is_scored,
         unscored_reason) %>%
  left_join(select(quality, player_season_key, n_metrics, quality_z, quality_pctile), by = "player_season_key") %>%
  mutate(n_metrics = as.integer(n_metrics))

# ---- guards before anything is written -------------------------------------------------------------
stopifnot(
  "every fact_player_season row is present exactly once" =
    nrow(out) == nrow(ps) && !anyDuplicated(out$player_season_key),
  "every scored row has a score" = all(!is.na(out$quality_z[out$is_scored])),
  "no unscored row has a score"  = all(is.na(out$quality_z[!out$is_scored])),
  "no metric input is missing"   = !anyNA(metric_long$value) && !anyNA(metric_long$z)
)
cells <- quality %>% group_by(season_key, position_group) %>%
  summarise(n = n(), m = mean(quality_z), s = sd(quality_z), .groups = "drop")
stopifnot(
  "42 season x group cells (7 x 6)" = nrow(cells) == 42,
  "each cell has mean 0 and sd 1"   = all(abs(cells$m) < 1e-9 & abs(cells$s - 1) < 1e-9)
)

# ---- write -----------------------------------------------------------------------------------------
invisible(dbWithTransaction(con, {
  run_sql_file(con, "sql/10_score_player_quality.sql")
  dbWriteTable(con, Id(schema = "score", table = "player_quality"),
               select(out, player_season_key, player_key, club_key, season_key, position_group_key, minutes,
                      is_scored, unscored_reason, n_metrics, quality_z, quality_pctile),
               append = TRUE)
  dbWriteTable(con, Id(schema = "score", table = "player_quality_metric"),
               select(metric_long, player_season_key, metric, value, value_used, possession_adjusted, z),
               append = TRUE)
}))
cat(sprintf("\nwrote score.player_quality: %d rows, %d scored (%.1f%% of minutes); score.player_quality_metric: %d rows\n",
            nrow(out), sum(out$is_scored),
            100 * sum(out$minutes[out$is_scored]) / sum(out$minutes), nrow(metric_long)))

cat("\nchecking against SQL (sql/11_check_player_quality.sql):\n")
run_checks(con, "sql/11_check_player_quality.sql")
