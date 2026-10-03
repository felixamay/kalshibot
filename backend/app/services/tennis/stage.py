"""Live-match stage for pattern suggestions.

The 5-minute observation clock is not used. A pattern may be suggested only
after the first serve, while the leading side is still at or under 85%, and
not in the last five serves when the winner is already clear. A new pattern
is made only after every two serves.
"""

from __future__ import annotations

import re
from typing import Any, Optional

LATE_GAME_PERCENT = 85.0
CLOSING_SERVES = 5
# The shortest tennis game is four serves. A game change is that many serves
# when the feed does not publish each point.
_SERVES_IN_COMPLETED_GAME = 4

_SCORE_PAIR = re.compile(r"(\d+)\s*-\s*(\d+)")
_POINT = {
    "0": 0,
    "15": 1,
    "30": 2,
    "40": 3,
    "ad": 4,
    "a": 4,
    "adv": 4,
    "advantage": 4,
}


def leading_percent(price_cents: Optional[float]) -> Optional[float]:
    """How far the favorite has pulled away. 50 is even. Above 85 is too late."""
    if price_cents is None:
        return None
    try:
        price = float(price_cents)
    except (TypeError, ValueError):
        return None
    if price <= 0 or price >= 100:
        return None
    return max(price, 100.0 - price)


def serve_fingerprint(tennis: Any) -> tuple[Any, ...]:
    """Score fields that move when a serve is played. The server name is not one of them."""
    recent = tuple(getattr(tennis, "recent_points", None) or [])
    return (
        getattr(tennis, "point_score", None),
        recent,
        getattr(tennis, "game_score", None),
        getattr(tennis, "set_score", None),
    )


def serves_added(previous: tuple[Any, ...], current: tuple[Any, ...], points_were_visible: bool) -> tuple[int, bool]:
    """How many serves the new score proves. The first snapshot adds none."""
    prev_point, prev_recent, prev_game, prev_set = previous
    point, recent, game, set_score = current
    points_visible = points_were_visible or bool(point) or bool(recent)
    if len(recent) > len(prev_recent):
        return len(recent) - len(prev_recent), True
    same_game = game == prev_game and set_score == prev_set
    if point and point != prev_point and same_game:
        return 1, True
    if point and point != prev_point and not same_game and (prev_point or points_were_visible):
        return 1, True
    if not points_were_visible and not prev_point and not prev_recent and not same_game:
        return _SERVES_IN_COMPLETED_GAME, points_visible
    return 0, points_visible


def pattern_serve_block(serves_seen: int, last_pattern_serve: int) -> Optional[str]:
    """A pattern is made on serve 2, 4, 6, … and not on the quotes in between."""
    advanced = serves_seen - last_pattern_serve
    if advanced >= 2 and advanced % 2 == 0:
        return None
    need = 1 if advanced % 2 == 1 else 2
    noun = "serve" if need == 1 else "serves"
    return (
        "A pattern is made only after every two serves. "
        f"{need} more {noun} before the next pattern."
    )


def suggestion_block(tennis: Any, price_cents: Optional[float]) -> Optional[str]:
    """Why a pattern must not be suggested. None means the early window is open."""
    if _before_first_serve(tennis):
        return (
            "The match has not had a first serve. Observation starts after that serve. "
            "No pattern is suggested yet."
        )
    percent = _game_percent(tennis, price_cents)
    if percent is not None and percent > LATE_GAME_PERCENT:
        return (
            f"Game percentage is {percent:.0f}%. "
            "No pattern is suggested once it passes 85%."
        )
    if _last_serves_and_winner_clear(tennis, price_cents):
        return (
            "The game is in the last 5 serves and the winner is clear. "
            "No pattern is suggested."
        )
    return None


def _game_percent(tennis: Any, price_cents: Optional[float]) -> Optional[float]:
    explicit = getattr(tennis, "game_percent", None) if tennis is not None else None
    if explicit is not None:
        try:
            return float(explicit)
        except (TypeError, ValueError):
            pass
    return leading_percent(price_cents)


def _before_first_serve(tennis: Any) -> bool:
    """True only when a score feed shows the match is still 0-0 before any point."""
    if tennis is None or not getattr(tennis, "available", False):
        return False
    if getattr(tennis, "recent_points", None):
        return False
    sets = _sets(tennis)
    games = _games(tennis)
    points = _points(tennis)
    if sets is None and games is None and points is None:
        return False
    sets = sets or (0, 0)
    games = games or (0, 0)
    points = points or (0, 0)
    return sets == (0, 0) and games == (0, 0) and points == (0, 0)


def _last_serves_and_winner_clear(tennis: Any, price_cents: Optional[float]) -> bool:
    if tennis is None or not getattr(tennis, "available", False):
        return False
    remaining = getattr(tennis, "serves_remaining", None)
    if remaining is not None:
        try:
            if int(remaining) > CLOSING_SERVES:
                return False
            return _winner_clear(tennis, price_cents)
        except (TypeError, ValueError):
            pass
    parsed = _min_points_for_leader(tennis)
    if parsed is None:
        return False
    points_left, clear = parsed
    return points_left <= CLOSING_SERVES and clear


