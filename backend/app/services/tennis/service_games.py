"""Completed service games, never individual serves or tiebreak points."""
from dataclasses import dataclass, field, asdict
import re
from uuid import uuid4


def pair(value):
    found = re.fullmatch(r'\s*(\d+)\s*[-:]\s*(\d+)\s*', value or '')
    return tuple(map(int, found.groups())) if found else None


@dataclass
class ServiceGame:
    game_id: str
    server: str
    winner: str
    start_time: float
    end_time: float
    stats: dict = field(default_factory=dict)


@dataclass
class ServeBlock:
    block_id: str
    match_id: str
    start_time: float
    end_time: float
    server_sequence: list
    games: list
    starting_market_price: float | None = None
    ending_market_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    price_change: float | None = None
    player_a_service_result: list = field(default_factory=list)
    player_b_service_result: list = field(default_factory=list)
    points_played: int | None = None
    break_points: int | None = None
    breaks_of_serve: int = 0
    holds_of_serve: int = 0
    double_faults: int | None = None
    aces: int | None = None
    return_pressure: dict = field(default_factory=dict)
    market_volatility: float | None = None
    spread: float | None = None
    liquidity: float | None = None
    trade_flow: float | None = None
    orderbook_imbalance: float | None = None
    momentum: float | None = None
    pattern_features: dict = field(default_factory=dict)
    invalidated: bool = False

    def as_dict(self):
        return asdict(self)


class ServiceGameTracker:
    def __init__(self, match_id):
        self.match_id = match_id
        self.previous = None
        self.games = []
        self.pending = []
        self.blocks = []
        self.seen = set()
        self.available = False
        self.note = 'SERVICE-GAME DATA UNAVAILABLE'
        self.revision = 0
        self.game_started_at = None

    def update(self, state, now, market=None):
        if state is None or not state.available:
            self.available = False
            self.note = 'SERVICE-GAME DATA UNAVAILABLE'
            return []
        score, sets = pair(state.game_score), pair(state.match_score)
        current = (score, sets, state.server, getattr(state, 'is_tiebreak', False), now)
        previous = self.previous
        if self.game_started_at is None:
            self.game_started_at = now
        self.previous = current
        self.available = score is not None and state.server in ('A', 'B')
        self.note = 'Score/server only; detailed statistics unavailable' if self.available else 'SERVICE-GAME DATA UNAVAILABLE'
        explicit = getattr(state, 'completed_service_games', None)
        if explicit is not None:
            self.available = True
            if getattr(state, 'feed_revision', 0) != self.revision:
                self.revision = state.feed_revision
                self.pending.clear()
                for block in self.blocks:
                    block.invalidated = True
                # Corrections replace the authoritative game log; replay it deterministically.
                self.games.clear()
                self.seen.clear()
            added = []
            for raw in explicit:
                if raw.get('kind', 'service_game') != 'service_game' or raw.get('server') not in ('A', 'B') or raw.get('winner') not in ('A', 'B'):
                    continue
                gid = str(raw['game_id'])
                if gid not in self.seen:
                    added.append(ServiceGame(gid, raw['server'], raw['winner'], raw.get('start_time', now), raw.get('end_time', now), raw.get('stats', {})))
        else:
            added = []
            if not previous or not self.available or not previous[0] or previous[2] not in ('A', 'B'):
                return []
            old, old_sets, server, tiebreak, _ = previous
            started = self.game_started_at
            # 6-6 and explicit match tiebreaks never count as service games.
            if tiebreak or old == (6, 6) or current[3]:
                return []
            delta = tuple(score[i] - old[i] for i in (0, 1))
            winner = None
            if sets == old_sets and delta in ((1, 0), (0, 1)):
                winner = 'A' if delta[0] else 'B'
            elif sets and old_sets and sum(sets) == sum(old_sets) + 1 and score == (0, 0):
                side = 0 if sets[0] > old_sets[0] else 1
                if old[side] >= 5 and old[side] + 1 - old[1-side] >= 2:
                    winner = 'A' if side == 0 else 'B'
            elif score != old:
                # Ambiguous jump/correction: no invented intermediate games.
                self.pending.clear()
                self.game_started_at = now
                self.note = 'Feed gap/correction; incomplete block discarded'
                for block in self.blocks[-1:]:
                    block.invalidated = True
            if winner:
                gid = f'{old_sets}:{old}:{score}:{sets}'
                self.game_started_at = now
                if gid not in self.seen:
                    added = [ServiceGame(gid, server, winner, started, now)]
        completed = []
        for game in added:
            if game.game_id in self.seen:
                continue
            self.seen.add(game.game_id)
            self.games.append(game)
            self.pending.append(game)
            if len(self.pending) == 2:
                games = self.pending[:]
                self.pending.clear()
                block = ServeBlock(str(uuid4()), self.match_id, games[0].start_time, games[-1].end_time,
                    [g.server for g in games], [asdict(g) for g in games])
                for side in ('A', 'B'):
                    setattr(block, f'player_{side.lower()}_service_result', ['HOLD' if g.server == g.winner else 'BROKEN' for g in games if g.server == side])
                block.holds_of_serve = sum(g.server == g.winner for g in games)
                block.breaks_of_serve = 2 - block.holds_of_serve
                for key in ('points_played', 'break_points', 'aces', 'double_faults'):
                    values = [g.stats.get(key) for g in games]
                    if all(v is not None for v in values):
                        setattr(block, key, sum(values))
                self.blocks.append(block)
                completed.append(block)
        return completed
