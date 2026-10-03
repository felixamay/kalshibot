"use client";

import { useMemo, useState } from "react";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { ManualBetModal } from "@/components/ManualBetModal";
import { LiveReadCard } from "@/components/LiveReadCard";
import { StrategySettings } from "@/components/StrategySettings";
import { MatchCard } from "@/components/MatchCard";
import { PrimarySignalCard } from "@/components/PrimarySignalCard";
import { useLiveFeed } from "@/hooks/useLiveFeed";
import type { LiveSignal } from "@/lib/types";

const BET_STATES = new Set([
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

export default function HomePage() {
  const { dashboard, signals, connection, wsState, serverNow, apiUrl } =
    useLiveFeed();
  const [alertsEnabled, setAlertsEnabled] = useState(true);
  const [token, setToken] = useState<string | null>(() =>
    typeof window !== "undefined" ? localStorage.getItem("kt_token") : null
  );
  const [authEmail, setAuthEmail] = useState("");
  const [authPass, setAuthPass] = useState("");
  const [authMode, setAuthMode] = useState<"login" | "register" | null>(null);
  const [placeSignal, setPlaceSignal] = useState<LiveSignal | null>(null);
  const [analysisSignal, setAnalysisSignal] = useState<LiveSignal | null>(null);

  const rankedMatches = useMemo(() => {
    const matches = dashboard?.matches ?? [];
    const group: Record<string, number> = {
      STRONG_ENTRY_SIGNAL: 0,
      STRONG_BET_SIGNAL: 0,
      ENTRY_SIGNAL: 1,
      BET_SIGNAL: 1,
      BET_NOW: 1,
      STOP_EXIT_SIGNAL: 2,
      SLIPPING: 3,
      ENTRY_DEVELOPING: 4,
      CLOSE_TO_SIGNAL: 4,
      WATCH_CLOSELY: 5,
      WATCH: 5,
      SEARCHING_FOR_ENTRY: 6,
      STUDYING_MATCH: 7,
      HOLD: 8,
      COOLDOWN: 9,
      DO_NOT_ENTER: 10,
      NO_BET: 10,
    };
    const rank = (m: (typeof matches)[number]) =>
      (group[m.display_state] ?? 6) * 1000 -
      (m.entry?.entry_score ?? m.read?.opportunity_score ?? 0);
    return [...matches].sort((a, b) => rank(a) - rank(b));
  }, [dashboard]);

  const betMatches = rankedMatches.filter((m) => needsBet(m.display_state));

  const featured =
    rankedMatches.find((m) => !needsBet(m.display_state) && m.model_probability != null) ??
    rankedMatches.find((m) => m.model_probability != null) ??
    null;

  const betSignals = useMemo(() => {
    const fromFeed = signals.filter((s) => {
      const rem = s.expires_at_ms - serverNow;
      return s.actionable && s.status === "ACTIVE" && rem > 0 && isBetSignal(s);
    });
    const fromMatches = betMatches
      .map((m) => m.active_signal)
      .filter((s): s is LiveSignal => !!s && s.actionable && isBetSignal(s));
    const merged = [...fromFeed, ...fromMatches];
    const seen = new Set<string>();
    return merged.filter((s) => {
      if (seen.has(s.signal_id)) return false;
      seen.add(s.signal_id);
      return true;
    });
  }, [signals, serverNow, betMatches]);

  const auth = async () => {
    if (!authMode) return;
    const res = await fetch(`${apiUrl}/api/auth/${authMode}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: authEmail, password: authPass }),
    });
    const data = await res.json();
    if (res.ok) {
      localStorage.setItem("kt_token", data.access_token);
      setToken(data.access_token);
      setAuthMode(null);
    } else {
      alert(data.detail || "Auth failed");
    }
  };

  return (
    <main className="min-h-screen px-4 pb-16 pt-6 md:px-8 max-w-6xl mx-auto">
      <header className="flex flex-wrap items-end justify-between gap-4 border-b border-white/10 pb-5">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-[0.25em] text-signal-mint">
            Live Kalshi Tennis · Read Only
          </p>
          <h1 className="font-display text-5xl md:text-7xl tracking-tight mt-1 text-mist">
            CourtEdge
          </h1>
          <p className="mt-2 max-w-xl text-mist/70 text-sm md:text-base">
            Real-time market analysis that tells you when to consider a bet —
            and exactly how long that advice remains valid. You place every
            order manually on Kalshi.
          </p>
        </div>
        <div className="space-y-3 text-right">
          <ConnectionBadge kalshi={connection} ws={wsState} />
          <div className="flex flex-wrap justify-end gap-2">
            <button
              type="button"
              onClick={() => setAlertsEnabled((v) => !v)}
              className="border border-white/15 px-3 py-2 font-mono text-[11px] uppercase tracking-wider"
            >
              Alerts {alertsEnabled ? "On" : "Off"}
            </button>
            {token ? (
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
            ) : (
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
            )}
          </div>
        </div>
      </header>

      <section className="mt-6">
        <div className="mb-3 flex items-baseline justify-between gap-3">
          <h2 className="font-display text-3xl">Needs a bet</h2>
          <span className="font-mono text-xs uppercase tracking-wider text-mist/50">
            {betSignals.length + betMatches.filter((m) => !betSignals.some((s) => s.market_ticker === m.market_ticker)).length} ready
          </span>
        </div>
        {betSignals.length > 0 || betMatches.length > 0 ? (
          <div className="space-y-4">
            {betSignals.map((signal) => (
              <PrimarySignalCard
                key={signal.signal_id}
                signal={signal}
                serverNow={serverNow}
                alertsEnabled={alertsEnabled}
                onPlaced={setPlaceSignal}
                onViewAnalysis={setAnalysisSignal}
              />
            ))}
            {betMatches
              .filter((m) => !betSignals.some((s) => s.market_ticker === m.market_ticker))
              .map((m) => (
                <MatchCard
                  key={m.market_ticker}
                  match={m}
                  serverNow={serverNow}
                  snapshotServerTimeMs={dashboard?.server_time_ms ?? serverNow}
                  maxDataAgeMs={dashboard?.max_data_age_ms ?? 5000}
                />
              ))}
          </div>
        ) : (
          <div className="border border-dashed border-white/15 p-5 font-mono text-sm text-mist/60">
            No match needs a bet right now. Entry signals will show here, above the rest of the board.
          </div>
        )}
      </section>

      {featured && betSignals.length === 0 && betMatches.length === 0 && (
        <section className="mt-6">
          <LiveReadCard
            match={featured}
            serverNow={serverNow}
            snapshotServerTimeMs={dashboard?.server_time_ms ?? serverNow}
            marketCount={dashboard?.live_match_count ?? rankedMatches.length}
          />
        </section>
      )}

      {!featured && betSignals.length === 0 && betMatches.length === 0 && (
        <section className="mt-6 border border-white/10 bg-ink-800/40 p-8 md:p-12">
          <p className="font-display text-4xl md:text-5xl text-mist/90">
            {dashboard?.no_live_markets ? "NO LIVE TENNIS MARKETS" : "Waiting for quotes."}
          </p>
        </section>
      )}

      {/* Matches */}
      <section className="mt-10">
        <div className="flex items-baseline justify-between gap-3 mb-4">
          <h2 className="font-display text-3xl">Live Matches</h2>
          <span className="font-mono text-xs text-mist/50">
            {dashboard?.live_match_count ?? 0} markets
          </span>
        </div>
        {dashboard?.no_live_markets || !dashboard?.matches?.length ? (
          <div className="border border-dashed border-white/15 p-8 text-center font-mono text-sm text-mist/60">
            NO LIVE TENNIS MARKETS
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {rankedMatches
              .filter((m) => !needsBet(m.display_state))
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

      <StrategySettings apiUrl={apiUrl} token={token} />

      <footer className="mt-12 border-t border-white/10 pt-6 font-mono text-[11px] text-mist/40 uppercase tracking-wider space-y-1">
        <p>CourtEdge never places, modifies, or cancels Kalshi orders.</p>
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
          <div className="w-full max-w-sm border border-white/15 bg-ink-900 p-5">
            <h3 className="font-display text-2xl capitalize">{authMode}</h3>
            <input
              className="mt-4 w-full bg-ink-950 border border-white/15 px-3 py-2"
              placeholder="Email"
              value={authEmail}
              onChange={(e) => setAuthEmail(e.target.value)}
            />
            <input
              type="password"
              className="mt-2 w-full bg-ink-950 border border-white/15 px-3 py-2"
              placeholder="Password"
              value={authPass}
              onChange={(e) => setAuthPass(e.target.value)}
            />
            <div className="mt-4 flex gap-2">
              <button
                type="button"
                className="flex-1 border border-white/20 py-2 font-mono text-xs uppercase"
                onClick={() => setAuthMode(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="flex-1 bg-signal-lime text-ink-950 py-2 font-mono text-xs uppercase font-semibold"
                onClick={auth}
              >
                Continue
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
