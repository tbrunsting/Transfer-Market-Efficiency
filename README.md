# Transfer Market Efficiency

**Which clubs in Europe's top five leagues get the most on-field value per euro
spent in the transfer market, and which got smarter over time?**

A two-page Power BI dashboard over a Postgres warehouse, built from free,
frozen, checksummed data. Seven seasons, 2017/18 to 2023/24, five leagues,
145 clubs.

`Dashboard.pbix` is the deliverable, and `web/` is the same two pages rebuilt
as a static site that needs no database to open. Everything behind them —
ingest, warehouse, scoring, presentation views — is in this repository, along
with the reasoning for every judgement call.

## The answer

**Most efficient clubs**, averaged across the window (composite index, z-score;
the 0–100 column is the same number rescaled for display):

| Club | Index | Score | Recruitment | Trading | Value growth | Points |
|---|---|---|---|---|---|---|
| Lille | +1.45 | 58.0 | +0.02 | +1.75 | +0.57 | +0.75 |
| Atalanta | +1.13 | 54.1 | −0.14 | +1.12 | +0.75 | +0.84 |
| Manchester City | +1.11 | 53.9 | +0.26 | +0.20 | +0.85 | +2.02 |
| Real Sociedad | +0.97 | 52.2 | +0.76 | −0.24 | +1.16 | +0.40 |
| Brighton | +0.95 | 52.0 | +0.14 | +1.07 | +0.60 | −0.37 |

**Least efficient:** Schalke −0.84, Manchester United −0.81, Juventus −0.78,
PSG −0.73, Marseille −0.65.

PSG and Barcelona sit near the bottom while ranking near the top of Europe on
points (+2.17 and +1.92). That is the point of an efficiency index: it measures
what a club got for its money, not how good it was.

### Three findings worth the write-up

**1. The Premier League pays roughly 2.4× for the same output.** At the same
squad value and season, Premier League clubs get **0.42×** the signing output
per deflated euro that Bundesliga clubs get. Ligue 1 1.08, La Liga 1.02,
Bundesliga 1.00, Serie A 0.79. The raw medians say the same thing: 0.11 output
per deflated euro in England against 0.32–0.53 elsewhere.

**2. Ligue 1 is the selling league.** Its clubs realise **+1.89 median fees**
more trading profit per club-season than comparable Bundesliga clubs; the
Premier League is last at −0.55. France buys low and sells high; England does
the reverse.

**3. Selling well costs points.** Trading profit is *negatively* related to
every measure of sporting success (−0.10 against points, −0.20 against
trophies at club level). Selling your best players at a profit is nearly the
same act as weakening your squad in the short term. Lille, Leipzig, Atalanta,
Brighton and Dortmund all convert value into money and, often, fewer points.
This is why the index keeps four pillars instead of collapsing into one.

## Method

Efficiency is computed **per club per season**, never as a single seven-year
average, so "who got smarter?" is answerable (scoping doc 4.3).

**Player quality first.** Every player-season is scored on per-90 output
against its own position group and season — six groups from Transfermarkt's
detailed positions, with FBref's season position overriding where they
disagree. A 900-minute floor applies; below it a rate is noise. Missing inputs
are gaps, never zeros.

**Four pillars, each a club-season z-score:**

| Pillar | What it measures |
|---|---|
| **Recruitment ROI** | output delivered by signings per deflated euro of fee, over three seasons |
| **Trading profit** | realised: sale price against the player's market value when the club acquired him |
| **Squad value growth** | unrealised: value gained by players still held, net of value written off when players leave for nothing |
| **Sporting return** | league points per match, standardised within league-season |

Realised and unrealised are kept strictly apart, so nothing is counted twice.

**Guardrails** (scoping doc 5): fees are deflated by each season's median fee;
ROI is log-transformed; club-seasons below the 10th-percentile spend are
labelled "insufficient spend" rather than scored; and every pillar is
normalised by the *residual* on log squad value with season and league effects,
never by division.

**The weights are chosen, and the reasoning is documented.** The scoping doc
originally specified deriving them by regressing each pillar on sporting
success. Built and measured, that failed three ways: trading profit took a
negative coefficient against every target, R² ran 0.006–0.129, and
leave-one-season-out coefficients moved as much as the coefficients themselves.
It is structural — the pillars are residualised against squad value, while
points correlate 0.67 with it — so no choice of target rescues it. The index
therefore uses 0.30 each for the three money pillars and **0.10 for sporting
return**, the smallest weight that stops "efficient" meaning cheap and bad.
Section 5 of the scoping doc records what was tried and why it changed.

## Data

All sources are free, frozen locally and checksummed (1,104 manifest rows); the
loader refuses to run if a checksum moves.

| Source | Used for |
|---|---|
| worldfootballR FBref snapshot (archived Sept 2025) | player and team performance |
| Kaggle FBref 2017–2024 | shot-creating actions, validated against the snapshot |
| transfermarkt-datasets (frozen July 2026) | transfers, valuations, matches, managers |
| 1,015 Transfermarkt club pages (plain HTTP) | the primary fee source |
| Wikidata | club cities, missing cup winners |

