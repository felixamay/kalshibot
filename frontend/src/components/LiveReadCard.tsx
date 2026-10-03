"use client";

import { formatMatchTimer, matchClockEndsMs, remainingFromTimestamps } from "@/lib/clock";
import type { MatchCard } from "@/lib/types";

export function LiveReadCard({
  match,
  serverNow,
  snapshotServerTimeMs,
  marketCount,
}: {
  match: MatchCard;
  serverNow: number;
  snapshotServerTimeMs: number;
  marketCount: number;
}) {
  const studying = match.display_state === "STUDYING_MATCH";
  const observationEnds = matchClockEndsMs(
    match.observation_ends_ms,
    snapshotServerTimeMs,
    match.observation_remaining_ms
  );
  const observationRemaining = remainingFromTimestamps(observationEnds, serverNow);
  const stateLabel = match.display_state.replace(/_/g, " ");

  return (
    <section className="border border-white/10 bg-ink-800/80 p-5 md:p-8">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-signal-mint">
            Live read · {marketCount} markets · not a bet
          </p>
          <h2 className="font-display text-4xl md:text-6xl tracking-tight mt-2 text-mist">
            {stateLabel}
          </h2>
          <p className="mt-3 font-display text-2xl md:text-3xl">
            {match.player_a}
            <span className="text-mist/40 mx-2">vs</span>
            {match.player_b}
          </p>
          <p className="mt-1 font-mono text-xs uppercase tracking-widest text-mist/50">
            {match.tournament || "Tennis"} · {match.market_ticker}
          </p>
        </div>
        {studying && (
          <div className="text-right">
            <p className="font-mono text-[11px] uppercase tracking-widest text-mist/50">
              Observation
            </p>
            <p className="font-mono text-4xl tabular-nums text-mist">
              {formatMatchTimer(observationRemaining)}
            </p>
          </div>
        )}
      </div>

      <div className="mt-6 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4 font-mono">
        <ReadStat
          label="Kalshi"
          value={
            match.kalshi_probability != null
              ? `${Math.round(match.kalshi_probability * 100)}¢`
              : "—"
          }
        />
        <ReadStat
          label="Model"
          value={
            match.model_probability != null
              ? `${Math.round(match.model_probability * 100)}%`
              : "—"
          }
        />
        <ReadStat
          label="Net edge"
          value={
            match.estimated_edge != null
              ? `${match.estimated_edge >= 0 ? "+" : ""}${(match.estimated_edge * 100).toFixed(1)}pp`
              : "—"
          }
        />
        <ReadStat
          label="Confidence"
          value={match.confidence != null ? `${Math.round(match.confidence)}` : "—"}
        />
        <ReadStat
          label="Spread"
          value={match.spread != null ? `${match.spread.toFixed(1)}¢` : "—"}
        />
        <ReadStat
          label="Liquidity"
          value={match.liquidity != null ? `${Math.round(match.liquidity)}` : "—"}
        />
      </div>

      <p className="mt-6 max-w-3xl text-base leading-relaxed text-mist/80">
        {match.hold_reason || "Scoring this market against the bet gates."}
      </p>
      <p className="mt-3 font-mono text-[11px] uppercase tracking-wider text-mist/40">
        BET NOW only if confidence is at least 85, net edge is at least 4pp, and
        the book stays tradable for 5 updates in a row.
      </p>
    </section>
  );
}

function ReadStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-widest text-mist/45">{label}</div>
      <div className="text-xl text-mist mt-1">{value}</div>
    </div>
  );
}
