# Pillar 2, trading profit: realised profit on a club-season's sales (scoping doc 5).
#
# Sales:  every transfer out of a club in a season it played in the big five.
#         - permanent_with_fee: income = the fee, profit = fee - basis;
#         - loan_with_fee: the loan fee is income at zero basis (the player is still owned);
#         - undisclosed: excluded from income, counted for context (scoping doc 7).
# Basis:  market value when the club acquired the player (decided 2026-09-14):
#         - the latest arrival into the club before the sale, other than a loan return; loans out do not
#           end ownership (Morata's Chelsea sale links to the 2017 purchase, not his loan to Atletico);
#         - no such arrival: the player was already there when the window opened, and is treated as
#           acquired at market value on 1 July 2017. The frozen pre-2017 transfer table is incomplete
#           (it has no record of Chelsea buying Hazard), so it is never used.
#         Valuation: the latest within 365 days before the basis date; failing that the first within 180
#         days after; failing both, 0. Valuations are matched by player and date only: the valuation's
#         club field is unreliable (docs/phase-3-scoring.md, known limitations).
# Realised only: value growth in players still held belongs to Pillar 3, so nothing is counted twice.
# Scale:  profit in that season's median fees, winsorised at the 1st/99th percentiles of club-seasons,
#         then the Pillar 1 normalisation: residual on log squad value with season and league effects.
#
# Verified by sql/41_check_trading_profit.sql, which recomputes every value independently in SQL.
#
# Run from the repository root:
#   "C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\40_trading_profit.R

source("R/db.R")
suppressPackageStartupMessages(library(dplyr))

WINDOW_OPEN          <- as.Date("2017-07-01")
BACKWARD_DAYS        <- 365
FORWARD_DAYS         <- 180
WINSOR_QUANTILES     <- c(0.01, 0.99)
REFERENCE_LEAGUE     <- "Bundesliga"

con <- warehouse_connect()
on.exit(dbDisconnect(con), add = TRUE)

