"""Each user trades from their own Kalshi price rules."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.services.trading.store import load_user, save_user
from app.services.trading.strategy import (
    OrderReceipt,
    Quote,
    StrategyBook,
    choose_entry_side,
    default_settings,
    should_play_alert,
    stop_loss_price,
    take_profit_price,
    trailing_stop_price,
    weighted_average_price,
)


def quote(ask=55, bid=54, **overrides) -> Quote:
    data = dict(
        ticker="KXTEST",
        yes_bid=bid,
        yes_ask=ask,
        depth=500,
        status="OPEN",
        data_age_ms=100,
        live=True,
        label="A vs B",
        now_ms=1_000_000,
    )
    data.update(overrides)
    return Quote(**data)


def armed(**overrides):
    settings = default_settings()
    settings.update(auto_entry=True, auto_exit=True, entry_price=55, bet_amount=20, entry_type="YES")
    settings.update(overrides)
    return settings


class FakeGateway:
    def __init__(self, connected=True, receipt=None):
        self.connected = connected
        self.receipt = receipt or OrderReceipt(ok=True, order_id="ord-1", filled_qty=10, average_price=54, remaining_qty=0)
        self.sent = []

    async def submit(self, order):
        self.sent.append(order)
        return self.receipt


def test_two_users_keep_separate_settings():
    book = StrategyBook()
    book.replace_settings("user-a", armed(entry_price=55, bet_amount=20), None)
    book.replace_settings("user-b", armed(entry_price=40, bet_amount=5), None)
    book.replace_settings("user-a", armed(entry_price=33), None)
    assert book.settings["user-a"]["entry_price"] == 33
    assert book.settings["user-b"]["entry_price"] == 40
    assert book.settings["user-b"]["bet_amount"] == 5


@pytest.mark.asyncio
async def test_settings_persist_for_the_same_user_after_a_new_session():
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    async with factory() as session:
        await save_user(session, "user-a", armed(entry_price=55, bet_amount=20))
        await save_user(session, "user-b", armed(entry_price=40, bet_amount=5))
        await session.commit()
    async with factory() as session:
        first = await load_user(session, "user-a")
        second = await load_user(session, "user-b")
    assert first["entry_price"] == 55
    assert first["bet_amount"] == 20
    assert second["entry_price"] == 40
    async with factory() as session:
        await save_user(session, "user-a", armed(entry_price=31))
        await session.commit()
    async with factory() as session:
        assert (await load_user(session, "user-a"))["entry_price"] == 31
        assert (await load_user(session, "user-b"))["bet_amount"] == 5
    await db.dispose()


def test_entry_uses_the_ask_and_ignores_a_price_above_the_threshold():
    assert choose_entry_side("YES", 54, 55, 55) == ("YES", 55)
    assert choose_entry_side("YES", 40, 56, 55)[0] is None
    # Midpoint would be 50. The ask is still above the user's price.
    assert choose_entry_side("YES", 40, 60, 55)[0] is None
    book = StrategyBook()
    book.trading_connected = True
    book.replace_settings("user-a", armed(), None)
    assert book.evaluate("user-a", quote(ask=56, bid=55)) is None
    order = book.evaluate("user-a", quote(ask=55, bid=54))
    assert order is not None
    assert order.price == 55
    assert order.side == "YES"


def test_targets_use_the_actual_fill_not_the_requested_price():
    assert take_profit_price(54, 8) == pytest.approx(58.32)
    assert take_profit_price(50, 8) == pytest.approx(54)
    assert stop_loss_price(54, 4) == pytest.approx(51.84)
    assert stop_loss_price(50, 4) == pytest.approx(48)
    book = StrategyBook()
    book.trading_connected = True
    book.replace_settings("user-a", armed(take_profit_percent=8, stop_loss_percent=4), None)
    order = book.evaluate("user-a", quote(ask=55, bid=54))
    book.apply_receipt(order.position_id, OrderReceipt(ok=True, order_id="ord", filled_qty=20, average_price=54, remaining_qty=0))
    # 55 * 1.08 = 59.4. A 59¢ bid is a profit on the 54¢ fill and not on the request.
    assert book.evaluate("user-a", quote(ask=60, bid=59)) is not None
    book2 = StrategyBook()
    book2.trading_connected = True
    book2.replace_settings("user-a", armed(), None)
    opened = book2.evaluate("user-a", quote(ask=55, bid=54))
    book2.apply_receipt(opened.position_id, OrderReceipt(ok=True, order_id="ord", filled_qty=20, average_price=54, remaining_qty=0))
    assert book2.evaluate("user-a", quote(ask=53, bid=52.5)) is None
    stopped = book2.evaluate("user-a", quote(ask=52, bid=51))
    assert stopped is not None
    assert stopped.action == "sell"


def test_trailing_stop_tracks_the_peak_after_activation():
    book = StrategyBook()
    book.trading_connected = True
    book.replace_settings(
        "user-a",
        armed(entry_price=60, trailing_activation_percent=5, trailing_stop_percent=3, take_profit_percent=80, stop_loss_percent=50),
        None,
    )
    order = book.evaluate("user-a", quote(ask=50, bid=49))
    book.apply_receipt(order.position_id, OrderReceipt(ok=True, order_id="ord", filled_qty=10, average_price=50, remaining_qty=0))
    assert book.evaluate("user-a", quote(ask=54, bid=53)) is None
    position = next(iter(book.positions.values()))
    assert position.trailing_active
    assert position.peak_price == 53
    assert book.evaluate("user-a", quote(ask=61, bid=60)) is None
    assert position.peak_price == 60
    assert trailing_stop_price(60, 3) == pytest.approx(58.2)
    exited = book.evaluate("user-a", quote(ask=59, bid=58))
    assert exited is not None
    assert exited.action == "sell"
    assert position.status == "TRAILING EXIT TRIGGERED"


def test_partial_fills_use_the_weighted_average():
    assert weighted_average_price([(55, 10), (53, 10)]) == pytest.approx(54)
    book = StrategyBook()
    book.trading_connected = True
    book.replace_settings("user-a", armed(), None)
    order = book.evaluate("user-a", quote())
    book.apply_receipt(order.position_id, OrderReceipt(ok=True, order_id="ord", filled_qty=20, average_price=54, remaining_qty=5))
    position = book.positions[order.position_id]
    assert position.status == "PARTIALLY FILLED"
    assert position.entry_price == 54
    assert position.filled_qty == 20


def test_daily_loss_trade_cap_and_cooldown_block_entries():
    book = StrategyBook()
    book.trading_connected = True
    book.replace_settings("user-a", armed(max_trades_per_match=2, cooldown_seconds=60, max_daily_loss=25), None)
    book.daily_loss["user-a"] = 25
    assert book.evaluate("user-a", quote()) is None
    assert "daily loss" in book.last_reason[("user-a", "KXTEST")].lower()
    book.daily_loss["user-a"] = 0
    first = book.evaluate("user-a", quote())
    book.apply_receipt(first.position_id, OrderReceipt(ok=True, order_id="1", filled_qty=10, average_price=54, remaining_qty=0), now_ms=1_000_000)
    book.positions[first.position_id].closed_at_ms = 1_000_000
    second = book.evaluate("user-a", quote(now_ms=1_070_000))
    book.apply_receipt(second.position_id, OrderReceipt(ok=True, order_id="2", filled_qty=10, average_price=54, remaining_qty=0), now_ms=1_070_000)
    book.positions[second.position_id].closed_at_ms = 1_070_000
    assert book.evaluate("user-a", quote(now_ms=1_200_000)) is None
    assert "max trades" in book.last_reason[("user-a", "KXTEST")].lower()

    cooled = StrategyBook()
    cooled.trading_connected = True
    cooled.replace_settings("user-a", armed(cooldown_seconds=60), None)
    opened = cooled.evaluate("user-a", quote())
    cooled.apply_receipt(opened.position_id, OrderReceipt(ok=True, order_id="1", filled_qty=10, average_price=54, remaining_qty=0))
    cooled.book_close = cooled.positions[opened.position_id]
    cooled.mark_closed_clock(cooled.positions[opened.position_id], 1_000_000)
    cooled.positions[opened.position_id].status = "POSITION CLOSED"
    assert cooled.evaluate("user-a", quote(now_ms=1_010_000)) is None
    assert "cooldown" in cooled.last_reason[("user-a", "KXTEST")].lower()


def test_auto_entry_and_auto_exit_default_off_and_block_orders():
    book = StrategyBook()
    book.trading_connected = True
    book.replace_settings("user-a", default_settings(), None)
    assert book.evaluate("user-a", quote()) is None
    book.replace_settings("user-a", armed(auto_exit=False), None)
    order = book.evaluate("user-a", quote(ask=50, bid=49))
    book.apply_receipt(order.position_id, OrderReceipt(ok=True, order_id="1", filled_qty=10, average_price=50, remaining_qty=0))
    assert book.evaluate("user-a", quote(ask=70, bid=69)) is None
    assert book.positions[order.position_id].status == "POSITION OPEN"


def test_disconnect_and_alert_cooldown():
    book = StrategyBook()
    book.trading_connected = False
    book.replace_settings("user-a", armed(), None)
    assert book.evaluate("user-a", quote()) is None
    assert "disconnected" in book.last_reason[("user-a", "KXTEST")].lower()
    assert should_play_alert(None, 1_000) is True
    assert should_play_alert(1_000, 5_000) is False
    assert should_play_alert(1_000, 21_000) is True


def test_open_position_settings_are_not_replaced_until_the_user_confirms():
    book = StrategyBook()
    book.trading_connected = True
    book.replace_settings("user-a", armed(take_profit_percent=8), None)
    order = book.evaluate("user-a", quote())
    book.apply_receipt(order.position_id, OrderReceipt(ok=True, order_id="1", filled_qty=10, average_price=50, remaining_qty=0))
    pending = book.replace_settings("user-a", armed(take_profit_percent=20), None)
    assert pending["needs_confirmation"] is True
    assert book.positions[order.position_id].rules["take_profit_percent"] == 8
    book.replace_settings("user-a", armed(take_profit_percent=20), False)
    assert book.positions[order.position_id].rules["take_profit_percent"] == 8
    assert book.settings["user-a"]["take_profit_percent"] == 20
    book.replace_settings("user-b", armed(take_profit_percent=3), None)
    assert book.settings["user-b"]["take_profit_percent"] == 3
