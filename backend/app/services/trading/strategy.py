"""Per-user Kalshi price strategy. Decisions use the executable book only."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

OPEN_STATUSES = {
    "ORDER SUBMITTED",
    "PARTIALLY FILLED",
    "POSITION OPEN",
    "PROFIT TARGET APPROACHING",
    "TRAILING STOP ACTIVE",
    "TAKE PROFIT TRIGGERED",
    "STOP LOSS TRIGGERED",
    "TRAILING EXIT TRIGGERED",
    "SELL DEVELOPING",
    "WATCH PROFIT",
    "SELL — LOCK PROFIT",
}

ALERT_KINDS = {
    "ENTRY CONDITION MET",
    "POSITION FILLED",
    "TAKE PROFIT TRIGGERED",
    "STOP LOSS TRIGGERED",
    "TRAILING EXIT TRIGGERED",
    "POSITION CLOSED",
    "BUY",
    "STRONG BUY",
    "SELL",
}

DEFAULTS: dict[str, Any] = {
    "auto_entry": False,
    "auto_exit": False,
    "paused": False,
    "emergency_stop": False,
    "bet_amount": 10.0,
    "entry_type": "YES",
    "entry_price": 50.0,
    "take_profit_percent": 8.0,
    "stop_loss_percent": 4.0,
    "trailing_stop_enabled": True,
    "trailing_activation_percent": 5.0,
    "trailing_stop_percent": 3.0,
    "max_trades_per_match": 2,
    "cooldown_seconds": 60,
    "max_position_per_match": 25.0,
    "max_daily_loss": 25.0,
    "max_daily_exposure": 50.0,
    "market_scope": "ALL_LIVE",
    "selected_tickers": [],
    "one_direction_per_market": True,
    "short_run_enabled": False,
    "watch_one_game": False,
    "watched_ticker": "",
    "min_buy_score": 75.0,
    "strong_buy_score": 85.0,
    "min_sell_score": 75.0,
    "min_liquidity_growth": 10.0,
    "max_spread": 4.0,
    "min_expected_profit_percent": 3.0,
    "pullback_min_percent": 4.0,
    "pullback_max_percent": 18.0,
    "max_entry_price": 65.0,
}


def default_settings() -> dict[str, Any]:
    return dict(DEFAULTS)


def executable_buy(side: str, yes_bid: Optional[float], yes_ask: Optional[float]) -> Optional[float]:
    """Price paid to buy this side. The midpoint is not used."""
    if side == "YES":
        return yes_ask if yes_ask and yes_ask > 0 else None
    if yes_bid and yes_bid > 0:
        return round(100.0 - yes_bid, 4)
    return None


def executable_sell(side: str, yes_bid: Optional[float], yes_ask: Optional[float]) -> Optional[float]:
    """Price received to sell this side. The midpoint is not used."""
    if side == "YES":
        return yes_bid if yes_bid and yes_bid > 0 else None
    if yes_ask and yes_ask > 0:
        return round(100.0 - yes_ask, 4)
    return None


def choose_entry_side(
    entry_type: str,
    yes_bid: Optional[float],
    yes_ask: Optional[float],
    entry_price: float,
) -> tuple[Optional[str], Optional[float]]:
    """Return the side whose executable buy is at or below the user's price."""
    wanted = (entry_type or "YES").upper()
    if wanted == "EITHER":
        options = []
        for side in ("YES", "NO"):
            price = executable_buy(side, yes_bid, yes_ask)
            if price is not None and price <= entry_price:
                options.append((side, price))
        if not options:
            return None, None
        return min(options, key=lambda item: item[1])
    price = executable_buy(wanted, yes_bid, yes_ask)
    if price is None or price > entry_price:
        return None, price
    return wanted, price


def take_profit_price(entry_price: float, percent: float) -> float:
    return entry_price * (1.0 + percent / 100.0)


def stop_loss_price(entry_price: float, percent: float) -> float:
    return entry_price * (1.0 - percent / 100.0)


def trailing_stop_price(peak_price: float, percent: float) -> float:
    return peak_price * (1.0 - percent / 100.0)


