"""Short-run Kalshi buy and sell readings. A price drop alone is not a buy."""

from app.services.trading.short_run import KalshiShortRunPatternEngine, buy_score, kalshi_fee_cents, sell_score
from app.services.trading.strategy import OrderReceipt, Quote, StrategyBook, default_settings


def point(ts, last, bid, ask, depth_yes, depth_no, volume, **overrides):
    data = dict(
        ticker="KXTEST",
        yes_bid=bid,
        yes_ask=ask,
        depth=depth_yes + depth_no,
        depth_yes=depth_yes,
        depth_no=depth_no,
        volume=volume,
        last_trade=last,
        status="OPEN",
        data_age_ms=100,
        live=True,
        label="Ada vs Bea",
        yes_player="Ada",
        no_player="Bea",
        now_ms=ts,
    )
    data.update(overrides)
    return Quote(**data)


def settings(**overrides):
    row = default_settings()
    row.update(
        short_run_enabled=True,
        auto_entry=True,
        auto_exit=True,
        entry_type="YES",
        bet_amount=20,
        max_entry_price=65,
        min_buy_score=75,
        strong_buy_score=85,
        min_sell_score=75,
        min_liquidity_growth=10,
        max_spread=4,
        min_expected_profit_percent=3,
        pullback_min_percent=4,
        pullback_max_percent=18,
    )
    row.update(overrides)
    return row


def feed(engine, rows):
    for row in rows:
        engine.observe(row)
    return engine.read_buy(rows[-1], settings())


def rising_pullback():
    return [
        point(0, 48, 47, 49, 100, 120, 10),
        point(5_000, 52, 51, 53, 120, 100, 30),
        point(10_000, 56, 54, 56, 140, 90, 60),
        point(13_000, 53, 51, 53, 150, 100, 90),
        point(16_000, 51, 50, 51, 189, 70, 95),
    ]


def test_buy_and_sell_weights_sum_to_one():
    assert buy_score({name: 100 for name in ("liquidity", "pullback", "imbalance", "momentum", "flow", "spread", "volatility")}) == 100
    assert sell_score({name: 100 for name in ("profit", "liquidity", "momentum", "book", "flow", "drawdown", "spread")}) == 100


def test_fee_uses_the_kalshi_price_curve():
    assert round(kalshi_fee_cents(51), 2) == round(7 * 0.51 * 0.49, 2)


def test_price_drop_without_liquidity_is_not_a_buy():
    rows = [
        point(0, 56, 54, 56, 140, 90, 20),
        point(5_000, 54, 52, 54, 130, 110, 40),
        point(10_000, 52, 50, 52, 110, 140, 70),
        point(15_000, 51, 49, 51, 90, 160, 100),
    ]
    reading = feed(KalshiShortRunPatternEngine(), rows)
    assert reading["action"] in {"WAIT", "AVOID"}
    assert reading["action"] not in {"BUY", "STRONG BUY"}
    assert "not a buy" in reading["reason"].lower() or "liquidity" in reading["reason"].lower() or "upward" in reading["reason"].lower()


def test_pullback_with_returning_bids_can_buy():
    reading = feed(KalshiShortRunPatternEngine(), rising_pullback())
    assert reading["action"] in {"BUY", "STRONG BUY"}
    assert reading["headline"].startswith("BUY") or reading["headline"].startswith("STRONG BUY")
    assert reading["player"] == "Ada"
    assert reading["side"] == "YES"
    assert reading["current_price"] == 51
    assert reading["entry_price"] == 51
    assert reading["recent_high"] == 56
    assert reading["buy_score"] >= 75
    assert reading["liquidity_trend"] == "RISING"
    assert reading["opportunity_percent"] > 0
    assert reading["guaranteed"] is False
    assert "not a guaranteed profit" in reading["reason"]


def test_recovered_price_is_do_not_chase():
    rows = rising_pullback() + [point(20_000, 58, 57, 58, 200, 60, 110)]
    reading = feed(KalshiShortRunPatternEngine(), rows)
    assert reading["action"] == "DO NOT CHASE"
    assert "already moved" in reading["reason"]


