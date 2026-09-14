-- =============================================================================
-- Transfer Market Efficiency -- score.player_quality
--
-- Owned by R/10_player_quality.R, which runs this file and refills both tables
-- in one transaction every time it runs. Everything here is derived, so
-- dropping it loses nothing.
--
-- The keys are the warehouse's surrogate keys and carry no foreign keys: the
-- loader truncates and renumbers the warehouse, so the scoring must be rerun
-- after every load. R/10_player_quality.R refuses to write if its row count
-- does not match fact_player_season.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS score;

DROP TABLE IF EXISTS score.player_quality_metric;
DROP TABLE IF EXISTS score.player_quality;

CREATE TABLE score.player_quality (
    player_season_key   bigint PRIMARY KEY,        -- fact_player_season
    player_key          int NOT NULL,
    club_key            int NOT NULL,
    season_key          int NOT NULL,
    position_group_key  int NOT NULL,
    minutes             int NOT NULL,
    is_scored           boolean NOT NULL,
    unscored_reason     text,
    n_metrics           smallint,
    quality_z           double precision,
    quality_pctile      double precision,
    CONSTRAINT scored_rows_have_a_score   CHECK (is_scored = (quality_z IS NOT NULL)),
    CONSTRAINT unscored_rows_say_why      CHECK (is_scored = (unscored_reason IS NULL)),
    CONSTRAINT pctile_in_range            CHECK (quality_pctile BETWEEN 0 AND 100)
);

COMMENT ON TABLE score.player_quality IS
    'One row per fact_player_season row. Quality = output per 90 against the player''s own position group '
    'and season (scoping doc 4.4, 4.5). Every row is present; rows under the minutes floor are kept but unscored.';
COMMENT ON COLUMN score.player_quality.quality_z IS
    'Mean of the group''s metric z-scores, re-standardised within season x position group: 0 is the group '
    'average that season, 1 is one standard deviation. Comparable across groups by construction.';
COMMENT ON COLUMN score.player_quality.quality_pctile IS
    'Percent rank of quality_z within season x position group, 0-100. For display.';
COMMENT ON COLUMN score.player_quality.unscored_reason IS
    'Why a row has no score, e.g. under 900 minutes: a per-90 rate from a few appearances is noise.';

CREATE TABLE score.player_quality_metric (
    player_season_key   bigint NOT NULL REFERENCES score.player_quality,
    metric              text NOT NULL,
    value               double precision NOT NULL, -- the raw per-90 rate or ratio
    value_used          double precision NOT NULL, -- what was z-scored: value, or its possession residual
    possession_adjusted boolean NOT NULL,
    z                   double precision NOT NULL, -- within season x position group, scored rows only
    PRIMARY KEY (player_season_key, metric),
    CONSTRAINT unadjusted_value_is_used_as_is CHECK (possession_adjusted OR value_used = value)
);

COMMENT ON COLUMN score.player_quality_metric.value_used IS
    'For centre-back tackles won, interceptions, clearances and aerials won: the residual of value on team '
    'possession (fact_club_season.possession_pct), regressed within season. Otherwise equal to value. The raw '
    'value is kept beside it so the adjustment can always be seen and undone.';

COMMENT ON TABLE score.player_quality_metric IS
    'The inputs behind quality_z, one row per scored player-season per metric, so every score can be taken apart.';