**Why the window ends at 2023/24:** on 20 January 2026 Opta terminated FBref's
data licence and every advanced statistic was removed, past seasons included.
The last complete public copy stops there.

**Why the club pages were pulled:** the frozen Transfermarkt table was missing
up to 21% of real spend in early seasons and almost none by 2023/24 — a 40×
coverage gradient that would have manufactured a "clubs got smarter" trend out
of nothing. This was the single most important catch in the project.

Warehouse: 684 club-seasons, 19,562 player-seasons, 34,726 transfers, 213,973
valuations, and 108 human decisions recorded in `meta.decision` with their
reasoning.

## Limitations

Stated plainly, each with a revisit criterion in the docs:

- **Undisclosed fees are excluded, never estimated.** 1,696 of the arrivals
  the scoring covers have no fee (2,988 across all 145 clubs, counting seasons
  they spent outside the big five). This understates spend most for the clubs
  that disclose least, flattering their efficiency.
- **Centre-backs are the worst-measured group.** Public event data records what
  a defender does, not what they prevent. Their defensive volume stats are
  adjusted for team possession by residual, but elite centre-backs at dominant
  clubs still rank mid-table.
- **Wages are excluded.** Fees only, so a club with modest fees and an enormous
  wage bill looks better than it is.
- **The valuations' club field is unreliable** (73% agreement with real
  ownership), so ownership is rebuilt from transfer events wherever it matters.
- **Pillar 3 is thinner in early seasons** by construction: 92% of 2018/19's
  value growth belongs to players since sold, whose value is in Pillar 2.
- **2022/23 and 2023/24 are provisional**, because recruitment ROI credits
  three seasons of output and the window ends.
- **Transfers after 2023/24 are not shown.** The recency layer was deferred.

## Reproducing it

Requires PostgreSQL, Python 3.14 and R 4.4, with credentials in `.env`
(`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`).

```
.venv\Scripts\python scripts\31_rebuild_warehouse.py     # drop and recreate the schema
.venv\Scripts\python scripts\30_load_warehouse.py        # verify checksums, load, preflight checks
psql -d transfer_market -f sql\03_checks.sql             # 47 checks, all must pass

"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\10_player_quality.R
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\20_sporting_return.R
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\30_recruitment_roi.R
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\40_trading_profit.R
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\50_value_growth.R
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\60_composite.R
"C:\Program Files\R\R-4.4.1\bin\Rscript.exe" R\70_presentation_views.R   # always last
```

The static dashboard is rebuilt from the same views:

```
.venv\Scripts\python scripts\80_export_web_data.py     # snapshot the views to web/public/data
.venv\Scripts\python scripts\81_check_web_export.py    # 20 checks, decoded and row-compared
.venv\Scripts\python scripts\82_web_expectations.py    # SQL expectations for the client tests
cd web && npm ci && npm test && npm run build
```

Each R script writes its results and then **recomputes every value
independently in SQL**, failing if the two disagree by more than 1e-9. Where R
fits a regression with dummy variables, the SQL check solves the same model a
different way (alternating projections), so agreement means more than shared
code. Several checks were also confirmed to fail on deliberately planted
errors.

| Layer | Checks |
|---|---|
| Warehouse (`03_checks.sql`) | 47 |
| Player quality, Pillars 4, 1, 2, 3, composite | 20, 9, 32, 26, 22, 14 |
| Presentation views (`71_check_presentation_views.sql`) | 40 |
| Web export (`81_check_web_export.py`) | 20 |
| Web client arithmetic (`web/test`, against SQL) | 22 |

## Repository

```
scripts/   Python: ingest, freezing, reference tables, warehouse loader, web export
sql/       DDL, warehouse checks, score tables, presentation views, check files
R/         the scoring layer, one script per pillar plus the composite and views
reference/ hand-built mapping tables and the review ledgers behind them
web/       the static Astro dashboard and the committed JSON snapshot it reads
docs/      the scoping document, phase write-ups, mockups
data/      frozen sources (gitignored; rebuilt from the manifests)
```

**Documentation**

- `docs/Transfer-Market-Efficiency-Scoping-Doc.docx` — the design, including
  the decisions that changed once measured
- `docs/phase-1-summary.md`, `docs/phase-1-data-coverage.md` — sources, coverage
  and the gaps
- `docs/phase-2-schema.md` — warehouse design and the rules baked into the DDL
- `docs/phase-3-scoring.md` — every pillar, the evidence behind each decision,
  and the limitations
- `docs/phase-4-presentation-layer.md` — the six views Power BI reads
- `docs/phase-5-web-dashboard.md` — the static web rebuild: export, encoding,
  checks, accessibility and deployment
- `docs/phase-5-published-data.md` — every field the public site republishes
  and which source it came from

An interesting wrong answer that is honestly documented is worth more than a
clean answer that quietly hides its assumptions. Where this project's own plan
did not survive contact with the data, the documentation says so.
