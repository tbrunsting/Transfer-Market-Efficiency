# Pillar 1, recruitment ROI: output delivered by a club-season's signings per deflated euro of fee.
#
# Cohort: every arrival into a club for a big-five season it played (loan returns are not arrivals).
# Credit: a player-season's output goes to the arrival that brought the player to that club.
#         - fee, free and undisclosed signings keep it through loans out, until a permanent departure
#           (sold, released, undisclosed move). Otherwise Julian Alvarez, bought by Manchester City and
#           loaned straight back to River Plate, would show no output for his fee.
#         - a loan's credit ends at the next departure of any kind.
#         - a newer arrival to the same club always takes over; events on the same date do not end each other.
# Output: season-equivalents x quality weight, summed over the first HORIZON seasons (signing season = 0).
#         season-equivalents = minutes / 90 / the club's league matches (34- and 38-game seasons compare);
#         quality weight = quality_pctile / 100, 0 under the minutes floor, DATA_GAP_WEIGHT where the
#         FBref snapshot lacks the stats.
# Spend:  disclosed fees that count as spend (permanent_with_fee, loan_with_fee), each divided by that
#         season's median fee (scoping doc 4.6).
# Guardrails (scoping doc 5):
#         - spend below the 10th percentile of all club-seasons: labelled insufficient spend, not scored;
#         - log ROI = log(output + EPS) - log(spend);
#         - scale: the residual of log ROI on log squad value at season start, with season and league
#           effects. Clubs are judged against their own league's market: without the league term the Premier
#           League's broadcast money, which squad value does not capture, made 11 of the bottom 12 clubs
#           English. The league effect itself is published as its own finding (score.league_premium),
#           not silently removed.
#
# Verified by sql/31_check_recruitment_roi.sql, which recomputes every value independently in SQL.
#
# Run from the repository root, after R/10_player_quality.R:
#   "C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\30_recruitment_roi.R

source("R/db.R")
suppressPackageStartupMessages(library(dplyr))

# ---- parameters (decisions: Tyler, 2026-09-14; see docs/phase-3-scoring.md) --------------------------
HORIZON                    <- 3L     # decided: captures development signings; 2022/23 and 2023/24 provisional
INCLUDE_UNDISCLOSED_OUTPUT <- FALSE  # decided: scoping doc 7, "excluded and documented, not guessed"
LEAGUE_EFFECTS             <- TRUE   # decided: normalise within league; publish the league effect separately
REFERENCE_LEAGUE           <- "Bundesliga"
UNDER_FLOOR_WEIGHT         <- 0      # sensitivity-tested: 0.25 moves club ranks by rho 0.99
DATA_GAP_WEIGHT            <- 0.5    # 9 player-seasons over the floor with no advanced stats: group median
EPS                        <- 0.1    # output offset inside the log; 0.5 moves club ranks by rho 0.98
SPEND_FLOOR_QUANTILE       <- 0.10
LAST_SCORED_YEAR           <- 2024L
PERMANENT_DEPARTURES       <- c("permanent_with_fee", "free", "undisclosed")
LOAN_ARRIVALS              <- c("loan", "loan_with_fee")

con <- warehouse_connect()
on.exit(dbDisconnect(con), add = TRUE)

