"""Metadata-first tennis classification and physical-match normalization."""
import re
from datetime import datetime, timezone


class TennisMarketDetector:
    marker = re.compile(r'(?i)(tennis|(?:^|[^a-z])(?:ATP|WTA|ITF)(?:[^a-z]|$)|KX(?:ATP|WTA|ITF)|challenger|wimbledon|roland garros|australian open|french open)')

    def detects(self, market, event=None, series=None):
        metadata = [market, event or {}, series or {}]
        sports = [str(item.get('sport', '')).casefold() for item in metadata if item.get('sport')]
        if sports and not any('tennis' in sport or sport in ('atp', 'wta', 'itf') for sport in sports):
            return False
        if any(any(str(key).startswith('tennis_') for key in item.get('custom_strike', {})) for item in metadata):
            return True
        return any(self.marker.search(str(value)) for item in metadata for key, value in item.items()
                   if key in {'sport', 'sports', 'category', 'tags', 'title', 'subtitle', 'ticker', 'event_ticker', 'series_ticker', 'competition', 'tournament', 'rules_primary'})

    def players(self, market):
        event = market.get('_event', {})
        yes = (market.get('yes_sub_title') or '').strip()
        name_key = lambda value: re.sub(r"[^\w]", "", value).casefold()
        team_key = lambda value: tuple(part.strip().split()[-1].casefold() for part in value.split('/') if part.strip())
        texts = [event.get('title'), market.get('title'), market.get('subtitle')]
        rules = market.get('rules_primary', '')
        names = r"[A-ZÀ-ÖØ-Ý][\wÀ-öø-ÿ.'-]*(?:\s+[A-ZÀ-ÖØ-Ý][\wÀ-öø-ÿ.'-]*)*(?:\s*/\s*[A-ZÀ-ÖØ-Ý][\wÀ-öø-ÿ.'-]*(?:\s+[A-ZÀ-ÖØ-Ý][\wÀ-öø-ÿ.'-]*)*)*"
        rule_match = re.search(rf"({names})\s+vs\.?\s+({names})", rules)
        if rule_match:
            texts.append(f"{rule_match[1]} vs {rule_match[2]}")
        for text in texts:
            if not text:
                continue
            text = re.sub(r'^Will\s+', '', text.strip(), flags=re.I)
            match = re.search(r'^(.+?)\s+(?:vs\.?|v\.?|versus|beat|-)\s+(.+?)(?:\?|$)', text, re.I)
            if not match:
                continue
            a, b = (v.strip() for v in match.groups())
            b = re.sub(r'\s+(?:wins?|to win).*$', '', b, flags=re.I)
            if yes:
                if name_key(yes) == name_key(b) or name_key(yes).endswith(name_key(b)) or team_key(yes) == team_key(b):
                    return yes, a
                if name_key(yes) == name_key(a) or name_key(yes).endswith(name_key(a)) or team_key(yes) == team_key(a):
                    return yes, b
            return a, b
        a = yes or re.sub(r'(?i)^(?:Will\s+)?(.+?)\s+(?:wins?|to win).*$', r'\1', market.get('title', ''))
        return a or market.get('ticker', 'Unknown player'), 'Opponent unavailable'

    _derivative = re.compile(r'(SPREAD|EXACT|SETWINNER|GTOTAL|GAMETOTAL|GAMESPREAD|ACES|HANDICAP|TOTALSETS|TIEBREAK|SETSWEEP|GWINNER|ANYSET|GAME)')

    def is_match_winner(self, market):
        series = market.get('_series') or {}
        ticker = f"{market.get('ticker', '')} {market.get('series_ticker', '')} {series.get('ticker', '')}".upper()
        title = f"{market.get('title', '')} {series.get('title', '')} {' '.join(series.get('tags') or [])}"
        if re.search(r'(?i)table tennis|pickleball|\bittf\b', f"{ticker} {title}"):
            return False
        if self._derivative.search(ticker) or re.search(r'(?i)\b(?:set|total|games|aces|handicap|spread|exact)\b', title):
            return False
        if 'tennis_competitor' in market.get('custom_strike', {}):
            return True
        kind = str(market.get('contract_type') or market.get('market_kind') or '').lower()
        if kind:
            return kind in ('match_winner', 'winner')
        return bool(re.search(r'(?i)\b(?:wins?|to win|beat)\b', title) or 'MATCH' in ticker or 'DOUBLES' in ticker)

    def is_match_series(self, series):
        """Head-to-head tennis series from Kalshi series metadata, across tours."""
        if not series or not self.detects(series):
            return False
        blob = f"{series.get('ticker', '')} {series.get('title', '')} {' '.join(series.get('tags') or [])}"
        if re.search(r'(?i)table tennis|pickleball|\bittf\b', blob):
            return False
        if self._derivative.search(blob.upper()) or re.search(r'(?i)\b(?:spread|exact|total|aces|handicap|tiebreak)\b', blob):
            return False
        return bool(re.search(r'(?i)(match|doubles)', blob))

    def start_ms(self, market):
        for source in (market, market.get('_event', {})):
            for key in ('occurrence_datetime', 'scheduled_start_time', 'start_time', 'expected_start_time'):
                value = source.get(key)
                if value:
                    try:
                        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
                        return dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp() * 1000
                    except ValueError:
                        pass
        return None

    def state(self, market, now_ms, score_live=False):
        status = str(market.get('status', '')).lower()
        if status in {'closed', 'settled', 'finalized', 'determined'}:
            return 'ENDED'
        if status in {'suspended', 'inactive', 'paused'}:
            return 'SUSPENDED'
        if status not in {'open', 'active', 'live'}:
            return 'UNKNOWN'
        close = market.get('close_time')
        if close:
            try:
                if datetime.fromisoformat(close.replace('Z', '+00:00')).timestamp() * 1000 <= now_ms:
                    return 'ENDED'
            except ValueError:
                pass
        start = self.start_ms(market)
        if score_live or market.get('is_live') is True or status == 'live':
            return 'LIVE'
        if start is not None and start > now_ms:
            return 'STARTING' if start - now_ms <= 300000 else 'UPCOMING'
        # Open after the scheduled start is a live Kalshi match even when the
        # score feed has not confirmed it. A missing start stays UNKNOWN.
        if start is not None and start <= now_ms:
            return 'LIVE'
        return 'UNKNOWN'

    def group_key(self, market):
        a, b = self.players(market)
        # Event identity prevents rematches on different dates being merged.
        signature = tuple(sorted('/'.join(part.strip().split()[-1].casefold() for part in name.split('/')) for name in (a, b)))
        return (market.get('event_ticker') or market.get('ticker'), signature) if b != 'Opponent unavailable' else (market.get('event_ticker') or market.get('ticker'), 'unresolved')
