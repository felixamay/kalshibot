import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderToStaticMarkup } from 'react-dom/server';
import { MatchCard } from './MatchCard';
import type { MatchCard as Match } from '../lib/types';
const match: Match = {match_id:'m', player_a:'Alice', player_b:'Beatrice', market_ticker:'T', market_status:'OPEN',
  analysis_mode:'MARKET_ONLY', display_state:'SEARCHING_FOR_ENTRY', observation_remaining_ms:0,
  observation_remaining_display:'00:00', signals_emitted:0, cooldown_remaining_ms:0,
  kalshi_probability:0.56, service_games_analyzed:2, serve_block_progress:0, service_game_data_available:false};
test('missing service data is visible and both prices display', () => {
  const html = renderToStaticMarkup(<MatchCard match={match} serverNow={1000} snapshotServerTimeMs={1000}/>);
  assert.match(html, /SERVICE-GAME DATA UNAVAILABLE/);
  assert.match(html, /56¢ \/ 44¢/);
  assert.match(html, /Alice/);
  assert.match(html, /Beatrice/);
});
test('completed block displays WAIT and reasoning history', () => {
  const reasoning = {block_id:'b', decision:'WAIT', reason:'NO RELIABLE PATTERN — WAIT', patterns:[], pattern_confidence:0,
    entry_score:0, confirmation_count:0, summary:'Two holds'};
  const html = renderToStaticMarkup(<MatchCard match={{...match, service_game_data_available:true, serve_block_progress:1,
    latest_reasoning:reasoning, reasoning_history:[reasoning]}} serverNow={1000} snapshotServerTimeMs={1000}/>);
  assert.match(html, /WAITING FOR TWO COMPLETED SERVICE GAMES/);
  assert.match(html, /NO RELIABLE PATTERN/);
  assert.match(html, /Block 1/);
});
test('legacy observation values cannot display a five-minute study clock', () => {
  const html = renderToStaticMarkup(<MatchCard match={{...match, observation_remaining_ms:300000,
    observation_ends_ms:301000}} serverNow={1000} snapshotServerTimeMs={1000}/>);
  assert.doesNotMatch(html, /STUDYING FIRST 5 MINUTES/);
});

test('AI view and deterministic final decision are transparent', () => {
  const html = renderToStaticMarkup(<MatchCard match={{...match,
    ai_pattern_analysis:{status:'VALID',model:'gpt-6-luna',output:{pattern_name:'PULLBACK_RECOVERY',
      favored_side:'PLAYER_A',pattern_confidence:82,pattern_stage:'DEVELOPING',recommendation:'ENTRY_SIGNAL',reasons:[],risks:[]}},
    hybrid_decision:{quantitative_engine:'PATTERN_WATCH',final_decision:'WAIT',score:60,blockers:['Spread failed']}
  }} serverNow={1000} snapshotServerTimeMs={1000}/>);
  assert.match(html, /GPT-6 Luna/);
  assert.match(html, /AI View: ENTRY SIGNAL/);
  assert.match(html, /Final Decision: WAIT/);
  assert.match(html, /Spread failed/);
});

test('a confirmed YES shows both sources and the player', () => {
  const html = renderToStaticMarkup(<MatchCard match={{...match,
    hybrid_decision:{quantitative_engine:'PATTERN_ENTRY_SIGNAL', final_decision:'PATTERN_ENTRY_SIGNAL', score:84, blockers:[],
      signal:{final:'YES', player_name:'Alice', pattern:'Pullback Recovery', confidence:84, current_cents:53, alert_key:'m|Alice|YES',
        gpt_visual:{pattern:'Pullback Recovery', favors:'Alice', confidence:81, stage:'DEVELOPING'},
        kalshi_live:{bias:'Bullish', bid_pressure_pct:72, liquidity:'Good'}}}
  }} serverNow={1000} snapshotServerTimeMs={1000}/>);
  assert.match(html, /YES — Alice/);
  assert.match(html, /GPT VISUAL ANALYSIS/);
  assert.match(html, /KALSHI LIVE ANALYSIS/);
  assert.match(html, /Bid Pressure/);
  assert.match(html, /FINAL: YES — Alice/);
});
