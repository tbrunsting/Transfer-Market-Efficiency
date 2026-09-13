-- =============================================================================
-- Transfer Market Efficiency -- warehouse schema
--
-- Star schema for the seven scored seasons, 2017/18-2023/24.
-- Design and rationale: docs/phase-2-schema.md
--
-- Three grains:
--   fact_transfer        one row per transfer event   (money)
--   fact_player_season   one row per player-club-season (performance)
--   bridge_player_club_spell  derived, for attribution (ROI, trading profit)
--
-- Conventions:
--   * surrogate keys (bigserial/serial); every natural key kept as a UNIQUE
--   * invariants that can be expressed as CHECK constraints are, so a bad load
--     fails loudly instead of producing a plausible wrong number
--   * COMMENT ON is used for the rules a reader must know, so they travel with
--     the database and show up in \d+, not only in a document
--
-- Run:  psql -d transfer_market -f sql/01_schema.sql
-- =============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS meta;
COMMENT ON SCHEMA meta IS
    'Provenance: which bytes were used, and every human decision behind the curated tables.';

-- =============================================================================
-- Dimensions
-- =============================================================================

CREATE TABLE dim_season (
    season_key           serial       PRIMARY KEY,
    season_label         text         NOT NULL UNIQUE,      -- '2017/18'
    season_end_year      smallint     NOT NULL UNIQUE,      -- 2018
    transfermarkt_year   smallint     NOT NULL UNIQUE,      -- 2017
    season_start_date    date         NOT NULL,
    season_end_date      date         NOT NULL,
    is_scored            boolean      NOT NULL,
    total_fees_eur       numeric(16,2),                     -- deflator inputs, scoping doc 4.6
    median_fee_eur       numeric(14,2),
    fee_coverage_pct     numeric(5,2),                      -- see meta.transfer_coverage
    CONSTRAINT season_dates_ordered CHECK (season_end_date > season_start_date),
    CONSTRAINT season_year_matches  CHECK (transfermarkt_year = season_end_year - 1)
);
COMMENT ON TABLE dim_season IS
    'The seven scored seasons plus the unscored recency seasons (2024/25-2026/27), flagged by is_scored.';
COMMENT ON COLUMN dim_season.total_fees_eur IS
    'Total in-scope big-five spend that season. Denominator for the scoping doc 4.6 deflator; excludes '
    'club-seasons played outside the big five.';

CREATE TABLE dim_date (
    date_key     integer  PRIMARY KEY,          -- yyyymmdd
    full_date    date     NOT NULL UNIQUE,
    year         smallint NOT NULL,
    month        smallint NOT NULL,
    day          smallint NOT NULL,
    season_key   integer  REFERENCES dim_season
);

CREATE TABLE dim_competition (
    competition_key   serial PRIMARY KEY,
    source_code       text   NOT NULL UNIQUE,   -- 'GB1', 'CL', 'FAC'
    competition_name  text   NOT NULL,
    competition_type  text   NOT NULL,
    country           text,
    CONSTRAINT competition_type_known
        CHECK (competition_type IN ('domestic_league', 'domestic_cup', 'european'))
);

CREATE TABLE dim_club (
    club_key           serial   PRIMARY KEY,
    fbref_team_id      char(8)  UNIQUE,          -- NULL for clubs outside the 145
    transfermarkt_id   integer  NOT NULL UNIQUE,
    club_name          text     NOT NULL,
    country            text,
    city               text,
    crest_url          text,
    wikidata_qid       text,
    is_in_scope        boolean  NOT NULL,
    CONSTRAINT in_scope_clubs_have_fbref_id CHECK (NOT is_in_scope OR fbref_team_id IS NOT NULL)
);
COMMENT ON TABLE dim_club IS
    'The 145 in-scope clubs plus every club a transfer points at (youth sides, "Without Club", '
    'clubs outside the big five), so transfer foreign keys always resolve.';
COMMENT ON COLUMN dim_club.country IS
    'The CLUB''s country, not its league''s: Cardiff and Swansea are Wales, Monaco is Monaco.';
