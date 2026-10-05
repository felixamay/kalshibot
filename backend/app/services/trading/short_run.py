"""Short-run Kalshi buy and sell readings. Prices and depth come from Kalshi only.

Nothing here promises a profit. A price drop by itself is not a buy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class BookPoint:
    ts_ms: float
    yes_bid: float
    yes_ask: float
    depth_yes: float
    depth_no: float
    volume: float
    last_trade: float


def kalshi_fee_cents(price_cents: float) -> float:
    """Approximate Kalshi taker fee in cents: 7 * p * (1-p), p in dollars."""
    if price_cents <= 0 or price_cents >= 100:
        return 0.0
    probability = price_cents / 100.0
    return 7.0 * probability * (1.0 - probability)


def microprice(bid: float, ask: float, bid_depth: float, ask_depth: float) -> float:
    weight = bid_depth + ask_depth
    if weight <= 0 or bid <= 0 or ask <= 0:
        return bid or ask or 0.0
    return (ask * bid_depth + bid * ask_depth) / weight


class ShortRunProfitEstimator:
    """Executable upside from the entry ask to the recent recovery level, after costs."""

    def estimate(
        self,
        entry_cents: float,
        recent_high: float,
        spread_cents: float,
        depth: float,
        quantity: int,
    ) -> dict[str, float]:
        zone_high = recent_high
        zone_low = max(entry_cents, recent_high - max(spread_cents, 1.0))
        expected_exit = recent_high
        slippage = 1.0 if depth < max(1, quantity) * 2 else 0.0
        # One entry fee plus half the spread. The exit is the recent high, not a second round trip.
        costs = kalshi_fee_cents(entry_cents) + (spread_cents / 2.0) + slippage
        net_cents = expected_exit - entry_cents - costs
        percent = (net_cents / entry_cents * 100.0) if entry_cents else 0.0
        return {
            "zone_low": zone_low,
            "zone_high": zone_high,
            "expected_exit": expected_exit,
            "costs_cents": costs,
            "net_cents": net_cents,
            "opportunity_percent": percent,
        }


def liquidity_recovery_score(
    bid_growth: float,
    ask_change: float,
    spread_now: float,
    spread_then: float,
    flow_balance: float,
    micro_slope: float,
) -> float:
    """0–100. Rising bids, lighter asks, a tighter spread, and a firming microprice raise it."""
    bid_points = _clamp(bid_growth / 35.0) * 30.0
    level_points = 15.0 if bid_growth > 0 and ask_change < 0 else 0.0
    ask_points = _clamp(-ask_change / 20.0) * 20.0
    tighten = spread_then - spread_now
    spread_points = _clamp(tighten / 1.0) * 15.0 if spread_then else (15.0 if spread_now <= 1 else 0.0)
    flow_points = _clamp((flow_balance + 1.0) / 2.0) * 10.0
    micro_points = 10.0 if micro_slope >= 0 else 0.0
    return round(min(100.0, bid_points + level_points + ask_points + spread_points + flow_points + micro_points), 1)


def buy_score(parts: dict[str, float], weights: Optional[dict[str, float]] = None) -> float:
    """SHORT_RUN_BUY_SCORE. Weights sum to 1 and each part is 0–100."""
    used = weights or {
        "liquidity": 0.25,
        "pullback": 0.20,
        "imbalance": 0.20,
        "momentum": 0.15,
        "flow": 0.10,
        "spread": 0.05,
        "volatility": 0.05,
    }
    total = sum(used.get(name, 0.0) * parts.get(name, 0.0) for name in used)
    return round(total, 1)


def sell_score(parts: dict[str, float], weights: Optional[dict[str, float]] = None) -> float:
    """SHORT_RUN_SELL_SCORE. Weights sum to 1 and each part is 0–100."""
    used = weights or {
        "profit": 0.20,
        "liquidity": 0.20,
        "momentum": 0.20,
        "book": 0.15,
        "flow": 0.10,
        "drawdown": 0.10,
        "spread": 0.05,
    }
    total = sum(used.get(name, 0.0) * parts.get(name, 0.0) for name in used)
    return round(total, 1)


class KalshiShortRunPatternEngine:
    """Rolling Kalshi book for one market. No score feed and no model API."""

    def __init__(self) -> None:
        self._points: dict[str, list[BookPoint]] = {}
        self._profit = ShortRunProfitEstimator()

    def observe(self, quote: Any) -> None:
        bid = float(quote.yes_bid or 0)
        ask = float(quote.yes_ask or 0)
        last = float(quote.last_trade or 0)
        if last <= 0:
            last = microprice(bid, ask, float(getattr(quote, "depth_yes", 0) or 0), float(getattr(quote, "depth_no", 0) or 0))
        point = BookPoint(
            ts_ms=float(quote.now_ms or 0),
            yes_bid=bid,
            yes_ask=ask,
            depth_yes=float(getattr(quote, "depth_yes", 0) or 0),
            depth_no=float(getattr(quote, "depth_no", 0) or 0),
            volume=float(getattr(quote, "volume", 0) or 0),
            last_trade=last,
        )
        rows = self._points.setdefault(quote.ticker, [])
        if rows and rows[-1].ts_ms == point.ts_ms:
            rows[-1] = point
        else:
            rows.append(point)
        cutoff = point.ts_ms - 60_000
        self._points[quote.ticker] = [row for row in rows if row.ts_ms >= cutoff][-400:]

    def read_buy(self, quote: Any, settings: dict[str, Any]) -> dict[str, Any]:
        allowed = (settings.get("entry_type") or "YES").upper()
        sides = ("YES", "NO") if allowed == "EITHER" else (allowed,)
        readings = [self._buy_side(quote, settings, side) for side in sides]
        ranked = sorted(readings, key=lambda row: (row["buy_score"], row["opportunity_percent"]), reverse=True)
        chosen = ranked[0]
        for row in ranked:
            if row["action"] in {"STRONG BUY", "BUY", "BUY DEVELOPING"}:
                return row
        for row in ranked:
            if row["action"] == "DO NOT CHASE":
                return row
        return chosen

    def read_sell(self, quote: Any, settings: dict[str, Any], *, side: str, entry: float, peak: float) -> dict[str, Any]:
        view = self._view(quote.ticker, side, float(quote.now_ms or 0))
        exit_price = _executable_sell(side, quote)
        current = exit_price or view["price"]
        peak_price = max(peak or entry, current)
        profit = ((current - entry) / entry * 100.0) if entry else 0.0
        drawdown = ((peak_price - current) / peak_price * 100.0) if peak_price else 0.0
        take_profit = float(settings.get("take_profit_percent") or 8)
        trail = float(settings.get("trailing_stop_percent") or 3)
        parts = {
            "profit": _clamp(profit / take_profit) * 100.0 if profit > 0 else 0.0,
            "liquidity": _exhaustion(view),
            "momentum": _momentum_exhaustion(view),
            "book": _book_reversal(view),
            "flow": _flow_reversal(view),
            "drawdown": _clamp(drawdown / max(trail, 0.5)) * 100.0,
            "spread": 100.0 if view["spread"] > float(settings.get("max_spread") or 4) else _clamp(view["volatility"] / 6.0) * 100.0,
        }
        score = sell_score(parts)
        min_sell = float(settings.get("min_sell_score") or 75)
        if profit <= 0:
            action = "HOLD"
        elif score >= min_sell:
            action = "SELL"
        elif score >= 65:
            action = "SELL DEVELOPING"
        elif score >= 50:
            action = "WATCH PROFIT"
        else:
            action = "HOLD"
        player = _player_name(quote, side)
        headline = f"SELL — {player} {side}" if action == "SELL" else action
        if action == "SELL":
            headline = f"SELL — {player} {side}"
        reason = _sell_reason(action, profit, view)
        return _signal(
            action=action,
            headline=headline,
            side=side,
            player=player,
            current=current,
            entry=entry,
            exit_price=exit_price,
            view=view,
            score=score,
            buy_score=None,
            opportunity=profit,
            reason=reason,
            now_ms=float(quote.now_ms or 0),
            extra={"sell_parts": {key: round(value, 1) for key, value in parts.items()}, "peak": round(peak_price, 2), "drawdown_percent": round(drawdown, 2)},
        )

    def _buy_side(self, quote: Any, settings: dict[str, Any], side: str) -> dict[str, Any]:
        now_ms = float(quote.now_ms or 0)
        view = self._view(quote.ticker, side, now_ms)
        entry = _executable_buy(side, quote)
        current = entry or view["price"]
        player = _player_name(quote, side)
        empty = _signal(
            action="WAIT",
            headline="WAIT",
            side=side,
            player=player,
            current=current,
            entry=entry,
            exit_price=_executable_sell(side, quote),
            view=view,
            score=0,
            buy_score=0,
            opportunity=0,
            reason="Waiting for enough Kalshi prints to read a short-run move.",
            now_ms=now_ms,
            extra={},
        )
        if view["count"] < 4 or entry is None:
            return empty
        pullback_min = float(settings.get("pullback_min_percent") or 4)
        pullback_max = float(settings.get("pullback_max_percent") or 18)
        max_spread = float(settings.get("max_spread") or 4)
        min_growth = float(settings.get("min_liquidity_growth") or 10)
        max_entry = float(settings.get("max_entry_price") or settings.get("entry_price") or 99)
        min_profit = float(settings.get("min_expected_profit_percent") or 3)
        prior_rise = view["high"] - view["price_30s"]
        pullback = ((view["high"] - view["price"]) / view["high"] * 100.0) if view["high"] else 0.0
        chase_high = view["prior_high"] or view["high"]
        zone_top = chase_high * (1.0 - pullback_min / 100.0)
        dipped = view["low_after_prior"] < chase_high and view["low_after_prior"] <= zone_top
        if dipped and current is not None and current > zone_top:
            return _signal(
                action="DO NOT CHASE",
                headline="DO NOT CHASE",
                side=side,
                player=player,
                current=current,
                entry=entry,
                exit_price=_executable_sell(side, quote),
                view=view,
                score=0,
                buy_score=0,
                opportunity=0,
                reason="Entry opportunity has already moved.",
                now_ms=now_ms,
                extra={"pullback_percent": round(pullback, 1)},
            )
        recovery = liquidity_recovery_score(
            view["bid_growth"],
            view["ask_change"],
            view["spread"],
            view["spread_then"],
            view["flow_balance"],
            view["micro_slope"],
        )
        pullback_quality = 0.0
        if prior_rise >= 3 and pullback_min <= pullback <= pullback_max:
            span = max(pullback_max - pullback_min, 1)
            pullback_quality = 70.0 + 30.0 * (1.0 - abs(pullback - (pullback_min + pullback_max) / 2.0) / (span / 2.0))
            pullback_quality = _clamp(pullback_quality / 100.0) * 100.0
        imbalance = _clamp((view["imbalance"] + 1.0) / 2.0) * 100.0
        if view["imbalance"] > view["imbalance_then"]:
            imbalance = min(100.0, imbalance + 10.0)
        momentum = 80.0 if view["acceleration"] >= 0 else (40.0 if view["slope"] > -0.3 else 0.0)
        if view["sell_pressure"] < view["sell_pressure_then"]:
            flow = 75.0
        else:
            flow = _clamp((view["flow_balance"] + 1.0) / 2.0) * 100.0
        spread_part = 100.0 if view["spread"] <= max_spread / 2 else (60.0 if view["spread"] <= max_spread else 0.0)
        volatility = max(0.0, 100.0 - view["volatility"] / 6.0 * 100.0)
        parts = {
            "liquidity": recovery,
            "pullback": pullback_quality,
            "imbalance": imbalance,
            "momentum": momentum,
            "flow": flow,
            "spread": spread_part,
            "volatility": volatility,
        }
        score = buy_score(parts)
        profit = self._profit.estimate(entry, view["high"], view["spread"], view["bid_depth"], 1)
        gates = []
        if prior_rise < 3:
            gates.append("No prior upward move on this side.")
        if not (pullback_min <= pullback <= pullback_max):
            gates.append("Pullback is outside the configured range.")
        if view["bid_growth"] < min_growth:
            gates.append("Bid liquidity is not returning.")
        if view["imbalance"] < view["imbalance_then"] and view["imbalance"] <= 0:
            gates.append("Order book is not supportive.")
        if view["sell_pressure"] > view["sell_pressure_then"] + 1e-9:
            gates.append("Selling pressure is still rising.")
        if view["spread"] > max_spread:
            gates.append("Spread is wider than the maximum.")
        if entry > max_entry:
            gates.append("Price is above the maximum entry.")
        if profit["opportunity_percent"] < min_profit:
            gates.append("Expected short-run gain after costs is too small.")
        price_only = prior_rise >= 3 and pullback >= pullback_min and view["bid_growth"] <= 0
        if price_only:
            action = "AVOID"
            reason = "Price drop alone is not a buy. Bid liquidity is not returning."
        elif gates and score >= 65 and view["bid_growth"] > 0 and prior_rise >= 3:
            action = "BUY DEVELOPING"
            reason = gates[0]
        elif gates:
            action = "WAIT" if "No prior" in gates[0] or "Pullback" in gates[0] else "AVOID"
            reason = gates[0]
        elif score >= float(settings.get("strong_buy_score") or 85):
            action = "STRONG BUY"
            reason = _buy_reason()
        elif score >= float(settings.get("min_buy_score") or 75):
            action = "BUY"
            reason = _buy_reason()
        elif score >= 65:
            action = "BUY DEVELOPING"
            reason = "Liquidity is improving, and the buy score is not high enough yet."
        else:
            action = "WAIT"
            reason = "Short-run evidence is still weak."
        headline = f"{action} — {player} {side}" if action in {"BUY", "STRONG BUY"} else action
        return _signal(
            action=action,
            headline=headline,
            side=side,
            player=player,
            current=current,
            entry=entry,
            exit_price=_executable_sell(side, quote),
            view=view,
            score=score,
            buy_score=score,
            opportunity=profit["opportunity_percent"],
            reason=reason,
            now_ms=now_ms,
            extra={
                "pullback_percent": round(pullback, 1),
                "recent_high": round(view["high"], 2),
                "zone_low": round(profit["zone_low"], 2),
                "zone_high": round(profit["zone_high"], 2),
                "liquidity_recovery": recovery,
                "buy_parts": {key: round(value, 1) for key, value in parts.items()},
                "bid_depth_change_percent": round(view["bid_growth"], 1),
            },
        )

    def _view(self, ticker: str, side: str, now_ms: float) -> dict[str, float]:
        rows = [row for row in self._points.get(ticker, []) if row.ts_ms <= now_ms]
        if not rows:
            return _empty_view()
        prices = [_series_price(row, side) for row in rows]
        window = [(row, price) for row, price in zip(rows, prices) if row.ts_ms >= now_ms - 30_000]
        if not window:
            window = list(zip(rows, prices))
        high = max(price for _, price in window)
        high_at = max(row.ts_ms for row, price in window if price == high)
        after = [price for row, price in window if row.ts_ms >= high_at]
        low_after = min(after) if after else high
        earlier = [price for row, price in window[:-1]] or [high]
        prior_high = max(earlier)
        prior_at = max(row.ts_ms for row, price in window if price == prior_high)
        low_after_prior = min((price for row, price in window if row.ts_ms >= prior_at), default=prior_high)

        def price_then(lag: float) -> float:
            target = now_ms - lag
            chosen = window[0][1]
            for row, price in window:
                if row.ts_ms <= target:
                    chosen = price
            return chosen

        current_row, current = window[-1]
        then = _at(window, now_ms, 5_000)
        earlier = _at(window, now_ms, 10_000)
        bid_now, ask_now = _depths(current_row, side)
        bid_then, ask_then = _depths(then[0], side)
        bid_growth = ((bid_now - bid_then) / bid_then * 100.0) if bid_then else 0.0
        ask_change = ((ask_now - ask_then) / ask_then * 100.0) if ask_then else 0.0
        imbalance = _imbalance(bid_now, ask_now)
        imbalance_then = _imbalance(bid_then, ask_then)
        sell_now, buy_now = _pressure(window, now_ms, 3_000, side)
        sell_then, buy_then = _pressure(window, now_ms - 3_000, 3_000, side)
        flow_balance = 0.0
        if buy_now + sell_now > 0:
            flow_balance = (buy_now - sell_now) / (buy_now + sell_now)
        slope = (current - price_then(3_000)) / 3.0
        previous_slope = (price_then(3_000) - price_then(6_000)) / 3.0
        micro_now = _micro(current_row, side)
        micro_then = _micro(then[0], side)
        sample = [price for _, price in window]
        return {
            "count": float(len(window)),
            "price": current,
            "price_1s": price_then(1_000),
            "price_3s": price_then(3_000),
            "price_5s": price_then(5_000),
            "price_10s": price_then(10_000),
            "price_30s": price_then(30_000),
            "slope": slope,
            "acceleration": slope - previous_slope,
            "high": high,
            "prior_high": prior_high,
            "low": min(sample),
            "low_after_high": low_after,
            "low_after_prior": low_after_prior,
            "bid_depth": bid_now,
            "ask_depth": ask_now,
            "bid_growth": bid_growth,
            "ask_change": ask_change,
            "imbalance": imbalance,
            "imbalance_then": imbalance_then,
            "spread": _spread(current_row, side),
            "spread_then": _spread(then[0], side),
            "sell_pressure": sell_now,
            "sell_pressure_then": sell_then,
            "buy_pressure": buy_now,
            "buy_pressure_then": buy_then,
            "flow_balance": flow_balance,
            "micro_slope": micro_now - micro_then,
            "volatility": _stdev(sample),
            "volume_delta": max(0.0, current_row.volume - earlier[0].volume),
        }


def _series_price(point: BookPoint, side: str) -> float:
    price = point.last_trade if point.last_trade > 0 else microprice(point.yes_bid, point.yes_ask, point.depth_yes, point.depth_no)
    return price if side == "YES" else 100.0 - price


def _depths(point: BookPoint, side: str) -> tuple[float, float]:
    if side == "YES":
        return point.depth_yes, point.depth_no
    return point.depth_no, point.depth_yes


def _spread(point: BookPoint, side: str) -> float:
    spread = max(0.0, point.yes_ask - point.yes_bid)
    return spread


def _micro(point: BookPoint, side: str) -> float:
    value = microprice(point.yes_bid, point.yes_ask, point.depth_yes, point.depth_no)
    return value if side == "YES" else 100.0 - value


def _imbalance(bid: float, ask: float) -> float:
    total = bid + ask
    if total <= 0:
        return 0.0
    return (bid - ask) / total


def _pressure(window: list[tuple[BookPoint, float]], now_ms: float, span: float, side: str) -> tuple[float, float]:
    start = now_ms - span
    sell = 0.0
    buy = 0.0
    previous = None
    for row, price in window:
        if previous is not None and row.ts_ms > start and row.ts_ms <= now_ms:
            traded = max(0.0, row.volume - previous[0].volume)
            if price < previous[1]:
                sell += traded
            elif price > previous[1]:
                buy += traded
        previous = (row, price)
    return sell, buy


def _at(window: list[tuple[BookPoint, float]], now_ms: float, lag: float) -> tuple[BookPoint, float]:
    target = now_ms - lag
    chosen = window[0]
    for item in window:
        if item[0].ts_ms <= target:
            chosen = item
    return chosen


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _exhaustion(view: dict[str, float]) -> float:
    slowing = 80.0 if view["bid_growth"] <= 0 else 20.0
    asks = 20.0 if view["ask_change"] > 0 else 0.0
    return min(100.0, slowing + asks)


def _momentum_exhaustion(view: dict[str, float]) -> float:
    if view["acceleration"] < 0 and view["slope"] <= 0.2:
        return 85.0
    if view["slope"] < view.get("acceleration", 0):
        return 60.0
    return 25.0


def _book_reversal(view: dict[str, float]) -> float:
    if view["imbalance"] < view["imbalance_then"] and view["ask_change"] > 0:
        return 85.0
    if view["ask_change"] > 0:
        return 55.0
    return 20.0


def _flow_reversal(view: dict[str, float]) -> float:
    if view["sell_pressure"] > view["buy_pressure"] and view["sell_pressure"] >= view["sell_pressure_then"]:
        return 80.0
    if view["buy_pressure"] < view["buy_pressure_then"]:
        return 55.0
    return 15.0


def _executable_buy(side: str, quote: Any) -> Optional[float]:
    from app.services.trading.strategy import executable_buy

    return executable_buy(side, quote.yes_bid, quote.yes_ask)


def _executable_sell(side: str, quote: Any) -> Optional[float]:
    from app.services.trading.strategy import executable_sell

    return executable_sell(side, quote.yes_bid, quote.yes_ask)


def _player_name(quote: Any, side: str) -> str:
    if side == "YES":
        return getattr(quote, "yes_player", "") or "YES"
    return getattr(quote, "no_player", "") or "NO"


def _buy_reason() -> str:
    return (
        "Strong prior upward move. Temporary pullback. Bid liquidity returning. "
        "Order book strengthening. Price still inside the favorable entry zone. "
        "This is not a guaranteed profit."
    )


def _sell_reason(action: str, profit: float, view: dict[str, float]) -> str:
    if action == "SELL":
        return (
            "Short-run target is available. Liquidity growth is slowing. Ask pressure is increasing. "
            "Momentum is weakening. Profit is available to lock. This is not a guaranteed profit."
        )
    if action == "SELL DEVELOPING":
        return "Profit is open and the short-run pattern is tiring. A sell is developing before a full reversal."
    if action == "WATCH PROFIT":
        return "The position is ahead. Liquidity and momentum have not exhausted yet."
    if profit <= 0:
        return "No short-run profit to lock yet."
    return "Holding. The Kalshi book has not turned enough to sell."


def _empty_view() -> dict[str, float]:
    return {
            "count": 0, "price": 0, "price_1s": 0, "price_3s": 0, "price_5s": 0, "price_10s": 0, "price_30s": 0,
        "slope": 0, "acceleration": 0, "high": 0, "prior_high": 0, "low": 0, "low_after_high": 0, "low_after_prior": 0, "bid_depth": 0, "ask_depth": 0,
        "bid_growth": 0, "ask_change": 0, "imbalance": 0, "imbalance_then": 0, "spread": 0, "spread_then": 0,
        "sell_pressure": 0, "sell_pressure_then": 0, "buy_pressure": 0, "buy_pressure_then": 0, "flow_balance": 0,
        "micro_slope": 0, "volatility": 0, "volume_delta": 0,
    }


def _signal(
    *,
    action: str,
    headline: str,
    side: str,
    player: str,
    current: Optional[float],
    entry: Optional[float],
    exit_price: Optional[float],
    view: dict[str, float],
    score: float,
    buy_score: Optional[float],
    opportunity: float,
    reason: str,
    now_ms: float,
    extra: dict[str, Any],
) -> dict[str, Any]:
    trend = "RISING" if view.get("bid_growth", 0) >= 5 else ("FALLING" if view.get("bid_growth", 0) <= -5 else "FLAT")
    if view.get("sell_pressure", 0) < view.get("sell_pressure_then", 0) and view.get("bid_growth", 0) > 0:
        flow = "RECOVERING"
    elif view.get("flow_balance", 0) > 0:
        flow = "RECOVERING"
    elif view.get("sell_pressure", 0) > view.get("buy_pressure", 0):
        flow = "SELLING"
    else:
        flow = "MIXED"
    body = {
        "action": action,
        "headline": headline,
        "side": side,
        "player": player,
        "current_price": round(current, 2) if current else None,
        "entry_price": round(entry, 2) if entry else None,
        "exit_price": round(exit_price, 2) if exit_price else None,
        "liquidity_trend": trend,
        "opportunity_percent": round(opportunity, 1),
        "reason": reason,
        "timestamp_ms": now_ms,
        "buy_score": buy_score,
        "sell_score": None if buy_score is not None else score,
        "spread": round(view.get("spread", 0), 2),
        "trade_flow": flow,
        "guaranteed": False,
    }
    body.update(extra)
    if buy_score is not None:
        body["sell_score"] = None
    else:
        body["sell_score"] = score
    return body


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