def profit_percent(entry_price: float, exit_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    return (exit_price - entry_price) / entry_price * 100.0


def profit_dollars(entry_price: float, exit_price: float, quantity: float) -> float:
    return (exit_price - entry_price) / 100.0 * quantity


def weighted_average_price(fills: list[tuple[float, float]]) -> float:
    quantity = sum(qty for _, qty in fills)
    if quantity <= 0:
        raise ValueError("A fill needs quantity")
    return sum(price * qty for price, qty in fills) / quantity


def contract_count(bet_dollars: float, price_cents: float) -> int:
    if price_cents <= 0 or bet_dollars <= 0:
        return 0
    return int(bet_dollars / (price_cents / 100.0))


def order_cost(price_cents: float, quantity: float) -> float:
    return price_cents / 100.0 * quantity


@dataclass
class RiskView:
    auto_entry: bool
    paused: bool
    emergency_stop: bool
    trading_connected: bool
    market_open: bool
    market_live: bool
    in_scope: bool
    data_age_ms: float
    max_data_age_ms: float
    depth: float
    min_liquidity: float
    open_notional: float
    order_cost: float
    max_position: float
    daily_loss: float
    max_daily_loss: float
    daily_exposure: float
    max_daily_exposure: float
    trades_this_match: int
    max_trades: int
    cooldown_until_ms: float
    now_ms: float
    opposite_open: bool


def risk_block_reason(view: RiskView) -> Optional[str]:
    if view.emergency_stop:
        return "Auto trading is stopped."
    if not view.auto_entry:
        return "AUTO ENTRY is off."
    if view.paused:
        return "Strategy is paused."
    if not view.trading_connected:
        return "Trading API is disconnected."
    if not view.market_live:
        return "Market is not a live tennis match."
    if not view.in_scope:
        return "Market is not in the selected list."
    if not view.market_open:
        return "Market is not open."
    if view.data_age_ms > view.max_data_age_ms:
        return "Market data is stale."
    if view.depth < view.min_liquidity:
        return "Executable liquidity is too thin."
    if view.order_cost <= 0:
        return "Bet amount is below one contract."
    if view.open_notional + view.order_cost > view.max_position + 1e-9:
        return "Max position per match would be exceeded."
    if view.daily_loss >= view.max_daily_loss - 1e-9:
        return "Max daily loss is reached."
    if view.daily_exposure + view.order_cost > view.max_daily_exposure + 1e-9:
        return "Max daily exposure would be exceeded."
    if view.trades_this_match >= view.max_trades:
        return "Max trades per match is reached."
    if view.now_ms < view.cooldown_until_ms:
        return "Cooldown after the last exit."
    if view.opposite_open:
        return "One direction per market. The other side is already open."
    return None


@dataclass
class PositionState:
    id: str
    user_id: str
    ticker: str
    label: str
    side: str
    status: str = "ORDER SUBMITTED"
    bet_amount: float = 0.0
    requested_price: float = 0.0
    entry_price: float = 0.0
    filled_qty: float = 0.0
    fees: float = 0.0
    peak_price: float = 0.0
    current_exit_price: float = 0.0
    trailing_active: bool = False
    rules: dict[str, Any] = field(default_factory=dict)
    order_id: Optional[str] = None
    exit_pending: bool = False
    opened_at_ms: float = 0.0
    closed_at_ms: Optional[float] = None
    close_price: Optional[float] = None
    realized_pnl: float = 0.0

    def public(self) -> dict[str, Any]:
        exit_price = self.current_exit_price
        entry = self.entry_price
        qty = self.filled_qty
        pnl_pct = profit_percent(entry, exit_price) if entry and qty else 0.0
        pnl_dollars = profit_dollars(entry, exit_price, qty) if entry and qty else 0.0
        rules = self.rules
        target = take_profit_price(entry, float(rules.get("take_profit_percent") or 0)) if entry else None
        stop = stop_loss_price(entry, float(rules.get("stop_loss_percent") or 0)) if entry else None
        trail = None
        if self.trailing_active and self.peak_price:
            trail = trailing_stop_price(self.peak_price, float(rules.get("trailing_stop_percent") or 0))
        return {
            "position_id": self.id,
            "user_id": self.user_id,
            "market_ticker": self.ticker,
            "player": self.label or self.ticker,
            "side": self.side,
            "status": self.status,
            "bet_amount": round(self.bet_amount, 2),
            "requested_price": round(self.requested_price, 2),
            "entry_price": round(entry, 4) if entry else None,
            "current_exit_price": round(exit_price, 4) if exit_price else None,
            "pnl_percent": round(pnl_pct, 2),
            "pnl_dollars": round(pnl_dollars, 2),
            "peak_price": round(self.peak_price, 4) if self.peak_price else None,
            "take_profit_target": round(target, 4) if target else None,
            "stop_loss_level": round(stop, 4) if stop else None,
            "trailing_stop_level": round(trail, 4) if trail else None,
            "trailing_active": self.trailing_active,
            "filled_qty": self.filled_qty,
            "fees": round(self.fees, 4),
            "order_id": self.order_id,
        }


@dataclass
class Quote:
    ticker: str
    yes_bid: Optional[float]
    yes_ask: Optional[float]
    depth: float
    status: str
    data_age_ms: float
    live: bool
    label: str = ""
    now_ms: float = 0.0
    depth_yes: float = 0.0
    depth_no: float = 0.0
    volume: float = 0.0
    last_trade: Optional[float] = None
    yes_player: str = ""
    no_player: str = ""


@dataclass
class OrderRequest:
    user_id: str
    ticker: str
    action: str
    side: str
    price: float
    quantity: int
    position_id: str


@dataclass
class OrderReceipt:
    ok: bool
    order_id: str = ""
    filled_qty: float = 0.0
    average_price: Optional[float] = None
    remaining_qty: float = 0.0
    fees: float = 0.0
    error: str = ""


class StrategyBook:
    """In-memory decisions for every user. Persistence is applied by the caller."""

    def __init__(self, *, max_data_age_ms: float = 5000, min_liquidity: float = 20) -> None:
        self.max_data_age_ms = max_data_age_ms
        self.min_liquidity = min_liquidity
        self.settings: dict[str, dict[str, Any]] = {}
        self.positions: dict[str, PositionState] = {}
        self.trades: dict[tuple[str, str], int] = {}
        self.cooldown_until: dict[tuple[str, str], float] = {}
        self.daily_loss: dict[str, float] = {}
        self.daily_exposure: dict[str, float] = {}
        self.trading_connected = False
        self.alerts: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.last_reason: dict[tuple[str, str], str] = {}
        self.signals: dict[tuple[str, str], dict[str, Any]] = {}
        self._signal_action: dict[tuple[str, str], str] = {}
        from app.services.trading.short_run import KalshiShortRunPatternEngine

        self.short_run = KalshiShortRunPatternEngine()

    def ensure(self, user_id: str) -> dict[str, Any]:
        row = self.settings.get(user_id)
        if row is None:
            row = default_settings()
            self.settings[user_id] = row
        return row

    def replace_settings(self, user_id: str, values: dict[str, Any], apply_to_open: Optional[bool]) -> dict[str, Any]:
        open_rows = self.open_positions(user_id)
        if open_rows and apply_to_open is None:
            return {"needs_confirmation": True, "open_positions": len(open_rows)}
        current = self.ensure(user_id)
        current.update({key: values[key] for key in DEFAULTS if key in values})
        if apply_to_open:
            for position in open_rows:
                position.rules = _rules_snapshot(current)
        return {"needs_confirmation": False, "settings": dict(current)}

    def open_positions(self, user_id: str) -> list[PositionState]:
        return [
            row
            for row in self.positions.values()
            if row.user_id == user_id and row.status in OPEN_STATUSES and row.filled_qty >= 0 and row.closed_at_ms is None
        ]

    def evaluate(self, user_id: str, quote: Quote) -> Optional[OrderRequest]:
        settings = self.ensure(user_id)
        self.short_run.observe(quote)
        if settings.get("watch_one_game") and quote.ticker != (settings.get("watched_ticker") or ""):
            self.signals.pop((user_id, quote.ticker), None)
            return None
        if not _in_scope(settings, quote.ticker):
            return None
        working = self._working(user_id, quote.ticker)
        if working is not None:
            if working.rules.get("short_run_enabled"):
                return self._manage_short(working, quote)
            return self._manage(working, quote)
        if settings.get("short_run_enabled"):
            return self._maybe_enter_short(user_id, settings, quote)
        return self._maybe_enter(user_id, settings, quote)

    def apply_receipt(self, position_id: str, receipt: OrderReceipt, *, closing: bool = False, now_ms: float = 0) -> None:
        position = self.positions.get(position_id)
        if position is None:
            return
        if not receipt.ok:
            self._event(position, "ORDER REJECTED", receipt.error or "Order was not accepted")
            position.exit_pending = False
            if position.filled_qty <= 0:
                position.status = "RISK LOCKED"
                position.closed_at_ms = now_ms
            return
        position.order_id = receipt.order_id or position.order_id
        position.fees = receipt.fees
        if closing:
            if position.closed_at_ms is not None:
                return
            if receipt.filled_qty > 0 and receipt.average_price:
                position.close_price = receipt.average_price
                position.realized_pnl = profit_dollars(position.entry_price, receipt.average_price, min(receipt.filled_qty, position.filled_qty))
                if position.realized_pnl < 0:
                    self.daily_loss[position.user_id] = self.daily_loss.get(position.user_id, 0) + abs(position.realized_pnl)
                position.status = "POSITION CLOSED"
                self.mark_closed_clock(position, now_ms)
                self._alert(position, "POSITION CLOSED")
            return
        if receipt.filled_qty <= 0 or not receipt.average_price:
            position.status = "ORDER SUBMITTED"
            return
        previous_cost = order_cost(position.entry_price, position.filled_qty) if position.entry_price and position.filled_qty else 0
        first_fill = position.filled_qty <= 0
        position.filled_qty = receipt.filled_qty
        position.entry_price = receipt.average_price
        position.peak_price = max(position.peak_price, receipt.average_price)
        cost = order_cost(receipt.average_price, receipt.filled_qty)
        position.bet_amount = cost
        self.daily_exposure[position.user_id] = self.daily_exposure.get(position.user_id, 0) + max(0.0, cost - previous_cost)
        if first_fill:
            key = (position.user_id, position.ticker)
            self.trades[key] = self.trades.get(key, 0) + 1
        if receipt.remaining_qty > 0:
            position.status = "PARTIALLY FILLED"
        else:
            position.status = "POSITION OPEN"
            self._alert(position, "POSITION FILLED")

    def mark_closed_clock(self, position: PositionState, now_ms: float) -> None:
        position.closed_at_ms = now_ms
        cooldown = float(position.rules.get("cooldown_seconds") or 0) * 1000
        self.cooldown_until[(position.user_id, position.ticker)] = now_ms + cooldown

    def _working(self, user_id: str, ticker: str) -> Optional[PositionState]:
        rows = [
            row
            for row in self.positions.values()
            if row.user_id == user_id and row.ticker == ticker and row.closed_at_ms is None and row.status in OPEN_STATUSES
        ]
        return rows[-1] if rows else None

    def _maybe_enter(self, user_id: str, settings: dict[str, Any], quote: Quote) -> Optional[OrderRequest]:
        side, price = choose_entry_side(settings["entry_type"], quote.yes_bid, quote.yes_ask, float(settings["entry_price"]))
        if side is None or price is None:
            return None
        quantity = contract_count(float(settings["bet_amount"]), price)
        cost = order_cost(price, quantity)
        opposite = self._opposite_open(user_id, quote.ticker, side)
        reason = risk_block_reason(
            RiskView(
                auto_entry=bool(settings["auto_entry"]),
                paused=bool(settings["paused"]),
                emergency_stop=bool(settings["emergency_stop"]),
                trading_connected=self.trading_connected,
                market_open=(quote.status or "OPEN").upper() not in {"CLOSED", "SETTLED", "FINALIZED", "DETERMINED", "SUSPENDED"},
                market_live=quote.live,
                in_scope=_in_scope(settings, quote.ticker),
                data_age_ms=quote.data_age_ms,
                max_data_age_ms=self.max_data_age_ms,
                depth=quote.depth,
                min_liquidity=self.min_liquidity,
                open_notional=self._open_notional(user_id, quote.ticker),
                order_cost=cost,
                max_position=float(settings["max_position_per_match"]),
                daily_loss=self.daily_loss.get(user_id, 0),
                max_daily_loss=float(settings["max_daily_loss"]),
                daily_exposure=self.daily_exposure.get(user_id, 0),
                max_daily_exposure=float(settings["max_daily_exposure"]),
                trades_this_match=self.trades.get((user_id, quote.ticker), 0),
                max_trades=int(settings["max_trades_per_match"]),
                cooldown_until_ms=self.cooldown_until.get((user_id, quote.ticker), 0),
                now_ms=quote.now_ms,
                opposite_open=opposite,
            )
        )
        if reason:
            self.last_reason[(user_id, quote.ticker)] = reason
            return None
        self.last_reason.pop((user_id, quote.ticker), None)
        position = PositionState(
            id=str(uuid4()),
            user_id=user_id,
            ticker=quote.ticker,
            label=quote.label or quote.ticker,
            side=side,
            status="ENTRY CONDITION MET",
            bet_amount=float(settings["bet_amount"]),
            requested_price=price,
            rules=_rules_snapshot(settings),
            opened_at_ms=quote.now_ms,
        )
        self.positions[position.id] = position
        self._alert(position, "ENTRY CONDITION MET")
        position.status = "ORDER SUBMITTED"
        return OrderRequest(user_id, quote.ticker, "buy", side, price, quantity, position.id)

    def _maybe_enter_short(self, user_id: str, settings: dict[str, Any], quote: Quote) -> Optional[OrderRequest]:
        reading = self.short_run.read_buy(quote, settings)
        self.signals[(user_id, quote.ticker)] = reading
        self._signal_alert(user_id, quote, reading)
        if reading["action"] not in {"BUY", "STRONG BUY"}:
            self.last_reason[(user_id, quote.ticker)] = reading["reason"]
            return None
        price = reading["entry_price"]
        side = reading["side"]
        if price is None:
            return None
        quantity = contract_count(float(settings["bet_amount"]), price)
        cost = order_cost(price, quantity)
        reason = risk_block_reason(
            RiskView(
                auto_entry=bool(settings["auto_entry"]),
                paused=bool(settings["paused"]),
                emergency_stop=bool(settings["emergency_stop"]),
                trading_connected=self.trading_connected,
                market_open=(quote.status or "OPEN").upper() not in {"CLOSED", "SETTLED", "FINALIZED", "DETERMINED", "SUSPENDED"},
                market_live=quote.live,
                in_scope=_in_scope(settings, quote.ticker),
                data_age_ms=quote.data_age_ms,
                max_data_age_ms=self.max_data_age_ms,
                depth=quote.depth,
                min_liquidity=self.min_liquidity,
                open_notional=self._open_notional(user_id, quote.ticker),
                order_cost=cost,
                max_position=float(settings["max_position_per_match"]),
                daily_loss=self.daily_loss.get(user_id, 0),
                max_daily_loss=float(settings["max_daily_loss"]),
                daily_exposure=self.daily_exposure.get(user_id, 0),
                max_daily_exposure=float(settings["max_daily_exposure"]),
                trades_this_match=self.trades.get((user_id, quote.ticker), 0),
                max_trades=int(settings["max_trades_per_match"]),
                cooldown_until_ms=self.cooldown_until.get((user_id, quote.ticker), 0),
                now_ms=quote.now_ms,
                opposite_open=self._opposite_open(user_id, quote.ticker, side),
            )
        )
        if reason:
            self.last_reason[(user_id, quote.ticker)] = reason
            return None
        self.last_reason.pop((user_id, quote.ticker), None)
        position = PositionState(
            id=str(uuid4()),
            user_id=user_id,
            ticker=quote.ticker,
            label=reading["player"] or quote.label or quote.ticker,
            side=side,
            status="ENTRY CONDITION MET",
            bet_amount=float(settings["bet_amount"]),
            requested_price=price,
            rules=_rules_snapshot(settings),
            opened_at_ms=quote.now_ms,
        )
        self.positions[position.id] = position
        self._alert(position, "ENTRY CONDITION MET")
        position.status = "ORDER SUBMITTED"
        return OrderRequest(user_id, quote.ticker, "buy", side, price, quantity, position.id)

    def _manage_short(self, position: PositionState, quote: Quote) -> Optional[OrderRequest]:
        if position.exit_pending or position.filled_qty <= 0 or position.entry_price <= 0:
            return None
        exit_price = executable_sell(position.side, quote.yes_bid, quote.yes_ask)
        if exit_price is None:
            return None
        position.current_exit_price = exit_price
        position.peak_price = max(position.peak_price or position.entry_price, exit_price)
        rules = position.rules
        stop = stop_loss_price(position.entry_price, float(rules["stop_loss_percent"]))
        if exit_price <= stop:
            reading = {
                "action": "STOP LOSS",
                "headline": f"SELL — {position.label} {position.side}",
                "reason": "Price is through the stop. This is not a guaranteed exit price until Kalshi fills it.",
                "current_price": exit_price,
                "entry_price": position.entry_price,
                "exit_price": exit_price,
                "liquidity_trend": "FLAT",
                "opportunity_percent": profit_percent(position.entry_price, exit_price),
                "timestamp_ms": quote.now_ms,
                "side": position.side,
                "player": position.label,
            }
            self.signals[(position.user_id, quote.ticker)] = reading
            self._signal_alert(position.user_id, quote, {"action": "STOP LOSS"})
            if not rules.get("auto_exit"):
                position.status = "STOP LOSS TRIGGERED"
                return None
            return self._exit(position, quote, exit_price, "STOP LOSS TRIGGERED")
        reading = self.short_run.read_sell(
            quote,
            rules,
            side=position.side,
            entry=position.entry_price,
            peak=position.peak_price,
        )
        self.signals[(position.user_id, quote.ticker)] = reading
        self._signal_alert(position.user_id, quote, reading)
        if reading["action"] == "SELL":
            position.status = "SELL — LOCK PROFIT"
            if rules.get("auto_exit"):
                return self._exit(position, quote, exit_price, "SELL")
            return None
        if reading["action"] == "SELL DEVELOPING":
            position.status = "SELL DEVELOPING"
        elif reading["action"] == "WATCH PROFIT":
            position.status = "WATCH PROFIT"
        else:
            position.status = "POSITION OPEN"
        gain = profit_percent(position.entry_price, exit_price)
        if rules.get("trailing_stop_enabled") and gain >= float(rules.get("trailing_activation_percent") or 0):
            position.trailing_active = True
        if rules.get("auto_exit") and position.trailing_active:
            trail = trailing_stop_price(position.peak_price, float(rules["trailing_stop_percent"]))
            if exit_price <= trail:
                return self._exit(position, quote, exit_price, "TRAILING EXIT TRIGGERED")
        target = take_profit_price(position.entry_price, float(rules["take_profit_percent"]))
        if rules.get("auto_exit") and exit_price >= target:
            return self._exit(position, quote, exit_price, "TAKE PROFIT TRIGGERED")
        return None

    def _signal_alert(self, user_id: str, quote: Quote, reading: dict[str, Any]) -> None:
        action = reading.get("action") or ""
        kind = {"STRONG BUY": "STRONG BUY", "BUY": "BUY", "SELL": "SELL", "STOP LOSS": "STOP LOSS TRIGGERED"}.get(action)
        if kind is None:
            return
        key = (user_id, quote.ticker, kind)
        if self._signal_action.get((user_id, quote.ticker)) == action:
            return
        self._signal_action[(user_id, quote.ticker)] = action
        self.alerts.append({"user_id": user_id, "market_ticker": quote.ticker, "kind": kind})
        self.events.append(
            {
                "user_id": user_id,
                "market_ticker": quote.ticker,
                "position_id": "",
                "kind": kind,
                "detail": reading.get("headline") or kind,
            }
        )

    def _manage(self, position: PositionState, quote: Quote) -> Optional[OrderRequest]:
        if position.exit_pending or position.filled_qty <= 0 or position.entry_price <= 0:
            return None
        exit_price = executable_sell(position.side, quote.yes_bid, quote.yes_ask)
        if exit_price is None:
            return None
        position.current_exit_price = exit_price
        position.peak_price = max(position.peak_price or position.entry_price, exit_price)
        rules = position.rules
        if not rules.get("auto_exit"):
            return None
        target = take_profit_price(position.entry_price, float(rules["take_profit_percent"]))
        stop = stop_loss_price(position.entry_price, float(rules["stop_loss_percent"]))
        gain = profit_percent(position.entry_price, exit_price)
        if rules.get("trailing_stop_enabled") and gain >= float(rules["trailing_activation_percent"]):
            position.trailing_active = True
            position.status = "TRAILING STOP ACTIVE"
        if position.trailing_active:
            trail = trailing_stop_price(position.peak_price, float(rules["trailing_stop_percent"]))
            if exit_price <= trail:
                return self._exit(position, quote, exit_price, "TRAILING EXIT TRIGGERED")
        if exit_price >= target:
            return self._exit(position, quote, exit_price, "TAKE PROFIT TRIGGERED")
        if exit_price <= stop:
            return self._exit(position, quote, exit_price, "STOP LOSS TRIGGERED")
        if gain >= float(rules["take_profit_percent"]) - 1:
            position.status = "PROFIT TARGET APPROACHING"
        elif position.trailing_active:
            position.status = "TRAILING STOP ACTIVE"
        else:
            position.status = "POSITION OPEN"
        return None

    def _exit(self, position: PositionState, quote: Quote, price: float, kind: str) -> Optional[OrderRequest]:
        position.status = kind
        position.exit_pending = True
        self._alert(position, kind)
        if not self.trading_connected:
            self._note(position.user_id, position.ticker, "RISK LOCKED", "Trading API is disconnected.")
            return None
        return OrderRequest(
            position.user_id,
            position.ticker,
            "sell",
            position.side,
            price,
            max(1, int(position.filled_qty)),
            position.id,
        )

    def _opposite_open(self, user_id: str, ticker: str, side: str) -> bool:
        settings = self.ensure(user_id)
        if not settings.get("one_direction_per_market", True):
            return False
        return any(
            row.user_id == user_id and row.ticker == ticker and row.side != side and row.closed_at_ms is None and row.status in OPEN_STATUSES
            for row in self.positions.values()
        )

    def _open_notional(self, user_id: str, ticker: str) -> float:
        return sum(
            order_cost(row.entry_price or row.requested_price, row.filled_qty or 0)
            for row in self.positions.values()
            if row.user_id == user_id and row.ticker == ticker and row.closed_at_ms is None and row.status in OPEN_STATUSES
        )

    def _alert(self, position: PositionState, kind: str) -> None:
        if kind not in ALERT_KINDS:
            return
        self.alerts.append({"user_id": position.user_id, "market_ticker": position.ticker, "kind": kind})
        self._event(position, kind, position.status)

    def _event(self, position: PositionState, kind: str, detail: str) -> None:
        self.events.append(
            {
                "user_id": position.user_id,
                "market_ticker": position.ticker,
                "position_id": position.id,
                "kind": kind,
                "detail": detail,
            }
        )

    def _note(self, user_id: str, ticker: str, kind: str, detail: str) -> None:
        self.events.append({"user_id": user_id, "market_ticker": ticker, "kind": kind, "detail": detail, "position_id": ""})


def _rules_snapshot(settings: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "auto_exit",
        "take_profit_percent",
        "stop_loss_percent",
        "trailing_stop_enabled",
        "trailing_activation_percent",
        "trailing_stop_percent",
        "cooldown_seconds",
        "short_run_enabled",
        "min_sell_score",
        "max_spread",
        "min_liquidity_growth",
    )
    return {key: settings.get(key) for key in keys}


def _in_scope(settings: dict[str, Any], ticker: str) -> bool:
    if settings.get("market_scope", "ALL_LIVE") != "SELECTED":
        return True
    return ticker in set(settings.get("selected_tickers") or [])


def should_play_alert(last_played_ms: Optional[float], now_ms: float, cooldown_ms: float = 20_000) -> bool:
    """First alert plays immediately. A repeat of the same alert waits 20 seconds."""
    if last_played_ms is None:
        return True
    return now_ms - last_played_ms >= cooldown_ms