transfers <- dbGetQuery(con, "
  SELECT t.transfer_key, t.player_key, t.from_club_key, t.to_club_key, t.season_key, tt.transfer_type,
         t.fee_eur::float8 AS fee_eur, d.full_date
  FROM fact_transfer t
  JOIN dim_transfer_type tt USING (transfer_type_key)
  JOIN dim_date d ON d.date_key = t.transfer_date_key")

club_seasons <- dbGetQuery(con, "
  SELECT cs.club_season_key, cs.club_key, cs.season_key, s.median_fee_eur::float8 AS median_fee,
         co.competition_name, cs.squad_value_start_eur::float8 AS squad_value
  FROM fact_club_season cs JOIN dim_season s USING (season_key) JOIN dim_competition co USING (competition_key)")

valuations <- dbGetQuery(con, "
  SELECT v.player_key, d.full_date AS valuation_date, v.market_value_eur::float8 AS market_value
  FROM fact_player_valuation v JOIN dim_date d ON d.date_key = v.valuation_date_key")

stopifnot("fact_transfer starts at the window" = min(transfers$full_date) >= WINDOW_OPEN)

# ---- sales and the arrival that owns each one --------------------------------------------------------
sales <- transfers %>%
  filter(transfer_type %in% c("permanent_with_fee", "loan_with_fee", "undisclosed")) %>%
  inner_join(select(club_seasons, club_key, season_key), by = c("from_club_key" = "club_key", "season_key")) %>%
  rename(club_key = from_club_key, sale_date = full_date)

owning <- sales %>%
  filter(transfer_type == "permanent_with_fee") %>%
  select(transfer_key, player_key, club_key, sale_date) %>%
  inner_join(transfers %>% filter(transfer_type != "loan_return") %>%
               select(owning_arrival_key = transfer_key, player_key, club_key = to_club_key, arrival_date = full_date),
             by = c("player_key", "club_key"), relationship = "many-to-many") %>%
  filter(arrival_date < sale_date) %>%
  arrange(transfer_key, desc(arrival_date), desc(owning_arrival_key)) %>%
  distinct(transfer_key, .keep_all = TRUE) %>%
  select(transfer_key, owning_arrival_key, arrival_date)

sales <- sales %>%
  left_join(owning, by = "transfer_key") %>%
  mutate(basis_source = case_when(transfer_type == "undisclosed"   ~ "undisclosed_excluded",
                                  transfer_type == "loan_with_fee" ~ "loan_fee_received",
                                  !is.na(owning_arrival_key)       ~ "window_arrival",
                                  TRUE                             ~ "at_club_on_2017_07_01"),
         basis_date = case_when(basis_source == "window_arrival"        ~ arrival_date,
                                basis_source == "at_club_on_2017_07_01" ~ WINDOW_OPEN))

# ---- value each basis --------------------------------------------------------------------------------
needs <- sales %>% filter(!is.na(basis_date)) %>% select(transfer_key, player_key, basis_date)
cand <- needs %>% inner_join(valuations, by = "player_key", relationship = "many-to-many")
backward <- cand %>%
  filter(valuation_date <= basis_date, valuation_date >= basis_date - BACKWARD_DAYS) %>%
  group_by(transfer_key) %>% slice_max(valuation_date, n = 1, with_ties = FALSE) %>% ungroup() %>%
  transmute(transfer_key, bw_date = valuation_date, bw_value = market_value)
forward <- cand %>%
  filter(valuation_date > basis_date, valuation_date <= basis_date + FORWARD_DAYS) %>%
  group_by(transfer_key) %>% slice_min(valuation_date, n = 1, with_ties = FALSE) %>% ungroup() %>%
  transmute(transfer_key, fw_date = valuation_date, fw_value = market_value)

sales <- sales %>%
  left_join(backward, by = "transfer_key") %>%
  left_join(forward, by = "transfer_key") %>%
  mutate(basis_lookup = case_when(is.na(basis_date) ~ NA_character_,
                                  !is.na(bw_value)  ~ "backward",
                                  !is.na(fw_value)  ~ "forward",
                                  TRUE              ~ "none"),
         basis_valuation_date = case_when(basis_lookup == "backward" ~ bw_date, basis_lookup == "forward" ~ fw_date),
         basis_eur = case_when(transfer_type == "loan_with_fee" ~ 0,
                               basis_lookup == "backward" ~ bw_value,
                               basis_lookup == "forward"  ~ fw_value,
                               basis_lookup == "none"     ~ 0),
         income_eur = if_else(transfer_type == "undisclosed", NA_real_, fee_eur),
         counts_toward_profit = transfer_type != "undisclosed",
         profit_eur = income_eur - basis_eur)

# ---- club-seasons ------------------------------------------------------------------------------------
per_club <- sales %>%
  group_by(club_key, season_key) %>%
  summarise(n_fee_sales         = sum(transfer_type == "permanent_with_fee"),
            n_loan_fee_income   = sum(transfer_type == "loan_with_fee"),
            n_undisclosed_sales = sum(transfer_type == "undisclosed"),
            n_legacy_basis      = sum(basis_source == "at_club_on_2017_07_01"),
            n_zero_basis        = sum(basis_lookup %in% "none"),
            income_eur          = sum(income_eur[counts_toward_profit]),
            basis_eur           = sum(basis_eur[counts_toward_profit]),
            .groups = "drop")

trade <- club_seasons %>%
  left_join(per_club, by = c("club_key", "season_key")) %>%
  mutate(across(starts_with("n_"), ~ as.integer(coalesce(.x, 0L))),
         across(c(income_eur, basis_eur), ~ coalesce(.x, 0)),
         profit_eur = income_eur - basis_eur,
         profit_median_fees = profit_eur / median_fee)

bounds <- unname(quantile(trade$profit_median_fees, WINSOR_QUANTILES, type = 7))
trade <- trade %>%
  mutate(profit_capped = pmin(pmax(profit_median_fees, bounds[1]), bounds[2]),
         is_capped = profit_median_fees < bounds[1] | profit_median_fees > bounds[2],
         league = relevel(factor(competition_name), ref = REFERENCE_LEAGUE))

fit <- lm(profit_capped ~ log(squad_value) + factor(season_key) + league, data = trade)
trade$profit_normalised <- unname(residuals(fit))
trade$profit_z <- trade$profit_normalised / sd(trade$profit_normalised)

# ---- guards ------------------------------------------------------------------------------------------
stopifnot(
  "684 club-seasons"                         = nrow(trade) == 684,
  "every counted sale has a profit"          = !anyNA(sales$profit_eur[sales$counts_toward_profit]),
  "no undisclosed sale carries income"       = all(is.na(sales$income_eur[!sales$counts_toward_profit])),
  "residuals carry no squad-value signal"    = abs(cor(trade$profit_normalised, log(trade$squad_value))) < 1e-9,
  "residuals average zero in every league"   = all(abs(tapply(trade$profit_normalised, trade$league, mean)) < 1e-9),
  "club profits add up to sale profits"      = abs(sum(trade$profit_eur) - sum(sales$profit_eur, na.rm = TRUE)) < 1
)

league_effects <- setNames(vapply(levels(trade$league), function(l)
  if (l == REFERENCE_LEAGUE) 0 else unname(coef(fit)[[paste0("league", l)]]), numeric(1)), levels(trade$league))

params <- data.frame(
  pillar = "trading_profit",
  name   = c("backward_days", "forward_days", "winsor_low_quantile", "winsor_high_quantile",
             "winsor_low_median_fees", "winsor_high_median_fees", "squad_value_log_slope", "profit_normalised_sd",
             paste0("league_effect_vs_", REFERENCE_LEAGUE, ":", names(league_effects))),
  value  = c(BACKWARD_DAYS, FORWARD_DAYS, WINSOR_QUANTILES, bounds, unname(coef(fit)[["log(squad_value)"]]),
             sd(trade$profit_normalised), unname(league_effects)),
  note   = c(rep("decided 2026-09-14", 4), "fitted", "fitted", "fitted", "fitted",
             rep("fitted: median fees of capped profit, same squad value and season", length(league_effects))))

invisible(dbWithTransaction(con, {
  run_sql_file(con, "sql/40_score_trading_profit.sql")
  dbWriteTable(con, Id(schema = "score", table = "sale_basis"),
               select(sales, transfer_key, club_key, season_key, player_key, transfer_type, income_eur,
                      owning_arrival_key, basis_source, basis_date, basis_valuation_date, basis_lookup, basis_eur,
                      profit_eur, counts_toward_profit),
               append = TRUE)
  dbWriteTable(con, Id(schema = "score", table = "club_season_trading"),
               select(trade, club_season_key, club_key, season_key, n_fee_sales, n_loan_fee_income,
                      n_undisclosed_sales, n_legacy_basis, n_zero_basis, income_eur, basis_eur, profit_eur,
                      profit_median_fees, profit_capped, is_capped, profit_normalised, profit_z),
               append = TRUE)
  dbWriteTable(con, Id(schema = "score", table = "run_parameter"), params, append = TRUE)
}))

cat(sprintf("wrote score.sale_basis: %d sales (%d fee, %d loan fee, %d undisclosed excluded)\n",
            nrow(sales), sum(sales$transfer_type == "permanent_with_fee"), sum(sales$transfer_type == "loan_with_fee"),
            sum(sales$transfer_type == "undisclosed")))
cat("  fee-sale basis sources:\n")
print(table(sales$basis_source[sales$transfer_type == "permanent_with_fee"],
            sales$basis_lookup[sales$transfer_type == "permanent_with_fee"]))
cat(sprintf("wrote score.club_season_trading: 684 club-seasons; winsor bounds %.2f to %.2f median fees (%d capped); slope %.3f\n",
            bounds[1], bounds[2], sum(trade$is_capped), coef(fit)[["log(squad_value)"]]))

cat("\nchecking against SQL (sql/41_check_trading_profit.sql):\n")
run_checks(con, "sql/41_check_trading_profit.sql")
