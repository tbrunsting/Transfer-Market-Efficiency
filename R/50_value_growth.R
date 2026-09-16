# Pillar 3, squad value growth: unrealised value growth of the players a club still held (scoping doc 5).
#
# Ownership, from transfer events only (never the valuation's club field, which is the player's later club
# as often as the club on the date):
#   - a permanent move (fee, free, undisclosed) passes ownership to the buying club;
#   - a loan does not: the asset stays with the owner, wherever the player is playing;
#   - a loan return proves who the owner was, and is taken as authoritative;
#   - before a player's first event, the owner is that event's origin club (its destination if it is a
#     loan return). Same-day events are ordered loan return, then permanent move, then loan out, because
#     most dates are estimated and would otherwise sort arbitrarily.
#   So: the owner at any moment is the destination of the latest non-loan event on or before it.
# Ownership built this way explains 99% of the minutes in the warehouse (89.6% owned, 9.4% on loan).
#
# Growth: each holding is marked to market in every season its club spent in the big five.
#   - the first season starts at the acquisition value (1 July 2017 for players already there);
#   - a holding that ends with a free departure is written off to 0: the asset left for nothing
#     (Messi EUR 80m, Donnarumma EUR 60m, Pogba EUR 48m);
#   - a holding that ends in a fee sale scored by Pillar 2 is excluded entirely: that sale already counts
#     the whole spell, from acquisition value to sale price, so counting it here would double count;
#   - undisclosed departures are excluded (unknown proceeds), as are holdings with no end valuation.
#
# Pillar 3 is therefore thinner in early seasons: by 2018/19, 92% of the value growth belongs to players
# who have since been sold, and their value is in Pillar 2. That is the realised/unrealised split working,
# not a gap (decided with Tyler, 2026-09-16; see docs/phase-3-scoring.md).
#
# Verified by sql/51_check_value_growth.sql, which rebuilds ownership, the marks and the normalisation in SQL.
#
# Run from the repository root:
#   "C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\50_value_growth.R

source("R/db.R")
suppressPackageStartupMessages({
  library(dplyr)
  library(tidyr)
})

WINDOW_OPEN      <- as.Date("2017-07-01")
WINDOW_CLOSE     <- as.Date("2024-07-01")
BACKWARD_DAYS    <- 365      # the Pillar 2 valuation rule, for acquisition and departure values
FORWARD_DAYS     <- 180
WINSOR_QUANTILES <- c(0.01, 0.99)
REFERENCE_LEAGUE <- "Bundesliga"
OWNERSHIP_TYPES  <- c("permanent_with_fee", "free", "undisclosed", "loan_return")
EVENT_RANK       <- c(loan_return = 0L, permanent_with_fee = 1L, free = 1L, undisclosed = 1L,
                      loan = 2L, loan_with_fee = 2L)

con <- warehouse_connect()
on.exit(dbDisconnect(con), add = TRUE)

