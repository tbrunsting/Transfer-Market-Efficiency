# web — the static dashboard

The Phase 4 Power BI dashboard, rebuilt as a static site so it can be opened
from a link. Two pages, no database at run time, no UI framework.

Full write-up: [`../docs/phase-5-web-dashboard.md`](../docs/phase-5-web-dashboard.md).

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
