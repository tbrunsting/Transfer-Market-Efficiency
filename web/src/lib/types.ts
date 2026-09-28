export interface ClubSeason {
  club_key: number;
  club_name: string;
  club_country: string;
  league: string;
  league_code: string;
  season: string;
  season_end_year: number;
  gross_spend_eur: number;
  gross_sales_eur: number;
  net_spend_eur: number;
  undisclosed_signings: number;
  undisclosed_sales: number;
  squad_value_start_eur: number;
  matches: number;
  points: number;
  points_per_match: number;
  has_known_deduction: boolean;
  efficiency_index: number;
  efficiency_score_0_100: number;
  recruitment_z: number | null;
  trading_z: number;
  value_growth_z: number;
  sporting_z: number;
  is_recruitment_scored: boolean;
  is_provisional: boolean;
}

/** One club, aggregated over the seasons currently selected. */
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
