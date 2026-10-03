"""SQLAlchemy ORM models for the Kalshi tennis signal platform."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    positions: Mapped[list["ManualPosition"]] = relationship(back_populates="user")
    risk_settings: Mapped[Optional["RiskSettings"]] = relationship(back_populates="user", uselist=False)


class TennisMatch(Base):
    __tablename__ = "tennis_matches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    tournament: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    player_a: Mapped[str] = mapped_column(String(255))
    player_b: Mapped[str] = mapped_column(String(255))
    surface: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="SCHEDULED")
    observation_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    observation_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    signals_emitted: Mapped[int] = mapped_column(Integer, default=0)
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    analysis_mode: Mapped[str] = mapped_column(String(32), default="OBSERVING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    markets: Mapped[list["KalshiMarket"]] = relationship(back_populates="match")
    score_updates: Mapped[list["TennisScoreUpdate"]] = relationship(back_populates="match")
    signals: Mapped[list["Signal"]] = relationship(back_populates="match")


class KalshiEvent(Base):
    __tablename__ = "kalshi_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_ticker: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    series_ticker: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    markets: Mapped[list["KalshiMarket"]] = relationship(back_populates="event")


class KalshiMarket(Base):
    __tablename__ = "kalshi_markets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ticker: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("kalshi_events.id"), nullable=True)
    match_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("tennis_matches.id"), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    subtitle: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    yes_sub_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    no_sub_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    close_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    yes_bid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    yes_ask: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    open_interest: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_update_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    event: Mapped[Optional["KalshiEvent"]] = relationship(back_populates="markets")
    match: Mapped[Optional["TennisMatch"]] = relationship(back_populates="markets")
    ticks: Mapped[list["MarketTick"]] = relationship(back_populates="market")
    orderbook_updates: Mapped[list["OrderbookUpdate"]] = relationship(back_populates="market")
    signals: Mapped[list["Signal"]] = relationship(back_populates="market")


class MarketTick(Base):
    __tablename__ = "market_ticks"
    __table_args__ = (Index("ix_market_ticks_market_ts", "market_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String(36), ForeignKey("kalshi_markets.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    yes_bid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    yes_ask: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    no_bid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    no_ask: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_trade: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    spread: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    microprice: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    market: Mapped["KalshiMarket"] = relationship(back_populates="ticks")


class OrderbookUpdate(Base):
    __tablename__ = "orderbook_updates"
    __table_args__ = (Index("ix_ob_market_ts", "market_id", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String(36), ForeignKey("kalshi_markets.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    yes_bids_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    yes_asks_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    imbalance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    depth_yes: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    depth_no: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    market: Mapped["KalshiMarket"] = relationship(back_populates="orderbook_updates")


class TennisScoreUpdate(Base):
    __tablename__ = "tennis_score_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(String(36), ForeignKey("tennis_matches.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    server: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    point_score: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    game_score: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    set_score: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    match_score: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    aces_a: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    aces_b: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    double_faults_a: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    double_faults_b: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    break_points_a: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    break_points_b: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    match: Mapped["TennisMatch"] = relationship(back_populates="score_updates")


class ModelPrediction(Base):
    __tablename__ = "model_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(String(36), ForeignKey("tennis_matches.id"), index=True)
    market_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("kalshi_markets.id"), nullable=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    player: Mapped[str] = mapped_column(String(255))
    model_win_probability: Mapped[float] = mapped_column(Float)
    executable_market_probability: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimated_net_edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="TennisProbabilityModel")


class SignalScore(Base):
    __tablename__ = "signal_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    market_id: Mapped[str] = mapped_column(String(36), ForeignKey("kalshi_markets.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confidence: Mapped[float] = mapped_column(Float)
    net_edge: Mapped[float] = mapped_column(Float)
    model_component: Mapped[float] = mapped_column(Float, default=0)
    orderbook_component: Mapped[float] = mapped_column(Float, default=0)
    momentum_component: Mapped[float] = mapped_column(Float, default=0)
    trade_flow_component: Mapped[float] = mapped_column(Float, default=0)
    liquidity_component: Mapped[float] = mapped_column(Float, default=0)
    spread_component: Mapped[float] = mapped_column(Float, default=0)
    trend_component: Mapped[float] = mapped_column(Float, default=0)
    volatility_component: Mapped[float] = mapped_column(Float, default=0)
    confirmation_count: Mapped[int] = mapped_column(Integer, default=0)


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (Index("ix_signals_match_status", "match_id", "status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. SIG-ALC-SIN-28491
    match_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("tennis_matches.id"), nullable=True)
    market_id: Mapped[str] = mapped_column(String(36), ForeignKey("kalshi_markets.id"), index=True)
    signal_type: Mapped[str] = mapped_column(String(64))
    player: Mapped[str] = mapped_column(String(255))
    direction: Mapped[str] = mapped_column(String(8))  # YES / NO
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    original_ttl_ms: Mapped[int] = mapped_column(Integer)
    actual_lifetime_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    signal_version: Mapped[int] = mapped_column(Integer, default=1)
    market_price: Mapped[float] = mapped_column(Float)  # cents 0-100
    target_entry_price: Mapped[float] = mapped_column(Float)
    maximum_entry_price: Mapped[float] = mapped_column(Float)
    model_probability: Mapped[float] = mapped_column(Float)
    net_edge: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    expiration_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    expiration_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    creation_price: Mapped[float] = mapped_column(Float)
    expiration_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    urgency_hint: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    match: Mapped[Optional["TennisMatch"]] = relationship(back_populates="signals")
    market: Mapped["KalshiMarket"] = relationship(back_populates="signals")
    lifecycle_events: Mapped[list["SignalLifecycleEvent"]] = relationship(back_populates="signal")
    results: Mapped[list["SignalResult"]] = relationship(back_populates="signal")


class SignalLifecycleEvent(Base):
    __tablename__ = "signal_lifecycle_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(64), ForeignKey("signals.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    event_type: Mapped[str] = mapped_column(String(64))  # created | updated | cancelled | expired
    market_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_edge: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    signal: Mapped["Signal"] = relationship(back_populates="lifecycle_events")


class SignalResult(Base):
    __tablename__ = "signal_results"
    __table_args__ = (UniqueConstraint("signal_id", "horizon_seconds", name="uq_signal_horizon"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(64), ForeignKey("signals.id"), index=True)
    horizon_seconds: Mapped[int] = mapped_column(Integer)  # 1,5,10,30,60
    entry_price: Mapped[float] = mapped_column(Float)
    future_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_favorable_movement: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_adverse_movement: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    signal: Mapped["Signal"] = relationship(back_populates="results")


class ManualPosition(Base):
    __tablename__ = "manual_positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    market_id: Mapped[str] = mapped_column(String(36), ForeignKey("kalshi_markets.id"), index=True)
    match_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("tennis_matches.id"), nullable=True)
    signal_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("signals.id"), nullable=True)
    direction: Mapped[str] = mapped_column(String(8))
    player: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="OPEN")
    entry_price: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    peak_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    current_exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    drawdown_from_peak: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    position_state: Mapped[str] = mapped_column(String(32), default="HOLD")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship(back_populates="positions")
    entries: Mapped[list["ManualEntry"]] = relationship(back_populates="position")
    exits: Mapped[list["ManualExit"]] = relationship(back_populates="position")


class ManualEntry(Base):
    __tablename__ = "manual_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    position_id: Mapped[str] = mapped_column(String(36), ForeignKey("manual_positions.id"), index=True)
    entry_price: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    position: Mapped["ManualPosition"] = relationship(back_populates="entries")


class ManualExit(Base):
    __tablename__ = "manual_exits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    position_id: Mapped[str] = mapped_column(String(36), ForeignKey("manual_positions.id"), index=True)
    exit_price: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    position: Mapped["ManualPosition"] = relationship(back_populates="exits")


class RiskSettings(Base):
    __tablename__ = "risk_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), unique=True)
    max_position_size: Mapped[float] = mapped_column(Float, default=100.0)
    max_open_positions: Mapped[int] = mapped_column(Integer, default=3)
    max_signals_per_match: Mapped[int] = mapped_column(Integer, default=3)
    alerts_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User"] = relationship(back_populates="risk_settings")


class StrategyConfig(Base):
    __tablename__ = "strategy_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    config_json: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO")
    category: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    detail_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
