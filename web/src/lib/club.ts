/**
 * Page 2 arithmetic: everything the club page shows, as pure functions over one club's shard.
 *
 * Tested against SQL in web/test/club.test.mjs, with expectations from scripts/82_web_expectations.py.
 */
export interface SquadRow {
  player_key: number; season: string; season_end_year: number; is_latest_season_for_club: boolean;
  clubs_in_season: number; is_primary_club_for_season: boolean; player_name: string;
  nationality_code: string | null; position_group: string; position_detail: string; position_short: string;
  age: number | null; matches_played: number; starts: number; minutes: number;
  quality_percentile: number | null; market_value_eur: number | null;
}

export interface TransferRow {
  transfer_key: number; season: string; season_end_year: number; transfer_date: string;
  date_is_estimated: boolean; player_name: string; direction: string; direction_filter: string;
  transfer_category: string; fee_status: string; fee_eur: number | null; is_fee_disclosed: boolean;
  other_club_name: string; other_club_in_scope: boolean; is_big_five_season: boolean;
}

export interface CashflowRow {
  season: string; season_end_year: number; fees_paid_eur: number; fees_received_eur: number;
  net_spend_eur: number; net_transfer_balance_eur: number; undisclosed_signings: number;
  undisclosed_sales: number; efficiency_index: number; efficiency_score_0_100: number;
  is_provisional: boolean; season_status: string; manager_name: string; manager_is_caretaker: boolean;
  managers_in_season: string; managers_count: number; manager_band_seq: number;
}

export interface PillarRow {
  season: string; season_end_year: number; efficiency_index: number; efficiency_score_0_100: number;
  recruitment_z: number | null; trading_z: number; value_growth_z: number; sporting_z: number;
  is_recruitment_scored: boolean; is_provisional: boolean;
}

/** Money in and out over the selected seasons, plus the score to show beside it. */
export function financials(cashflow: CashflowRow[]) {
  const paid = cashflow.reduce((t, r) => t + r.fees_paid_eur, 0);
  const received = cashflow.reduce((t, r) => t + r.fees_received_eur, 0);
  const score = cashflow.length
    ? cashflow.reduce((t, r) => t + r.efficiency_score_0_100, 0) / cashflow.length : 0;
  const index = cashflow.length
    ? cashflow.reduce((t, r) => t + r.efficiency_index, 0) / cashflow.length : 0;
  return { paid, received, net: paid - received, score, index,
           provisional: cashflow.some((r) => r.is_provisional) };
}

/** The four pillars, averaged over the selected seasons. Recruitment skips seasons below the spend floor. */
export function pillarAverages(pillars: PillarRow[]) {
  const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
  const scored = pillars.filter((p) => p.is_recruitment_scored && p.recruitment_z !== null);
  return [
    { name: "Recruitment ROI", value: mean(scored.map((p) => p.recruitment_z as number)),
      note: scored.length < pillars.length ? `${pillars.length - scored.length} season(s) below the spend floor` : "" },
    { name: "Trading Profit", value: mean(pillars.map((p) => p.trading_z)), note: "" },
    { name: "Squad Value Growth", value: mean(pillars.map((p) => p.value_growth_z)), note: "" },
    { name: "Sporting Return", value: mean(pillars.map((p) => p.sporting_z)), note: "" },
  ];
}

/**
 * The spending breakdown, honestly: permanent and loan fees carry euros, free signings are a count,
 * and undisclosed moves are counted but never valued.
 */
export function spendingBreakdown(transfers: TransferRow[]) {
  const incoming = transfers.filter((t) => t.direction === "Bought" || t.direction === "Loan in");
  const sum = (rows: TransferRow[]) => rows.reduce((t, r) => t + (r.fee_eur ?? 0), 0);
  return {
    permanent: sum(incoming.filter((t) => t.fee_status === "Fee")),
    loanFees: sum(incoming.filter((t) => t.fee_status === "Loan fee")),
    freeCount: incoming.filter((t) => t.fee_status === "Free").length,
    undisclosedCount: incoming.filter((t) => t.fee_status === "Undisclosed").length,
    loanNoFeeCount: incoming.filter((t) => t.fee_status === "Loan (no fee)").length,
  };
}

/** Contiguous seasons under one manager, for the bands under the cash-flow chart. */
export function managerBands(cashflow: CashflowRow[]) {
  const bands: { manager: string; from: number; to: number; caretaker: boolean }[] = [];
  for (const row of cashflow) {
    const last = bands[bands.length - 1];
    if (last && last.manager === row.manager_name && row.season_end_year === last.to + 1) last.to = row.season_end_year;
    else bands.push({ manager: row.manager_name, from: row.season_end_year, to: row.season_end_year,
                      caretaker: row.manager_is_caretaker });
  }
  return bands;
}

export function squadForSeason(squad: SquadRow[], seasonEndYear: number) {
  return squad.filter((r) => r.season_end_year === seasonEndYear)
              .sort((a, b) => (b.market_value_eur ?? -1) - (a.market_value_eur ?? -1));
}

export function squadValue(rows: SquadRow[]) {
  return rows.reduce((t, r) => t + (r.market_value_eur ?? 0), 0);
}
