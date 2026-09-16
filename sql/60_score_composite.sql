-- =============================================================================
-- Transfer Market Efficiency -- the composite efficiency index
--   score.composite_weight       the weight each pillar carries, and why
--   score.club_season_efficiency one row per club-season: the four pillars combined
--
-- Owned by R/60_composite.R, which runs this file and refills both tables in one
-- transaction. Derived only; rerun after the four pillar scripts.
--
-- The weights are CHOSEN, not derived. Regressing the pillars on sporting
-- success (the scoping doc's original method) gave negative coefficients for
-- trading profit against every target, near-zero explanatory power (R2 0.006 to
-- 0.129) and coefficients that moved as much as their own size when a season was
-- left out. That is structural: every pillar is residualised against squad value,
-- while league points correlate 0.67 with squad value. See docs/phase-3-scoring.md.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS score;

DROP TABLE IF EXISTS score.club_season_efficiency;
DROP TABLE IF EXISTS score.composite_weight;

CREATE TABLE score.composite_weight (
    pillar        text PRIMARY KEY,
    source_table  text NOT NULL,
    source_column text NOT NULL,
    weight        double precision NOT NULL,
    rationale     text NOT NULL,
    CONSTRAINT weight_is_a_share CHECK (weight > 0 AND weight <= 1)
);

COMMENT ON TABLE score.composite_weight IS
    'The composite''s weights, as data rather than buried in code: three money pillars at 0.30 each and '
    'sporting return at 0.10, the guard rail that stops "efficient" meaning cheap and bad (scoping doc 5).';

CREATE TABLE score.club_season_efficiency (
    club_season_key        bigint PRIMARY KEY,
    club_key               int NOT NULL,
    season_key             int NOT NULL,
    recruitment_z          double precision,        -- NULL below the spend floor
    trading_z              double precision NOT NULL,
    value_growth_z         double precision NOT NULL,
    sporting_z             double precision NOT NULL,
    weight_applied         double precision NOT NULL, -- the weights actually available, before renormalising
    is_recruitment_missing boolean NOT NULL,
    is_provisional         boolean NOT NULL,          -- inherits Pillar 1's provisional cohorts
    efficiency_index       double precision NOT NULL, -- weighted mean of the available pillars
    efficiency_z           double precision NOT NULL, -- standardised across all club-seasons
    CONSTRAINT missing_recruitment_is_flagged CHECK (is_recruitment_missing = (recruitment_z IS NULL)),
    CONSTRAINT efficiency_club_season_unique UNIQUE (club_key, season_key)
);

COMMENT ON TABLE score.club_season_efficiency IS
    'The efficiency index, one row per club-season: 0.30 recruitment ROI + 0.30 trading profit + 0.30 value '
    'growth + 0.10 sporting return, over the pillars available. Club pages average these across seasons.';
COMMENT ON COLUMN score.club_season_efficiency.weight_applied IS
    'Sum of the weights of the pillars this club-season actually has. 1.0 normally; 0.70 for the 69 '
    'club-seasons below the recruitment spend floor, whose remaining weights are renormalised.';
COMMENT ON COLUMN score.club_season_efficiency.is_provisional IS
    'True for the 2022/23 and 2023/24 signing cohorts: Pillar 1 credits three seasons of output, which run '
    'past the scored window. Flag these in any trend.';
