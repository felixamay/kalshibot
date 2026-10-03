"""Application configuration — all strategy and signal parameters are configurable."""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
    tennis_provider: str = "none"  # none | sportradar | api_tennis | custom
    tennis_api_key: str = ""
    tennis_api_base_url: str = ""
    tennis_poll_interval_seconds: float = 5.0

    # Observation & signal strategy
    initial_observation_seconds: int = 300
    min_bet_confidence: float = 85.0
    strong_bet_confidence: float = 92.0
    min_net_edge: float = 0.04
    entry_confirmation_count: int = 5
    max_signals_per_match: int = 3
    reentry_cooldown_seconds: int = 60

    # Signal TTL
    min_signal_ttl_seconds: float = 2.0
    default_signal_ttl_seconds: float = 8.0
    max_signal_ttl_seconds: float = 15.0
    signal_timer_refresh_ms: int = 100

    # Market quality thresholds
    max_data_age_ms: int = 5000
    max_spread_cents: float = 6.0
    min_liquidity_contracts: int = 50
    max_entry_slippage_cents: float = 2.0
    estimated_fee_rate: float = 0.07  # Kalshi fee approx on profit
    safety_margin: float = 0.015
    expected_slippage: float = 0.01

    # Confidence weights (must sum ~1.0)
    weight_model_edge: float = 0.30
    weight_orderbook: float = 0.20
    weight_momentum: float = 0.15
    weight_trade_flow: float = 0.10
    weight_liquidity: float = 0.10
    weight_spread: float = 0.05
    weight_trend: float = 0.05
    weight_volatility_risk: float = 0.05

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
