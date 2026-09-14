-- =============================================================================
-- Transfer Market Efficiency -- score.club_season_sporting (Pillar 4)
--
-- Owned by R/20_sporting_return.R, which runs this file and refills the table
-- in one transaction. Derived only; rerun after every warehouse load.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS score;

DROP TABLE IF EXISTS score.club_season_sporting;

CREATE TABLE score.club_season_sporting (
    club_season_key      bigint PRIMARY KEY,        -- fact_club_season
    club_key             int NOT NULL,
    season_key           int NOT NULL,
    competition_key      int NOT NULL,
    matches              smallint NOT NULL,
    points_from_results  smallint NOT NULL,
    points_per_match     double precision NOT NULL,
    ppm_z                double precision NOT NULL,
    has_known_deduction  boolean NOT NULL,
    CONSTRAINT ppm_in_range CHECK (points_per_match BETWEEN 0 AND 3),
    CONSTRAINT sporting_club_season_unique UNIQUE (club_key, season_key)
);

COMMENT ON TABLE score.club_season_sporting IS
    'Pillar 4, sporting return (scoping doc 5): league points per match, standardised within league-season. '
    'Per match because leagues and seasons differ in length (18- and 20-club leagues; Ligue 1 2019/20 was '
    'abandoned after 27-28 matches).';
COMMENT ON COLUMN score.club_season_sporting.points_from_results IS
    'Points earned on the pitch (3 x wins + draws). Administrative deductions are deliberately not applied: '
    'sporting return measures results. See has_known_deduction (Juventus 2022/23, Everton 2023/24).';
COMMENT ON COLUMN score.club_season_sporting.ppm_z IS
    'points_per_match z-scored within competition x season (sample standard deviation): 0 is that league''s '
    'average club that season. Compares a club with its own league, not across leagues.';