COMMENT ON COLUMN dim_club.is_in_scope IS
    'True for the 145 clubs that played a big-five season in the window. Spend, the deflator and every '
    'ranking must filter on this: a relegated club''s second-tier season is present but must not count.';
-- League deliberately absent: clubs are promoted and relegated, so it lives on fact_club_season.

CREATE TABLE dim_player (
    player_key         serial   PRIMARY KEY,
    fbref_player_id    char(8)  UNIQUE,
    transfermarkt_id   integer  UNIQUE,
    player_name        text     NOT NULL,
    nationality        text,
    birth_year         smallint,
    primary_position   text,
    foot               text,
    CONSTRAINT player_has_an_id CHECK (fbref_player_id IS NOT NULL OR transfermarkt_id IS NOT NULL)
);

CREATE TABLE dim_position_group (
    position_group_key  serial PRIMARY KEY,
    position_group      text   NOT NULL UNIQUE,   -- GK, CB, FB, CM, AM/W, FW
    fbref_positions     text   NOT NULL
);
COMMENT ON TABLE dim_position_group IS
    'The six groups from scoping doc 4.4. Assigned from the FIRST listed FBref position; the raw Pos '
    'string is kept on fact_player_season for auditing.';

CREATE TABLE dim_manager (
    manager_key      serial  PRIMARY KEY,
    manager_name     text    NOT NULL UNIQUE,   -- resolved name
    source_name      text    NOT NULL,          -- as recorded, e.g. 'Luis García'
    was_name_split   boolean NOT NULL DEFAULT false
);
COMMENT ON COLUMN dim_manager.was_name_split IS
    'True where one recorded name was two people, separated by club context (reference/manager_name_review.csv).';

CREATE TABLE dim_transfer_type (
    transfer_type_key   serial  PRIMARY KEY,
    transfer_type       text    NOT NULL UNIQUE,
    counts_as_signing   boolean NOT NULL,
    counts_as_spend     boolean NOT NULL,
    description         text    NOT NULL
);
COMMENT ON TABLE dim_transfer_type IS
    'Read from Transfermarkt''s own row labels, not inferred. The reversal-pair heuristic survives only '
    'as a fallback for rows that exist solely in the frozen dataset (is_type_heuristic on the fact).';

-- =============================================================================
-- fact_transfer -- the money atom
-- =============================================================================

CREATE TABLE fact_transfer (
    transfer_key          bigserial     PRIMARY KEY,
    player_key            integer       NOT NULL REFERENCES dim_player,
    from_club_key         integer       NOT NULL REFERENCES dim_club,
    to_club_key           integer       NOT NULL REFERENCES dim_club,
    transfer_date_key     integer       NOT NULL REFERENCES dim_date,
    season_key            integer       NOT NULL REFERENCES dim_season,
    transfer_type_key     integer       NOT NULL REFERENCES dim_transfer_type,

    fee_eur               numeric(14,2),          -- NULL = undisclosed. See the rule below.
    is_fee_disclosed      boolean       NOT NULL,
    market_value_eur      numeric(14,2),
    fee_share_of_season   numeric(10,8),
    fee_vs_season_median  numeric(10,4),

    is_type_heuristic     boolean       NOT NULL DEFAULT false,
    date_is_estimated     boolean       NOT NULL DEFAULT false,
    source_system         text          NOT NULL,
    source_ref            text          NOT NULL,

    -- The NULL-means-undisclosed rule, enforced rather than documented ------------------
    CONSTRAINT fee_null_iff_undisclosed
        CHECK ((fee_eur IS NOT NULL) = is_fee_disclosed),
    CONSTRAINT fee_never_negative
        CHECK (fee_eur IS NULL OR fee_eur >= 0),
    -- A disclosed fee of exactly 0 is meaningful (free transfer, fee-free loan) and stays allowed;
    -- what cannot happen is an undisclosed fee masquerading as a number, or a number recorded as unknown.
    CONSTRAINT deflator_only_with_a_fee
        CHECK ((fee_share_of_season IS NULL AND fee_vs_season_median IS NULL) OR fee_eur IS NOT NULL),
    CONSTRAINT transfer_has_two_clubs
        CHECK (from_club_key <> to_club_key),
    CONSTRAINT source_system_known
        CHECK (source_system IN ('transfermarkt-pages', 'transfermarkt-datasets')),

    CONSTRAINT transfer_natural_key
        UNIQUE (player_key, transfer_date_key, from_club_key, to_club_key, source_system)
);

