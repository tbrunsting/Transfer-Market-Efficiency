# Phase 5 — the web dashboard

The Power BI dashboard from Phase 4 is the reference implementation: it reads
the six `presentation.*` views live from Postgres. This phase rebuilds the same
two pages as a static site that needs no database at run time, so the work can
be opened from a link instead of a `.pbix` file.

Nothing in the scoring layer changed. The site is a *second reader* of the same
views, and its numbers are tested against SQL rather than against Power BI.

## 1. The constraint that shaped everything

GitHub Pages serves files. There is no server, no database connection and no
build-time access to the warehouse. So the pipeline is:

```
presentation.vw_*   →   scripts/80_export_web_data.py   →   web/public/data/*.json   →   Astro build
      (Postgres)              (run locally)                    (committed)              (CI, no DB)
```

The JSON snapshot is committed. Refreshing the dashboard is: re-run the export,
re-run the checker, commit the diff. CI never touches Postgres.

## 2. The export

`scripts/80_export_web_data.py` writes, into `web/public/data/`:

| File | Grain | Size |
|---|---|---|
| `page1.json` | 684 club-seasons, the league overview | 51 KB |
| `clubs/index.json` | 145 clubs, for the search list | ~11 KB |
| `clubs/<club_key>.json` | one club: squad, transfers, cash flow, pillars, honours | 17 KB median |
| `manifest.json` | SHA-256 of every file written, plus row counts | — |
| `crests/<club_key>.png` | 145 crests, vendored, never hotlinked | — |

Sharding by club is what makes the club page cheap: opening a club costs one
~17 KB request, not a 2.5 MB download of all 145.

The export also carries only the fields the pages read: a field-level audit on
2026-09-27 found 34 unread fields and three unread tables, which were dropped,
and the checker now pins the surviving column list in both directions. See
[`phase-5-published-data.md`](phase-5-published-data.md) §5.

**Packed encoding.** Each table is stored column-by-column, and text columns
whose values repeat at least `DICTIONARY_RATIO = 3` times are dictionary-coded
(`{"dict": [...], "codes": [...]}`). `web/src/lib/pack.ts` decodes it back into
row objects; the decoder mirrors the encoder exactly and is the only place the
format is known.

**Determinism.** Every query carries a full tiebreaker in its `ORDER BY`, floats
are rounded to `FLOAT_DP = 6` and money to whole euros. Two consecutive exports
produce byte-identical files — verified over all 147 data files — so a commit
diff means the data changed, not the export.

**Rounding.** 3 decimal places reordered clubs whose efficiency indices differ
in the fourth; 6 is the floor that preserves the SQL ordering, and the checker
asserts it.

## 3. The checks

`scripts/81_check_web_export.py` — **20 checks**, run against a live warehouse
after every export. It decodes the packed tables in Python and row-compares them
against the views (`MONEY_TOLERANCE = 0.5` euro, `FLOAT_TOLERANCE = 5e-7`),
verifies the manifest hashes, the shard count, the crest count, and that no
undisclosed fee was turned into a zero anywhere in transit.

`web/test/*.test.mjs` — **22 tests** under `node --test`, run with no database.
They hold the browser's own arithmetic to numbers computed in SQL:
`scripts/82_web_expectations.py` writes `web/test/expectations.json` from the
warehouse (4 filter scenarios on page 1, 3 clubs on page 2), and the tests
recompute the same quantities through the real TypeScript modules.

The three clubs are chosen, not arbitrary: **LOSC Lille** (a selling club with a
full history), **Chelsea FC** (a heavy spender) and **1. FC Heidenheim 1846**
(falls below the recruitment spend floor, so the pillar average must *skip* a
season rather than treat a missing score as zero).

Aggregation rules the tests pin down, because they are easy to get subtly wrong
in JavaScript:

- spend is summed; **points per match is total points ÷ total matches**, never a
  mean of per-season means
- efficiency is the mean of the club's season scores
- medians recompute against the current filter selection, matching Power BI
- free transfers are a **count**, never €0 of spend; undisclosed fees stay `null`

## 4. The pages

Astro 7 with no UI framework: each page ships one vanilla TypeScript island and
a tree-shaken ECharts build. There is no framework runtime in the bundle.

**Page 1 — league overview** (`web/src/pages/index.astro`). Season and league
filters, a "How to read this" strip, four KPIs, the spend-vs-points-per-match
quadrant scatter (log x axis, dashed median lines, shaded quadrants), a spend
donut that cross-filters by league when a slice is clicked, and best/worst
efficiency bars. Clicking a scatter point or a ranking bar opens that club's
page.

**Page 2 — club detail** (`web/src/pages/club.astro`). Club search, season
checkboxes, KPIs, squad table, the four pillars as zero-centred bars, the
spending breakdown, honours, largest transfers with a direction filter, and the
cash-flow chart with manager tenure bands beneath it.

