# Tennis discovery and two-service-game reasoning changes

The existing FastAPI, Next.js, read-only Kalshi client, price PatternEngine, signal TTL logic, probability model, tennis providers, and manual position flow were updated in place.

## Discovery

Removed the 24-series, four-page, and 400-result caps and the final hard-coded match-ticker filter. Reconciliation now exhausts cursor pagination for open events and ordinary markets, joins series/event metadata, and inspects structured tennis competitor metadata before title/ticker fallbacks. Multi-event combination contracts are excluded from the physical-match dashboard.

The detector recognizes ATP, WTA, Challenger, ITF, major tennis tournaments, singles, and doubles naming. It parses vs/v/dash/beat/win forms, expands player names from sibling contracts, preserves YES orientation (including full doubles names against abbreviated rules), and groups related contracts. A match-winner contract supplies the displayed probability; totals cannot silently supply a winner price.

Match states are UPCOMING, STARTING, LIVE, SUSPENDED, ENDED, and UNKNOWN. LIVE requires an open/active market with a passed occurrence/start time or explicit live evidence. Opening a contract for pre-match trading is insufficient. External score coverage no longer determines whether a Kalshi match disappears. Closed lifecycle events remove a match immediately.

Lifecycle WebSocket subscriptions trigger reconciliation and reconnects clear old books/sequence state. Order-book snapshots and deltas are accumulated with duplicate/gap checks. REST reconciliation runs every five minutes, with paced complete pagination; fallback quote refresh covers tracked series, with slower REST reads while the WebSocket works. Failed discovery is visibly incomplete and never masquerades as a confirmed zero-market result.

The saved real Kalshi snapshot in `kalshi-discovery-verification.json` checked 13,212 events and 123,497 markets, classified 1,032 tennis-related contracts (including upcoming/futures), and found **22 open, started contracts grouped into 11 matches**. This count is a timestamped observation and changes as contracts open and close. Some occurrence dates are older than the snapshot: open + started identifies Kalshi-live eligibility and does not prove players are currently on court. The snapshot was collected through public read endpoints; an authenticated WebSocket session was not available for live verification.

## Completed games and blocks

ServiceGameTracker counts verified completed service games, never point-score changes, serve attempts, or tiebreak points. It uses the previous actual server and game winner, rather than assuming alternating servers. Two completions create a ServeBlock immediately and reset the pending counter.

Set transitions, normal tiebreaks, explicitly tagged match tiebreaks, repeated updates, authoritative game-log revisions, score corrections, and ambiguous jumps are handled conservatively. An ambiguous jump discards the incomplete block; it does not invent intermediate games. Corrected blocks are invalidated. First/last observation timestamps preserve the whole observed game interval.

ServeBlock includes two games, server sequence, holds/breaks, observed price path, volatility, spread, depth, imbalance, momentum, optional tennis statistics, derived features, and invalidation state. Missing points, aces, faults, trade flow, and other unsupported fields remain null.

## Reasoning and entry

Restored the 300-second initial learning period and froze the price baseline to that interval. Every completed block triggers a reasoning pass, even if the result is WAIT or the market is suspended/stale. Current/previous/earlier blocks and the initial available service baseline are compared with configurable 40/25/20/15 weighting; unavailable components are omitted rather than fabricated.

Reasoning records game outcomes, service strength, return pressure, service changes, tennis momentum, separate market momentum, divergence, candidate patterns, data quality, entry score, confidence, and structured answers to the ten reasoning questions. Configurable entry weights begin at 25/20/15/15/10/5/5/3/2; WATCH/ENTRY/STRONG thresholds remain 65/75/87 and are heuristics, not validated optimal trading parameters.

New block features search for service dominance/deterioration, return/break pressure, pullback recovery, possible overreaction, underreaction/market lag, tennis-market divergence, and repeated hold/break repricing. Existing price-pattern support continues to cover recovery, support/resistance behavior, breakout/failed breakout, momentum/reversal, order-book reversal and liquidity absorption where supplied evidence supports them.

A repeated, sufficiently confident pattern and acceptable market evidence are required. A block candidate starts with zero confirmations; at least three subsequent market updates must confirm it. Entry caps are anchored to the block candidate, rather than expanding with price. Low confidence, incomplete patterns, missing liquidity, late opportunities, and stale quotes cannot force entry. A changed pattern requires a new block. The normal result is **NO RELIABLE PATTERN — WAIT**.

Signal expiry continues to use absolute timestamps and the existing TTL calculator. Removed the forced 20-second floor and eight-cent chase allowance. Price-cap violations, broken patterns, stale data, spread expansion, liquidity loss, or reversed order-book pressure cancel entries. Timer checks also cancel stale signals without waiting for another tick.

Manual entry cancels the outstanding entry signal and switches to position monitoring. Every subsequent block reasons about pattern health; service deterioration, breaks, and lost bid support can generate a manual stop/exit warning. Existing SlipDetector logic continues between blocks; the timer loop also raises position warnings when quotes go stale without new ticks. No Kalshi order-writing capability was added.

## Storage and UI

Added service_games, serve_blocks, serve_block_features, serve_block_reasoning, tennis_market_divergence, and reasoning_history tables plus Alembic migration 002. Existing pattern_instances and pattern_results tables remain in the reused schema. Individual games, full block features and reasoning are persisted; stable Kalshi event identities restore histories after restart. Restored recommendations begin in WAIT until fresh evidence arrives.

All live match cards now remain in the dashboard, including cards with signals. Cards show both contract prices, current server, games analyzed, block progress, study countdown, service data availability, player service/return scores, tennis/market momentum, divergence, latest reasoning, confirmations, position pattern health, and block history. The discovery health panel shows event/market totals, tennis/live match totals, refresh time, completion, and errors.

## Verification

Backend regression coverage includes full discovery pagination, ATP/WTA/Challenger/ITF metadata, naming variants, doubles YES orientation, related contracts, upcoming/closed/live states, recovery after missed discovery, reconnect reconciliation, game boundaries, set/tiebreak behavior, duplicate/corrected scores, no fabricated stats, block comparison, divergence, WAIT behavior, three subsequent confirmations, stale timestamps, pattern failure, and durable database restoration.

Frontend tests execute the existing nine countdown cases plus two rendered match-card regressions. TypeScript and production Next.js build were checked. Final results: 112 backend tests passed (including real public Kalshi requests), 11 frontend tests passed, TypeScript checks passed, and the production Next.js build passed. The final timer addition also passed all 110 local backend regressions. Database-thread/network-dependent checks and Next.js font fetching require execution outside the restricted sandbox.

## Provider and deployment limits

Detailed service/return statistics are available only when supplied by the configured provider. The custom HTTP adapter accepts authoritative completed_service_games, feed_revision, and is_tiebreak/is_match_tiebreak. Existing public score providers may provide only score/server information; that lowers confidence and leaves unsupported scores null. Without score/server or an authoritative game log, SERVICE-GAME DATA UNAVAILABLE is shown and only market analysis continues. Ambiguous retirement/walkover or match-tiebreak feeds cannot be reconstructed safely; terminal Kalshi lifecycle events stop entries.

This is a verified workspace update and production build, not a deployment to the existing Firebase site or its remote backend. Apply the migration (or startup create_all), restart the backend, and publish the built frontend through the existing deployment workflow. No automatic order placement is enabled.

API references used to verify reconciliation semantics: [Kalshi Get Markets](https://docs.kalshi.com/api-reference/market/get-markets) and [Kalshi Get Events](https://docs.kalshi.com/api-reference/events/get-events).
