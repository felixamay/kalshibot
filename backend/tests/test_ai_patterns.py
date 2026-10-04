import asyncio
import json
import time
from unittest.mock import AsyncMock
import httpx
import pytest
from app.config import Settings
from app.services.signals.ai_patterns import (GPTPatternAnalyst, PatternAnalysis, HybridDecisionEngine,
    HybridPatternDecisionEngine, needs_second_opinion, update_outcomes, performance_report)
from app.services.signals.engine import SignalEngine
from app.services.signals.patterns import PatternAssessment
from app.services.market.analyzer import MarketState
from app.services.tennis.service_games import ServeBlock


def output(**changes):
    return dict(pattern_name='PULLBACK_RECOVERY', favored_side='PLAYER_A', pattern_stage='DEVELOPING',
        pattern_confidence=90, entry_score=90, recommendation='ENTRY_SIGNAL', ideal_entry_low=.48,
        ideal_entry_high=.52, maximum_entry=.53, expected_pattern_move=.03, pattern_invalidation_price=.45,
        reasons=['Observed recovery'], risks=['Small sample'], requires_second_opinion=False,
        repeating=True, statistical_meaning='DESCRIPTIVE_ONLY', market_overreacting=False,
        market_underreacting=True, market_lagging_tennis=True, **changes)


def settings(**kw):
    return Settings(_env_file=None, openai_api_key='test', **kw)


def market(**kw):
    return MarketState('T', yes_bid=49, yes_ask=50, depth_yes=5000, depth_no=5000,
                       last_update_ms=time.time()*1000, **kw)


