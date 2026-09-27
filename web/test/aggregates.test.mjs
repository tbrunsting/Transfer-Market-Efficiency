/**
 * The browser does the filtering, so the browser's arithmetic has to be right.
 *
 * Every number on page 1 comes from src/lib/aggregate.ts. This runs those functions over the exported
 * page1.json and compares the results with expectations computed in SQL by
 * scripts/82_web_expectations.py — the same independent-recomputation pattern the scoring layer uses.
 *
 *     npm test        (from web/)
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { unpack } from "../src/lib/pack.ts";
import { aggregateByClub, filterRows, kpis, median, quadrantOf, spendByLeague } from "../src/lib/aggregate.ts";

const here = (p) => fileURLToPath(new URL(p, import.meta.url));
const page1 = JSON.parse(readFileSync(here("../public/data/page1.json"), "utf8"));
const expectations = JSON.parse(readFileSync(here("expectations.json"), "utf8"));
const rows = unpack(page1.overview);

const allSeasons = [...new Set(rows.map((r) => r.season_end_year))];
const allLeagues = [...new Set(rows.map((r) => r.league))];
const EUR = 1;        // spend is exported as whole euros
const SMALL = 1e-6;

test("the export decodes to the full set of club-seasons", () => {
  assert.equal(rows.length, 684);
  assert.equal(new Set(rows.map((r) => r.club_key)).size, 145);
});

for (const [name, want] of Object.entries(expectations.scenarios)) {
  test(`aggregation matches SQL: ${name}`, () => {
    const filters = {
      seasons: new Set(want.filters.seasons ?? allSeasons),
      leagues: new Set(want.filters.leagues ?? allLeagues),
    };
    const filtered = filterRows(rows, filters);
    const clubs = aggregateByClub(filtered);
    const summary = kpis(clubs);

    assert.equal(filtered.length, want.club_seasons, "club-seasons after filtering");
    assert.equal(clubs.length, want.clubs, "clubs after aggregation");
    assert.ok(Math.abs(summary.totalSpend - want.total_spend_eur) <= EUR, "total spend");
    assert.ok(Math.abs(summary.medianScore - want.median_efficiency_score) < SMALL, "median efficiency score");
    assert.equal(summary.best?.club_name, want.best_club, "most efficient club");
    assert.ok(Math.abs((summary.best?.efficiency_index ?? 0) - want.best_index) < SMALL, "its index");
    assert.equal(summary.unplotted, want.clubs_without_spend, "clubs a log axis cannot plot");

    assert.deepEqual(clubs.slice(0, 5).map((c) => c.club_name), want.top5, "best five");
    assert.deepEqual(clubs.slice(-5).reverse().map((c) => c.club_name), want.bottom5, "worst five");

    const plotted = clubs.filter((c) => c.spend > 0);
    assert.ok(Math.abs(median(plotted.map((c) => c.spend)) - want.median_spend_plotted) <= EUR, "median spend");
    assert.ok(Math.abs(median(plotted.map((c) => c.points_per_match)) - want.median_points_per_match_plotted) < SMALL,
      "median points per match");

    const donut = spendByLeague(filtered);
    assert.deepEqual(donut.map((d) => d.league), want.spend_by_league.map((d) => d.league), "donut order");
    donut.forEach((d, i) => assert.ok(Math.abs(d.spend - want.spend_by_league[i].spend) <= EUR, `donut ${d.league}`));
  });
}

test("quadrants split on the medians of whatever is displayed", () => {
  const filters = { seasons: new Set(allSeasons), leagues: new Set(allLeagues) };
  const clubs = aggregateByClub(filterRows(rows, filters)).filter((c) => c.spend > 0);
  const medSpend = median(clubs.map((c) => c.spend));
  const medPpm = median(clubs.map((c) => c.points_per_match));
  const counts = { smart: 0, burned: 0, heavy: 0, frugal: 0 };
  for (const c of clubs) counts[quadrantOf(c, medSpend, medPpm)] += 1;

  assert.equal(Object.values(counts).reduce((a, b) => a + b), clubs.length, "every club lands in one quadrant");
  for (const [q, n] of Object.entries(counts)) assert.ok(n > 0, `${q} quadrant is not empty`);
  // a median split puts half the clubs on each side of each axis, within one club for odd counts
  const highSpend = clubs.filter((c) => c.spend >= medSpend).length;
  assert.ok(Math.abs(highSpend - clubs.length / 2) <= 1, "the vertical median splits the clubs in half");
});

test("filters compose: leagues and seasons narrow independently", () => {
  const one = filterRows(rows, { seasons: new Set([2024]), leagues: new Set(allLeagues) });
  const two = filterRows(rows, { seasons: new Set([2024]), leagues: new Set(["Premier League"]) });
  assert.ok(two.length < one.length);
  assert.ok(two.every((r) => r.league === "Premier League" && r.season_end_year === 2024));
  assert.equal(filterRows(rows, { seasons: new Set(), leagues: new Set(allLeagues) }).length, 0);
});
