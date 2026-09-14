-- =============================================================================
-- Transfer Market Efficiency -- Pillar 2, trading profit
--   score.sale_basis                  one row per sale: income, the purchase basis and where it came from
--   score.club_season_trading         one row per club-season: realised profit on that season's sales
--   score.run_parameter               (shared) the parameters and fitted values behind the run
--
-- Owned by R/40_trading_profit.R, which runs this file and refills the tables
-- in one transaction. Derived only; rerun after every warehouse load.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS score;

DROP TABLE IF EXISTS score.sale_basis;
DROP TABLE IF EXISTS score.club_season_trading;

CREATE TABLE IF NOT EXISTS score.run_parameter (
    pillar      text NOT NULL,
    name        text NOT NULL,
    value       double precision NOT NULL,
    note        text,
    PRIMARY KEY (pillar, name)
);
DELETE FROM score.run_parameter WHERE pillar = 'trading_profit';

CREATE TABLE score.sale_basis (
    transfer_key          bigint PRIMARY KEY,       -- fact_transfer: the sale
    club_key              int NOT NULL,             -- the selling club
    season_key            int NOT NULL,
    player_key            int NOT NULL,
    transfer_type         text NOT NULL,            -- permanent_with_fee | loan_with_fee | undisclosed
    income_eur            double precision,         -- NULL for undisclosed sales: never valued
    owning_arrival_key    bigint,                   -- fact_transfer: the latest arrival into this club before the sale
    basis_source          text NOT NULL,
    basis_date            date,
    basis_valuation_date  date,
    basis_lookup          text,                     -- backward | forward | none
    basis_eur             double precision,
    profit_eur            double precision,
    counts_toward_profit  boolean NOT NULL,
    CONSTRAINT basis_source_known CHECK (basis_source IN
        ('window_arrival', 'at_club_on_2017_07_01', 'loan_fee_received', 'undisclosed_excluded')),
    CONSTRAINT undisclosed_never_counts CHECK ((transfer_type = 'undisclosed') = NOT counts_toward_profit),
    CONSTRAINT counted_sales_have_profit CHECK (NOT counts_toward_profit OR profit_eur IS NOT NULL)
);

COMMENT ON TABLE score.sale_basis IS
    'Pillar 2 inputs: every sale by a club in a big-five season. Basis = market value when the club acquired the '
    'player: at the latest arrival into the club before the sale (loans out do not end ownership), or, for a '
    'player already at the club when the window opened, as if acquired at market value on 1 July 2017.';
COMMENT ON COLUMN score.sale_basis.basis_lookup IS
    'backward: latest valuation on or up to 365 days before basis_date. forward: none there, so the first '
    'valuation up to 180 days after (young signings are often valued only after joining). none: no valuation '
    'in either window, basis 0.';

CREATE TABLE score.club_season_trading (
    club_season_key          bigint PRIMARY KEY,    -- fact_club_season
    club_key                 int NOT NULL,
    season_key               int NOT NULL,          -- the sale window's season
    n_fee_sales              smallint NOT NULL,
    n_loan_fee_income        smallint NOT NULL,
    n_undisclosed_sales      smallint NOT NULL,
    n_legacy_basis           smallint NOT NULL,     -- fee sales priced at the 1 July 2017 valuation
    n_zero_basis             smallint NOT NULL,     -- fee sales with no valuation to price them
    income_eur               double precision NOT NULL,
    basis_eur                double precision NOT NULL,
    profit_eur               double precision NOT NULL,
    profit_median_fees       double precision NOT NULL, -- profit / that season's median permanent fee
    profit_capped            double precision NOT NULL, -- winsorised at the 1st and 99th percentiles
    is_capped                boolean NOT NULL,
    profit_normalised        double precision NOT NULL,
    profit_z                 double precision NOT NULL,
    CONSTRAINT trading_club_season_unique UNIQUE (club_key, season_key)
);

COMMENT ON TABLE score.club_season_trading IS
    'Pillar 2 (scoping doc 5): realised profit on a club-season''s sales, sale price against market value at '
    'acquisition. Realised only: growth in players still held belongs to Pillar 3, so nothing is counted twice. '
    'See docs/phase-3-scoring.md.';
COMMENT ON COLUMN score.club_season_trading.profit_normalised IS
    'Residual of profit_capped regressed on log(squad_value_start_eur) with season and league effects: the same '
    'normalisation as Pillar 1.';