def test_sell_locks_profit_when_the_book_tires():
    engine = KalshiShortRunPatternEngine()
    rows = [
        point(0, 51, 50, 51, 100, 100, 10),
        point(5_000, 54, 53, 54, 160, 80, 40),
        point(10_000, 58, 57, 58, 220, 70, 80),
        point(13_000, 59, 58, 59, 230, 80, 100),
        point(16_000, 58, 57, 59, 210, 120, 130),
    ]
    for row in rows:
        engine.observe(row)
    reading = engine.read_sell(rows[-1], settings(), side="YES", entry=51, peak=59)
    assert reading["action"] in {"SELL", "SELL DEVELOPING"}
    assert reading["current_price"] == 57 or reading["exit_price"] == 57
    assert reading["opportunity_percent"] > 0
    assert reading["guaranteed"] is False


def test_user_buy_threshold_and_risk_block_orders():
    book = StrategyBook(min_liquidity=20)
    book.trading_connected = True
    book.replace_settings("ada", settings(min_buy_score=75), None)
    book.replace_settings("bea", settings(min_buy_score=99, strong_buy_score=100), None)
    for row in rising_pullback():
        book.evaluate("ada", row)
        book.evaluate("bea", row)
    ada = book.signals[("ada", "KXTEST")]
    bea = book.signals[("bea", "KXTEST")]
    assert ada["action"] in {"BUY", "STRONG BUY"}
    assert bea["action"] != "BUY" or bea["buy_score"] >= 99
    assert any(order for order in book.positions.values() if order.user_id == "ada")
    if bea["action"] not in {"BUY", "STRONG BUY"}:
        assert not any(order for order in book.positions.values() if order.user_id == "bea")


def test_watch_one_game_ignores_every_other_match():
    book = StrategyBook()
    book.trading_connected = True
    book.replace_settings("ada", settings(watch_one_game=True, watched_ticker="KXTEST"), None)
    other = point(16_000, 51, 50, 51, 189, 70, 95, ticker="OTHER")
    assert book.evaluate("ada", other) is None
    assert ("ada", "OTHER") not in book.signals


def test_daily_loss_overrides_a_buy_signal():
    book = StrategyBook()
    book.trading_connected = True
    book.replace_settings("ada", settings(), None)
    book.daily_loss["ada"] = 25
    order = None
    for row in rising_pullback():
        order = book.evaluate("ada", row)
    assert order is None
    assert "daily loss" in book.last_reason[("ada", "KXTEST")].lower()


def test_auto_exit_off_shows_sell_without_an_order_and_auto_exit_uses_the_bid():
    book = StrategyBook()
    book.trading_connected = True
    book.replace_settings("ada", settings(auto_exit=False), None)
    entered = None
    for row in rising_pullback():
        entered = book.evaluate("ada", row) or entered
    assert entered is not None
    assert entered.price == 51
    assert entered.action == "buy"
    position = next(row for row in book.positions.values() if row.user_id == "ada")
    book.apply_receipt(position.id, OrderReceipt(ok=True, order_id="o", filled_qty=10, average_price=51, remaining_qty=0), now_ms=16_000)
    sell_quote = point(20_000, 58, 57, 59, 180, 140, 140)
    book.short_run.observe(point(18_000, 59, 58, 59, 230, 80, 110))
    request = book.evaluate("ada", sell_quote)
    assert request is None
    shown = book.signals[("ada", "KXTEST")]
    assert shown["exit_price"] == 57
    book.settings["ada"]["auto_exit"] = True
    position.rules["auto_exit"] = True
    book._signal_action.pop(("ada", "KXTEST"), None)
    request = book.evaluate("ada", point(22_000, 58, 57, 59, 160, 160, 160))
    if shown["action"] == "SELL" or book.signals[("ada", "KXTEST")]["action"] == "SELL":
        assert request is not None
        assert request.action == "sell"
        assert request.price == 57
