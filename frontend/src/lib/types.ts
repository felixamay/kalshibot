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
  pattern_reasons?: string[];
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

export interface BlockReasoning {
  block_id: string; decision: string; reason: string; patterns: string[];
  pattern_confidence: number; entry_score: number; confirmation_count: number;
  summary: string; pattern_health?: number | null;
  players?: Record<string, { service_strength: number | null; return_pressure: number | null; momentum: string }>;
  tennis_momentum?: number | null; market_momentum?: number | null; divergence?: number | null; data_quality?: string;
}
export interface MatchCard {
  points?: {
    available: boolean; note: string; last_seq: number; source_match_id?: string | null;
    history: TennisPoint[];
    analysis: { last_two: TennisPoint[]; pattern: string; leader: string | null; ready: boolean;
                has_gap: boolean; revised: boolean; tracked_points: number; summary: string };
  };
  match_status?: string;
  service_games_analyzed?: number;
  ai_pattern_analysis?: {
    status: string; message?: string; model?: string;
    output?: { pattern_name: string; favored_side: string; pattern_confidence: number;
      pattern_stage: string; recommendation: string; reasons: string[]; risks: string[] };
  };
  hybrid_decision?: {
    quantitative_engine: string; final_decision: string; score: number; blockers: string[];
    signal?: {
      final: "YES" | "WATCH" | "WAIT" | "NO" | "DO NOT CHASE" | string;
      player_name?: string; pattern?: string; confidence?: number | null; current_cents?: number;
      alert_key?: string; message?: string | null; play_sound?: boolean;
      gpt_visual?: { pattern?: string; favors?: string; confidence?: number | null; stage?: string | null };
      kalshi_live?: { bias?: string; bid_pressure_pct?: number; liquidity?: string };
      gpt_snapshot_time?: number | null; gpt_response_time?: number | null;
      kalshi_current_time?: number | null; kalshi_price_at_snapshot?: number | null; kalshi_current_price?: number | null;
    };
  };
  serve_block_progress?: number;
  service_game_data_available?: boolean;
  service_game_data_note?: string;
  latest_reasoning?: BlockReasoning | null;
  reasoning_history?: BlockReasoning[];
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
  live_evidence_expires_at_ms?: number | null;
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

export interface TennisPoint {
  seq: number; set: number | null; game: number | null; number: number | null;
  winner: string | null; next_server: string | null; score: string | null; tiebreak: boolean;
  ts?: string | null; serve?: number | null; outcome?: string | null;
}

export interface DashboardPayload {
  discovery_health?: {
    events_checked?: number; markets_checked?: number; tennis_markets_found?: number; live_matches_found?: number;
    last_refresh_ms?: number; complete?: boolean; errors?: string[];
    kalshi_live_matches_found?: number; ws_markets_subscribed?: number;
    gpt_browser_connected?: "YES" | "NO" | string; gpt_viewing?: string;
    service_games_counted?: string; last_gpt_analysis_ms?: number | null;
    last_pattern_result?: string; no_signal_reason?: string;
  };
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