Client-side logic lives in `web/src/lib/`: `pack.ts` (decoder), `aggregate.ts`
(page 1 arithmetic), `club.ts` (page 2 arithmetic), `format.ts`, `charts.ts`.
The two arithmetic modules are the ones under test.

## 5. State, accessibility and small screens

**Empty states.** Clearing every season or league leaves the page with nothing
to draw: the charts are hidden and replaced with "No clubs match the current
filters", and the ranking bars say so too, rather than rendering empty axes. On
the club page, a club with no seasons selected shows "No seasons selected" in
place of four `n/a` pillar bars, and the squad and transfer tables carry their
own empty rows.

**Error states.** A static site cannot retry against a server, so a failed fetch
replaces the page body with a card naming the exact file that 404'd and the
command that regenerates it. Verified by moving `page1.json` and a club shard
out of the way and reloading. A stale or hand-edited `#club=` falls back to the
first club instead of failing.

**Accessibility.** Skip link on both pages; filter groups are `fieldset` +
`legend`; the KPI row is an `aria-live="polite"` region so a filter change is
announced; ranking bars are anchors, so the route to a club page is keyboard
operable (25 focusable elements on page 1, in document order); the direction
filter is a `role="group"` with `aria-pressed`; tables carry a `caption` and
`scope="col"`; every chart is `role="img"` with an `aria-label` that is rewritten
on each render with the actual numbers, so a screen reader gets the totals
rather than "canvas".

The donut's click-to-filter is mouse-only — the same filtering is available from
the league checkboxes, which is why it is an enhancement rather than a gap.

**Contrast.** The chart palette was measured against WCAG AA rather than
eyeballed, and four text colours failed at 14px or smaller: fee-out `#e8674f`
(3.24:1), fee-in `#2fae87` (2.79:1), the provisional marker `#b07d2b` (3.61:1)
and the manager-tenure band text on its tinted background (4.50:1). The marks
in the charts keep the bright palette — they are graphics, and every one of
them is labelled with a number — but the *text* now uses darkened tokens
(`--good-text`, `--bad-text`, `--prov-text`) measuring 5.0–6.3:1 on both the
card and the page background.

**Narrow screens.** Two breakpoints. At 1100px the multi-column grids collapse
and the filters wrap. At 760px the label column of the bars and pillars moves
above its track, KPIs go to two across, tables shrink and scroll horizontally
inside their card, the segmented filter goes full width, and the charts get
shorter. The scatter's quadrant corner labels are dropped below 760px, where
they would sit on top of the points; crossing that breakpoint re-renders the
charts rather than only resizing them.

Verified in the browser at 375×812 and at desktop width, not only in a passing
build: no horizontal page scroll at 375px on either page.

## 5a. Attribution and what is republished

Both pages end in a **Data & sources** footer, rendered once in
`web/src/layouts/Base.astro` so the two pages cannot drift apart and the credit
survives the fetch-failure state (the footer sits outside `<main>`, which is
what the error card replaces). It credits Transfermarkt and
transfermarkt-datasets, FBref via the archived worldfootballR_data snapshot,
the Kaggle FBref 2017–2024 dataset and Wikidata; says plainly that the scores
are derived by this project and endorsed by none of those sources; says the
data is a frozen snapshot rather than a live feed; and links to the repository
README.

A field-by-field inventory of everything the published JSON exposes, with the
source of each field and the volumes involved, is in
[`phase-5-published-data.md`](phase-5-published-data.md). It was written before
the first public deploy so that the decision to publish could be made against
the actual contents.

## 6. Deployment

`.github/workflows/deploy-web.yml` builds `/web` and publishes it to GitHub
Pages on a push to `main` that touches `web/`, or on manual dispatch. The job
installs with `npm ci`, runs the 22 tests, builds, and uploads `web/dist`.

`PAGES_BASE` is the one setting that has to match where the site is served
from; it is set in the workflow and read by `web/astro.config.mjs`. Every
internal URL is built from `import.meta.env.BASE_URL`, so moving the site — for
example under the portfolio site rather than at
`/Transfer-Market-Efficiency/` — is a one-line change.

Before the first deployment, Pages has to be switched to "GitHub Actions" as its
source in the repository settings. That is a repository setting, not something
the workflow can do for itself.

## 7. Known limitations

- **The snapshot can go stale.** The site shows whatever was last exported and
  committed; `manifest.json` records the generation timestamp, and both pages
  print it in the footer. *Revisit if* the warehouse starts being refreshed on a
  schedule — that is the point to automate the export instead of hand-running it.
- **No deep link to a filtered view of page 1.** Page 2 is addressable
  (`club/#club=<key>`), page 1 is not: its filter state lives in memory only.
  *Revisit if* anyone needs to share a specific cut of the overview.
- **Charts are canvas.** The `aria-label` carries the totals, but not every
  point. *Revisit if* a full screen-reader table of the plotted clubs is wanted.
- **Squad and transfer tables scroll sideways on a phone** rather than dropping
  columns, because every column carries information the page claims to show.
