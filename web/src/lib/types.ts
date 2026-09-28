/**
 * The shapes the export actually publishes. Only fields the pages read are exported (see
 * scripts/80_export_web_data.py and the PUBLISHED list in scripts/81_check_web_export.py), so this
 * interface is the whole of a page-1 row, not a subset of a wider table.
 */
export interface ClubSeason {
  club_key: number;
  club_name: string;
  league: string;
  season: string;
  season_end_year: number;
  gross_spend_eur: number;
  gross_sales_eur: number;
  net_spend_eur: number;
  matches: number;
  points: number;
  efficiency_index: number;
  efficiency_score_0_100: number;
  is_provisional: boolean;
}

/** One club, aggregated over the seasons currently selected. Points per match is computed here,
 *  as summed points over summed matches, which is not the mean of the per-season rates. */
export interface ClubAggregate {
  club_key: number;
  club_name: string;
  league: string;
  seasons: number;
  spend: number;
  sales: number;
  net: number;
  points: number;
  matches: number;
  points_per_match: number;
  efficiency_index: number;
  efficiency_score: number;
  has_provisional: boolean;
}
