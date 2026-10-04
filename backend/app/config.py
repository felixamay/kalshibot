"""Application configuration — all strategy and signal parameters are configurable."""

from functools import lru_cache
from typing import Optional

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Backend-only pattern analyst; empty key keeps deterministic operation.
    openai_api_key: str = Field(default="", repr=False)
    openai_pattern_model: str = Field(default="gpt-6-luna", validation_alias=AliasChoices("OPENAI_PATTERN_MODEL", "DEFAULT_PATTERN_MODEL", "openai_pattern_model"))
    openai_deep_model: str = Field(default="gpt-6.1-sol", validation_alias=AliasChoices("OPENAI_DEEP_MODEL", "DEEP_ANALYSIS_MODEL", "openai_deep_model"))
    openai_request_timeout_seconds: float = Field(default=8, gt=0, le=30)
    openai_max_advice_age_seconds: float = Field(default=60, gt=0)
    openai_stale_price_move: float = Field(default=0.025, gt=0, le=1)
    openai_disagreement_score: float = Field(default=20, gt=0)
    openai_divergence_threshold: float = Field(default=40, gt=0)
    hybrid_weights: list[float] = Field(default=[0.50, 0.30, 0.20], min_length=3, max_length=3)

    @field_validator("hybrid_weights")
    @classmethod
    def validate_hybrid_weights(cls, value):
        import math
        if any(not math.isfinite(v) or v < 0 for v in value) or sum(value) <= 0:
            raise ValueError("Hybrid weights must be finite, nonnegative and sum above zero")
        return value

    # Application
    app_name: str = "Kalshi Tennis Signal Analyst"
    app_env: str = "development"
    debug: bool = False
    secret_key: str = "change-me-in-production-use-openssl-rand-hex-32"
    access_token_expire_minutes: int = 60 * 24 * 7
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Database
    database_url: str = "postgresql+asyncpg://kalshi:kalshi@localhost:5432/kalshi_signals"
    database_url_sync: str = "postgresql://kalshi:kalshi@localhost:5432/kalshi_signals"

    # Redis (optional)
    redis_url: Optional[str] = "redis://localhost:6379/0"
    redis_enabled: bool = False

    # Kalshi Production API
    kalshi_api_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    kalshi_ws_url: str = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
    kalshi_api_key_id: str = ""
    kalshi_private_key_path: str = ""
    kalshi_private_key_pem: str = ""
    # Demo/fallback for unread markets discovery without auth (public endpoints)
    kalshi_use_auth: bool = True

    # Tennis data provider
    tennis_provider: str = "livetennis"  # livetennis | espn | none | sportradar | api_tennis | custom
    tennis_api_key: str = ""
    tennis_api_base_url: str = ""
    tennis_poll_interval_seconds: float = 5.0

    # Observation & signal strategy
    # Confidence is signal confidence, not the player's win probability.
    # Legacy field retained for configuration compatibility; study runs after each game win.
    initial_observation_seconds: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "INITIAL_OBSERVATION_SECONDS",
            "INITIAL_STUDY_SECONDS",
        ),
    )
    watch_confidence: float = 72.0
    min_bet_confidence: float = 80.0
    strong_bet_confidence: float = 90.0
    # Base / "normal" minimum net edge. DynamicEdgeThreshold moves this.
    min_net_edge: float = 0.02
    excellent_market_min_edge: float = 0.015
    medium_market_min_edge: float = 0.03
    poor_market_min_edge: float = 0.04
    strong_net_edge: float = 0.045
    entry_confirmation_count: int = 3
    max_signals_per_match: int = 4
    reentry_cooldown_seconds: int = 60
    # Entry timing is a 0–100 score against this match's own baseline.
    watch_entry_score: float = 70.0
    entry_signal_score: float = 78.0
    strong_entry_score: float = 88.0
    watch_slip_score: float = 45.0
    slipping_score: float = 65.0
    stop_exit_score: float = 75.0
    exit_confirmation_count: int = 3
    # Profit protection tightens after a gain measured in baseline-volatility units.
    profit_protect_moderate_vols: float = 2.0
    profit_protect_strong_vols: float = 4.0
    profit_protect_moderate_multiplier: float = 1.25
    profit_protect_strong_multiplier: float = 1.6
    emergency_move_vols: float = 3.5
    baseline_volatility_floor: float = 0.75

    # Signal TTL
    min_signal_ttl_seconds: float = 2.0
    default_signal_ttl_seconds: float = 8.0
    max_signal_ttl_seconds: float = 30.0
    signal_timer_refresh_ms: int = 100

    # Market quality thresholds
    max_data_age_ms: int = 5000
    max_spread_cents: float = 6.0
    spread_excellent_cents: float = 1.0
    spread_good_cents: float = 2.0
    spread_acceptable_cents: float = 3.0
    # Absolute depth bands. LiquidityQuality also compares depth to recent activity.
    liquidity_high_contracts: float = 2000.0
    liquidity_good_contracts: float = 400.0
    liquidity_medium_contracts: float = 80.0
    liquidity_low_contracts: float = 20.0
    min_liquidity_contracts: int = 20
    max_entry_slippage_cents: float = 2.0
    # Kalshi-like fee: rate * p * (1-p). Plus slippage and safety margin.
    estimated_fee_rate: float = 0.07
    safety_margin: float = 0.005
    expected_slippage: float = 0.003
    extreme_volatility: float = 3.5
    # Subtracted from net edge when the model itself is weakly informed.
    uncertainty_penalty_low: float = 0.0
    uncertainty_penalty_medium: float = 0.005
    uncertainty_penalty_high: float = 0.01
    uncertainty_confidence_haircut: float = 0.10

    # Confidence weights (must sum ~1.0). Not claimed to be optimal.
    weight_model_edge: float = 0.35
    weight_orderbook: float = 0.20
    weight_momentum: float = 0.15
    weight_trade_flow: float = 0.10
    weight_liquidity: float = 0.08
    weight_spread: float = 0.05
    weight_trend: float = 0.04
    weight_volatility_risk: float = 0.03

    # Entry-score weights. Not claimed to be optimal.
    weight_entry_strength: float = 0.25
    weight_entry_price: float = 0.20
    weight_entry_book: float = 0.15
    weight_entry_momentum: float = 0.15
    weight_entry_flow: float = 0.10
    weight_entry_liquidity: float = 0.05
    weight_entry_spread: float = 0.05
    weight_entry_reversal: float = 0.05

    # Pattern engine. A detected pattern is not automatically a bet.
    serve_entry_weights: list[float] = [0.25, 0.20, 0.15, 0.15, 0.10, 0.05, 0.05, 0.03, 0.02]
    serve_block_weights: list[float] = [0.40, 0.25, 0.20, 0.15]
    pattern_engine_enabled: bool = True
    min_pattern_occurrences: int = 2
    pattern_watch_score: float = 65.0
    pattern_entry_score: float = 75.0
    strong_pattern_entry_score: float = 87.0
    min_pattern_confidence: float = 70.0
    strong_pattern_confidence: float = 85.0
    pattern_confirmation_count: int = 3
    pattern_late_stage_percent: float = 75.0
    pattern_completed_percent: float = 95.0
    pattern_weaken_health: float = 60.0
    pattern_risk_health: float = 40.0
    pattern_broken_health: float = 25.0
    # How often a live book is turned into patterns. Ticks are still recorded
    # between passes. Zero means every update, which can block login.
    pattern_eval_interval_ms: int = 500
    min_pattern_sample: int = 5
    # Current match outweighs older tennis markets. Not claimed to be optimal.
    weight_memory_current: float = 1.0
    weight_memory_tournament: float = 0.55
    weight_memory_historical: float = 0.30
    # Pattern confidence weights. Not claimed to be optimal.
    weight_pattern_similarity: float = 0.25
    weight_pattern_repetition: float = 0.20
    weight_pattern_historical: float = 0.15
    weight_pattern_book: float = 0.15
    weight_pattern_momentum: float = 0.10
    weight_pattern_flow: float = 0.05
    weight_pattern_liquidity: float = 0.05
    weight_pattern_spread: float = 0.03
    weight_pattern_reversal: float = 0.02
    # Pattern entry-score weights. Not claimed to be optimal.
    weight_pattern_entry_confidence: float = 0.30
    weight_pattern_entry_history: float = 0.15
    weight_pattern_entry_price: float = 0.15
    weight_pattern_entry_book: float = 0.15
    weight_pattern_entry_momentum: float = 0.10
    weight_pattern_entry_flow: float = 0.05
    weight_pattern_entry_liquidity: float = 0.05
    weight_pattern_entry_spread: float = 0.03
    weight_pattern_entry_reversal: float = 0.02

    # Slip-score weights. Not claimed to be optimal.
    weight_slip_price: float = 0.25
    weight_slip_book: float = 0.20
    weight_slip_momentum: float = 0.20
    weight_slip_flow: float = 0.10
    weight_slip_liquidity: float = 0.10
    weight_slip_model: float = 0.10
    weight_slip_spread: float = 0.05

    # Rolling windows (ms)
    window_sizes_ms: str = "250,500,1000,2000,5000,10000,30000,60000,180000,300000"

    # Engine loops
    market_poll_interval_seconds: float = 2.0
    signal_eval_interval_ms: int = 100
    clock_sync_interval_seconds: float = 5.0
    heartbeat_interval_seconds: float = 5.0

    # Tennis series filters on Kalshi
    tennis_series_keywords: str = "TENNIS,ATP,WTA,tennis"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def window_sizes(self) -> list[int]:
        return [int(x.strip()) for x in self.window_sizes_ms.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
