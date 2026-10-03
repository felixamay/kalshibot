"use client";

import {
  formatMatchTimer,
  matchClockEndsMs,
  remainingFromTimestamps,
} from "@/lib/clock";
import type { MatchCard as MatchCardType } from "@/lib/types";

export function MatchCard({
  match,
  serverNow,
  snapshotServerTimeMs,
  maxDataAgeMs = 5000,
}: {
  match: MatchCardType;
  serverNow: number;
  snapshotServerTimeMs: number;
  maxDataAgeMs?: number;
}) {
  const studying = match.display_state === "STUDYING_MATCH";
  const observationEnds = matchClockEndsMs(
    match.observation_ends_ms,
    snapshotServerTimeMs,
    match.observation_remaining_ms
  );
  const observationRemaining = remainingFromTimestamps(observationEnds, serverNow);
  const cooldownEnds = match.cooldown_until_ms
    ? match.cooldown_until_ms
    : snapshotServerTimeMs + (match.cooldown_remaining_ms || 0);
  const cooldownRemaining =
    cooldownEnds > snapshotServerTimeMs
      ? remainingFromTimestamps(cooldownEnds, serverNow)
      : 0;
  const dataAgeMs =
    match.quote_updated_at_ms && match.quote_updated_at_ms > 0
      ? Math.max(0, serverNow - match.quote_updated_at_ms)
      : match.data_age_ms;
  const tennisNote =
    match.tennis && match.tennis.available
      ? "TENNIS-ENHANCED"
      : "MARKET-ONLY ANALYSIS";

  return (
    <article className="border border-white/10 bg-ink-800/50 p-4 md:p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-widest text-signal-mint">
            {match.tournament || "Tennis"}
          </p>
          <h3 className="font-display text-2xl mt-1 leading-tight">
            {match.player_a}
            <span className="text-mist/40 mx-2">vs</span>
            {match.player_b}
          </h3>
        </div>
        <StatusPill status={match.market_status} />
      </div>

      <div className="mt-4 grid grid-cols-2 sm:grid-cols-3 gap-3 font-mono text-sm">
        <Stat
          label="Kalshi"
          value={
            match.kalshi_probability != null
              ? `${Math.round(match.kalshi_probability * 100)}¢`
              : "—"
          }
        />
        <Stat
          label="Model"
          value={
            match.model_probability != null
              ? `${Math.round(match.model_probability * 100)}%`
              : "—"
          }
        />
        <Stat
          label="Edge"
          value={
            match.estimated_edge != null
              ? `${match.estimated_edge >= 0 ? "+" : ""}${(match.estimated_edge * 100).toFixed(1)}pp`
              : "—"
          }
        />
        <Stat
          label="Confidence"
          value={match.confidence != null ? `${Math.round(match.confidence)}` : "—"}
        />
        <Stat
          label="Spread"
          value={match.spread != null ? `${match.spread.toFixed(1)}¢` : "—"}
        />
        <Stat
          label="Liquidity"
          value={match.liquidity != null ? `${Math.round(match.liquidity)}` : "—"}
        />
        <Stat
          label="Momentum"
          value={
            match.momentum != null
              ? `${match.momentum >= 0 ? "+" : ""}${match.momentum.toFixed(2)}`
              : "—"
          }
        />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3 text-xs font-mono uppercase tracking-wider">
        <span className="text-mist/50">OB pressure</span>
        <span>{match.orderbook_pressure?.toFixed(2) ?? "—"}</span>
        <span className="text-mist/30">·</span>
        <span className="text-signal-mint">{tennisNote}</span>
        <span className="text-mist/30">·</span>
        <span className="text-mist/70">{match.display_state.replace(/_/g, " ")}</span>
      </div>

      <div className="mt-3 flex flex-wrap gap-2 font-mono text-[10px] uppercase tracking-wider text-mist/50">
        <span>{match.read?.liquidity_quality || "Liquidity —"}</span>
        <span>·</span>
        <span>{match.read?.spread_quality || "Spread —"}</span>
        <span>·</span>
        <span>
          Signals {match.signals_today ?? match.signals_emitted ?? 0}/
          {match.max_signals_per_match ?? 4}
        </span>
        {match.read?.close_to_signal && (
          <span className="text-signal-amber">· Close to signal</span>
        )}
      </div>

      {match.hold_reason && (
        <p className="mt-3 text-sm leading-relaxed text-mist/75">{match.hold_reason}</p>
      )}

      {studying && (
        <div className="mt-4 border border-signal-mint/30 bg-ink-950/60 px-3 py-3">
          <p className="font-mono text-xs uppercase tracking-widest text-signal-mint">
            Studying Match
          </p>
          <p className="font-mono text-4xl tabular-nums tracking-tight text-mist mt-1">
            {formatMatchTimer(observationRemaining)}
            <span className="font-mono text-sm ml-2 text-mist/50">REMAINING</span>
          </p>
          <p className="text-xs text-mist/50 mt-1">
            {observationRemaining > 0
              ? "No BET SIGNAL until the observation clock ends."
              : "Observation complete. Reanalyzing."}
          </p>
        </div>
      )}

      {cooldownRemaining > 0 && (
        <div className="mt-4 border border-white/15 bg-ink-950/60 px-3 py-3">
          <p className="font-mono text-xs uppercase tracking-widest text-mist/60">
            Cooldown
          </p>
          <p className="font-mono text-3xl tabular-nums tracking-tight text-mist mt-1">
            {formatMatchTimer(cooldownRemaining)}
            <span className="font-mono text-sm ml-2 text-mist/50">REMAINING</span>
          </p>
        </div>
      )}

      {dataAgeMs != null && dataAgeMs > maxDataAgeMs && (
        <div className="mt-3 border border-signal-coral/40 px-3 py-2 font-mono text-xs text-signal-coral">
          DATA DELAY · Last update {(dataAgeMs / 1000).toFixed(1)}s ago · DO NOT BET
        </div>
      )}

      <p className="mt-3 font-mono text-[10px] text-mist/40 truncate">
        {match.market_ticker}
      </p>
    </article>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-widest text-mist/45">{label}</div>
      <div className="text-base text-mist">{value}</div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const color =
    status === "OPEN"
      ? "text-signal-lime border-signal-lime/40"
      : status === "SUSPENDED"
        ? "text-signal-amber border-signal-amber/40"
        : "text-mist/60 border-white/20";
  return (
    <span className={`border px-2 py-1 font-mono text-[10px] uppercase ${color}`}>
      {status}
    </span>
  );
}
