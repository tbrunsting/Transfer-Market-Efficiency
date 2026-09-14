-- =============================================================================
-- Transfer Market Efficiency -- Pillar 1, recruitment ROI
--   score.signing_credit              which signing each player-season's output is credited to
--   score.club_season_recruitment     one row per club-season: the signing cohort's output per deflated euro
--   score.run_parameter               the parameters and fitted values behind the run
--
-- Owned by R/30_recruitment_roi.R, which runs this file and refills the tables
-- in one transaction. Derived only; rerun after every warehouse load and after
-- R/10_player_quality.R.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS score;

DROP TABLE IF EXISTS score.signing_credit;
DROP TABLE IF EXISTS score.club_season_recruitment;
DROP TABLE IF EXISTS score.league_premium;

CREATE TABLE IF NOT EXISTS score.run_parameter (
    pillar      text NOT NULL,
    name        text NOT NULL,
    value       double precision NOT NULL,
    note        text,
    PRIMARY KEY (pillar, name)
);
DELETE FROM score.run_parameter WHERE pillar = 'recruitment_roi';

CREATE TABLE score.signing_credit (
    player_season_key     bigint PRIMARY KEY,       -- each season's output is credited to at most one arrival
    transfer_key          bigint NOT NULL,          -- fact_transfer: the arrival credited
    club_key              int NOT NULL,
    arrival_season_key    int NOT NULL,
    season_key            int NOT NULL,             -- the season the output was delivered
    season_offset         smallint NOT NULL,        -- 0 = the signing season
    transfer_type         text NOT NULL,
    season_equivalents    double precision NOT NULL, -- minutes / 90 / the club's league matches that season
    quality_weight        double precision NOT NULL, -- quality_pctile / 100; 0 under the minutes floor
    output                double precision NOT NULL, -- season_equivalents x quality_weight
    counts_toward_output  boolean NOT NULL,         -- false for undisclosed-fee signings unless included
    CONSTRAINT offset_in_horizon CHECK (season_offset >= 0)
);

COMMENT ON TABLE score.signing_credit IS
    'Links a player-season''s output to the arrival that brought the player to that club. A fee, free or '
    'undisclosed signing keeps the credit through loans out until a permanent departure; a loan''s credit ends '
    'at the next departure of any kind; a newer arrival to the same club always takes over.';

CREATE TABLE score.club_season_recruitment (
    club_season_key        bigint PRIMARY KEY,      -- fact_club_season
    club_key               int NOT NULL,
    season_key             int NOT NULL,             -- the signing window's season
    n_arrivals             smallint NOT NULL,        -- excluding loan returns
    n_spend_signings       smallint NOT NULL,        -- permanent_with_fee + loan_with_fee
    n_undisclosed          smallint NOT NULL,
    spend_eur              double precision NOT NULL,
    spend_median_fees      double precision NOT NULL, -- spend deflated: sum of fee / that season's median fee
    output                 double precision NOT NULL, -- credited output within the horizon, counted signings
    output_undisclosed     double precision NOT NULL, -- output of undisclosed-fee signings, shown for context
    is_insufficient_spend  boolean NOT NULL,
    is_provisional         boolean NOT NULL,         -- horizon runs past 2023/24
    log_roi                double precision,          -- log(output + eps) - log(spend_median_fees)
    roi_normalised         double precision,          -- residual on log squad value, with season and league effects
    roi_z                  double precision,
    CONSTRAINT scored_unless_insufficient CHECK (is_insufficient_spend = (roi_normalised IS NULL)),
    CONSTRAINT recruitment_club_season_unique UNIQUE (club_key, season_key)
);

COMMENT ON TABLE score.club_season_recruitment IS
    'Pillar 1 (scoping doc 5): output delivered by a club-season''s signings per deflated euro of fee. See '
    'docs/phase-3-scoring.md for the credit rule, horizon and guardrails.';
COMMENT ON COLUMN score.club_season_recruitment.is_insufficient_spend IS
    'Spend below the 10th percentile of all 684 club-seasons (in median-fee units): labelled, not scored. '
    'A near-zero denominator would otherwise put the most passive clubs at the top (scoping doc 5).';
COMMENT ON COLUMN score.club_season_recruitment.roi_normalised IS
    'Residual of log_roi regressed on log(squad_value_start_eur) with season and league effects. The '
    'squad-value term removes club scale (no revenue data exists); the season effects measure each club '
    'against that season''s market (the scoping doc 4.6 deflator in log form); the league effects judge a '
    'club against its own league, whose broadcast money squad value does not capture. The league effect is '
    'published in score.league_premium rather than discarded.';
COMMENT ON COLUMN score.club_season_recruitment.is_provisional IS
    'The three-season output horizon runs past 2023/24: the 2022/23 and 2023/24 cohorts. Flag them in any trend.';

CREATE TABLE score.league_premium (
    competition_key               int PRIMARY KEY,
    competition_name              text NOT NULL,
    reference_league              text NOT NULL,
    n_club_seasons                smallint NOT NULL,  -- scored club-seasons behind the estimate
    log_effect_vs_reference       double precision NOT NULL,
    output_per_euro_vs_reference  double precision NOT NULL,  -- exp(log effect)
    median_output_per_spend       double precision NOT NULL   -- raw, unadjusted, for context
);
COMMENT ON TABLE score.league_premium IS
    'A headline finding in its own right: how much signing output a league''s clubs get per deflated euro, '
    'relative to the reference league, at the same squad value and season. 0.4 means 40% as much.';
