# CourtEdge — Live Kalshi Tennis Signal Analyst

Production-ready web application that analyzes **live Kalshi tennis markets** in real time and tells you **when to consider placing a bet** — with millisecond-accurate signal expiration countdowns.

## Absolute safety rule

**This application NEVER places, modifies, cancels, or executes bets on Kalshi.**

You place every bet manually on Kalshi. The app is advisory only.

The Kalshi client is read-only. Write methods (`POST`/`PUT`/`PATCH`/`DELETE`) are blocked at the client layer. There is no `place_order` API anywhere in the codebase.

---

## Stack

| Layer | Tech |
|-------|------|
| Frontend | Next.js, React, TypeScript, Tailwind CSS |
| Backend | Python 3.12, FastAPI, asyncio, WebSockets |
| Database | PostgreSQL |
| Optional | Redis |

---

## 1. Startup instructions

### Option A — Docker Compose (recommended)

```bash
cp .env.example .env
# Edit .env with SECRET_KEY and Kalshi credentials
mkdir -p secrets
# Place your Kalshi private key at secrets/kalshi_private_key.pem

docker compose up --build
```

- Frontend: http://localhost:3000
- Backend API / docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/health

### Option B — Local development

**Database**

```bash
# Start Postgres (example)
docker run -d --name kalshi-pg -e POSTGRES_USER=kalshi -e POSTGRES_PASSWORD=kalshi \
  -e POSTGRES_DB=kalshi_signals -p 5432:5432 postgres:16-alpine
```

**Backend**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env
# For quick local test without Postgres:
# export DATABASE_URL=sqlite+aiosqlite:///./kalshi.db
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Frontend**

```bash
cd frontend
npm install
export NEXT_PUBLIC_API_URL=http://localhost:8000
export NEXT_PUBLIC_WS_URL=ws://localhost:8000/ws
npm run dev
```

---

## 2. Environment variables

See `.env.example` for the full list. Critical variables:

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | JWT signing secret |
| `DATABASE_URL` | Async SQLAlchemy URL |
| `KALSHI_API_BASE_URL` | Production REST base (`https://api.elections.kalshi.com/trade-api/v2`) |
| `KALSHI_WS_URL` | Production WebSocket |
| `KALSHI_API_KEY_ID` | API key id for authenticated **reads** |
| `KALSHI_PRIVATE_KEY_PATH` / `KALSHI_PRIVATE_KEY_PEM` | RSA private key for request signing |
| `TENNIS_PROVIDER` | `none` \| `custom` \| `api_tennis` \| `sportradar` |
| `TENNIS_API_KEY` / `TENNIS_API_BASE_URL` | Live tennis stats provider |
| `INITIAL_OBSERVATION_SECONDS` | Default `300` — no BET NOW during study period |
| `MIN_BET_CONFIDENCE` | Default `85` |
| `STRONG_BET_CONFIDENCE` | Default `92` |
| `MIN_NET_EDGE` | Default `0.04` (4 percentage points) |
| `ENTRY_CONFIRMATION_COUNT` | Default `5` consecutive confirms |
| `MIN/DEFAULT/MAX_SIGNAL_TTL_SECONDS` | `2` / `8` / `15` |
| `SIGNAL_TIMER_REFRESH_MS` | Default `100` |
| `MAX_DATA_AGE_MS` | Default `1500` — stale data cancels signals |

---

## 3. Deployment instructions

1. Provision PostgreSQL 16+ and (optional) Redis.
2. Set strong `SECRET_KEY` and Kalshi credentials via secrets manager.
3. Build and push images, or run `docker compose up --build -d` on the host.
4. Put TLS termination (Caddy/nginx/Cloudflare) in front of ports 3000/8000.
5. Point `CORS_ORIGINS` and `NEXT_PUBLIC_*` URLs at your public domains.
6. Confirm `/api/health` returns `"order_placement_enabled": false`.
7. Monitor logs for `KalshiWriteAttempt` — any occurrence is a critical bug.

Migrations: on boot the API runs `create_all`. For production schema control:

```bash
cd backend && alembic upgrade head
```

---

## 4. Kalshi API configuration

1. Create an API key in the Kalshi account settings (production).
2. Download the RSA private key PEM.
3. Set:
   - `KALSHI_API_KEY_ID=<key id>`
   - `KALSHI_PRIVATE_KEY_PATH=/path/to/key.pem`
4. Endpoints used (GET only):
   - `/exchange/status`
   - `/series`, `/events`, `/markets`, `/markets/{ticker}`
   - `/markets/{ticker}/orderbook`
   - `/markets/trades`
   - WebSocket channels: `ticker`, `orderbook_delta`, `trade`
5. **Do not** grant or use order-writing workflows. The client rejects non-GET requests.

Public market discovery works without auth for many endpoints; auth improves reliability and rate limits.

---

## 5. Tennis-data-provider integration

When unset (`TENNIS_PROVIDER=none`), the UI shows **MARKET-ONLY ANALYSIS**. Stats are never invented.

To integrate:

