-- =============================================================================
-- Transfer Market Efficiency -- Pillar 3, squad value growth
--   score.player_holding            who owned which player, when, and what he was worth at each end
--   score.holding_season_growth     that holding marked to market, one row per season
--   score.club_season_value_growth  one row per club-season: unrealised growth, normalised
--   score.run_parameter             (shared) parameters and fitted values
--
-- Owned by R/50_value_growth.R, which runs this file and refills the tables in
-- one transaction. Derived only; rerun after every warehouse load.
--
-- Ownership is built from transfer events, never from the valuation's club
-- field: that field is the player's later club as often as the club on the
-- date (73% agreement), which would land straight in this pillar's totals.
-- See docs/phase-3-scoring.md, Known limitations.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS score;

DROP TABLE IF EXISTS score.holding_season_growth;
DROP TABLE IF EXISTS score.player_holding;
DROP TABLE IF EXISTS score.club_season_value_growth;

CREATE TABLE IF NOT EXISTS score.run_parameter (
    pillar      text NOT NULL,
    name        text NOT NULL,
    value       double precision NOT NULL,
    note        text,
    PRIMARY KEY (pillar, name)
);
DELETE FROM score.run_parameter WHERE pillar = 'value_growth';

CREATE TABLE score.player_holding (
    holding_key       bigint PRIMARY KEY,        -- assigned by R, stable within a run
    player_key        int  NOT NULL,
    club_key          int  NOT NULL,             -- the owner; a player on loan elsewhere is still owned here
    start_date        date NOT NULL,
    end_date          date NOT NULL,
    start_via         text NOT NULL,             -- how the club came to own him
    end_via           text NOT NULL,             -- how ownership ended; held_at_close = still there on 1 July 2024
    start_value_eur   double precision,          -- market value when acquired (1 July 2017 for legacy holdings)
    end_value_eur     double precision,          -- market value when ownership ended; 0 for a free departure
    is_scored         boolean NOT NULL,
    excluded_because  text,
    CONSTRAINT holding_dates_ordered CHECK (end_date > start_date),
    CONSTRAINT scored_or_reason CHECK (is_scored = (excluded_because IS NULL)),
    CONSTRAINT holding_unique UNIQUE (player_key, club_key, start_date)
);

COMMENT ON TABLE score.player_holding IS
    'One row per ownership spell at an in-scope club, built from transfer events: a permanent move passes '
    'ownership, a loan does not, a loan return proves who the owner was. Ownership explains 99% of the minutes '
    'played in the warehouse (89.6% owned, 9.4% on loan).';
COMMENT ON COLUMN score.player_holding.excluded_because IS
    'fee sale scored in Pillar 2: the whole spell belongs there, so counting it here would double count. '
    'undisclosed departure: the club got an unknown amount, so the outcome cannot be valued. '
    'no end valuation: nothing to measure (fringe and youth players who were never valued).';

CREATE TABLE score.holding_season_growth (
    holding_key       bigint NOT NULL REFERENCES score.player_holding,
    season_key        int    NOT NULL,
    club_key          int    NOT NULL,
    value_start_eur   double precision NOT NULL,   -- value at the season start, or at acquisition if mid-season
    value_end_eur     double precision NOT NULL,   -- value at the season end, or at departure if mid-season
    growth_eur        double precision NOT NULL,
    is_write_off      boolean NOT NULL,            -- the spell ended this season with a free departure
    PRIMARY KEY (holding_key, season_key)
);

COMMENT ON TABLE score.holding_season_growth IS
    'Each scored holding marked to market in every season its club spent in the big five. Growth is only '
    'attributed to seasons the club actually played there.';

CREATE TABLE score.club_season_value_growth (
    club_season_key      bigint PRIMARY KEY,
    club_key             int NOT NULL,
    season_key           int NOT NULL,
    n_holdings           smallint NOT NULL,
    n_write_offs         smallint NOT NULL,
    write_off_eur        double precision NOT NULL,   -- value lost by players leaving for nothing
    growth_eur           double precision NOT NULL,
    growth_median_fees   double precision NOT NULL,
    growth_capped        double precision NOT NULL,
    is_capped            boolean NOT NULL,
    growth_normalised    double precision NOT NULL,
    growth_z             double precision NOT NULL,
    CONSTRAINT value_growth_club_season_unique UNIQUE (club_key, season_key)
);

COMMENT ON TABLE score.club_season_value_growth IS
    'Pillar 3 (scoping doc 5): unrealised value growth of the players a club still held, marked to market each '
    'season, net of value written off when players left for nothing. Realised profit on players who were sold '
    'is Pillar 2; nothing is counted twice. See docs/phase-3-scoring.md.';
COMMENT ON COLUMN score.club_season_value_growth.growth_normalised IS
    'Residual of growth_capped on log(squad_value_start_eur) with season and league effects: the same '
    'normalisation as Pillars 1 and 2.';
