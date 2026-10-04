import type { MatchCard } from '@/lib/types';

export function TennisPointPanel({ match }: { match: MatchCard }) {
  const points = match.points;
  const analysis = points?.analysis;
  const player = (side: string | null | undefined) => side === 'A' ? match.player_a : side === 'B' ? match.player_b : 'Unknown';
  const text = (field: string) => typeof match.tennis?.[field] === 'string' ? String(match.tennis[field]) : 'Unavailable';
  return (
    <div className="mt-4 border border-white/10 bg-ink-950/50 p-3 text-sm">
      <p className="font-mono text-xs uppercase tracking-wider text-signal-mint">Live score &amp; last two points</p>
      <p className="mt-2">Sets {text('match_score')} · Games {text('game_score')} · Points {text('point_score')}</p>
      <p>Server: {player(typeof match.tennis?.server === 'string' ? match.tennis.server : null)}</p>
      <p className="mt-2 text-mist/60">{points?.note || 'Waiting for live point history'}</p>
      <p>Points tracked: {analysis?.tracked_points ?? 0}</p>
      {analysis?.last_two && analysis.last_two.length > 0 && <ol className="mt-2 space-y-1">
        {analysis.last_two.map(point => <li key={point.seq}>Set {point.set ?? '?'} · Game {point.game ?? '?'} · Point {point.number ?? '?'}: {player(point.winner)} won · {point.score || 'Score unavailable'}{point.tiebreak ? ' · Tiebreak' : ''}</li>)}
      </ol>}
      <p className="mt-2">{analysis?.ready ? analysis.leader ? `${player(analysis.leader)} won the last two points` : 'Players split the last two points' : analysis?.summary || 'Waiting for two verified points'}</p>
      {analysis?.has_gap && <p className="text-signal-amber">Point history has a gap; waiting for recovery.</p>}
      {analysis?.revised && <p className="text-signal-amber">The provider revised the point order; analysis is waiting.</p>}
      {!!points?.history.length && <details className="mt-3">
        <summary className="cursor-pointer text-mist/70">Recent point history</summary>
        <ol className="mt-2 max-h-48 overflow-y-auto space-y-1">
          {points.history.map(point => <li key={point.seq}>Set {point.set ?? '?'} · Game {point.game ?? '?'} · Point {point.number ?? '?'} · {point.winner ? `${player(point.winner)} won` : 'Winner unavailable'} · {point.score || '—'}</li>)}
        </ol>
      </details>}
    </div>
  );
}
