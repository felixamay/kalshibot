export type ConnectionStatus = "CONNECTED" | "RECONNECTING" | "DISCONNECTED";

export type UrgencyStage = "NORMAL" | "CAUTION" | "FINAL" | "EXPIRED";

export interface LiveSignal {
  signal_id: string;
  signal_version: number;
  match_id?: string;
  market_id: string;
  market_ticker: string;
  signal_type: string;
  raw_signal_type?: string;
  player: string;
  direction: string;
  created_at_ms: number;
  expires_at_ms: number;
  original_ttl_ms: number;
  remaining_ms: number;
  remaining_seconds: number;
  progress: number;
  urgency: UrgencyStage;
  market_price: number;
  target_entry_price: number;
  maximum_entry_price: number;
  model_probability: number;
  net_edge: number;
  confidence: number;
  status: string;
  actionable: boolean;
  expiration_reason?: string | null;
  expiration_message?: string | null;
  expiration_price?: number | null;
  display_label: string;
  display_sublabel?: string | null;
  tournament?: string | null;
  analysis_mode?: string;
  server_time_ms: number;
  lifecycle?: Array<Record<string, unknown>>;
}

export interface MatchCard {
  match_id: string;
  player_a: string;
  player_b: string;
  tournament?: string | null;
  market_ticker: string;
  market_status: string;
  kalshi_probability?: number | null;
  yes_bid?: number | null;
  yes_ask?: number | null;
  spread?: number | null;
  liquidity?: number | null;
  momentum?: number | null;
  orderbook_pressure?: number | null;
  model_probability?: number | null;
  estimated_edge?: number | null;
  analysis_mode: string;
  display_state: string;
  observation_ends_ms?: number;
  observation_remaining_ms: number;
  observation_remaining_display: string;
  signals_emitted: number;
  cooldown_until_ms?: number;
  cooldown_remaining_ms: number;
  quote_updated_at_ms?: number | null;
  data_age_ms?: number | null;
  tennis?: Record<string, unknown>;
  active_signal?: LiveSignal | null;
}

export interface DashboardPayload {
  server_time_ms: number;
  connection_status: ConnectionStatus;
  live_match_count: number;
  matches: MatchCard[];
  actionable_signals: LiveSignal[];
  signal_history: LiveSignal[];
  no_live_markets: boolean;
  message?: string | null;
  max_data_age_ms?: number;
}