COMMENT ON TABLE fact_transfer IS
    'One row per transfer event: the financial atom. Never netted (spend is to_club_key, income is '
    'from_club_key) and never inferred. Club-level money must filter dim_club.is_in_scope.';

COMMENT ON COLUMN fact_transfer.fee_eur IS
    'Euros. NULL MEANS UNDISCLOSED, NOT ZERO -- do not COALESCE(fee_eur, 0). A disclosed 0 is a genuinely '
    'free move; NULL is a fee Transfermarkt does not publish (about 12% of arrivals). Zero-filling turns '
    '"we do not know" into "it was free" and silently understates spend. Aggregate with SUM(fee_eur), '
    'which ignores NULLs, and report the undisclosed COUNT alongside -- vw_transfer_money does both. '
    'Scoping doc section 7: excluded and documented, not guessed.';
COMMENT ON COLUMN fact_transfer.is_fee_disclosed IS
    'Redundant with fee_eur IS NOT NULL by CHECK constraint fee_null_iff_undisclosed. It exists so the '
    'rule is visible in the row itself and so a load that gets it wrong fails at insert.';
COMMENT ON COLUMN fact_transfer.is_type_heuristic IS
    'False for rows from Transfermarkt''s pages (the label is stated). True only where the type was '
    'inferred from the reversal-pair fallback.';
COMMENT ON COLUMN fact_transfer.date_is_estimated IS
    'Club pages give a season, not a date. Rows matched to the frozen dataset inherit its date; the rest '
    'take the season''s nominal start with this flag set.';

CREATE INDEX ix_transfer_to_club   ON fact_transfer (to_club_key, season_key);
CREATE INDEX ix_transfer_from_club ON fact_transfer (from_club_key, season_key);
CREATE INDEX ix_transfer_player    ON fact_transfer (player_key, transfer_date_key);

-- The safe path for money. Querying this instead of the fact makes the rule hard to get wrong.
CREATE VIEW vw_transfer_money AS
SELECT t.transfer_key,
       t.season_key,
       t.player_key,
       t.from_club_key,
       t.to_club_key,
       tt.transfer_type,
       tt.counts_as_signing,
       tt.counts_as_spend,
       t.fee_eur                                    AS fee_eur,            -- NULL when undisclosed
       (NOT t.is_fee_disclosed)                     AS fee_is_undisclosed,
       CASE WHEN tt.counts_as_spend THEN t.fee_eur END AS spend_eur,
       t.market_value_eur,
       t.fee_share_of_season
FROM fact_transfer t
JOIN dim_transfer_type tt USING (transfer_type_key);
COMMENT ON VIEW vw_transfer_money IS
    'Money-safe view of fact_transfer. SUM(spend_eur) gives fees that count as spend and ignores '
    'undisclosed rows; COUNT(*) FILTER (WHERE fee_is_undisclosed) reports how many were left out. '
    'Never zero-fill fee_eur.';

-- =============================================================================
-- fact_player_season -- the performance atom (wide by design)
-- =============================================================================

