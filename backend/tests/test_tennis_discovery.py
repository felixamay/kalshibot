import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
import pytest
from app.services.kalshi.tennis_detector import TennisMarketDetector
from app.services.kalshi.client import KalshiReadOnlyClient
from app.services.market.orchestrator import MarketOrchestrator
from app.services.signals.engine import SignalEngine
from app.config import Settings


def market(ticker='KXATPMATCH-X-A', title='Alice vs Beatrice', **kwargs):
    return {'ticker': ticker, 'event_ticker':'E', 'title': title, 'status':'active',
            'occurrence_datetime': (datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat(), **kwargs}


@pytest.mark.parametrize('metadata', [{'ticker':'KXATPMATCH-X'}, {'ticker':'KXWTAMATCH-X'}, {'category':'ITF'},
    {'title':'Challenger singles'}, {'sport':'tennis'}, {'title':'Wimbledon'}, {'title':"Women's tennis"}])
def test_metadata_detection(metadata):
    assert TennisMarketDetector().detects(metadata)


@pytest.mark.parametrize('title', ['Alice vs Beatrice', 'Alice v Beatrice', 'Alice - Beatrice', 'Will Alice beat Beatrice?'])
def test_players(title):
    assert TennisMarketDetector().players(market(title=title)) == ('Alice', 'Beatrice')


def test_group_and_states():
    detector = TennisMarketDetector()
    m = market()
    assert detector.group_key(m) == detector.group_key(market(ticker='KXATPMATCH-X-B', title='Beatrice vs Alice'))
    assert detector.state(m, time.time()*1000) == 'LIVE'
    assert detector.state({**m, 'status':'closed'}, time.time()*1000) == 'ENDED'
    assert detector.state({**m, 'occurrence_datetime':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}, time.time()*1000) == 'UPCOMING'
    assert detector.state({**m, 'occurrence_datetime':None}, time.time()*1000) == 'UNKNOWN'


@pytest.mark.asyncio
async def test_pagination_no_caps(monkeypatch):
    c = KalshiReadOnlyClient(Settings(_env_file=None))
    c.get_series_list = AsyncMock(return_value={'series':[{'ticker':'NEW', 'sport':'tennis'}]})
    c.get_events = AsyncMock(side_effect=[{'events':[{'event_ticker':'E','series_ticker':'NEW'}], 'cursor':'next'}, {'events':[]}])
    c.get_markets = AsyncMock(side_effect=[{'markets':[], 'cursor':'next'}, {'markets':[market(ticker='NEW-X')]}])
    found = await c.search_tennis_markets()
    assert len(found) == 1
    assert c.get_markets.await_count == 2
    assert c.discovery_health['complete']


@pytest.mark.asyncio
async def test_reconciliation_restores_missed_and_groups_and_removes_closed():
    engine = SignalEngine(Settings(_env_file=None))
    engine.restore_match_history = AsyncMock()
    o = MarketOrchestrator(engine)
    o.tennis.list_live_matches = AsyncMock(return_value=[])
    o.ws = AsyncMock()
    o.client.search_tennis_markets = AsyncMock(return_value=[market(), market(ticker='KXATPMATCH-X-B', title='Beatrice vs Alice')])
    await o._discover()
    assert engine.dashboard_payload()['live_match_count'] == 1
    assert len(engine.dashboard_payload()['matches'][0]['market_ids']) == 2
    await o._discover()
    assert engine.dashboard_payload()['live_match_count'] == 1
    o.client.search_tennis_markets.return_value = []
    await o._discover()
    assert engine.dashboard_payload()['live_match_count'] == 0
    o.client.search_tennis_markets.return_value = [market()]
    await o._discover()
    assert engine.dashboard_payload()['live_match_count'] == 1
    await o.client.close()
    await o.tennis.close()

@pytest.mark.asyncio
async def test_reconnect_requests_reconciliation_and_clears_old_books():
    from app.core.enums import ConnectionStatus
    engine = SignalEngine(Settings(_env_file=None))
    o = MarketOrchestrator(engine)
    o._books['T'] = {'yes':{50:5}, 'no':{50:5}}
    await o._on_ws_status(ConnectionStatus.CONNECTED)
    assert o._reconcile.is_set()
    assert not o._books
    await o.client.close()
    await o.tennis.close()


@pytest.mark.asyncio
async def test_lifecycle_close_immediately_removes_match():
    engine = SignalEngine(Settings(_env_file=None))
    engine.register_match(match_id='m', player_a='Alice', player_b='Beatrice', tournament=None, market_ticker='T', market_db_id='t')
    o = MarketOrchestrator(engine)
    await o._on_ws_message({'type':'market_lifecycle_v2', 'msg':{'market_ticker':'T', 'event_type':'closed'}})
    assert engine.dashboard_payload()['live_match_count'] == 0
    await o.client.close()
    await o.tennis.close()


def test_other_sports_and_unknown_names_are_not_fabricated():
    detector = TennisMarketDetector()
    assert not detector.detects({'sport':'golf', 'title':'US Open'})
    assert detector.players({'title':'Alice to win'}) == ('Alice','Opponent unavailable')


def test_doubles_yes_orientation_ignores_slash_spacing():
    detector = TennisMarketDetector()
    assert detector.players({'title':'Melo / Zverev vs Cash / Glasspool', 'yes_sub_title':'Cash/Glasspool'}) == ('Cash/Glasspool','Melo / Zverev')


def test_doubles_full_yes_names_match_short_rule_team():
    detector = TennisMarketDetector()
    assert detector.players({'title':'Julian Cash / Lloyd Glasspool wins', 'yes_sub_title':'Julian Cash / Lloyd Glasspool',
        'rules_primary':'If Julian Cash / Lloyd Glasspool wins the Melo / Zverev vs Cash / Glasspool professional tennis match.'}) == ('Julian Cash / Lloyd Glasspool','Melo / Zverev')

@pytest.mark.asyncio
async def test_related_total_contract_does_not_supply_winner_price():
    engine = SignalEngine(Settings(_env_file=None))
    engine.restore_match_history = AsyncMock()
    o = MarketOrchestrator(engine)
    o.tennis.list_live_matches = AsyncMock(return_value=[])
    event = {'title':'Alice vs Beatrice', 'sport':'tennis'}
    o._market_meta = {
        'A-TOTAL':market(ticker='A-TOTAL', title='Total games over 21.5', _event=event),
        'Z-WINNER':market(ticker='Z-WINNER', title='Alice wins', yes_sub_title='Alice', _event=event),
    }
    await o._link_scoreboard()
    cards = engine.dashboard_payload()['matches']
    assert len(cards) == 1
    assert cards[0]['market_ticker'] == 'Z-WINNER'
    assert len(cards[0]['market_ids']) == 2
    await o.client.close()
    await o.tennis.close()
