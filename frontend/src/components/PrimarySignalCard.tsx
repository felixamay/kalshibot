"use client";

import { useSignalCountdown } from "@/hooks/useSignalCountdown";
import { useSignalSounds } from "@/hooks/useSignalSounds";
import type { LiveSignal } from "@/lib/types";

interface Props {
  signal: LiveSignal;
  serverNow: number;
  alertsEnabled: boolean;
  onPlaced: (signal: LiveSignal) => void;
  onViewAnalysis: (signal: LiveSignal) => void;
}

export function PrimarySignalCard({
  signal,
  serverNow,
  alertsEnabled,
  onPlaced,
  onViewAnalysis,
}: Props) {
  const active = signal.actionable && signal.status === "ACTIVE";
  const cd = useSignalCountdown(
    signal.expires_at_ms,
    signal.original_ttl_ms,
    serverNow,
    active
  );

  useSignalSounds(
    signal.signal_id,
    active && !cd.expired,
    cd.remainingMs,
    alertsEnabled
  );

  const showActionable = active && !cd.expired;
  const label = showActionable ? signal.raw_signal_type || signal.signal_type : signal.display_label;
  const sub = showActionable ? null : signal.display_sublabel;

  const urgencyText =
    cd.urgency === "FINAL"
      ? "SIGNAL ABOUT TO EXPIRE"
      : cd.urgency === "CAUTION"
        ? "ACT SOON"
        : null;

  const urgencyClass =
    cd.urgency === "FINAL"
      ? "border-signal-coral text-signal-coral"
      : cd.urgency === "CAUTION"
        ? "border-signal-amber text-signal-amber"
        : "border-signal-lime text-signal-lime";

  return (
    <section
      className={`relative overflow-hidden rounded-sm border bg-ink-800/80 p-5 md:p-7 backdrop-blur-sm ${
        showActionable ? urgencyClass : "border-white/10 text-mist"
      }`}
      aria-live="assertive"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-display text-3xl md:text-5xl tracking-wide uppercase animate-pulse-signal">
            {showActionable ? label.replace(/_/g, " ") : label}
          </p>
          {sub && (
            <p className="mt-1 font-mono text-sm uppercase tracking-widest text-signal-amber">
              {sub}
            </p>
          )}
          <p className="mt-3 font-display text-xl md:text-3xl text-mist">
            {signal.player} — {signal.direction}
          </p>
          {signal.pattern_name && (
            <p className="mt-2 font-mono text-xs uppercase tracking-widest text-mist/70">
              {signal.pattern_name}
              {signal.pattern_similarity != null
                ? ` · similarity ${Math.round(signal.pattern_similarity)}%`
                : ""}
              {signal.pattern_confidence != null
                ? ` · confidence ${Math.round(signal.pattern_confidence)}`
                : ""}
            </p>
          )}
        </div>
        <div className="text-right font-mono text-xs text-mist/60">
          <div>v{signal.signal_version}</div>
          <div>{signal.signal_id}</div>
        </div>
      </div>

      <div className="mt-6 grid grid-cols-2 md:grid-cols-4 gap-4 font-mono">
        <Metric label="Current" value={`${Math.round(signal.market_price)}¢`} />
        {signal.entry_zone_low != null && signal.entry_zone_high != null && (
          <Metric
            label="Entry zone"
            value={`${Math.round(signal.entry_zone_low)}–${Math.round(signal.entry_zone_high)}¢`}
          />
        )}
        <Metric
          label="Max Entry"
          value={`${Math.round(signal.maximum_entry_price)}¢`}
          emphasize
        />
        <Metric
          label="Model"
          value={`${Math.round(signal.model_probability * 100)}%`}
        />
        <Metric
          label="Net Edge"
          value={`${signal.net_edge >= 0 ? "+" : ""}${(signal.net_edge * 100).toFixed(1)}%`}
        />
      </div>

      <div className="mt-4 flex flex-wrap gap-6 font-mono text-sm">
        <div>
          <span className="text-mist/50">Confidence </span>
          <span className="text-signal-mint text-lg">
            {Math.round(signal.confidence)} / 100
          </span>
          <p className="text-[10px] uppercase tracking-wider text-mist/40">
            Signal confidence, not win probability
          </p>
        </div>
        <div>
          <span className="text-mist/50">Generated </span>
          <span>
            {new Date(signal.created_at_ms).toLocaleTimeString(undefined, {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
              fractionalSecondDigits: 3,
            } as Intl.DateTimeFormatOptions)}
          </span>
        </div>
      </div>

      {/* Countdown — prominent on mobile, always visible */}
      <div className="mt-6 sticky top-0 z-10">
        {showActionable ? (
          <>
            <p className="font-mono text-xs uppercase tracking-[0.2em] text-mist/60">
              Signal valid for
            </p>
            <div className="flex items-end gap-3 mt-1">
              <span
                className={`font-display text-6xl md:text-7xl leading-none tabular-nums ${
                  cd.urgency === "FINAL"
                    ? "text-signal-coral"
                    : cd.urgency === "CAUTION"
                      ? "text-signal-amber"
                      : "text-signal-lime"
                }`}
              >
                {cd.remainingDisplay}
              </span>
              <span className="pb-2 font-mono text-lg text-mist/70">SEC</span>
            </div>
            {urgencyText && (
              <p
                className={`mt-2 font-mono text-sm uppercase tracking-widest ${
                  cd.urgency === "FINAL" ? "text-signal-coral" : "text-signal-amber"
                }`}
              >
                {urgencyText}
              </p>
            )}
            <div className="mt-3 h-2 w-full bg-ink-950 overflow-hidden rounded-sm">
              <div
                className={`h-full transition-none ${
                  cd.urgency === "FINAL"
                    ? "bg-signal-coral"
                    : cd.urgency === "CAUTION"
                      ? "bg-signal-amber"
                      : "bg-signal-lime"
                }`}
                style={{ width: `${cd.progress * 100}%` }}
              />
            </div>
          </>
        ) : (
          <div className="py-4">
            <p className="font-display text-3xl text-mist/80">SIGNAL EXPIRED</p>
            <p className="font-mono text-signal-amber mt-1">
              {sub || signal.expiration_message || "REANALYZING..."}
            </p>
            {signal.expiration_reason === "PRICE_MOVED" && (
              <p className="mt-2 font-mono text-sm">
                Current: {Math.round(signal.expiration_price || signal.market_price)}¢ · Max:{" "}
                {Math.round(signal.maximum_entry_price)}¢
              </p>
            )}
          </div>
        )}
      </div>

      {showActionable && (
        <div className="mt-6 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={() => onViewAnalysis(signal)}
            className="border border-mist/30 px-4 py-3 font-mono text-sm uppercase tracking-wider hover:border-signal-mint hover:text-signal-mint transition"
          >
            View Analysis
          </button>
          <button
            type="button"
            onClick={() => onPlaced(signal)}
            className="bg-signal-lime text-ink-950 px-4 py-3 font-mono text-sm uppercase tracking-wider font-semibold hover:brightness-110 transition"
          >
            I Placed This Bet
          </button>
        </div>
      )}

      <p className="mt-4 text-[11px] font-mono text-mist/40 uppercase tracking-wider">
        Advisory only · You place every bet manually on Kalshi · No auto-orders
      </p>
    </section>
  );
}

function Metric({
  label,
  value,
  emphasize,
}: {
  label: string;
  value: string;
  emphasize?: boolean;
}) {
  return (
    <div>
      <div className="text-[11px] uppercase tracking-widest text-mist/50">{label}</div>
      <div
        className={`text-xl md:text-2xl ${emphasize ? "text-signal-amber" : "text-mist"}`}
      >
        {value}
      </div>
    </div>
  );
}