CREATE TABLE fact_player_season (
    player_season_key       bigserial PRIMARY KEY,
    player_key              integer   NOT NULL REFERENCES dim_player,
    club_key                integer   NOT NULL REFERENCES dim_club,
    season_key              integer   NOT NULL REFERENCES dim_season,
    position_group_key      integer   NOT NULL REFERENCES dim_position_group,
    fbref_position_raw      text,                    -- e.g. 'MF,FW': the messy truth beside the clean group
    age                     smallint,

    matches_played          smallint,
    starts                  smallint,
    minutes                 integer,
    nineties                numeric(6,2),
    team_matches_available  smallint,                -- availability denominator, scoping doc 4.5

    goals                   smallint,
    assists                 smallint,
    xg                      numeric(7,2),
    npxg                    numeric(7,2),
    xag                     numeric(7,2),
    shots                   smallint,
    shots_on_target         smallint,

    progressive_passes      smallint,
    progressive_carries     smallint,
    progressive_received    smallint,
    key_passes              smallint,
    passes_into_final_third smallint,
    passes_into_pen_area    smallint,
    passes_completed        smallint,
    passes_attempted        smallint,

    tackles                 smallint,
    tackles_won             smallint,
    interceptions           smallint,
    blocks                  smallint,
    clearances              smallint,
    errors                  smallint,
    aerials_won             smallint,
    aerials_lost            smallint,

    touches                 integer,
    touches_att_pen_area    smallint,
    take_ons_attempted      smallint,
    take_ons_won            smallint,
    carries                 integer,
    carries_into_final_third smallint,

    sca                     smallint,
    gca                     smallint,
    sca_source              text,                    -- 'kaggle' where SCA came from the Kaggle crosswalk

    gk_saves                smallint,
    gk_goals_against        smallint,
    gk_psxg                 numeric(7,2),
    gk_clean_sheets         smallint,

    is_value_filled         boolean NOT NULL DEFAULT false,

    CONSTRAINT player_season_natural_key UNIQUE (player_key, club_key, season_key),
    CONSTRAINT minutes_non_negative CHECK (minutes IS NULL OR minutes >= 0),
    CONSTRAINT starts_within_matches CHECK (starts IS NULL OR matches_played IS NULL OR starts <= matches_played),
    CONSTRAINT nineties_match_minutes
        CHECK (nineties IS NULL OR minutes IS NULL OR abs(nineties - minutes / 90.0) < 0.2)
);
COMMENT ON TABLE fact_player_season IS
    'One row per player per club per season, from the frozen FBref snapshot. Wide on purpose: the metric '
    'set is fixed by scoping doc 4.4. A player who moved mid-season has one row per club.';
COMMENT ON COLUMN fact_player_season.fbref_position_raw IS
    'FBref''s raw Pos value. position_group_key is derived from the first listed position; this keeps the '
    'original visible for auditing.';
COMMENT ON COLUMN fact_player_season.is_value_filled IS
    'True where blank snapshot values were filled from Kaggle (reference/fbref_blank_fill.csv).';

CREATE INDEX ix_player_season_club   ON fact_player_season (club_key, season_key);
CREATE INDEX ix_player_season_player ON fact_player_season (player_key, season_key);

-- =============================================================================
-- fact_club_season -- sporting return
-- =============================================================================

CREATE TABLE fact_club_season (
    club_season_key      bigserial PRIMARY KEY,
    club_key             integer   NOT NULL REFERENCES dim_club,
    season_key           integer   NOT NULL REFERENCES dim_season,
    competition_key      integer   NOT NULL REFERENCES dim_competition,
    matches              smallint  NOT NULL,
    wins                 smallint  NOT NULL,
    draws                smallint  NOT NULL,
    losses               smallint  NOT NULL,
    goals_for            smallint  NOT NULL,
    goals_against        smallint  NOT NULL,
    goal_difference      smallint  NOT NULL,
    points_from_results  smallint  NOT NULL,
    position_computed    smallint,
    position_source      smallint,
    has_known_deduction  boolean   NOT NULL DEFAULT false,
    deduction_note       text,

    CONSTRAINT club_season_natural_key UNIQUE (club_key, season_key),
    CONSTRAINT results_add_up          CHECK (matches = wins + draws + losses),
    CONSTRAINT goal_difference_correct CHECK (goal_difference = goals_for - goals_against),
    CONSTRAINT points_follow_results   CHECK (points_from_results = 3 * wins + draws),
    CONSTRAINT deduction_note_present  CHECK (NOT has_known_deduction OR deduction_note IS NOT NULL)
);
COMMENT ON COLUMN fact_club_season.points_from_results IS
    'Points from match results only. Administrative deductions are NOT applied -- see has_known_deduction. '
    'The CHECK constraint points_follow_results guarantees this column can never quietly become '
    '"official points" without the constraint being dropped first.';
