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
  assert.match(html, /SERVE BLOCK: 1 \/ 2 GAMES COMPLETE/);
  assert.match(html, /NO RELIABLE PATTERN/);
  assert.match(html, /Block 1/);
});
