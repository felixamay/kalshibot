"use client";

import { useEffect, useState } from "react";

const FIELDS: Array<{ key: string; label: string; tip: string; step: string }> = [
  {
    key: "watch_confidence",
    label: "Watch confidence",
    tip: "Show WATCH around this score. WATCH is not an instruction to place a bet.",
    step: "1",
  },
  {
    key: "min_bet_confidence",
    label: "Bet signal confidence",
    tip: "Minimum signal confidence for BET SIGNAL. This is not the player's win probability.",
    step: "1",
  },
  {
    key: "strong_bet_confidence",
    label: "Strong signal confidence",
    tip: "STRONG BET SIGNAL confidence. 90 does not mean the player has a 90% chance to win.",
    step: "1",
  },
  {
    key: "excellent_market_min_edge",
    label: "Excellent market minimum edge",
    tip: "Required adjusted edge when liquidity is high and the spread is 1¢ or tighter.",
    step: "0.005",
  },
  {
    key: "min_net_edge",
    label: "Normal minimum edge",
    tip: "Required adjusted edge in a good market with a spread of about 2¢.",
    step: "0.005",
  },
  {
    key: "medium_market_min_edge",
    label: "Medium market minimum edge",
    tip: "Required adjusted edge when liquidity or the spread is only medium.",
    step: "0.005",
  },
  {
    key: "poor_market_min_edge",
    label: "Poor market minimum edge",
    tip: "Required adjusted edge when liquidity is low or the spread is wide.",
    step: "0.005",
  },
  {
    key: "strong_net_edge",
    label: "Strong signal edge",
    tip: "Adjusted edge required before a STRONG BET SIGNAL, on top of the confidence gate.",
    step: "0.005",
  },
  {
    key: "entry_confirmation_count",
    label: "Entry confirmation count",
    tip: "How many consecutive passing updates are required. A single spike cannot become a bet.",
    step: "1",
  },
  {
    key: "max_signals_per_match",
    label: "Maximum signals per match",
    tip: "Cap on entry signals for one match.",
    step: "1",
  },
  {
    key: "pattern_watch_score",
    label: "Pattern watch score",
    tip: "Show PATTERN WATCH at this entry score. A watch is not an instruction to bet.",
    step: "1",
  },
  {
    key: "pattern_entry_score",
    label: "Pattern entry score",
    tip: "Minimum pattern entry score. Still needs confirmation, a fresh book, and a pattern that has repeated.",
    step: "1",
  },
  {
    key: "strong_pattern_entry_score",
    label: "Strong pattern score",
    tip: "STRONG PATTERN SIGNAL score. This is not a guaranteed result.",
    step: "1",
  },
  {
    key: "min_pattern_confidence",
    label: "Minimum pattern confidence",
    tip: "Pattern confidence required before an entry signal. Not a win probability.",
    step: "1",
  },
  {
    key: "pattern_confirmation_count",
    label: "Pattern confirmations",
    tip: "A pattern that exists and is about to begin alerts without waiting for this count.",
    step: "1",
  },
  {
    key: "pattern_late_stage_percent",
    label: "Late pattern percent",
    tip: "Do not enter after the pattern has already traveled this much of its typical move.",
    step: "1",
  },
  {
    key: "pattern_completed_percent",
    label: "Completed pattern percent",
    tip: "Treat the pattern as finished at this progress. No entry.",
    step: "1",
  },
  {
    key: "pattern_weaken_health",
    label: "Weakening health",
    tip: "Below this pattern health, show PATTERN WEAKENING.",
    step: "1",
  },
  {
    key: "pattern_risk_health",
    label: "At-risk health",
    tip: "Below this pattern health, prepare to exit.",
    step: "1",
  },
  {
    key: "pattern_broken_health",
    label: "Broken health",
    tip: "Below this pattern health, the pattern is broken and the exit warning fires. Nothing is sold automatically.",
    step: "1",
  },
];

export function StrategySettings({
  apiUrl,
  token,
}: {
  apiUrl: string;
  token: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [values, setValues] = useState<Record<string, number>>({});
  const [saved, setSaved] = useState("");

  useEffect(() => {
    fetch(`${apiUrl}/api/config`)
      .then((r) => r.json())
      .then((data) => {
        const next: Record<string, number> = {};
        for (const field of FIELDS) {
          if (typeof data[field.key] === "number") next[field.key] = data[field.key];
        }
        setValues(next);
      })
      .catch(() => undefined);
  }, [apiUrl]);

  const save = async () => {
    if (!token) {
      setSaved("Log in to change strategy settings.");
      return;
    }
    const res = await fetch(`${apiUrl}/api/config/strategy`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(values),
    });
    setSaved(res.ok ? "Strategy updated for this server." : "Could not save settings.");
  };

  return (
    <section className="mt-10 border border-white/10">
      <button
        type="button"
        className="w-full px-4 py-3 text-left font-mono text-xs uppercase tracking-widest text-mist/70"
        onClick={() => setOpen((v) => !v)}
      >
        Strategy settings {open ? "−" : "+"}
      </button>
      {open && (
        <div className="border-t border-white/10 p-4 grid gap-4 md:grid-cols-2">
          {FIELDS.map((field) => (
            <label key={field.key} className="block" title={field.tip}>
              <span className="font-mono text-[10px] uppercase tracking-widest text-mist/50">
                {field.label}
              </span>
              <input
                className="mt-1 w-full bg-ink-950 border border-white/15 px-3 py-2 font-mono"
                type="number"
                step={field.step}
                value={values[field.key] ?? ""}
                onChange={(e) =>
                  setValues((prev) => ({ ...prev, [field.key]: Number(e.target.value) }))
                }
              />
              <span className="mt-1 block text-xs text-mist/45">{field.tip}</span>
            </label>
          ))}
          <div className="md:col-span-2 flex items-center gap-3">
            <button
              type="button"
              className="border border-white/20 px-4 py-2 font-mono text-xs uppercase"
              onClick={save}
            >
              Save
            </button>
            <span className="font-mono text-xs text-mist/50">{saved}</span>
          </div>
        </div>
      )}
    </section>
  );
}