transfers <- dbGetQuery(con, "
  SELECT t.transfer_key, t.player_key, t.from_club_key, t.to_club_key, s.season_key, s.season_end_year AS yr,
         tt.transfer_type, tt.counts_as_spend, t.fee_eur::float8 AS fee_eur,
         (t.fee_eur / s.median_fee_eur)::float8 AS fee_median_units, d.full_date
  FROM fact_transfer t
  JOIN dim_transfer_type tt USING (transfer_type_key)
  JOIN dim_season s USING (season_key)
  JOIN dim_date d ON d.date_key = t.transfer_date_key")

club_seasons <- dbGetQuery(con, "
  SELECT cs.club_season_key, cs.club_key, cs.season_key, s.season_end_year AS yr, cs.competition_key,
         co.competition_name, cs.squad_value_start_eur::float8 AS squad_value
  FROM fact_club_season cs JOIN dim_season s USING (season_key) JOIN dim_competition co USING (competition_key)")

player_seasons <- dbGetQuery(con, "
  SELECT f.player_season_key, f.player_key, f.club_key, f.season_key, s.season_end_year AS yr,
         f.minutes, f.team_matches_available, q.is_scored, q.unscored_reason, q.quality_pctile
  FROM fact_player_season f
  JOIN dim_season s USING (season_key)
  JOIN score.player_quality q USING (player_season_key)")
stopifnot("score.player_quality is current" = nrow(player_seasons) ==
            dbGetQuery(con, "SELECT count(*) AS n FROM fact_player_season")$n)

# ---- arrivals and when each one's credit ends --------------------------------------------------------
arrivals <- transfers %>%
  filter(transfer_type != "loan_return") %>%
  semi_join(club_seasons, by = c("to_club_key" = "club_key", "season_key")) %>%
  rename(club_key = to_club_key, arrival_date = full_date, arrival_yr = yr, arrival_season_key = season_key)

departures <- transfers %>%
  select(player_key, club_key = from_club_key, departure_date = full_date, departure_yr = yr, departure_type = transfer_type)

ends <- arrivals %>%
  select(transfer_key, player_key, club_key, arrival_date, transfer_type) %>%
  left_join(departures, by = c("player_key", "club_key"), relationship = "many-to-many") %>%
  filter(departure_date > arrival_date,
         transfer_type %in% LOAN_ARRIVALS | departure_type %in% PERMANENT_DEPARTURES) %>%
  group_by(transfer_key) %>%
  summarise(end_yr = min(departure_yr), .groups = "drop")

arrivals <- arrivals %>%
  left_join(ends, by = "transfer_key") %>%
  mutate(end_yr = coalesce(end_yr, LAST_SCORED_YEAR))

# ---- credit each player-season to one arrival --------------------------------------------------------
credit <- player_seasons %>%
  inner_join(select(arrivals, transfer_key, player_key, club_key, arrival_date, arrival_yr, arrival_season_key,
                    end_yr, transfer_type),
             by = c("player_key", "club_key"), relationship = "many-to-many") %>%
  filter(yr >= arrival_yr, yr <= end_yr, yr - arrival_yr < HORIZON) %>%
  arrange(player_season_key, desc(arrival_date), desc(transfer_key)) %>%
  distinct(player_season_key, .keep_all = TRUE) %>%
  mutate(season_offset        = yr - arrival_yr,
         season_equivalents   = minutes / 90 / team_matches_available,
         quality_weight       = case_when(is_scored ~ quality_pctile / 100,
                                          grepl("^advanced stats missing", unscored_reason) ~ DATA_GAP_WEIGHT,
                                          TRUE ~ UNDER_FLOOR_WEIGHT),
         output               = season_equivalents * quality_weight,
         counts_toward_output = INCLUDE_UNDISCLOSED_OUTPUT | transfer_type != "undisclosed")

# ---- club-season cohorts -----------------------------------------------------------------------------
cohort_arrivals <- arrivals %>%
  group_by(club_key, season_key = arrival_season_key) %>%
  summarise(n_arrivals        = n(),
            n_spend_signings  = sum(counts_as_spend),
            n_undisclosed     = sum(transfer_type == "undisclosed"),
            spend_eur         = sum(fee_eur[counts_as_spend], na.rm = TRUE),
            spend_median_fees = sum(fee_median_units[counts_as_spend], na.rm = TRUE),
            .groups = "drop")

cohort_output <- credit %>%
  group_by(club_key, season_key = arrival_season_key) %>%
  # undisclosed first: summarise() evaluates in order, so a line after output = sum(...) would see the total
  summarise(output_undisclosed = sum(output[transfer_type == "undisclosed"]),
            output             = sum(output[counts_toward_output]),
            .groups = "drop")

rec <- club_seasons %>%
  left_join(cohort_arrivals, by = c("club_key", "season_key")) %>%
  left_join(cohort_output, by = c("club_key", "season_key")) %>%
  mutate(across(c(n_arrivals, n_spend_signings, n_undisclosed), ~ as.integer(coalesce(.x, 0L))),
         across(c(spend_eur, spend_median_fees, output, output_undisclosed), ~ coalesce(.x, 0)))

spend_floor <- unname(quantile(rec$spend_median_fees, SPEND_FLOOR_QUANTILE, type = 7))

rec <- rec %>%
  mutate(is_insufficient_spend = spend_median_fees < spend_floor,
         is_provisional        = yr + HORIZON - 1L > LAST_SCORED_YEAR,
         log_roi               = if_else(is_insufficient_spend, NA_real_, log(output + EPS) - log(spend_median_fees)))

scored <- filter(rec, !is_insufficient_spend) %>%
  mutate(league = relevel(factor(competition_name), ref = REFERENCE_LEAGUE))
stopifnot("league effects are on" = LEAGUE_EFFECTS)
fit <- lm(log_roi ~ log(squad_value) + factor(season_key) + league, data = scored)
scored$roi_normalised <- unname(residuals(fit))
scored$roi_z <- scored$roi_normalised / sd(scored$roi_normalised)

rec <- rec %>%
  left_join(select(scored, club_season_key, roi_normalised, roi_z), by = "club_season_key")

# ---- guards ------------------------------------------------------------------------------------------
stopifnot(
  "684 club-seasons"                                = nrow(rec) == 684,
  "each player-season credited at most once"        = !anyDuplicated(credit$player_season_key),
  "credit stays inside the horizon"                 = all(credit$season_offset >= 0 & credit$season_offset < HORIZON),
  "scored exactly when spend clears the floor"      = all(is.na(rec$roi_normalised) == rec$is_insufficient_spend),
  "residuals carry no squad-value signal"           = abs(cor(scored$roi_normalised, log(scored$squad_value))) < 1e-9,
  "residuals average zero in every league"          = all(abs(tapply(scored$roi_normalised, scored$league, mean)) < 1e-9),
  "spend is never negative"                         = all(rec$spend_median_fees >= 0)
)

# ---- the league effect, published as a finding ------------------------------------------------------
league_premium <- scored %>%
  group_by(competition_key, competition_name) %>%
  summarise(n_club_seasons           = n(),
            median_output_per_spend  = median(output / spend_median_fees),
            .groups = "drop") %>%
  mutate(log_effect_vs_reference = vapply(competition_name, function(l)
           if (l == REFERENCE_LEAGUE) 0 else unname(coef(fit)[[paste0("league", l)]]), numeric(1)),
         output_per_euro_vs_reference = exp(log_effect_vs_reference),
         reference_league = REFERENCE_LEAGUE)
cat("
league effect on log ROI, same squad value and season, vs", REFERENCE_LEAGUE, "
")
print(as.data.frame(select(league_premium, competition_name, n_club_seasons, log_effect_vs_reference,
                           output_per_euro_vs_reference, median_output_per_spend)), row.names = FALSE)

params <- data.frame(
  pillar = "recruitment_roi",
  name   = c("horizon_seasons", "include_undisclosed_output", "under_floor_weight", "data_gap_weight", "eps",
             "spend_floor_quantile", "spend_floor_median_fees", "squad_value_log_slope", "roi_normalised_sd"),
  value  = c(HORIZON, as.numeric(INCLUDE_UNDISCLOSED_OUTPUT), UNDER_FLOOR_WEIGHT, DATA_GAP_WEIGHT, EPS,
             SPEND_FLOOR_QUANTILE, spend_floor, unname(coef(fit)[["log(squad_value)"]]), sd(scored$roi_normalised)),
  note   = c("decided 2026-09-14", "decided 2026-09-14", NA, NA, NA, NA, "fitted", "fitted", "fitted"))

invisible(dbWithTransaction(con, {
  run_sql_file(con, "sql/30_score_recruitment_roi.sql")
  dbWriteTable(con, Id(schema = "score", table = "signing_credit"),
               select(credit, player_season_key, transfer_key, club_key, arrival_season_key, season_key,
                      season_offset, transfer_type, season_equivalents, quality_weight, output, counts_toward_output),
               append = TRUE)
  dbWriteTable(con, Id(schema = "score", table = "club_season_recruitment"),
               select(rec, club_season_key, club_key, season_key, n_arrivals, n_spend_signings, n_undisclosed,
                      spend_eur, spend_median_fees, output, output_undisclosed, is_insufficient_spend,
                      is_provisional, log_roi, roi_normalised, roi_z),
               append = TRUE)
  dbWriteTable(con, Id(schema = "score", table = "run_parameter"), params, append = TRUE)
  dbWriteTable(con, Id(schema = "score", table = "league_premium"),
               select(league_premium, competition_key, competition_name, reference_league, n_club_seasons,
                      log_effect_vs_reference, output_per_euro_vs_reference, median_output_per_spend),
               append = TRUE)
}))
cat(sprintf("wrote score.club_season_recruitment: %d club-seasons, %d scored, %d insufficient spend (floor %.3f median fees), %d provisional\n",
            nrow(rec), sum(!rec$is_insufficient_spend), sum(rec$is_insufficient_spend), spend_floor, sum(rec$is_provisional)))
cat(sprintf("score.signing_credit: %d player-seasons credited; log squad value slope %.3f\n",
            nrow(credit), coef(fit)[["log(squad_value)"]]))

cat("\nchecking against SQL (sql/31_check_recruitment_roi.sql):\n")
run_checks(con, "sql/31_check_recruitment_roi.sql")
