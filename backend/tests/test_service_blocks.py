import time
from dataclasses import replace
from types import SimpleNamespace
import pytest
from app.config import Settings
from app.services.tennis.provider import TennisLiveState
from app.services.tennis.service_games import ServiceGameTracker, ServeBlock
from app.services.signals.tennis_reasoner import TennisPatternReasoner
from app.services.signals.engine import SignalEngine
from app.services.market.analyzer import MarketState


def score(games='0-0', server='A', **kwargs):
    return TennisLiveState('m', 'Alice', 'Beatrice', game_score=games, match_score='0-0', server=server, available=True, **kwargs)


def test_points_do_not_count_and_second_completed_game_closes_block():
    tracker = ServiceGameTracker('m')
    tracker.update(score(point_score='0-0'), 0)
    tracker.update(score(point_score='15-0'), 1)
    tracker.update(score(point_score='30-0'), 2)
    assert not tracker.games
    assert not tracker.update(score('1-0', 'B'), 3)
    assert len(tracker.pending) == 1
    assert tracker.update(score('1-1', 'A'), 4)[0].server_sequence == ['A', 'B']
    assert not tracker.pending
    tracker.update(score('2-1', 'B'), 5)
    assert len(tracker.pending) == 1
    tracker.update(score('2-1', 'B'), 6)
    assert len(tracker.games) == 3


def test_set_transition_and_tiebreak():
    tracker = ServiceGameTracker('m')
    tracker.update(score('5-3'), 0)
    end = score('0-0', 'B')
    end.match_score = '1-0'
    tracker.update(end, 1)
    assert len(tracker.games) == 1
    tracker.update(score('6-6', 'A', is_tiebreak=True), 2)
    tracker.update(score('6-6', 'B', point_score='2-1', is_tiebreak=True), 3)
    assert len(tracker.games) == 1


def test_correction_and_feed_gap_do_not_invent_games():
    tracker = ServiceGameTracker('m')
    tracker.update(score(), 0)
    tracker.update(score('1-0', 'B'), 1)
    tracker.update(score('0-0'), 2)
    assert not tracker.pending
    tracker.update(score('3-2', 'B'), 3)
    assert len(tracker.games) == 1


def test_authoritative_games_and_revision():
    tracker = ServiceGameTracker('m')
    log = [{'game_id': '1', 'server': 'A', 'winner': 'A'}, {'game_id': '2', 'server': 'B', 'winner': 'B'}]
    state = score(completed_service_games=log)
    assert len(tracker.update(state, 1)) == 1
    assert not tracker.update(state, 2)
    state.feed_revision = 1
    state.completed_service_games[0]['winner'] = 'B'
    new = tracker.update(state, 3)
    assert tracker.blocks[0].invalidated
    assert new[0].breaks_of_serve == 1


def block(a=80, b=60, change=0):
    games = [{'server': side, 'winner': side, 'stats': {'service_points_won': won, 'service_points_played': 100,
               'return_points_won': 50, 'return_points_played': 100}} for side, won in [('A', a), ('B', b)]]
    return ServeBlock('block', 'm', 0, 2, ['A', 'B'], games, price_change=change, holds_of_serve=2)


@pytest.mark.parametrize('expected,current,prior', [
    ('SERVICE DOMINANCE', block(), [block()]),
    ('SERVICE DETERIORATION', block(a=40), [block(a=90)]),
    ('RETURN PRESSURE', block(), []),
    ('MARKET LAG', block(a=95, b=45), []),
    ('UNDERREACTION', block(a=95, b=45), []),
    ('TENNIS MARKET DIVERGENCE', block(a=90, b=50, change=-2), []),
    ('POSSIBLE MARKET OVERREACTION', block(change=10), [block(change=1)]),
])
def test_reasoning_features(expected, current, prior):
    reasoner = TennisPatternReasoner(Settings(_env_file=None))
    result = reasoner.analyze(current, prior, SimpleNamespace(normal_volatility=1), MarketState('t'))
    assert expected in result['patterns']
    assert result['decision'] == 'WAIT'
    assert not result['reliable']


def test_no_fabricated_service_strength():
    b = block()
    for game in b.games:
        game['stats'] = {}
    result = TennisPatternReasoner(Settings(_env_file=None)).analyze(b, [], None, MarketState('t'))
    assert result['players']['A']['service_strength'] is None
    assert result['decision'] == 'WAIT'


@pytest.mark.asyncio
async def test_every_block_runs_reasoning_even_when_no_pattern(monkeypatch):
    engine = SignalEngine(Settings(initial_observation_seconds=0, _env_file=None))
    ctx = engine.register_match(match_id='m', player_a='Alice', player_b='Beatrice', tournament=None,
                               market_ticker='T', market_db_id='t')
    async def persist(*args):
        pass
    monkeypatch.setattr(engine, '_persist_block', persist)
    monkeypatch.setattr(engine, '_persist_games', persist)
    for games, server in [('0-0','A'), ('1-0','B'), ('1-1','A'), ('2-1','B'), ('2-2','A')]:
        ctx.tennis = score(games, server)
        await engine.on_market_update('T', yes_bid=49, yes_ask=51, depth_yes=1000, depth_no=1000)
    assert len(ctx.reasoning_history) == 2
    assert ctx.reasoning_history[-1]['previous_blocks_compared'] == 1
    assert ctx.latest_reasoning['decision'] == 'WAIT'
    assert not engine.dashboard_payload()['actionable_signals']

