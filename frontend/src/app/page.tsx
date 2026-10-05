"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { ManualBetModal } from "@/components/ManualBetModal";
import { LiveReadCard } from "@/components/LiveReadCard";
import { StrategySettings } from "@/components/StrategySettings";
import { MatchCard, liveScoreLine } from "@/components/MatchCard";
import { PrimarySignalCard } from "@/components/PrimarySignalCard";
import { useLiveFeed } from "@/hooks/useLiveFeed";
import { considerYesAlert, enableSignalAudio, playSignalAudio, signalAlertKey } from "@/lib/signalAudio";
import type { LiveSignal } from "@/lib/types";

const BET_STATES = new Set([
  "STRONG_PATTERN_SIGNAL",
  "PATTERN_ENTRY_SIGNAL",
  "STRONG_ENTRY_SIGNAL",
  "ENTRY_SIGNAL",
  "STRONG_BET_SIGNAL",
  "BET_SIGNAL",
  "BET_NOW",
]);

function needsBet(state: string) {
  return BET_STATES.has(state);
}

function isBetSignal(signal: LiveSignal) {
  const kind = signal.raw_signal_type || signal.signal_type;
  return BET_STATES.has(kind);
}

const BET_ROTATE_MS = 10_000;

function useRotatingBet(signals: LiveSignal[]): LiveSignal | null {
  const hold = useRef<{ id: string; until: number } | null>(null);
  return useMemo(() => {
    if (signals.length === 0) {
      hold.current = null;
      return null;
    }
    const now = Date.now();
    const held = hold.current;
    const current = held ? signals.find((s) => s.signal_id === held.id) : undefined;
    if (current && held && now < held.until) return current;
    const idx = held ? signals.findIndex((s) => s.signal_id === held.id) : -1;
    const next = signals[(idx + 1) % signals.length];
    hold.current = { id: next.signal_id, until: now + BET_ROTATE_MS };
    return next;
  }, [signals]);
}

