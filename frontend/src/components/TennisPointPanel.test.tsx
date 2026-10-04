import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renderToStaticMarkup } from 'react-dom/server';
import { TennisPointPanel } from './TennisPointPanel';
import type { MatchCard, TennisPoint } from '../lib/types';

const match: MatchCard = {match_id:'m', player_a:'Alice', player_b:'Beatrice', market_ticker:'T',
  market_status:'OPEN', analysis_mode:'TENNIS_ENHANCED', display_state:'SEARCHING_FOR_ENTRY',
  observation_remaining_ms:0, observation_remaining_display:'00:00', signals_emitted:0,
  cooldown_remaining_ms:0, tennis:{available:true, match_score:'0-0', game_score:'3-2', point_score:'30-0', server:'A'}};
const points: TennisPoint[] = [2,3].map(seq => ({seq, set:1, game:6, number:seq-1,
  winner:'B', next_server:'A', score:'0-30', tiebreak:false}));

test('live panel shows score, named server, and the actual last two point winners', () => {
  const html=renderToStaticMarkup(<TennisPointPanel match={{...match, points:{available:true,
    note:'Live point history connected', last_seq:3, history:points,
    analysis:{last_two:points, pattern:'TWO POINTS WON IN A ROW', leader:'B', ready:true,
      has_gap:false, revised:false, tracked_points:2, summary:'Two points won'}}}}/>);
  assert.match(html,/Games 3-2/);
  assert.match(html,/Points 30-0/);
  assert.match(html,/Server: Alice/);
  assert.match(html,/Beatrice won the last two points/);
  assert.match(html,/Recent point history/);
});

test('score-only feed cannot display an invented point trend', () => {
  const html=renderToStaticMarkup(<TennisPointPanel match={match}/>);
  assert.match(html,/Waiting for live point history/);
  assert.match(html,/Points tracked: 0/);
  assert.doesNotMatch(html,/won the last two points/);
});