@pytest.mark.asyncio
async def test_responses_schema_and_no_tools():
    async def handler(request):
        body=json.loads(request.content)
        assert request.url.path == '/v1/responses'
        assert body['model'] == 'gpt-6-luna'
        assert body['text']['format']['strict']
        assert 'tools' not in body and not body['store']
        return httpx.Response(200, json={'status':'completed', 'output':[{'type':'message', 'content':[{'type':'output_text','text':json.dumps(output())}]}]})
    analyst=GPTPatternAnalyst(settings(), httpx.MockTransport(handler))
    assert (await analyst.analyze({'serve_block_id':'b'}, 'gpt-6-luna')).entry_score == 90


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['invalid','refused','rate_limited','unavailable','timeout'])
async def test_api_failures_rejected(mode):
    async def handler(request):
        if mode == 'timeout':
            await asyncio.sleep(.1)
        if mode in ('rate_limited','unavailable'):
            return httpx.Response(429 if mode == 'rate_limited' else 503)
        return httpx.Response(200, json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text' if mode=='invalid' else 'refusal','text':'{}'}]}]})
    analyst=GPTPatternAnalyst(settings(openai_request_timeout_seconds=.01), httpx.MockTransport(handler))
    with pytest.raises(Exception):
        await analyst.analyze({}, 'gpt-6-luna')


def test_validation_and_local_vetoes():
    with pytest.raises(ValueError):
        PatternAnalysis.model_validate(dict(output(), ideal_entry_low=.8))
    with pytest.raises(ValueError):
        PatternAnalysis.model_validate(dict(output(), pattern_confidence=float('nan')))
    hybrid=HybridPatternDecisionEngine(settings())
    def view():
        return PatternAssessment(decision='PATTERN_ENTRY_SIGNAL', entry_score=90, maximum_entry_price=55, player_side='YES')
    ai={'status':'VALID','output':output()}
    m=market(); m.yes_ask=60
    assert hybrid.decide(view(), ai, m, 'PLAYER_A')['final_decision']=='PATTERN_ALREADY_ADVANCED'
    m=market(); m.yes_bid=40
    assert hybrid.decide(view(), ai, m, 'PLAYER_A')['final_decision']=='PATTERN_WATCH'
    v=view(); v.decision='PATTERN_WATCH'
    assert hybrid.decide(v, ai, market(), 'PLAYER_A')['final_decision']=='PATTERN_WATCH'
    assert hybrid.decide(view(), {'status':'UNAVAILABLE'}, market(), 'PLAYER_A')['final_decision']=='PATTERN_ENTRY_SIGNAL'


def test_selective_deep_model():
    snapshot={'quantitative':{'entry_score':90,'decision':'PATTERN_ENTRY_SIGNAL'}, 'momentum':{'divergence':0},'kalshi':{'unusual_conditions':False}}
    assert not needs_second_opinion(PatternAnalysis.model_validate(output()), snapshot, settings())
    assert needs_second_opinion(PatternAnalysis.model_validate(dict(output(),pattern_confidence=77)), snapshot, settings())
    assert needs_second_opinion(PatternAnalysis.model_validate(dict(output(),entry_score=60)), snapshot, settings())
    assert needs_second_opinion(PatternAnalysis.model_validate(output()), dict(snapshot,deep_requested=True), settings())


@pytest.mark.asyncio
async def test_failure_fallback_and_stale_response_without_locking_market(monkeypatch):
    engine=SignalEngine(settings())
    ctx=engine.register_match(match_id='m', player_a='A',player_b='B',tournament=None,market_ticker='T',market_db_id='t')
    block=ServeBlock('b','m',0,1,['A','B'],[{},{}])
    ctx.tracker.blocks=[block]; ctx.latest_reasoning={'block_id':'b'}; ctx.ai_request_id='r'
    engine.snap.analyzers['T'].state=market()
    snapshot={'analysis_request_id':'r','serve_block_id':'b','market_price_at_request':.495,
              'requested_at_ms':time.time()*1000,'match':{'yes_side':'PLAYER_A'}}
    monkeypatch.setattr(engine, '_persist_ai', AsyncMock())
    gate=asyncio.Event()
    async def analyze(*args):
        assert not engine._lock.locked()
        await gate.wait()
        return PatternAnalysis.model_validate(output())
    monkeypatch.setattr(engine.ai_analyst, 'analyze', analyze)
    task=asyncio.create_task(engine._run_ai(ctx,block,snapshot,True))
    await asyncio.sleep(0)
    engine.snap.analyzers['T'].state.yes_ask=70
    gate.set(); await task
    assert ctx.ai_analysis['status']=='STALE'
    assert not engine.snap.signals
    monkeypatch.setattr(engine.ai_analyst,'analyze', AsyncMock(side_effect=httpx.ConnectError('offline')))
    await engine._run_ai(ctx,block,snapshot,True)
    assert ctx.ai_analysis['message']=='AI ANALYSIS TEMPORARILY UNAVAILABLE'
    await engine.on_market_update('T',yes_bid=49,yes_ask=50)
    assert engine.snap.analyzers['T'].state.yes_ask==50
    await engine.close()


def test_observed_outcomes_and_confidence_calibration():
    row={'timestamp':0,'status':'VALID','output':output(), 'input_features':{'match':{'yes_side':'PLAYER_A'}},
         'price_at_analysis':.5,'horizon_prices':{},'maximum_favorable_movement':0,'maximum_adverse_movement':0}
    for sec,price in [(5,.51),(15,.49),(30,.54),(60,.55)]:
        update_outcomes([row],price,sec*1000,1000)
    assert row['horizon_prices']=={'5':.51,'15':.49,'30':.54,'60':.55}
    assert row['pattern_success']
    assert row['maximum_adverse_movement']==pytest.approx(-.01)
    assert performance_report([row])['by_confidence_bucket']['90-100']['hit_rate']==1
    missing=dict(row,timestamp=0,evaluation_complete=False,horizon_prices={})
    update_outcomes([missing],.6,70000,1000)
    assert missing['pattern_success'] is None


def _book(**changes):
    base = dict(price=0.50, spread=1, imbalance=0.44, depth_yes=5000, depth_no=2000, momentum=0.2, time=1_000_000)
    base.update(changes)
    return base


def _view(**changes):
    row = output()
    row.update(changes)
    return row


def _gpt(**changes):
    row = dict(status='VALID', output=_view(), price_at_snapshot=0.50, snapshot_time=999_000, response_time=999_500)
    row.update(changes)
    return row


def _call(engine, gpt, book, yes_side='PLAYER_A'):
    return engine.combine(gpt, book, match_id='m1', player_a='Ann', player_b='Bea', yes_side=yes_side)


def test_hybrid_yes_needs_kalshi_and_obeys_the_sound_rules():
    engine = HybridDecisionEngine(settings())
    first = _call(engine, _gpt(), _book())
    assert first['final'] == 'YES'
    assert first['player_name'] == 'Ann'
    assert first['pattern'] == 'Pullback Recovery'
    assert first['play_sound'] is True
    assert first['gpt_snapshot_time'] == 999_000
    assert first['kalshi_current_price'] == 0.50
    held = _call(engine, _gpt(), _book(time=1_005_000))
    assert held['final'] == 'YES' and held['play_sound'] is False
    other = _call(engine, _gpt(output=_view(favored_side='PLAYER_B')), _book(imbalance=-0.44, time=1_006_000))
    assert other['final'] == 'YES' and other['player_name'] == 'Bea' and other['play_sound'] is True
    cancelled = _call(engine, _gpt(output=_view(favored_side='PLAYER_B', recommendation='AVOID', pattern_stage='BROKEN')), _book(time=1_007_000))
    assert cancelled['final'] == 'NO' and cancelled['play_sound'] is False
    replay = _call(engine, _gpt(output=_view(favored_side='PLAYER_B')), _book(imbalance=-0.44, time=1_008_000))
    assert replay['final'] == 'YES' and replay['play_sound'] is False
    waited = _call(engine, _gpt(output=_view(recommendation='WAIT')), _book(time=1_009_000))
    assert waited['final'] == 'WAIT'
    again = _call(engine, _gpt(output=_view(favored_side='PLAYER_B')), _book(imbalance=-0.44, time=1_006_000 + 21_000))
    assert again['final'] == 'YES' and again['play_sound'] is True


def test_gpt_without_kalshi_confirmation_is_watch_and_a_late_window_is_do_not_chase():
    engine = HybridDecisionEngine(settings())
    watch = _call(engine, _gpt(), _book(imbalance=0))
    assert watch['final'] == 'WATCH'
    assert watch['play_sound'] is False
    alone = _call(engine, _gpt(), _book(depth_yes=0, depth_no=0, imbalance=0.8))
    assert alone['final'] == 'WATCH'
    chase = _call(engine, _gpt(price_at_snapshot=0.70), _book(price=0.70, time=1_100_000))
    assert chase['final'] == 'DO NOT CHASE'
    mature = _call(engine, _gpt(output=_view(pattern_stage='MATURE', recommendation='DO_NOT_CHASE'), price_at_snapshot=0.70), _book(price=0.70, time=1_200_000))
    assert mature['final'] == 'DO NOT CHASE'


def test_stale_gpt_cannot_create_yes():
    engine = HybridDecisionEngine(settings())
    stale = _call(engine, _gpt(status='STALE'), _book())
    assert stale['final'] == 'WAIT'
    assert stale['message'] == 'GPT ANALYSIS STALE'
    assert stale['play_sound'] is False
    moved = _call(engine, _gpt(price_at_snapshot=0.50), _book(price=0.60))
    assert moved['final'] == 'WAIT'
    assert moved['message'] == 'GPT ANALYSIS STALE'
    assert moved['kalshi_price_at_snapshot'] == 0.50
    assert moved['kalshi_current_price'] == 0.60


@pytest.mark.asyncio
async def test_gpt_analysis_does_not_block_kalshi_updates(monkeypatch):
    engine = SignalEngine(settings())
    ctx = engine.register_match(match_id='m', player_a='A', player_b='B', tournament=None, market_ticker='T', market_db_id='t')
    block = ServeBlock('b', 'm', 0, 1, ['A', 'B'], [{}, {}])
    ctx.tracker.blocks = [block]
    ctx.latest_reasoning = {'block_id': 'b'}
    ctx.ai_request_id = 'r'
    engine.snap.analyzers['T'].state = market()
    snapshot = {'analysis_request_id': 'r', 'serve_block_id': 'b', 'market_price_at_request': .495,
                'requested_at_ms': time.time() * 1000, 'match': {'yes_side': 'PLAYER_A'}}
    monkeypatch.setattr(engine, '_persist_ai', AsyncMock())
    started = asyncio.Event()
    release = asyncio.Event()

    async def analyze(*args):
        started.set()
        await release.wait()
        return PatternAnalysis.model_validate(output())

    monkeypatch.setattr(engine.ai_analyst, 'analyze', analyze)
    task = asyncio.create_task(engine._run_ai(ctx, block, snapshot, True))
    await started.wait()
    began = time.perf_counter()
    await engine.on_market_update('T', yes_bid=51, yes_ask=52)
    assert time.perf_counter() - began < 0.5
    assert engine.snap.analyzers['T'].state.yes_ask == 52
    release.set()
    await task
    await engine.close()