export default function HomePage() {
  const [alertsEnabled, setAlertsEnabled] = useState(false);
  const { dashboard, signals, feedDown, serverNow, apiUrl } = useLiveFeed(alertsEnabled);
  // Start empty. A useState initializer runs on the server and is not re-run
  // on hydration, so a saved token would be ignored in every new tab.
  const [token, setToken] = useState<string | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [authEmail, setAuthEmail] = useState("");
  const [authPass, setAuthPass] = useState("");
  const [authMode, setAuthMode] = useState<"login" | "register" | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [placeSignal, setPlaceSignal] = useState<LiveSignal | null>(null);
  const [analysisSignal, setAnalysisSignal] = useState<LiveSignal | null>(null);

  const rankedMatches = useMemo(() => {
    const matches = (dashboard?.matches ?? []).filter((m) => m.is_live !== false);
    const group: Record<string, number> = {
      STRONG_PATTERN_SIGNAL: 0,
      STRONG_ENTRY_SIGNAL: 0,
      STRONG_BET_SIGNAL: 0,
      PATTERN_ENTRY_SIGNAL: 1,
      ENTRY_SIGNAL: 1,
      BET_SIGNAL: 1,
      BET_NOW: 1,
      PATTERN_BROKEN: 2,
      STOP_EXIT_SIGNAL: 2,
      PATTERN_AT_RISK: 3,
      SLIPPING: 3,
      PATTERN_WEAKENING: 4,
      PATTERN_WATCH: 5,
      PATTERN_DEVELOPING: 5,
      ENTRY_DEVELOPING: 5,
      CLOSE_TO_SIGNAL: 5,
      WATCH_CLOSELY: 6,
      WATCH: 6,
      SEARCHING_FOR_ENTRY: 6,
      PATTERN_ALREADY_ADVANCED: 7,
      FAILED_BREAKOUT: 7,
      STUDYING_MATCH: 8,
      PATTERN_HEALTHY: 9,
      HOLD: 9,
      COOLDOWN: 10,
      DO_NOT_ENTER: 11,
      NO_BET: 11,
    };
    const rank = (m: (typeof matches)[number]) =>
      (group[m.display_state] ?? 6) * 1000 -
      (m.pattern?.entry_score ?? m.entry?.entry_score ?? m.read?.opportunity_score ?? 0);
    return [...matches].sort((a, b) => rank(a) - rank(b));
  }, [dashboard, serverNow]);

  const betMatches = rankedMatches.filter((m) => needsBet(m.display_state));

  const featured =
    rankedMatches.find((m) => !needsBet(m.display_state) && m.model_probability != null) ??
    rankedMatches.find((m) => m.model_probability != null) ??
    null;

  const betSignals = useMemo(() => {
    const verifiedTickers = new Set(rankedMatches.map(m => m.market_ticker));
    const fromFeed = signals.filter((s) => {
      const rem = s.expires_at_ms - serverNow;
      return !feedDown && verifiedTickers.has(s.market_ticker) && s.actionable && s.status === "ACTIVE" && rem > 0 && isBetSignal(s);
    });
    const fromMatches = betMatches
      .map((m) => m.active_signal)
      .filter((s): s is LiveSignal => {
        if (!s || !s.actionable || !isBetSignal(s)) return false;
        return s.expires_at_ms - serverNow > 0;
      });
    const byMarket = new Map<string, LiveSignal>();
    for (const signal of [...fromFeed, ...fromMatches]) {
      const key = signal.market_ticker || signal.signal_id;
      const current = byMarket.get(key);
      if (!current || signal.created_at_ms >= current.created_at_ms) {
        byMarket.set(key, signal);
      }
    }
    return [...byMarket.values()].sort((a, b) =>
      (a.market_ticker || a.signal_id).localeCompare(b.market_ticker || b.signal_id)
    );
  }, [signals, serverNow, betMatches, rankedMatches, feedDown]);

  const shownBet = useRotatingBet(betSignals);

  useEffect(() => {
    if (!alertsEnabled || feedDown) return;
    for (const signal of betSignals) {
      playSignalAudio(signalAlertKey(signal));
      if (signal.expires_at_ms - serverNow < 2000) playSignalAudio(signalAlertKey(signal), true);
    }
    const now = serverNow || Date.now();
    for (const match of dashboard?.matches ?? []) {
      const hybrid = match.hybrid_decision?.signal;
      if (!hybrid?.alert_key) continue;
      if (considerYesAlert(hybrid.alert_key, hybrid.final === "YES", now)) {
        playSignalAudio(`yes:${hybrid.alert_key}:${Math.floor(now)}`);
      }
    }
  }, [alertsEnabled, feedDown, betSignals, serverNow, dashboard]);

  useEffect(() => {
    const read = () => localStorage.getItem("kt_token");
    setToken(read());
    setAuthReady(true);
    const onStorage = (event: StorageEvent) => {
      if (event.key === "kt_token") setToken(event.newValue);
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${apiUrl}/api/auth/me`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.status === 401 && !cancelled) {
          localStorage.removeItem("kt_token");
          setToken(null);
        }
      } catch {
        // Keep the saved session if the API is briefly unreachable.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token, apiUrl]);

  const auth = async () => {
    if (!authMode || authBusy) return;
    setAuthBusy(true);
    setAuthError(null);
    try {
      const res = await fetch(`${apiUrl}/api/auth/${authMode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: authEmail.trim(), password: authPass }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.access_token) {
        localStorage.setItem("kt_token", data.access_token);
        setToken(data.access_token);
        setAuthMode(null);
        setAuthPass("");
        setAuthError(null);
      } else {
        const detail = data.detail;
        const message = Array.isArray(detail)
          ? detail.map((item: { msg?: string }) => item.msg || "Check the email and password.").join(" ")
          : detail || "Sign-in failed. Check the email and password.";
        setAuthError(String(message));
      }
    } catch {
      setAuthError("Could not reach the server. Try again in a moment.");
    } finally {
      setAuthBusy(false);
    }
  };

  return (
    <main className="min-h-screen px-4 pb-16 pt-6 md:px-8 max-w-6xl mx-auto">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-white/10 pb-5">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-[0.25em] text-signal-mint">
            Live Kalshi Tennis
          </p>
          <h1 className="font-display text-5xl md:text-7xl tracking-tight mt-1 text-mist">
            CourtEdge
          </h1>
          <p className="mt-2 max-w-xl text-mist/70 text-sm md:text-base">
            Your strategy watches live Kalshi prices. Automatic orders stay off
            until you enable them. You can still decide each bet yourself.
          </p>
        </div>
        <div className="space-y-3 text-right">
          <ConnectionBadge />
          <div className="flex flex-wrap justify-end gap-2">
            <button
              type="button"
              onClick={async () => {
                if (alertsEnabled) setAlertsEnabled(false);
                else setAlertsEnabled(await enableSignalAudio());
              }}
              className="border border-white/15 px-3 py-2 font-mono text-[11px] uppercase tracking-wider"
            >
              {alertsEnabled ? "Sound On" : "Enable Sound"}
            </button>
            {authReady && token ? (
              <button
                type="button"
                onClick={() => {
                  localStorage.removeItem("kt_token");
                  setToken(null);
                }}
                className="border border-white/15 px-3 py-2 font-mono text-[11px] uppercase"
              >
                Log out
              </button>
            ) : authReady ? (
              <>
                <button
                  type="button"
                  onClick={() => setAuthMode("login")}
                  className="border border-white/15 px-3 py-2 font-mono text-[11px] uppercase"
                >
                  Log in
                </button>
                <button
                  type="button"
                  onClick={() => setAuthMode("register")}
                  className="bg-signal-mint/90 text-ink-950 px-3 py-2 font-mono text-[11px] uppercase font-semibold"
                >
                  Register
                </button>
              </>
            ) : null}
          </div>
        </div>
      </header>

      <section className="mt-6">
        <div className="mb-3 flex items-baseline justify-between gap-3">
          <h2 className="font-display text-3xl">Needs a bet</h2>
          <span className="font-mono text-xs uppercase tracking-wider text-mist/50">
            {shownBet ? "1 ready" : "0 ready"}
          </span>
        </div>
        {shownBet ? (
          <PrimarySignalCard
            signal={shownBet}
            serverNow={serverNow}
            alertsEnabled={alertsEnabled}
            onPlaced={setPlaceSignal}
            onViewAnalysis={setAnalysisSignal}
          />
        ) : rankedMatches.length > 0 ? (
          <div className="border border-dashed border-white/15 p-5 font-mono text-sm text-mist/60">
            {rankedMatches[0].player_a} vs {rankedMatches[0].player_b} is live · {liveScoreLine(rankedMatches[0].tennis).label}. No bet yet. {rankedMatches[0].hold_reason}
          </div>
        ) : dashboard == null ? (
          <div className="border border-dashed border-white/15 p-5 font-mono text-sm text-mist/60">
            {feedDown
              ? "The live feed is unreachable. Saved matches stay hidden until it reconnects."
              : "Checking live matches…"}
          </div>
        ) : (
          <div className="border border-dashed border-white/15 p-5 font-mono text-sm text-mist/60">
            No live game needs a bet right now. When one is ready, this section shows that single bet and moves to the next every 10 seconds.
          </div>
        )}
      </section>

      {featured && betSignals.length === 0 && betMatches.length === 0 && (
        <section className="mt-6">
          <LiveReadCard
            match={featured}
            serverNow={serverNow}
            snapshotServerTimeMs={dashboard?.server_time_ms ?? serverNow}
            marketCount={rankedMatches.length}
          />
        </section>
      )}

      {dashboard && !featured && betSignals.length === 0 && betMatches.length === 0 && (
        <section className="mt-6 border border-white/10 bg-ink-800/40 p-8 md:p-12">
          <p className="font-display text-4xl md:text-5xl text-mist/90">
            {dashboard.message || (dashboard.no_live_markets ? "NO LIVE TENNIS MARKETS ON KALSHI" : "Waiting for fresh live confirmation.")}
          </p>
        </section>
      )}

      {dashboard?.discovery_health && <section className="mt-8 border border-white/10 p-4 font-mono text-sm">
        <h2>TENNIS DISCOVERY STATUS</h2>
        <p>Kalshi Events Checked: {dashboard.discovery_health.events_checked ?? "—"} · Kalshi Markets Checked: {dashboard.discovery_health.markets_checked ?? "—"}</p>
        <p>Tennis Markets Found: {dashboard.discovery_health.tennis_markets_found ?? "—"} · Live Tennis Matches Found: {dashboard.live_match_count}</p>
        <p>Kalshi live matches found: {dashboard.discovery_health.kalshi_live_matches_found ?? dashboard.live_match_count}</p>
        <p>WebSocket markets subscribed: {dashboard.discovery_health.ws_markets_subscribed ?? "—"}</p>
        <p>Last Discovery Refresh: {dashboard.discovery_health.last_refresh_ms ? new Date(dashboard.discovery_health.last_refresh_ms).toISOString() : "Pending"}</p>
        {!dashboard.discovery_health.complete && <p>Discovery incomplete — reconciliation pending</p>}
        {dashboard.discovery_health.errors?.map(e => <p key={e}>{e}</p>)}
      </section>}

      {/* Matches */}
      <section className="mt-10">
        <div className="flex items-baseline justify-between gap-3 mb-4">
          <h2 className="font-display text-3xl">LIVE TENNIS — {rankedMatches.length} MATCHES</h2>
          <span className="font-mono text-xs text-mist/50">
            {rankedMatches.length} live
          </span>
        </div>
        {dashboard == null ? (
          <div className="border border-dashed border-white/15 p-8 text-center font-mono text-sm text-mist/60">
            {feedDown
              ? "The live feed is unreachable. Saved matches stay hidden until it reconnects."
              : "Checking live matches…"}
          </div>
        ) : dashboard.no_live_markets || rankedMatches.length === 0 ? (
          <div className="border border-dashed border-white/15 p-8 text-center font-mono text-sm text-mist/60">
            {dashboard.message || "NO LIVE TENNIS MARKETS ON KALSHI"}
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {rankedMatches
              .map((m) => (
              <MatchCard
                key={m.market_ticker}
                match={m}
                serverNow={serverNow}
                snapshotServerTimeMs={dashboard.server_time_ms}
                maxDataAgeMs={dashboard.max_data_age_ms ?? 5000}
              />
            ))}
          </div>
        )}
      </section>

      {/* History */}
      <section className="mt-10">
        <h2 className="font-display text-3xl mb-4">Signal History</h2>
        <div className="overflow-x-auto border border-white/10">
          <table className="w-full font-mono text-xs text-left">
            <thead className="bg-ink-800 text-mist/50 uppercase tracking-wider">
              <tr>
                <th className="p-3">Time</th>
                <th className="p-3">Type</th>
                <th className="p-3">Player</th>
                <th className="p-3">Price</th>
                <th className="p-3">TTL</th>
                <th className="p-3">Reason</th>
              </tr>
            </thead>
            <tbody>
              {(dashboard?.signal_history || [])
                .slice()
                .reverse()
                .slice(0, 30)
                .map((s, i) => (
                  <tr key={`${s.signal_id}-${i}`} className="border-t border-white/5">
                    <td className="p-3">
                      {new Date(s.created_at_ms).toLocaleTimeString()}
                    </td>
                    <td className="p-3">{s.display_label || s.signal_type}</td>
                    <td className="p-3">{s.player}</td>
                    <td className="p-3">{Math.round(s.market_price)}¢</td>
                    <td className="p-3">{(s.original_ttl_ms / 1000).toFixed(1)}s</td>
                    <td className="p-3 text-mist/60">
                      {s.expiration_reason || "—"}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </section>

      <StrategySettings apiUrl={apiUrl} token={token} alertsEnabled={alertsEnabled} />

      <footer className="mt-12 border-t border-white/10 pt-6 font-mono text-[11px] text-mist/40 uppercase tracking-wider space-y-1">
        <p>Automatic Kalshi orders run only for the signed-in user who turns them on.</p>
        <p>Server clock · Latency-adjusted TTL · Early invalidation on condition change.</p>
      </footer>

      {placeSignal && (
        <ManualBetModal
          signal={placeSignal}
          apiUrl={apiUrl}
          token={token}
          onClose={() => setPlaceSignal(null)}
          onDone={() => undefined}
        />
      )}

      {analysisSignal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="max-w-lg w-full border border-white/15 bg-ink-900 p-5 max-h-[80vh] overflow-y-auto">
            <h3 className="font-display text-2xl">Analysis</h3>
            <pre className="mt-3 font-mono text-[11px] text-mist/70 whitespace-pre-wrap">
              {JSON.stringify(analysisSignal, null, 2)}
            </pre>
            <button
              type="button"
              className="mt-4 border border-white/20 px-4 py-2 font-mono text-xs uppercase"
              onClick={() => setAnalysisSignal(null)}
            >
              Close
            </button>
          </div>
        </div>
      )}

      {authMode && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <form
            className="w-full max-w-sm border border-white/15 bg-ink-900 p-5"
            onSubmit={(event) => {
              event.preventDefault();
              void auth();
            }}
          >
            <h3 className="font-display text-2xl capitalize">{authMode}</h3>
            <input
              className="mt-4 w-full bg-ink-950 border border-white/15 px-3 py-2"
              placeholder="Email"
              type="email"
              autoComplete="email"
              value={authEmail}
              onChange={(e) => setAuthEmail(e.target.value)}
              required
            />
            <input
              type="password"
              autoComplete={authMode === "register" ? "new-password" : "current-password"}
              className="mt-2 w-full bg-ink-950 border border-white/15 px-3 py-2"
              placeholder="Password"
              value={authPass}
              onChange={(e) => setAuthPass(e.target.value)}
              required
              minLength={authMode === "register" ? 8 : 1}
            />
            {authError && (
              <p className="mt-3 text-sm text-red-300" role="alert">
                {authError}
              </p>
            )}
            <div className="mt-4 flex gap-2">
              <button
                type="button"
                className="flex-1 border border-white/20 py-2 font-mono text-xs uppercase"
                onClick={() => {
                  setAuthMode(null);
                  setAuthError(null);
                }}
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={authBusy}
                className="flex-1 bg-signal-lime text-ink-950 py-2 font-mono text-xs uppercase font-semibold disabled:opacity-60"
              >
                {authBusy ? "Please wait" : "Continue"}
              </button>
            </div>
          </form>
        </div>
      )}
    </main>
  );
}
