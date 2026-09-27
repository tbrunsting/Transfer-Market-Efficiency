/**
 * Page 2's arithmetic, held to the same standard as page 1: every number the club page shows is
 * recomputed here from the club's shard and compared with SQL (scripts/82_web_expectations.py).
 *
 * Three clubs on purpose: a selling club with a full history (Lille), a heavy spender (Chelsea) and a
 * club that falls below the recruitment spend floor (Heidenheim), which is where the pillar averages
 * have to skip a season rather than treat a missing score as zero.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { unpack } from "../src/lib/pack.ts";
import { financials, managerBands, pillarAverages, spendingBreakdown, squadForSeason, squadValue }
  from "../src/lib/club.ts";

const here = (p) => fileURLToPath(new URL(p, import.meta.url));
const expectations = JSON.parse(readFileSync(here("expectations.json"), "utf8"));
const EUR = 1;
const SMALL = 1e-6;

const shardFor = (clubKey) =>
  JSON.parse(readFileSync(here(`../public/data/clubs/${clubKey}.json`), "utf8"));

for (const [clubName, want] of Object.entries(expectations.clubs)) {
  const shard = shardFor(want.club_key);
  const cashflow = unpack(shard.cashflow);
  const pillars = unpack(shard.pillars);
  const transfers = unpack(shard.transfers);
  const squad = unpack(shard.squad);

  test(`club shard matches SQL: ${clubName}`, () => {
    assert.equal(shard.club.club_name, clubName);
    assert.equal(cashflow.length, want.seasons, "seasons in the shard");

    const totals = financials(cashflow);
    assert.ok(Math.abs(totals.paid - want.fees_paid_eur) <= EUR, "fees paid");
    assert.ok(Math.abs(totals.received - want.fees_received_eur) <= EUR, "fees received");
    assert.ok(Math.abs(totals.net - want.net_spend_eur) <= EUR, "net spend");
    assert.ok(Math.abs(totals.score - want.efficiency_score) < SMALL, "efficiency score");
    assert.ok(Math.abs(totals.index - want.efficiency_index) < SMALL, "efficiency index");
  });

  test(`pillar averages match SQL, skipping unscored seasons: ${clubName}`, () => {
    const [recruitment, trading, growth, sporting] = pillarAverages(pillars);
    const w = want.pillars;
    if (w.recruitment === null) assert.equal(recruitment.value, null, "recruitment has no scored season");
    else assert.ok(Math.abs(recruitment.value - w.recruitment) < SMALL, "recruitment");
    assert.ok(Math.abs(trading.value - w.trading) < SMALL, "trading");
    assert.ok(Math.abs(growth.value - w.value_growth) < SMALL, "value growth");
    assert.ok(Math.abs(sporting.value - w.sporting) < SMALL, "sporting");
    // a season below the spend floor must be left out, never counted as zero
    const scored = pillars.filter((p) => p.is_recruitment_scored).length;
    assert.equal(pillars.length - scored, w.seasons_below_spend_floor, "seasons below the spend floor");
  });

  test(`spending breakdown keeps free transfers as a count, not euros: ${clubName}`, () => {
    const b = spendingBreakdown(transfers);
    const w = want.breakdown;
    assert.ok(Math.abs(b.permanent - w.permanent_eur) <= EUR, "permanent fees");
    assert.ok(Math.abs(b.loanFees - w.loan_fees_eur) <= EUR, "loan fees");
    assert.equal(b.freeCount, w.free_count, "free signings counted");
    assert.equal(b.loanNoFeeCount, w.loan_no_fee_count, "fee-free loans counted");
    assert.equal(b.undisclosedCount, w.undisclosed_count, "undisclosed counted");
    // free and undisclosed moves must contribute no money at all
    const freeMoney = transfers.filter((t) => t.fee_status === "Free").reduce((s, t) => s + (t.fee_eur ?? 0), 0);
    assert.equal(freeMoney, 0, "free transfers carry no fee");
    assert.ok(transfers.filter((t) => t.fee_status === "Undisclosed").every((t) => t.fee_eur === null),
      "undisclosed fees stay null");
  });

  test(`squad, honours and manager bands match SQL: ${clubName}`, () => {
    const latest = squadForSeason(squad, want.latest_season_end_year);
    assert.equal(latest.length, want.latest_squad_players, "players in the latest season");
    assert.ok(Math.abs(squadValue(latest) - want.latest_squad_value_eur) <= EUR, "squad market value");
    assert.ok(latest.every((p, i) => i === 0 || (latest[i - 1].market_value_eur ?? -1) >= (p.market_value_eur ?? -1)),
      "squad is sorted by market value");

    const honours = unpack(shard.honours)[0];
    assert.equal(honours.league_titles, want.honours.league_titles);
    assert.equal(honours.domestic_cups, want.honours.domestic_cups);
    assert.equal(honours.european_trophies, want.honours.european);

    assert.deepEqual(managerBands(cashflow).map((b) => b.manager), want.manager_bands, "tenure bands");
  });

  test(`biggest sales match SQL: ${clubName}`, () => {
    const sold = transfers.filter((t) => t.direction === "Sold" && t.fee_eur !== null)
      .sort((a, b) => b.fee_eur - a.fee_eur || a.player_name.localeCompare(b.player_name))
      .slice(0, 3);
    assert.deepEqual(sold.map((t) => [t.player_name, t.fee_eur]), want.top_sales);
  });
}
