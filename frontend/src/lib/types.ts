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
  pattern_type?: string;
  pattern_name?: string;
  pattern_similarity?: number | null;
  pattern_confidence?: number | null;
  pattern_entry_score?: number | null;
  entry_zone_low?: number | null;
  entry_zone_high?: number | null;
  pattern_progress?: number | null;
  pattern_stage?: string;
  bet_instruction?: string;
  market_instruction?: string;
  lifecycle?: Array<Record<string, unknown>>;
}

export interface PatternView {
  decision?: string;
  pattern_type?: string;
  pattern_name?: string;
  pattern_id?: string;
  player?: string;
  player_side?: string;
  similarity?: number;
  confidence?: number;
  entry_score?: number;
  confirmation_count?: number;
  confirmation_needed?: number;
  repetition_label?: string;
  occurrences?: number;
  successes?: number;
  failures?: number;
  success_rate?: number | null;
  low_sample_size?: boolean;
  stage?: string;
  progress?: number;
  expected_move?: number;
  current_move?: number;
  entry_zone_low?: number;
  entry_zone_high?: number;
  maximum_entry_price?: number;
  current_price?: number;
  explanation?: string;
  reasons?: string[];
  blockers?: string[];
  orderbook_evidence?: string[];
  support_zones?: Array<{ low: number; high: number; touches: number }>;
  resistance_zones?: Array<{ low: number; high: number; touches: number }>;
  timeline?: Array<{ t_ms: number; kind: string; price?: number }>;
  prior_examples?: Array<Record<string, unknown>>;
  cluster_name?: string;
  typical_pullback?: number;
  typical_recovery?: number;
  success_note?: string;
}

export interface MarketRead {
  raw_edge?: number;
  estimated_fees?: number;
  net_edge?: number;
  model_uncertainty?: string;
  uncertainty_penalty?: number;
  uncertainty_adjusted_edge?: number;
  liquidity_quality?: string;
  spread_quality?: string;
  market_quality_score?: number;
  dynamic_min_edge?: number | null;
  mispricing_score?: number;
  opportunity_score?: number;
  max_entry_price_cents?: number;
  confirmation_needed?: number;
  decision?: string;
  reasons?: string[];
  blockers?: string[];
  explanation?: string;
  close_to_signal?: boolean;
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
  raw_edge?: number | null;
  confidence?: number | null;
  hold_reason?: string | null;
  read?: MarketRead | null;
  signals_today?: number;
  max_signals_per_match?: number;
  analysis_mode: string;
  display_state: string;
  is_live?: boolean;
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
  entry?: { entry_score?: number; decision?: string } | null;
  pattern?: PatternView | null;
  pattern_health?: { health?: number; decision?: string; explanation?: string; reasons?: string[] } | null;
  phase_events?: Array<{ t_ms: number; state: string }>;
  position?: {
    entry_price?: number;
    current_price?: number;
    peak_price?: number;
    pattern_type?: string;
    pattern_health?: number | null;
    status?: string;
  } | null;
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
