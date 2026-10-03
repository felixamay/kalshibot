"use client";

import { useState } from "react";
import type { LiveSignal } from "@/lib/types";

interface Props {
  signal: LiveSignal | null;
  apiUrl: string;
  token: string | null;
  onClose: () => void;
  onDone: () => void;
}

export function ManualBetModal({ signal, apiUrl, token, onClose, onDone }: Props) {
  const [entryPrice, setEntryPrice] = useState(
    signal ? Math.round(signal.market_price) : 50
  );
  const [amount, setAmount] = useState(10);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  if (!signal) return null;

  const submit = async () => {
    if (!token) {
      setError("Log in to track manual bets.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(`${apiUrl}/api/positions/enter`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          signal_id: signal.signal_id,
          market_ticker: signal.market_ticker,
          direction: signal.direction,
          player: signal.player,
          entry_price: entryPrice,
          amount,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed");
      if (data.kalshi_order_placed) {
        throw new Error("Safety violation: order placement must never be true");
      }
      onDone();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-end md:items-center justify-center bg-black/70 p-4">
      <div className="w-full max-w-md border border-white/15 bg-ink-900 p-5">
        <h2 className="font-display text-2xl">I Placed This Bet</h2>
        <p className="mt-2 font-mono text-xs text-mist/60 uppercase tracking-wider">
          Tracking only · Never sends an order to Kalshi
        </p>
        <p className="mt-4 text-mist">
          {signal.player} — {signal.direction}
        </p>
        <label className="mt-4 block font-mono text-xs uppercase text-mist/50">
          Entry Price (¢)
          <input
            type="number"
            min={1}
            max={99}
            value={entryPrice}
            onChange={(e) => setEntryPrice(Number(e.target.value))}
            className="mt-1 w-full bg-ink-950 border border-white/15 px-3 py-2 text-mist"
          />
        </label>
        <label className="mt-3 block font-mono text-xs uppercase text-mist/50">
          Amount ($)
          <input
            type="number"
            min={1}
            value={amount}
            onChange={(e) => setAmount(Number(e.target.value))}
            className="mt-1 w-full bg-ink-950 border border-white/15 px-3 py-2 text-mist"
          />
        </label>
        {error && <p className="mt-3 text-signal-coral text-sm">{error}</p>}
        <div className="mt-5 flex gap-3">
          <button
            type="button"
            onClick={onClose}
            className="flex-1 border border-white/20 py-3 font-mono text-sm uppercase"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={saving}
            onClick={submit}
            className="flex-1 bg-signal-lime text-ink-950 py-3 font-mono text-sm uppercase font-semibold"
          >
            {saving ? "Saving…" : "Record Entry"}
          </button>
        </div>
      </div>
    </div>
  );
}
