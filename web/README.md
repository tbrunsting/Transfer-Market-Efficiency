# web — the static dashboard

The Phase 4 Power BI dashboard, rebuilt as a static site so it can be opened
from a link. Two pages, no database at run time, no UI framework.

Full write-up: [`../docs/phase-5-web-dashboard.md`](../docs/phase-5-web-dashboard.md).
What the published snapshot contains, field by field, and where each field came
from: [`../docs/phase-5-published-data.md`](../docs/phase-5-published-data.md).

## Running it

```
cd web
npm install
npm run dev        # http://localhost:4321
npm test           # 22 tests, no database needed
npm run build      # writes dist/
```

## Refreshing the data

The site reads a committed JSON snapshot of the `presentation.*` views, because
GitHub Pages cannot reach Postgres. Refreshing it needs a live warehouse and
`.env`, and is run from the repository root:

```
.venv\Scripts\python scripts\80_export_web_data.py       # writes web/public/data + crests
.venv\Scripts\python scripts\81_check_web_export.py      # 20 checks against the views
.venv\Scripts\python scripts\82_web_expectations.py      # regenerates web/test/expectations.json
cd web && npm test
```

Then commit the diff under `web/public/data/` and `web/test/expectations.json`.
Two exports of unchanged data are byte-identical, so a diff means the warehouse
changed.

## Why this page's median differs from the Power BI card

The Power BI card is labelled **Median Efficiency Index** and reads **−0.03**.
This site's card is labelled **Median efficiency score (0–100)** and reads
**39.2**. Both are right; they differ in two ways, and the labels now say so.

1. **Scale.** The index is a z-score, roughly −3.4 to +4.9 across the window.
   The 0–100 score is that same index put on a fixed scale
   (`sql/70_presentation_views.sql`), so it is comparable between clubs but is
   not a percentile and not a percentage.
2. **Grain.** Power BI takes the median over **club-seasons**; this page takes
   the median over **clubs**, after averaging each club's selected seasons,
   because everything else on the page is per club.

Checked in SQL against the warehouse:

| Median of | Index | 0–100 score |
|---|---|---|
| 684 club-seasons | −0.0313 | 40.20 |
| 145 club means | −0.1167 | 39.17 |

So the site's 39.2 is exactly −0.1167 rescaled, and Power BI's −0.03 is the
club-season figure on the index scale. The same grain difference applies to the
scatter: Power BI plots one point per club-season, this page plots one per club
over the seasons selected.

## Layout

```
src/pages/index.astro   page 1, league overview
src/pages/club.astro    page 2, club detail (club/#club=<club_key>)
src/lib/pack.ts         decoder for the packed JSON written by scripts/80
src/lib/aggregate.ts    page 1 arithmetic   (tested against SQL)
src/lib/club.ts         page 2 arithmetic   (tested against SQL)
src/lib/charts.ts       the tree-shaken ECharts build
public/data/            the committed snapshot
test/                   node --test, expectations generated from the warehouse
```

`PAGES_BASE` sets the path the site is served from; it is read in
`astro.config.mjs` and set in `.github/workflows/deploy-web.yml`.