@pytest.mark.asyncio
async def test_durable_block_and_reasoning_restore():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.database import Base
    from app import models
    db = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with db.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(db, expire_on_commit=False)
    settings = Settings(_env_file=None, initial_observation_seconds=0)
    engine = SignalEngine(settings, session_factory=factory)
    ctx = engine.register_match(match_id='persisted', player_a='Alice', player_b='Beatrice', tournament=None, market_ticker='T', market_db_id='t')
    for games, server in [('0-0','A'), ('1-0','B'), ('1-1','A')]:
        ctx.tennis = score(games, server)
        await engine.on_market_update('T', yes_bid=49, yes_ask=51, depth_yes=1000, depth_no=1000)
    assert len(ctx.reasoning_history) == 1
    restart = SignalEngine(settings, session_factory=factory)
    restored = restart.register_match(match_id='persisted', player_a='Alice', player_b='Beatrice', tournament=None, market_ticker='T', market_db_id='t')
    await restart.restore_match_history(restored)
    assert len(restored.tracker.blocks) == 1
    assert len(restored.tracker.games) == 2
    assert len(restored.reasoning_history) == 1
    assert not restored.latest_reasoning['reliable']
    await db.dispose()


@pytest.mark.asyncio
async def test_tennis_updates_do_not_refresh_stale_market(monkeypatch):
    engine = SignalEngine(Settings(_env_file=None, initial_observation_seconds=0))
    ctx = engine.register_match(match_id='m', player_a='Alice', player_b='Beatrice', tournament=None, market_ticker='T', market_db_id='t')
    await engine.on_market_update('T', yes_bid=49, yes_ask=51, ts_ms=100)
    ctx.tennis = score()
    await engine.on_tennis_update('T')
    assert engine.snap.analyzers['T'].state.last_update_ms == 100

@pytest.mark.asyncio
async def test_block_candidate_requires_three_subsequent_market_updates(monkeypatch):
    from app.services.signals.patterns import PatternAssessment
    from unittest.mock import AsyncMock
    engine = SignalEngine(Settings(_env_file=None, initial_observation_seconds=0, pattern_eval_interval_ms=0))
    ctx = engine.register_match(match_id='m', player_a='Alice', player_b='Beatrice', tournament=None, market_ticker='T', market_db_id='t')
    monkeypatch.setattr(engine, '_persist_block', AsyncMock())
    monkeypatch.setattr(engine, '_persist_games', AsyncMock())
    def assess(ctx, market, now):
        return PatternAssessment(pattern_type='PULLBACK_RECOVERY', pattern_name='Pullback Recovery', occurrences=3,
            confidence=95, entry_score=95, player='Alice', player_side='YES', stage='EARLY', progress=20,
            maximum_entry_price=52, entry_zone_low=49, entry_zone_high=51, confirming=True,
            decision='PATTERN_ENTRY_SIGNAL' if ctx.confirmation_count >= 3 else 'PATTERN_DEVELOPING')
    monkeypatch.setattr(engine, '_assess_pattern', assess)
    await engine.on_market_update('T', yes_bid=49, yes_ask=50, depth_yes=5000, depth_no=1000, imbalance=.7)
    now = time.time()*1000
    ctx.tennis = score(completed_service_games=[
        {'game_id':'1','server':'A','winner':'A','start_time':now-1000,'end_time':now-500,
         'stats':{'service_points_won':90,'service_points_played':100}},
        {'game_id':'2','server':'B','winner':'B','start_time':now-500,'end_time':now,
         'stats':{'service_points_won':60,'service_points_played':100}}])
    await engine.on_tennis_update('T')
    assert ctx.latest_reasoning['reliable']
    assert not engine.dashboard_payload()['actionable_signals']
    for count in (1,2,3):
        await engine.on_market_update('T', yes_bid=49, yes_ask=50, depth_yes=5000, depth_no=1000, imbalance=.7)
        if count < 3:
            assert ctx.confirmation_count == count
            assert not engine.dashboard_payload()['actionable_signals']
    assert engine.dashboard_payload()['actionable_signals']
    assert ctx.latest_reasoning['confirmation_count'] == 3


def test_missing_market_reaction_does_not_claim_underreaction():
    current = block(a=95,b=45)
    current.price_change = None
    result = TennisPatternReasoner(Settings(_env_file=None)).analyze(current, [], None, MarketState('T'))
    assert 'UNDERREACTION' not in result['patterns']
    assert result['divergence'] is None
