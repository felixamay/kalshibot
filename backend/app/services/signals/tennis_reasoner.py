"""Block reasoning supplements the existing price-pattern engine; WAIT is normal."""
from statistics import mean


def weighted(values, weights):
    available = [(v, w) for v, w in zip(values, weights) if v is not None]
    return sum(v*w for v, w in available) / sum(w for _, w in available) if available else None


def service_score(games, side):
    observations = []
    for game in games:
        if game['server'] != side:
            continue
        stats = game.get('stats', {})
        factors, weights = [], []
        for won_key, total_key, weight in [('service_points_won', 'service_points_played', .55),
                ('first_serve_points_won', 'first_serve_points_played', .20),
                ('second_serve_points_won', 'second_serve_points_played', .15),
                ('break_points_saved', 'break_points_faced', .10)]:
            won, total = stats.get(won_key), stats.get(total_key)
            if won is not None and total and 0 <= won <= total:
                factors.append(100 * won / total)
                weights.append(weight)
        if factors:
            score = weighted(factors, weights)
            points = stats.get('service_points_played')
            faults = stats.get('double_faults')
            if faults is not None and points:
                score -= min(15, 100 * faults / points)
            observations.append(max(0, min(100, score)))
    return mean(observations) if observations else None


class TennisPatternReasoner:
    def __init__(self, settings):
        self.settings = settings

    def analyze(self, block, earlier, baseline, market, pattern=None, position=None, baseline_strength=None):
        earlier = [b for b in earlier if not b.invalidated]
        weights = self.settings.serve_block_weights
        players = {}
        for side in ('A', 'B'):
            latest = service_score(block.games, side)
            previous = service_score(earlier[-1].games, side) if earlier else None
            historic = [service_score(b.games, side) for b in earlier[:-1]]
            historic = [v for v in historic if v is not None]
            strength = weighted([latest, previous, mean(historic) if historic else None, (baseline_strength or {}).get(side)], weights)
            opponents = [g for g in block.games if g['server'] != side]
            returns = [(g['stats'].get('return_points_won'), g['stats'].get('return_points_played')) for g in opponents]
            pressure = [100*w/t for w, t in returns if w is not None and t]
            players[side] = {'service_strength': strength, 'return_pressure': mean(pressure) if pressure else None,
                'service_strength_change': latest - previous if latest is not None and previous is not None else None,
                'momentum': None}
        a, b = players['A']['service_strength'], players['B']['service_strength']
        return_a, return_b = players['A']['return_pressure'], players['B']['return_pressure']
        tennis_momentum = weighted([a-b if a is not None and b is not None else None,
            return_a-return_b if return_a is not None and return_b is not None else None], [.60, .40])
        for side in ('A', 'B'):
            players[side]['momentum'] = ('Improving' if players[side]['service_strength_change'] > 5 else
                'Weakening' if players[side]['service_strength_change'] < -5 else 'Stable') if players[side]['service_strength_change'] is not None else 'Unavailable'

        market_change = block.price_change
        window = market.windows.get(5000)
        market_momentum = weighted([
            max(-100, min(100, market_change*5)) if market_change is not None else None,
            max(-100, min(100, (market.microprice-market.mid)*10)) if market.last_update_ms else None,
            market.imbalance*100 if market.last_update_ms else None,
            max(-100, min(100, block.momentum*10)) if block.momentum is not None else None,
            max(-100, min(100, window.price_acceleration*10)) if window and window.count else None], [.45,.10,.20,.15,.10])
        divergence = tennis_momentum - market_momentum if tennis_momentum is not None and market_momentum is not None else None
        patterns = []
        for side in ('A', 'B'):
            p = players[side]
            if p['service_strength'] is not None and p['service_strength'] >= 75 and len(earlier) >= 1:
                patterns.append('SERVICE DOMINANCE')
            if p['service_strength_change'] is not None and p['service_strength_change'] <= -15:
                patterns.append('SERVICE DETERIORATION')
            if p['return_pressure'] is not None and p['return_pressure'] >= 45:
                patterns.append('RETURN PRESSURE')
        if block.breaks_of_serve:
            patterns.append('BREAK PRESSURE')
        normal = baseline.normal_volatility if baseline else None
        changes = [abs(b.price_change) for b in earlier if b.price_change is not None]
        reaction = mean(changes) if changes else normal
        if reaction and market_change is not None and abs(market_change) > max(3, reaction*3):
            patterns.append('POSSIBLE MARKET OVERREACTION')
        if tennis_momentum is not None and abs(tennis_momentum) >= 20 and market_change is not None and abs(market_change) < 1:
            patterns.extend(['UNDERREACTION', 'MARKET LAG'])
        if divergence is not None and abs(divergence) >= 20:
            patterns.append('TENNIS MARKET DIVERGENCE')
        if len(earlier) >= 2:
            matching = [b for b in earlier if b.server_sequence == block.server_sequence and b.holds_of_serve == block.holds_of_serve
                and b.breaks_of_serve == block.breaks_of_serve and b.price_change is not None]
            if len(matching) >= 2 and market_change is not None and all(b.price_change * market_change > 0 for b in matching[-2:]):
                patterns.append('REPEATED HOLD-OF-SERVE REACTION' if block.holds_of_serve == 2 else 'REPEATED BREAK-OF-SERVE REACTION')
        if 'SERVICE DOMINANCE' in patterns and block.low_price is not None and block.starting_market_price is not None and block.ending_market_price is not None:
            if block.low_price < block.starting_market_price - 1 and block.ending_market_price > block.low_price + 1:
                patterns.append('PULLBACK + RECOVERY')
        if pattern and pattern.pattern_type and pattern.decision != 'SEARCHING':
            patterns.append(pattern.pattern_type)
        reliable = bool(pattern and pattern.pattern_type and pattern.confidence >= self.settings.min_pattern_confidence
                        and pattern.occurrences >= self.settings.min_pattern_occurrences and not block.invalidated)
        confidence = pattern.confidence if reliable else min(60, pattern.confidence if pattern else 0)
        direction = 1 if not pattern or pattern.player_side != 'NO' else -1
        components = [confidence, players['A' if direction == 1 else 'B']['service_strength'],
            max(0, min(100, 50 + direction*divergence)) if divergence is not None else None,
            max(0, min(100, 50 + direction*market.imbalance*50)),
            max(0, min(100, 50 + direction*market_momentum/2)) if market_momentum is not None else None, None,
            max(0, min(100, (market.depth_yes + market.depth_no) / max(1, self.settings.min_liquidity_contracts)*50)),
            max(0, 100 - market.spread / max(1, self.settings.max_spread_cents)*100),
            max(0, 100 - block.market_volatility / max(1, self.settings.extreme_volatility)*100) if block.market_volatility is not None else None]
        score = weighted(components, self.settings.serve_entry_weights) if reliable else 0
        if a is None or b is None:
            confidence *= .85
            score *= .85
        reliable = reliable and confidence >= self.settings.min_pattern_confidence and score >= self.settings.pattern_watch_score

        decision = 'WATCH' if reliable else 'WAIT'
        if pattern and pattern.stage in ('LATE', 'COMPLETED'):
            decision = 'DO NOT CHASE'
            reliable = False
        health = None
        if position:
            side = 'A' if position.direction == 'YES' else 'B'
            change = players[side]['service_strength_change']
            broken_games = sum(g['server'] == side and g['winner'] != side for g in block.games)
            book_penalty = 25 if market.imbalance * (1 if side == 'A' else -1) < -.4 else 0
            health = max(0, min(100, 75 + (change or 0)*2 - 25*broken_games - book_penalty))
            decision = 'STOP / EXIT SIGNAL' if health <= 25 else 'SLIPPING' if health <= 40 else 'WATCH CLOSELY' if health <= 60 else 'HOLD'
        questions = {
            'what_happened': f'{block.holds_of_serve} holds and {block.breaks_of_serve} breaks',
            'better_player': ('A' if tennis_momentum > 0 else 'B' if tennis_momentum < 0 else 'Neither') if tennis_momentum is not None else 'Insufficient statistics',
            'kalshi_reaction_cents': market_change,
            'overreaction': 'POSSIBLE MARKET OVERREACTION' in patterns,
            'underreaction': 'UNDERREACTION' in patterns,
            'service_deterioration': 'SERVICE DETERIORATION' in patterns,
            'return_pressure': 'RETURN PRESSURE' in patterns,
            'repeated_pattern': bool(pattern and pattern.occurrences >= self.settings.min_pattern_occurrences),
            'entry_timing': pattern.stage if pattern else 'Unavailable',
            'tradeable_window_now': False,
        }
        return {'block_id' : block.block_id, 'end_time': block.end_time, 'players': players,
            'patterns': list(dict.fromkeys(patterns)), 'pattern_confidence': confidence, 'entry_score': score,
            'decision': decision, 'reliable': reliable, 'pattern_health': health,
            'tennis_momentum': tennis_momentum, 'market_momentum': market_momentum, 'market_price_change': market_change, 'divergence': divergence,
            'previous_blocks_compared': len(earlier), 'baseline_available': baseline is not None,
            'data_quality': 'DETAILED' if a is not None and b is not None else 'LIMITED',
            'reason': 'WAIT FOR ENTRY CONFIRMATION' if reliable else 'NO RELIABLE PATTERN — WAIT',
            'summary': f'{block.holds_of_serve} holds, {block.breaks_of_serve} breaks; server sequence {block.server_sequence}.',
            'confirmation_count': 0, 'questions': questions, 'entry_components': components}
