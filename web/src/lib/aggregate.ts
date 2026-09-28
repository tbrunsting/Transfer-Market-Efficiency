/**
 * The cross-filtering layer: pure functions over the 684 club-seasons, no charts, no DOM.
 *
 * Every number the dashboard shows comes from here, so it can be tested against the warehouse
 * (see test/aggregates.test.mjs, whose expectations are generated from SQL by
 * scripts/82_web_expectations.py).
 *
 * Aggregation across several selected seasons, matching the Power BI behaviour:
 *   spend, sales, net   summed
 *   points per match    total points / total matches, not a mean of means
 *   efficiency          the mean of the club's season scores
 *   medians             taken across the clubs currently displayed, so the crosshairs move with filters
 */
import type { ClubAggregate, ClubSeason } from "./types";

export interface Filters { seasons: Set<number>; leagues: Set<string> }

export function filterRows(rows: ClubSeason[], f: Filters): ClubSeason[] {
  return rows.filter((r) => f.seasons.has(r.season_end_year) && f.leagues.has(r.league));
}

export function aggregateByClub(rows: ClubSeason[]): ClubAggregate[] {
  const byClub = new Map<number, ClubAggregate>();
  for (const r of rows) {
    let c = byClub.get(r.club_key);
    if (!c) {
      c = { club_key: r.club_key, club_name: r.club_name, league: r.league, seasons: 0, spend: 0, sales: 0,
            net: 0, points: 0, matches: 0, points_per_match: 0, efficiency_index: 0, efficiency_score: 0,
            has_provisional: false };
      byClub.set(r.club_key, c);
    }
    c.seasons += 1;
    c.spend += r.gross_spend_eur;
    c.sales += r.gross_sales_eur;
    c.net += r.net_spend_eur;
    c.points += r.points;
    c.matches += r.matches;
    c.efficiency_index += r.efficiency_index;
    c.efficiency_score += r.efficiency_score_0_100;
    c.has_provisional ||= r.is_provisional;
    c.league = r.league;                       // the club's league in the latest row seen
  }
  for (const c of byClub.values()) {
    c.points_per_match = c.matches ? c.points / c.matches : 0;
    c.efficiency_index /= c.seasons;
    c.efficiency_score /= c.seasons;
  }
  return [...byClub.values()].sort((a, b) => b.efficiency_index - a.efficiency_index);
}

export function median(values: number[]): number {
  if (!values.length) return 0;
  const s = [...values].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

export interface Kpis {
  clubs: number; totalSpend: number; medianScore: number;
  best: ClubAggregate | null; provisionalClubs: number; unplotted: number;
}

export function kpis(clubs: ClubAggregate[]): Kpis {
  return {
    clubs: clubs.length,
    totalSpend: clubs.reduce((t, c) => t + c.spend, 0),
    medianScore: median(clubs.map((c) => c.efficiency_score)),
    best: clubs.length ? clubs.reduce((a, b) => (b.efficiency_index > a.efficiency_index ? b : a)) : null,
    provisionalClubs: clubs.filter((c) => c.has_provisional).length,
    unplotted: clubs.filter((c) => c.spend <= 0).length,   // a log axis cannot show zero spend
  };
}

export function spendByLeague(rows: ClubSeason[]): { league: string; spend: number }[] {
  const byLeague = new Map<string, number>();
  for (const r of rows) byLeague.set(r.league, (byLeague.get(r.league) ?? 0) + r.gross_spend_eur);
  return [...byLeague].map(([league, spend]) => ({ league, spend })).sort((a, b) => b.spend - a.spend);
}

/** Which quadrant a club sits in, relative to the medians of the current selection. */
export type Quadrant = "smart" | "burned" | "heavy" | "frugal";
export function quadrantOf(c: ClubAggregate, medSpend: number, medPpm: number): Quadrant {
  const highReturn = c.points_per_match >= medPpm;
  const highSpend = c.spend >= medSpend;
  if (highReturn && !highSpend) return "smart";
  if (!highReturn && highSpend) return "burned";
  return highReturn ? "heavy" : "frugal";
}