transfers <- dbGetQuery(con, "
  SELECT t.transfer_key, t.player_key, t.from_club_key, t.to_club_key, t.season_key, tt.transfer_type,
         d.full_date AS event_date
  FROM fact_transfer t
  JOIN dim_transfer_type tt USING (transfer_type_key)
  JOIN dim_date d ON d.date_key = t.transfer_date_key")

club_seasons <- dbGetQuery(con, "
  SELECT cs.club_season_key, cs.club_key, cs.season_key, s.season_start_date, s.season_end_date,
         s.median_fee_eur::float8 AS median_fee, co.competition_name,
         cs.squad_value_start_eur::float8 AS squad_value
  FROM fact_club_season cs JOIN dim_season s USING (season_key) JOIN dim_competition co USING (competition_key)")

valuations <- dbGetQuery(con, "
  SELECT v.player_key, d.full_date AS valuation_date, v.market_value_eur::float8 AS market_value
  FROM fact_player_valuation v JOIN dim_date d ON d.date_key = v.valuation_date_key")

player_seasons <- dbGetQuery(con, "SELECT DISTINCT player_key, club_key FROM fact_player_season")
in_scope <- dbGetQuery(con, "SELECT club_key FROM dim_club WHERE is_in_scope")$club_key

# ---- ownership spells --------------------------------------------------------------------------------
ev <- transfers %>%
  mutate(rank = unname(EVENT_RANK[transfer_type])) %>%
  arrange(player_key, event_date, rank, transfer_key)

owner_before_first <- ev %>%
  group_by(player_key) %>%
  slice(1) %>%
  ungroup() %>%
  transmute(player_key, event_date = WINDOW_OPEN, rank = -1L, transfer_key = 0L,
            owner = if_else(transfer_type == "loan_return", to_club_key, from_club_key),
            via = "at_club_when_the_window_opened")

steps <- ev %>%
  filter(transfer_type %in% OWNERSHIP_TYPES) %>%
  transmute(player_key, event_date, rank, transfer_key, owner = to_club_key, via = transfer_type) %>%
  bind_rows(owner_before_first) %>%
  arrange(player_key, event_date, rank, transfer_key) %>%
  group_by(player_key) %>%
  filter(is.na(lag(owner)) | owner != lag(owner)) %>%          # collapse runs of the same owner
  mutate(end_date = lead(event_date, default = WINDOW_CLOSE),
         end_via  = lead(via, default = "held_at_close")) %>%
  ungroup() %>%
  rename(start_date = event_date, start_via = via, club_key = owner) %>%
  filter(end_date > start_date)

# players with no transfer events at all: owned by the one club they played for
no_events <- player_seasons %>%
  filter(!player_key %in% transfers$player_key) %>%
  group_by(player_key) %>%
  filter(n() == 1) %>%
  ungroup() %>%
  transmute(player_key, club_key, start_date = WINDOW_OPEN, end_date = WINDOW_CLOSE,
            start_via = "at_club_when_the_window_opened", end_via = "held_at_close",
            transfer_key = 0L, rank = -1L)

holdings <- bind_rows(steps, no_events) %>%
  filter(club_key %in% in_scope) %>%
  arrange(player_key, start_date) %>%
  mutate(holding_key = row_number())
cat("ownership spells at in-scope clubs:", nrow(holdings), "\n")

# ---- values at the two ends --------------------------------------------------------------------------
value_at <- function(keys, dates, backward_days = BACKWARD_DAYS, forward_days = FORWARD_DAYS) {
  want <- tibble::tibble(i = seq_along(keys), player_key = keys, want_date = dates)
  cand <- want %>% inner_join(valuations, by = "player_key", relationship = "many-to-many")
  back <- cand %>%
    filter(valuation_date <= want_date,
           is.na(backward_days) | valuation_date >= want_date - backward_days) %>%
    group_by(i) %>% slice_max(valuation_date, n = 1, with_ties = FALSE) %>% ungroup() %>%
    select(i, back = market_value)
  fwd <- cand %>%
    filter(valuation_date > want_date, valuation_date <= want_date + forward_days) %>%
    group_by(i) %>% slice_min(valuation_date, n = 1, with_ties = FALSE) %>% ungroup() %>%
    select(i, fwd = market_value)
  want %>% left_join(back, by = "i") %>% left_join(fwd, by = "i") %>%
    mutate(v = coalesce(back, fwd)) %>% pull(v)
}

holdings <- holdings %>%
  mutate(start_value_eur = value_at(player_key, start_date),
         end_value_eur   = if_else(end_via == "free", 0, value_at(player_key, end_date)))

# ---- which holdings are scored -----------------------------------------------------------------------
# A spell is excluded if Pillar 2 counted a fee sale of this player by this club at any point inside it, not
# only exactly at its end. Same-day estimated dates can put another club's loan return after the sale, which
# would otherwise end the spell with the wrong reason and let the same value into both pillars (5 cases:
# Knockaert, Afobe, Rolan, Lammers, Ciervo).
fee_sale_in_pillar2 <- transfers %>%
  semi_join(club_seasons, by = c("from_club_key" = "club_key", "season_key")) %>%
  filter(transfer_type == "permanent_with_fee") %>%
  transmute(player_key, club_key = from_club_key, sale_date = event_date)

sold_keys <- holdings %>%
  select(holding_key, player_key, club_key, start_date, end_date) %>%
  inner_join(fee_sale_in_pillar2, by = c("player_key", "club_key"), relationship = "many-to-many") %>%
  filter(sale_date >= start_date, sale_date <= end_date) %>%
  distinct(holding_key) %>%
  pull(holding_key)

holdings <- holdings %>%
  mutate(sold_in_pillar2 = holding_key %in% sold_keys,
         excluded_because = case_when(
           sold_in_pillar2                                   ~ "fee sale scored in Pillar 2",
           end_via == "undisclosed"                          ~ "undisclosed departure",
           is.na(end_value_eur)                              ~ "no end valuation",
           TRUE                                              ~ NA_character_),
         is_scored = is.na(excluded_because))
cat("holdings by outcome:\n"); print(count(holdings, end_via, is_scored, excluded_because), n = Inf)

# ---- mark each scored holding to market, season by season --------------------------------------------
seasons <- club_seasons %>%
  transmute(club_key, season_key, club_season_key, s0 = season_start_date, s1 = season_end_date + 1)

marks <- holdings %>%
  filter(is_scored) %>%
  select(holding_key, player_key, club_key, start_date, end_date, start_value_eur, end_value_eur, end_via) %>%
  inner_join(seasons, by = "club_key", relationship = "many-to-many") %>%
  filter(start_date < s1, end_date > s0) %>%
  mutate(starts_here = start_date >= s0, ends_here = end_date <= s1)

# Season boundaries use the last known valuation (no 365-day limit), so a stale valuation carries forward
# rather than dropping a player to zero mid-spell.
marks <- marks %>%
  mutate(value_start_eur = if_else(starts_here, coalesce(start_value_eur, 0),
                                   coalesce(value_at(player_key, s0, backward_days = NA), 0)),
         value_end_eur   = if_else(ends_here, coalesce(end_value_eur, 0),
                                   coalesce(value_at(player_key, s1, backward_days = NA), 0)),
         growth_eur      = value_end_eur - value_start_eur,
         is_write_off    = ends_here & end_via == "free")

# ---- club-seasons ------------------------------------------------------------------------------------
per_club <- marks %>%
  group_by(club_key, season_key) %>%
  summarise(n_holdings    = n(),
            n_write_offs  = sum(is_write_off),
            write_off_eur = sum(value_start_eur[is_write_off]),
            growth_eur    = sum(growth_eur),
            .groups = "drop")

growth <- club_seasons %>%
  left_join(per_club, by = c("club_key", "season_key")) %>%
  mutate(across(c(n_holdings, n_write_offs), ~ as.integer(coalesce(.x, 0L))),
         across(c(write_off_eur, growth_eur), ~ coalesce(.x, 0)),
         growth_median_fees = growth_eur / median_fee)

bounds <- unname(quantile(growth$growth_median_fees, WINSOR_QUANTILES, type = 7))
growth <- growth %>%
  mutate(growth_capped = pmin(pmax(growth_median_fees, bounds[1]), bounds[2]),
         is_capped = growth_median_fees < bounds[1] | growth_median_fees > bounds[2],
         league = relevel(factor(competition_name), ref = REFERENCE_LEAGUE))

fit <- lm(growth_capped ~ log(squad_value) + factor(season_key) + league, data = growth)
growth$growth_normalised <- unname(residuals(fit))
growth$growth_z <- growth$growth_normalised / sd(growth$growth_normalised)

# ---- guards ------------------------------------------------------------------------------------------
stopifnot(
  "684 club-seasons"                        = nrow(growth) == 684,
  "no holding is scored twice in a season"  = !anyDuplicated(marks[c("holding_key", "season_key")]),
  "every spell has an owner in scope"       = all(holdings$club_key %in% in_scope),
  "spells never overlap for one player"     = holdings %>% arrange(player_key, start_date) %>%
    group_by(player_key) %>% summarise(bad = any(start_date < lag(end_date), na.rm = TRUE), .groups = "drop") %>%
    pull(bad) %>% any() %>% isFALSE(),
  "residuals carry no squad-value signal"   = abs(cor(growth$growth_normalised, log(growth$squad_value))) < 1e-9,
  "residuals average zero in every league"  = all(abs(tapply(growth$growth_normalised, growth$league, mean)) < 1e-9),
  "club growth adds up to the marks"        = abs(sum(growth$growth_eur) - sum(marks$growth_eur)) < 1
)

league_effects <- setNames(vapply(levels(growth$league), function(l)
  if (l == REFERENCE_LEAGUE) 0 else unname(coef(fit)[[paste0("league", l)]]), numeric(1)), levels(growth$league))

params <- data.frame(
  pillar = "value_growth",
  name   = c("backward_days", "forward_days", "winsor_low_quantile", "winsor_high_quantile",
             "winsor_low_median_fees", "winsor_high_median_fees", "squad_value_log_slope", "growth_normalised_sd",
             "holdings_scored", "holdings_excluded_no_end_valuation",
             paste0("league_effect_vs_", REFERENCE_LEAGUE, ":", names(league_effects))),
  value  = c(BACKWARD_DAYS, FORWARD_DAYS, WINSOR_QUANTILES, bounds, unname(coef(fit)[["log(squad_value)"]]),
             sd(growth$growth_normalised), sum(holdings$is_scored),
             sum(holdings$excluded_because == "no end valuation", na.rm = TRUE), unname(league_effects)),
  note   = c(rep("decided 2026-09-14", 4), "fitted", "fitted", "fitted", "fitted", "count", "count",
             rep("fitted", length(league_effects))))

invisible(dbWithTransaction(con, {
  run_sql_file(con, "sql/50_score_value_growth.sql")
  dbWriteTable(con, Id(schema = "score", table = "player_holding"),
               select(holdings, holding_key, player_key, club_key, start_date, end_date, start_via, end_via,
                      start_value_eur, end_value_eur, is_scored, excluded_because),
               append = TRUE)
  dbWriteTable(con, Id(schema = "score", table = "holding_season_growth"),
               select(marks, holding_key, season_key, club_key, value_start_eur, value_end_eur, growth_eur,
                      is_write_off),
               append = TRUE)
  dbWriteTable(con, Id(schema = "score", table = "club_season_value_growth"),
               select(growth, club_season_key, club_key, season_key, n_holdings, n_write_offs, write_off_eur,
                      growth_eur, growth_median_fees, growth_capped, is_capped, growth_normalised, growth_z),
               append = TRUE)
  dbWriteTable(con, Id(schema = "score", table = "run_parameter"), params, append = TRUE)
}))

cat(sprintf("\nwrote score.player_holding: %d spells (%d scored); score.holding_season_growth: %d rows\n",
            nrow(holdings), sum(holdings$is_scored), nrow(marks)))
cat(sprintf("wrote score.club_season_value_growth: 684 club-seasons; winsor %.2f to %.2f median fees (%d capped); slope %.3f\n",
            bounds[1], bounds[2], sum(growth$is_capped), coef(fit)[["log(squad_value)"]]))
cat(sprintf("write-offs: %d players left for nothing, %.0f EURm of value\n",
            sum(growth$n_write_offs), sum(growth$write_off_eur) / 1e6))

cat("\nchecking against SQL (sql/51_check_value_growth.sql):\n")
run_checks(con, "sql/51_check_value_growth.sql")
