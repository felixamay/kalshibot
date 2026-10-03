"""In-play matches from the public Tennis365 scoreboard.

ESPN's ATP/WTA scoreboard misses challenger matches that are on court.
This page lists those matches, and only rows with a live icon are used.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.services.tennis.espn import EspnLiveMatch

LIVESCORE_URL = "https://livescore.tennis365.com/Tennis/All/{date}"
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_HEADER = re.compile(
    r'<div class="header-cg-\d+[^"]*"[^>]*>.*?<h2>(.*?)</h2>',
    re.S,
)
_CARD = re.compile(
    r'<div id="(\d+)" class="[^"]*match_table_content(.*?)</a>\s*</div>',
    re.S,
)


def livescore_dates(now: Optional[datetime] = None) -> list[str]:
    """The score page is dated in US Eastern time. UTC can already be the next day."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    eastern = moment.astimezone(timezone(timedelta(hours=-4)))
    dates = []
    for item in (eastern, moment):
        text = item.strftime("%m-%d-%Y")
        if text not in dates:
            dates.append(text)
    return dates


def parse_livescore(html: str) -> list[EspnLiveMatch]:
    """Matches that are in a set now. Finished and not-started rows are left out."""
    found: list[EspnLiveMatch] = []
    seen: set[str] = set()
    for tournament, block in _sections(html):
        for card in _CARD.finditer(block):
            match_id, body = card.group(1), card.group(2)
            if "live_icon" not in body or match_id in seen:
                continue
            home = _field(body, r'class="tennis_home_player">(.*?)</span>')
            away = _field(body, r'class="tennis_away_player">(.*?)</span>')
            if not home or not away:
                continue
            home_name = _display_name(home)
            away_name = _display_name(away)
            sets = _sets(body)
            detail = _field(body, r"<strong>(.*?)</strong>") or "Live"
            server = home_name if _serving(body, "tennis_home_ball") else away_name if _serving(body, "tennis_away_ball") else None
            href = _field(body, r'href="([^"]+)"') or ""
            seen.add(match_id)
            found.append(
                EspnLiveMatch(
                    match_id=match_id,
                    player_a=home_name,
                    player_b=away_name,
                    tournament=tournament,
                    detail=detail,
                    sets=sets,
                    source_url=("https://livescore.tennis365.com" + href) if href.startswith("/") else href,
                    server_name=server,
                    source="livescore",
                )
            )
    return found


def _sections(html: str) -> list[tuple[Optional[str], str]]:
    marks = list(_HEADER.finditer(html))
    if not marks:
        return [(None, html)]
    sections: list[tuple[Optional[str], str]] = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(html)
        title = _plain(mark.group(1)) or None
        sections.append((title, html[mark.end() : end]))
    return sections


def _sets(body: str) -> list[tuple[int, int]]:
    home = [int(item) for item in re.findall(r'tennis_home_set_design[^"]*">\s*(\d+)\s*<', body)]
    away = [int(item) for item in re.findall(r'tennis_away_set_design[^"]*">\s*(\d+)\s*<', body)]
    width = min(len(home), len(away))
    return [(home[i], away[i]) for i in range(width)]


def _serving(body: str, css: str) -> bool:
    match = re.search(rf'class="{css}"[^>]*>(.*?)</span>', body, re.S)
    return bool(match and "<img" in match.group(1))


def _field(body: str, pattern: str) -> str:
    match = re.search(pattern, body, re.S)
    return _plain(match.group(1)) if match else ""


def _plain(value: str) -> str:
    return _SPACE.sub(" ", _TAG.sub(" ", value)).replace("&nbsp;", " ").strip()


def _display_name(name: str) -> str:
    if "," not in name:
        return name.strip()
    last, first = name.split(",", 1)
    return f"{first.strip()} {last.strip()}".strip()
