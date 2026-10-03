"use client";

import { formatMatchTimer, remainingFromTimestamps } from "@/lib/clock";
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
          <ScoreLink tennis={match.tennis} />
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

      {match.pattern && match.pattern.pattern_name && (
        <PatternStatus
          pattern={match.pattern}
          offering={
            match.display_state === "PATTERN_ENTRY_SIGNAL" ||
            match.display_state === "STRONG_PATTERN_SIGNAL"
          }
        />
      )}

      {match.hold_reason && (
        <p className="mt-3 text-sm leading-relaxed text-mist/75">{match.hold_reason}</p>
      )}

      {(match.pattern?.timeline?.length || match.phase_events?.length) ? (
        <ol className="mt-3 flex gap-2 overflow-x-auto pb-1">
          {(match.pattern?.timeline?.length
            ? match.pattern.timeline.map((mark) => ({
                key: `${mark.kind}-${mark.t_ms}`,
                label: mark.kind.replace(/_/g, " "),
              }))
            : (match.phase_events || []).map((mark) => ({
                key: `${mark.state}-${mark.t_ms}`,
                label: mark.state.replace(/_/g, " "),
              }))
          ).map((mark) => (
            <li
              key={mark.key}
              className="shrink-0 border border-white/10 px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-mist/70"
            >
              {mark.label}
            </li>
          ))}
        </ol>
      ) : null}

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

function ScoreLink({ tennis }: { tennis?: MatchCardType["tennis"] }) {
  const score = tennis && typeof tennis.set_score === "string" ? tennis.set_score : "";
  const href = tennis && typeof tennis.source_url === "string" ? tennis.source_url : "";
  if (!tennis?.available && !score) return null;
  return (
    <p className="mt-1 font-mono text-[11px] uppercase tracking-wider text-mist/60">
      {score ? `Live ${score}` : "Live"}
      {href ? (
        <>
          {" "}
          ·{" "}
          <a href={href} target="_blank" rel="noreferrer" className="text-signal-mint underline">
            {href.includes("espn.com") ? "ESPN score" : "Live score"}
          </a>
        </>
      ) : null}
    </p>
  );
}

function PatternStatus({
  pattern,
  offering,
}: {
  pattern: NonNullable<import("@/lib/types").MatchCard["pattern"]>;
  offering: boolean;
}) {
  return (
    <div className="mt-4 border border-white/10 bg-ink-950/50 px-3 py-3">
      <p className="font-mono text-[10px] uppercase tracking-widest text-signal-mint">
        Pattern status
      </p>
      <p className="mt-1 font-display text-xl">{pattern.pattern_name}</p>
      {offering && (
        <p className="mt-2 font-mono text-sm uppercase tracking-wider text-signal-lime">
          {pattern.player_side === "NO"
            ? `Bet NO on ${pattern.player || "this player"} now`
            : `Bet YES on ${pattern.player || "this player"} now`}
        </p>
      )}
      <div className="mt-2 grid grid-cols-2 sm:grid-cols-4 gap-2 font-mono text-xs">
        <Stat label="Stage" value={pattern.stage || "—"} />
        <Stat label="Similarity" value={pattern.similarity != null ? `${Math.round(pattern.similarity)}%` : "—"} />
        <Stat label="Confidence" value={pattern.confidence != null ? `${Math.round(pattern.confidence)}` : "—"} />
        <Stat label="Entry score" value={pattern.entry_score != null ? `${Math.round(pattern.entry_score)}` : "—"} />
        <Stat label="Progress" value={pattern.progress != null ? `${Math.round(pattern.progress)}%` : "—"} />
        <Stat
          label="Status"
          value={(pattern.decision || "").replace(/_/g, " ") || "—"}
        />
        <Stat
          label="Seen"
          value={`${pattern.occurrences ?? 0} / ${pattern.successes ?? 0} continued`}
        />
      </div>
      {pattern.low_sample_size && (
        <p className="mt-2 font-mono text-[10px] uppercase tracking-wider text-signal-amber">
          Low sample size · observed success is not a future probability
        </p>
      )}
      <div className="mt-3 space-y-1 text-xs text-mist/75">
        <p className="font-mono text-[11px] uppercase tracking-wider text-mist/70">
          Current pattern
        </p>
        {(pattern.reasons || []).slice(0, 6).map((reason) => (
          <p key={reason}>{reason}</p>
        ))}
        <p>
          Typical pullback {pattern.typical_pullback ?? "—"}¢ · typical continuation{" "}
          {pattern.typical_recovery ?? "—"}¢ · current move {pattern.current_move ?? "—"}¢.
        </p>
        {pattern.cluster_name ? <p>{pattern.cluster_name}</p> : null}
      </div>
    </div>
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
