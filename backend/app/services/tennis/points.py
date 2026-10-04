"""Authoritative point history; score snapshots never invent missing points."""

from dataclasses import dataclass


def orient_points(rows, flip=False):
    def side(value):
        if value not in (1, 2):
            return None
        return ('B' if value == 1 else 'A') if flip else ('A' if value == 1 else 'B')
    result = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get('seq')) is not int or row['seq'] < 1:
            continue
        score = row.get('score') if isinstance(row.get('score'), dict) else {}
        a, b = score.get('p1'), score.get('p2')
        result.append({
            'seq': row['seq'], 'set': row.get('set'), 'game': row.get('game'),
            'number': row.get('number'), 'winner': side(row.get('winner')),
            'next_server': side(row.get('server')), 'tiebreak': bool(row.get('tiebreak')),
            'score': f'{b}-{a}' if flip and a is not None and b is not None else
                     f'{a}-{b}' if a is not None and b is not None else None,
            'ts': row.get('ts'), 'serve': row.get('serve'), 'outcome': row.get('outcome'),
        })
    return result


@dataclass
class TennisPointTracker:
    match_id: str

    def __post_init__(self):
        self.events = {}
        self.source_match_id = None
        self.basis = 'live'
        self.available = False
        self.note = 'Waiting for live point history'
        self.quality = 'unknown'
        self.analysis = self.analyze()
        self.analyses = []
        self.updated_at_ms = None

    @property
    def last_seq(self):
        return max(self.events, default=0)

    @property
    def resume_seq(self):
        cursor = 0
        while cursor + 1 in self.events:
            cursor += 1
        return cursor

    @staticmethod
    def order(event):
        return (event.get('set') or 0, event.get('game') or 0,
                event.get('number') if event.get('number') is not None else -1, event['seq'])

    def update(self, state, now):
        self.updated_at_ms = now
        self.available = bool(state and state.available and state.point_feed_available)
        self.note = state.point_feed_note if state and state.available else 'Live tennis feed unavailable'
        if state and state.available:
            self.quality = state.point_feed_quality
        added = []
        if state and state.available and state.point_events:
            if self.source_match_id != state.match_external_id or self.basis != state.point_feed_basis:
                self.events.clear()
                self.analyses.clear()
            self.source_match_id = state.match_external_id
            self.basis = state.point_feed_basis
            self.quality = state.point_feed_quality
            for event in sorted(state.point_events, key=self.order):
                if event['seq'] in self.events:
                    continue
                self.events[event['seq']] = event
                added.append(event)
                self.analysis = self.analyze()
                self.analyses.append(dict(self.analysis, trigger_seq=event['seq']))
            self.analyses = self.analyses[-100:]
        self.analysis = self.analyze()
        return added

    def analyze(self):
        ordered = sorted(self.events.values(), key=self.order)
        # The first love-love opener is a state, not a played point.
        played = [e for e in ordered if not (e['seq'] == 1 and e.get('number') == 0 and e.get('winner') is None)]
        last_two = played[-2:]
        seqs = sorted(self.events)
        gaps = bool(seqs and (seqs[0] != 1 or any(b != a + 1 for a, b in zip(seqs, seqs[1:]))))
        positions = [(e.get('set'), e.get('game'), e.get('number')) for e in ordered]
        revised = self.quality == 'revised' or len(positions) != len(set(positions))
        recent_games = {(e.get('set'), e.get('game')) for e in last_two}
        order_known = all(e.get('set') is not None and e.get('game') is not None and e.get('number') is not None
                          for e in played if (e.get('set'), e.get('game')) in recent_games)
        complete = self.available and len(last_two) == 2 and not gaps and not revised and order_known and all(e.get('winner') in ('A', 'B') for e in last_two)
        pattern = 'WAIT'
        leader = None
        if complete:
            if last_two[0]['winner'] == last_two[1]['winner']:
                pattern, leader = 'TWO POINTS WON IN A ROW', last_two[-1]['winner']
            else:
                pattern = 'SPLIT LAST TWO POINTS'
        return {'last_two': last_two, 'pattern': pattern, 'leader': leader,
                'ready': complete, 'has_gap': gaps, 'revised': revised,
                'tracked_points': len(played), 'note': self.note,
                'summary': ('Last two points won by the same player' if leader else
                            'Players split the last two points' if complete else
                            'Waiting for two verified points with complete order and coverage')}

    def to_dict(self):
        return {'available': self.available, 'source_match_id': self.source_match_id,
                'basis': self.basis, 'quality': self.quality, 'last_seq': self.last_seq,
                'resume_seq': self.resume_seq,
                'updated_at_ms': self.updated_at_ms, 'analysis': self.analysis,
                'history': sorted(self.events.values(), key=self.order)[-50:],
                'analysis_history': self.analyses, 'note': self.note}