1. Set `TENNIS_PROVIDER=custom` (or `api_tennis` / `sportradar`).
2. Set `TENNIS_API_BASE_URL` and `TENNIS_API_KEY`.
3. Implement (or proxy) `GET {base}/live?player_a=&player_b=` returning JSON fields such as:

```json
{
  "id": "match-123",
  "player_a": "Carlos Alcaraz",
  "player_b": "Jannik Sinner",
  "tournament": "ATP Finals",
  "server": "A",
  "point_score": "30-15",
  "game_score": "3-4",
  "set_score": "1-1",
  "match_score": "1-1",
  "aces_a": 5,
  "aces_b": 3,
  "double_faults_a": 1,
  "double_faults_b": 2,
  "break_points_a": 2,
  "break_points_b": 1,
  "recent_points": ["A", "B", "A"]
}
```

Missing fields stay `null`. The probability model only applies bounded adjustments when real stats exist.

---

## 6. Signal expiration algorithm

1. **Observation** — For `INITIAL_OBSERVATION_SECONDS` after a market is discovered, only `STUDYING MATCH` is shown.
2. **Confirmation** — Entry conditions must hold for `ENTRY_CONFIRMATION_COUNT` consecutive evaluations (edge, confidence, liquidity, spread, momentum, order book, freshness, market open).
3. **TTL** — `SignalTTLCalculator` sets lifetime between `MIN` and `MAX` from volatility, trade velocity, liquidity, spread, acceleration, and match state.
4. **Timestamps** — Each signal stores `created_at` / `expires_at` (server ms). Remaining time is always:

   `remaining = signal_expires_at - estimated_server_now`

   Never “subtract 100ms per tick.”
5. **Latency adjustment** — Frontend clock sync uses WebSocket ping RTT; late delivery shortens displayed remaining time.
6. **Early invalidation** — Before TTL hits zero, cancel if price > max entry, edge/confidence/liquidity/spread fail, momentum/order-book reverse, data stale, WS disconnect, market suspended/closed, or a newer `signal_version` arrives.
7. **Display safety** — If not (`server_now < expires_at` AND `status == ACTIVE` AND conditions OK), **BET NOW is never shown**. UI switches to `SIGNAL EXPIRED` / `OPPORTUNITY EXPIRED` / `DO NOT CHASE`.

---

## 7. Testing countdown accuracy

```bash
cd backend
source .venv/bin/activate
pytest tests/test_countdown.py -v
```

Covered cases:

- Correct initial TTL
- Remaining decreases via timestamps
- Network delay reduces remaining
- Exact expiry at `expires_at`
- No negative remaining
- Browser freeze / background tab recalculation
- Urgency stages (NORMAL / CAUTION / FINAL)

Frontend helpers: `frontend/src/lib/clock.ts` (+ `clock.test.ts`).

Manual UI check:

1. Open the dashboard, enable alerts.
2. When a BET NOW appears, confirm countdown updates ~10×/sec.
3. Throttle CPU or background the tab for several seconds; on return, remaining should jump to the timestamp-correct value (not resume from a stale tick).

---

## 8. Testing early signal cancellation

```bash
pytest tests/test_countdown.py -k "price or stale or disconnect or edge" -v
```

Manual:

1. Note `MAX ENTRY` on a live BET NOW.
2. If the market ask moves above max entry, the card must flip to **OPPORTUNITY EXPIRED / DO NOT CHASE** even with seconds left on the timer.
3. Kill network / stop backend WS → signals cancel with **CONNECTION_LOST**.
4. Stale ticks beyond `MAX_DATA_AGE_MS` → **DATA DELAY / STALE_DATA**.

---

## 9. Verifying expired signals can never remain actionable

Invariant enforced in:

- `LiveSignal.is_actionable()`
- `SignalEngine.dashboard_payload()` filter
- `GET /api/signals/active` filter
- Frontend `PrimarySignalCard` (`active && !cd.expired`)

Test:

```bash
pytest tests/test_countdown.py -k "expired_bet_now_never_remains_actionable" -v
```

At `server_now >= expires_at`, `actionable` is false and display label is `SIGNAL EXPIRED` (not BET NOW).

---

## 10. Confirmation: no automatic order placement

- `KalshiReadOnlyClient.request` allows **GET only**; other methods raise `PermissionError`.
- No `place_order` / `create_order` / `cancel_order` methods exist.
- WebSocket outbound messages reject order-related payloads.
- Manual “I Placed This Bet” / “I Exited” write **only** to our database for tracking; responses include `"kalshi_order_placed": false`.
- `/api/health` reports `"order_placement_enabled": false`.

---

## Project layout

```
backend/app/
  services/kalshi/     # read-only REST + WS
  services/tennis/     # provider + probability model
  services/signals/    # TTL, confidence, engine, live signals
  services/market/     # rolling indicators + orchestrator
  api/                 # HTTP routes
  ws/                  # client hub
frontend/src/
  components/          # signal card, match card, modals
  hooks/               # live feed, countdown, sounds
  lib/clock.ts         # drift-free remaining time
```

If no tennis markets are open on Kalshi, the UI shows **NO LIVE TENNIS MARKETS**.