COMMENT ON COLUMN fact_club_season.position_source IS
    'Transfermarkt''s own reported position. Differs from position_computed in 49 of 684 club-seasons: '
    'points deductions, Spanish/Italian head-to-head tie-breaks, or a rescheduled final match.';

-- =============================================================================
-- Remaining facts
-- =============================================================================

CREATE TABLE fact_player_valuation (
    valuation_key      bigserial PRIMARY KEY,
    player_key         integer   NOT NULL REFERENCES dim_player,
    valuation_date_key integer   NOT NULL REFERENCES dim_date,
    club_key           integer   REFERENCES dim_club,
    market_value_eur   numeric(14,2) NOT NULL,
    CONSTRAINT valuation_natural_key UNIQUE (player_key, valuation_date_key),
    CONSTRAINT valuation_positive    CHECK (market_value_eur >= 0)
);

CREATE TABLE fact_club_trophy (
    trophy_key       bigserial PRIMARY KEY,
    club_key         integer   NOT NULL REFERENCES dim_club,
    season_key       integer   NOT NULL REFERENCES dim_season,
    competition_key  integer   NOT NULL REFERENCES dim_competition,
    trophy_category  text      NOT NULL,
    source_system    text      NOT NULL,
    source_ref       text      NOT NULL,
    CONSTRAINT trophy_natural_key UNIQUE (club_key, season_key, competition_key),
    CONSTRAINT trophy_category_known
        CHECK (trophy_category IN ('league_titles', 'domestic_cups', 'european_trophies'))
);
COMMENT ON TABLE fact_club_trophy IS
    'Trophies won inside the scored window only -- the dashboard panel is "Trophies since 2017/18", not '
    'an all-time count. source_ref traces each to a match id, a league table, or a Wikidata Q-item.';

CREATE TABLE fact_manager_tenure (
    tenure_key                 bigserial PRIMARY KEY,
    club_key                   integer   NOT NULL REFERENCES dim_club,
    manager_key                integer   NOT NULL REFERENCES dim_manager,
    stint_seq                  smallint  NOT NULL,
    first_match_date_key       integer   NOT NULL REFERENCES dim_date,
    last_match_date_key        integer   NOT NULL REFERENCES dim_date,
    first_season_key           integer   NOT NULL REFERENCES dim_season,
    last_season_key            integer   NOT NULL REFERENCES dim_season,
    matches                    smallint  NOT NULL,
    league_matches             smallint  NOT NULL,
    is_likely_caretaker        boolean   NOT NULL,
    boundaries_are_match_based boolean   NOT NULL DEFAULT true,
    CONSTRAINT tenure_natural_key   UNIQUE (club_key, manager_key, stint_seq),
    CONSTRAINT tenure_dates_ordered CHECK (last_match_date_key >= first_match_date_key),
    CONSTRAINT league_within_total  CHECK (league_matches <= matches),
    CONSTRAINT tenure_is_match_based CHECK (boundaries_are_match_based)
);
COMMENT ON COLUMN fact_manager_tenure.boundaries_are_match_based IS
    'Always true, enforced by CHECK. The source has no appointment or departure dates: a tenure runs from '
    'the manager''s first match to their last. Nothing downstream may present these as official dates.';