def _winner_clear(tennis: Any, price_cents: Optional[float]) -> bool:
    favorite = leading_percent(price_cents)
    if favorite is not None and favorite > LATE_GAME_PERCENT:
        return True
    sets = _sets(tennis) or (0, 0)
    games = _games(tennis) or (0, 0)
    return _score_is_clear(sets, games)


def _min_points_for_leader(tennis: Any) -> Optional[tuple[int, bool]]:
    sets = _sets(tennis)
    games = _games(tennis)
    points = _points(tennis)
    if sets is None and games is None and points is None:
        return None
    sets_a, sets_b = sets or (0, 0)
    games_a, games_b = games or (0, 0)
    pts_a, pts_b = points or (0, 0)
    if (sets_a, games_a, pts_a) == (sets_b, games_b, pts_b):
        return None
    if (sets_a, games_a, pts_a) > (sets_b, games_b, pts_b):
        leader = (sets_a, games_a, pts_a, sets_b, games_b, pts_b)
    else:
        leader = (sets_b, games_b, pts_b, sets_a, games_a, pts_a)
    sets_l, games_l, pts_l, sets_t, games_t, pts_t = leader
    sets_needed_to = _sets_to_win(sets_a, sets_b, games_a, games_b, pts_a, pts_b)
    if max(sets_a, sets_b) >= sets_needed_to:
        return 0, True
    sets_left = sets_needed_to - sets_l
    if sets_left >= 2:
        return 24, False
    points_left = _points_to_win_set(games_l, games_t, pts_l, pts_t)
    return points_left, _score_is_clear((sets_l, sets_t), (games_l, games_t))


def _score_is_clear(sets: tuple[int, int], games: tuple[int, int]) -> bool:
    sets_l, sets_t = sets
    games_l, games_t = games
    if games_l - games_t >= 3:
        return True
    if sets_l > sets_t and games_l - games_t >= 2 and games_t <= 2:
        return True
    return False


def _sets_to_win(
    sets_a: int,
    sets_b: int,
    games_a: int,
    games_b: int,
    pts_a: int,
    pts_b: int,
) -> int:
    """Best of 3, unless a third set is already underway."""
    if max(sets_a, sets_b) >= 2 and (games_a or games_b or pts_a or pts_b or sets_a + sets_b >= 3):
        return 3
    return 2


def _points_to_win_set(games_l: int, games_t: int, pts_l: int, pts_t: int) -> int:
    if games_l >= 6 and games_l - games_t >= 2:
        return 0
    if games_l == 6 and games_t == 6:
        return 7
    if games_l == 6 and games_t == 5:
        games_needed = 1
    elif games_l >= 5 and games_l > games_t:
        games_needed = 1
    elif games_l >= 5 and games_l == games_t:
        games_needed = 2
    else:
        games_needed = max(0, 6 - games_l)
        if games_l + games_needed - games_t < 2:
            games_needed += 1
    if games_needed <= 0:
        return 0
    this_game = _points_to_win_game(pts_l, pts_t)
    return this_game + 4 * (games_needed - 1)


def _points_to_win_game(mine: int, theirs: int) -> int:
    steps = 0
    a, b = mine, theirs
    while not (a >= 4 and a - b >= 2):
        a += 1
        steps += 1
        if steps > 12:
            break
    return steps


def _sets(tennis: Any) -> Optional[tuple[int, int]]:
    match = _first_pair(getattr(tennis, "match_score", None))
    if match is not None:
        return match
    text = getattr(tennis, "set_score", None)
    if not text:
        return None
    pairs = _all_pairs(str(text))
    if len(pairs) >= 2:
        # Completed sets are every pair except the current games.
        won_a = sum(1 for a, b in pairs[:-1] if a > b)
        won_b = sum(1 for a, b in pairs[:-1] if b > a)
        return won_a, won_b
    return None


def _games(tennis: Any) -> Optional[tuple[int, int]]:
    game = _first_pair(getattr(tennis, "game_score", None))
    if game is not None and _looks_like_games(game):
        return game
    text = getattr(tennis, "set_score", None)
    pairs = _all_pairs(str(text)) if text else []
    if len(pairs) >= 2:
        return pairs[-1]
    if len(pairs) == 1 and _looks_like_games(pairs[0]):
        return pairs[0]
    return None


def _points(tennis: Any) -> Optional[tuple[int, int]]:
    raw = getattr(tennis, "point_score", None)
    if not raw:
        return None
    text = str(raw).strip().lower().replace("–", "-").replace("—", "-")
    if "-" not in text:
        return None
    left, right = text.split("-", 1)
    a = _POINT.get(left.strip())
    b = _POINT.get(right.strip())
    if a is None or b is None:
        numeric = _first_pair(text)
        if numeric is None:
            return None
        return numeric
    return a, b


def _looks_like_games(pair: tuple[int, int]) -> bool:
    return max(pair) <= 7


def _first_pair(text: Any) -> Optional[tuple[int, int]]:
    pairs = _all_pairs(str(text)) if text else []
    return pairs[0] if pairs else None


def _all_pairs(text: str) -> list[tuple[int, int]]:
    return [(int(a), int(b)) for a, b in _SCORE_PAIR.findall(text)]
