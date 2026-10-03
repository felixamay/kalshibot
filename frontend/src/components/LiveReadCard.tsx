"use client";

import type { MatchCard } from "@/lib/types";

export function LiveReadCard({
  match,
  marketCount,
}: {
  match: MatchCard;
  serverNow: number;
  snapshotServerTimeMs: number;
  marketCount: number;
}) {
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
          {typeof match.tennis?.set_score === "string" && match.tennis.set_score ? (
            <p className="mt-2 font-mono text-sm text-signal-mint">
              Live {match.tennis.set_score}
              {typeof match.tennis.source_url === "string" ? (
                <>
                  {" "}
                  ·{" "}
                  <a
                    href={match.tennis.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="underline"
                  >
                    ESPN score
                  </a>
                </>
              ) : null}
            </p>
          ) : null}
        </div>
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
          label="Adjusted edge"
          value={
            match.read?.uncertainty_adjusted_edge != null
              ? `${match.read.uncertainty_adjusted_edge >= 0 ? "+" : ""}${(match.read.uncertainty_adjusted_edge * 100).toFixed(1)}pp`
              : match.estimated_edge != null
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

      {match.read?.close_to_signal && (
        <p className="mt-5 font-mono text-sm uppercase tracking-wider text-signal-amber">
          Close to signal · confidence {Math.round(match.confidence ?? 0)} / 80 ·
          edge{" "}
          {match.read.uncertainty_adjusted_edge != null
            ? `${(match.read.uncertainty_adjusted_edge * 100).toFixed(1)}%`
            : "—"}{" "}
          / required{" "}
          {match.read.dynamic_min_edge != null
            ? `${(match.read.dynamic_min_edge * 100).toFixed(1)}%`
            : "—"}{" "}
          · confirmation {match.read.confirmation_needed ?? 3} needed
        </p>
      )}

      <div className="mt-5 flex flex-wrap gap-3 font-mono text-[11px] uppercase tracking-wider text-mist/60">
        <span>Liquidity {match.read?.liquidity_quality || "—"}</span>
        <span>Spread {match.read?.spread_quality || "—"}</span>
        <span>
          Market quality{" "}
          {match.read?.market_quality_score != null
            ? Math.round(match.read.market_quality_score)
            : "—"}
        </span>
        <span>Uncertainty {match.read?.model_uncertainty || "—"}</span>
        <span>
          Signals {match.signals_today ?? match.signals_emitted ?? 0} /{" "}
          {match.max_signals_per_match ?? 4}
        </span>
      </div>

      <p className="mt-4 max-w-3xl text-base leading-relaxed text-mist/80">
        {match.hold_reason || match.read?.explanation || "Scoring this market against the bet gates."}
      </p>
      {match.read?.reasons && match.read.reasons.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm text-mist/70">
          {match.read.reasons.slice(0, 4).map((reason) => (
            <li key={reason}>+ {reason}</li>
          ))}
        </ul>
      )}
      <p className="mt-3 font-mono text-[11px] uppercase tracking-wider text-mist/40">
        A pattern is made only after every two serves, and only after it has been discovered
        on a live match, past the first serve, while the game is still at or under 85%. The last 5 serves are skipped
        when the winner is clear. You bet that player yourself on Kalshi.
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