CREATE TABLE bridge_player_club_spell (
    spell_key               bigserial PRIMARY KEY,
    player_key              integer   NOT NULL REFERENCES dim_player,
    club_key                integer   NOT NULL REFERENCES dim_club,
    arrival_transfer_key    bigint    REFERENCES fact_transfer,
    departure_transfer_key  bigint    REFERENCES fact_transfer,
    start_date_key          integer   REFERENCES dim_date,
    end_date_key            integer   REFERENCES dim_date,
    purchase_fee_eur        numeric(14,2),
    sale_fee_eur            numeric(14,2),
    market_value_at_arrival numeric(14,2),
    market_value_at_exit    numeric(14,2),
    seasons_at_club         smallint,
    minutes_at_club         integer,
    arrival_known           boolean   NOT NULL,
    departure_known         boolean   NOT NULL,
    arrival_is_academy      boolean,
    CONSTRAINT spell_natural_key UNIQUE (player_key, club_key, start_date_key),
    CONSTRAINT arrival_flag_matches_key
        CHECK (arrival_known = (arrival_transfer_key IS NOT NULL)),
    CONSTRAINT departure_flag_matches_key
        CHECK (departure_known = (departure_transfer_key IS NOT NULL)),
    CONSTRAINT purchase_fee_needs_arrival
        CHECK (purchase_fee_eur IS NULL OR arrival_known),
    CONSTRAINT spell_dates_ordered
        CHECK (end_date_key IS NULL OR start_date_key IS NULL OR end_date_key >= start_date_key)
);
COMMENT ON TABLE bridge_player_club_spell IS
    'Derived from fact_transfer: a player''s continuous time at one club, for recruitment ROI and trading '
    'profit. Only 27% of player-club-seasons sit inside a reconstructable spell, which is why this is a '
    'bridge and not the core grain.';
COMMENT ON COLUMN bridge_player_club_spell.arrival_known IS
    'False for an academy graduate, a pre-2012 arrival, or a player whose history the source lacks. '
    'Trading profit on such a spell has no purchase price: it must be reported as unknown, never as 0. '
    'CHECK purchase_fee_needs_arrival stops a fee appearing without one.';

-- =============================================================================
-- meta -- provenance
-- =============================================================================

CREATE TABLE meta.source_manifest (
    path               text PRIMARY KEY,
    role               text,
    origin             text,
    version            text,
    source_url         text,
    source_updated_at  text,
    size_bytes         bigint,
    sha256             char(64),
    retrieved_at       text,
    rows               bigint,
    first_season       text,
    last_season        text,
    notes              text
);
COMMENT ON TABLE meta.source_manifest IS
    'Every frozen source file with its checksum: the FBref snapshot, the Kaggle set, the Transfermarkt '
    'tables and the 1,015 fetched club pages. Makes "this run used exactly these bytes" checkable in SQL.';

CREATE TABLE meta.decision (
    decision_key  serial PRIMARY KEY,
    source_table  text NOT NULL,      -- which review ledger
    entity_id     text NOT NULL,      -- fbref_team_id, manager name, ...
    field         text,
    value         text,
    decision      text NOT NULL,
    decided_by    text NOT NULL,
    decided_on    date NOT NULL,
    reason        text NOT NULL
);
COMMENT ON TABLE meta.decision IS
    'Every human judgement behind the curated tables: club-mapping conflicts, city overrides, manager '
    'name splits. Keyed to the specific pair or value approved.';

CREATE TABLE meta.transfer_coverage (
    club_key             integer NOT NULL REFERENCES dim_club,
    season_key           integer NOT NULL REFERENCES dim_season,
    page_fees_eur        numeric(14,2),
    frozen_fees_eur      numeric(14,2),
    fees_missing_eur     numeric(14,2),
    page_arrivals        smallint,
    frozen_arrivals      smallint,
    is_in_scope          boolean NOT NULL,
    PRIMARY KEY (club_key, season_key)
);
COMMENT ON TABLE meta.transfer_coverage IS
    'How complete the frozen dataset was per club-season, measured against Transfermarkt''s own pages: '
    '79% of true spend present in 2017/18 rising to 99.6% in 2023/24. Kept so the fix is auditable.';

COMMIT;
