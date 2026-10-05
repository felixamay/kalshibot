"use client";

import { useEffect, useState } from "react";
import { considerStrategyAlert, playSignalAudio } from "@/lib/signalAudio";

type Settings = {
  auto_entry: boolean;
  auto_exit: boolean;
  paused: boolean;
  emergency_stop: boolean;
  bet_amount: number;
  entry_type: "YES" | "NO" | "EITHER";
  entry_price: number;
  take_profit_percent: number;
  stop_loss_percent: number;
  trailing_stop_enabled: boolean;
  trailing_activation_percent: number;
  trailing_stop_percent: number;
  max_trades_per_match: number;
  cooldown_seconds: number;
  max_position_per_match: number;
  max_daily_loss: number;
  max_daily_exposure: number;
  market_scope: "ALL_LIVE" | "SELECTED";
  selected_tickers: string[];
  one_direction_per_market: boolean;
};

type Position = {
  position_id: string;
  player: string;
  side: string;
  status: string;
  bet_amount: number;
  entry_price: number | null;
  current_exit_price: number | null;
  pnl_percent: number;
  pnl_dollars: number;
  peak_price: number | null;
  take_profit_target: number | null;
  stop_loss_level: number | null;
  trailing_stop_level: number | null;
};

const EMPTY: Settings = {
  auto_entry: false,
  auto_exit: false,
  paused: false,
  emergency_stop: false,
  bet_amount: 10,
  entry_type: "YES",
  entry_price: 50,
  take_profit_percent: 8,
  stop_loss_percent: 4,
  trailing_stop_enabled: true,
  trailing_activation_percent: 5,
  trailing_stop_percent: 3,
  max_trades_per_match: 2,
  cooldown_seconds: 60,
  max_position_per_match: 25,
  max_daily_loss: 25,
  max_daily_exposure: 50,
  market_scope: "ALL_LIVE",
  selected_tickers: [],
  one_direction_per_market: true,
};

export function StrategySettings({
  apiUrl,
  token,
  alertsEnabled = false,
}: {
  apiUrl: string;
  token: string | null;
  alertsEnabled?: boolean;
}) {
  const [values, setValues] = useState<Settings>(EMPTY);
  const [tickers, setTickers] = useState("");
  const [positions, setPositions] = useState<Position[]>([]);
  const [reasons, setReasons] = useState<Array<{ market_ticker: string; detail: string }>>([]);
  const [marketData, setMarketData] = useState("DISCONNECTED");
  const [tradingApi, setTradingApi] = useState("DISCONNECTED");
  const [saved, setSaved] = useState("");
  const [confirmOpen, setConfirmOpen] = useState<"save" | "reset" | null>(null);
  const [closeConfirm, setCloseConfirm] = useState(false);

  const headers = token
    ? { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }
    : undefined;

  const load = async () => {
    if (!token) return;
    const [settingsRes, statusRes, positionRes] = await Promise.all([
      fetch(`${apiUrl}/api/strategy/settings`, { headers, cache: "no-store" }),
      fetch(`${apiUrl}/api/strategy/status`, { headers, cache: "no-store" }),
      fetch(`${apiUrl}/api/strategy/positions`, { headers, cache: "no-store" }),
    ]);
    if (settingsRes.ok) {
      const data = await settingsRes.json();
      const settings = { ...EMPTY, ...data.settings } as Settings;
      setValues(settings);
      setTickers((settings.selected_tickers || []).join(", "));
    }
    if (statusRes.ok) {
      const data = await statusRes.json();
      setMarketData(data.market_data || "DISCONNECTED");
      setTradingApi(data.trading_api || "DISCONNECTED");
    }
    if (positionRes.ok) {
      const data = await positionRes.json();
      setPositions(data.positions || []);
      setReasons(data.reasons || []);
      if (alertsEnabled) {
        const now = Date.now();
        for (const event of data.events || []) {
          const key = `${event.market_ticker}|${event.kind}`;
          if (considerStrategyAlert(key, now)) playSignalAudio(key);
        }
      }
    }
  };

  useEffect(() => {
    load().catch(() => undefined);
    if (!token) return undefined;
    const timer = setInterval(() => load().catch(() => undefined), 3000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiUrl, token, alertsEnabled]);

  const payload = (apply?: boolean) => ({
    ...values,
    selected_tickers: tickers.split(",").map((item) => item.trim()).filter(Boolean),
    apply_to_open: apply,
  });

  const save = async (apply?: boolean) => {
    if (!token) {
      setSaved("Log in to save your strategy.");
      return;
    }
    const res = await fetch(`${apiUrl}/api/strategy/settings`, {
      method: "PUT",
      headers,
      body: JSON.stringify(payload(apply)),
    });
    if (res.status === 409) {
      setConfirmOpen("save");
      setSaved("A position is open.");
      return;
    }
    setConfirmOpen(null);
    setSaved(res.ok ? "Settings saved for your account." : "Could not save settings.");
    if (res.ok) await load();
  };

  const post = async (path: string, body?: unknown) => {
    if (!token) return;
    const res = await fetch(`${apiUrl}${path}`, { method: "POST", headers, body: body ? JSON.stringify(body) : "{}" });
    if (res.status === 409 && path.endsWith("/reset")) {
      setConfirmOpen("reset");
      setSaved("A position is open.");
      return;
    }
    await load();
  };

  const number = (key: keyof Settings, step = "1") => (
    <input
      className="mt-1 w-full bg-ink-950 border border-white/15 px-3 py-2 font-mono"
      type="number"
      step={step}
      value={String(values[key] ?? "")}
      onChange={(event) => setValues((prev) => ({ ...prev, [key]: Number(event.target.value) }))}
    />
  );

  return (
    <section className="mt-10 border border-white/10 p-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 className="font-display text-3xl">Strategy settings</h2>
        <div className="font-mono text-[11px] uppercase tracking-widest text-mist/70">
          <div>Market data: {marketData}</div>
          <div>Trading API: {tradingApi}</div>
        </div>
      </div>
      <p className="mt-2 text-sm text-mist/60">
        These settings belong to your account. Automatic orders stay off until you turn them on.
      </p>
      {!token && <p className="mt-3 font-mono text-xs text-mist/50">Log in to edit and save your strategy.</p>}
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">
          Auto entry
          <select className="mt-1 w-full bg-ink-950 border border-white/15 px-3 py-2 font-mono" value={values.auto_entry ? "ON" : "OFF"} onChange={(event) => setValues((prev) => ({ ...prev, auto_entry: event.target.value === "ON" }))}>
            <option>OFF</option>
            <option>ON</option>
          </select>
        </label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">
          Auto exit
          <select className="mt-1 w-full bg-ink-950 border border-white/15 px-3 py-2 font-mono" value={values.auto_exit ? "ON" : "OFF"} onChange={(event) => setValues((prev) => ({ ...prev, auto_exit: event.target.value === "ON" }))}>
            <option>OFF</option>
            <option>ON</option>
          </select>
        </label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">Bet amount ${number("bet_amount", "1")}</label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">
          Entry type
          <select className="mt-1 w-full bg-ink-950 border border-white/15 px-3 py-2 font-mono" value={values.entry_type} onChange={(event) => setValues((prev) => ({ ...prev, entry_type: event.target.value as Settings["entry_type"] }))}>
            <option value="YES">Buy YES</option>
            <option value="NO">Buy NO</option>
            <option value="EITHER">Either side</option>
          </select>
        </label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">Entry price ¢{number("entry_price", "1")}</label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">Take profit %{number("take_profit_percent", "0.1")}</label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">Stop loss %{number("stop_loss_percent", "0.1")}</label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">
          Trailing stop
          <select className="mt-1 w-full bg-ink-950 border border-white/15 px-3 py-2 font-mono" value={values.trailing_stop_enabled ? "ON" : "OFF"} onChange={(event) => setValues((prev) => ({ ...prev, trailing_stop_enabled: event.target.value === "ON" }))}>
            <option>OFF</option>
            <option>ON</option>
          </select>
        </label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">Trailing stop %{number("trailing_stop_percent", "0.1")}</label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">Trailing activation %{number("trailing_activation_percent", "0.1")}</label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">Max trades per match{number("max_trades_per_match")}</label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">Cooldown after exit (seconds){number("cooldown_seconds")}</label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">Max position per match ${number("max_position_per_match")}</label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">Max daily loss ${number("max_daily_loss")}</label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">Max daily exposure ${number("max_daily_exposure")}</label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50">
          Markets
          <select className="mt-1 w-full bg-ink-950 border border-white/15 px-3 py-2 font-mono" value={values.market_scope} onChange={(event) => setValues((prev) => ({ ...prev, market_scope: event.target.value as Settings["market_scope"] }))}>
            <option value="ALL_LIVE">All live tennis</option>
            <option value="SELECTED">Selected matches only</option>
          </select>
        </label>
        <label className="font-mono text-[10px] uppercase tracking-widest text-mist/50 md:col-span-2">
          Selected tickers
          <input className="mt-1 w-full bg-ink-950 border border-white/15 px-3 py-2 font-mono" value={tickers} onChange={(event) => setTickers(event.target.value)} placeholder="KXTEST-A, KXTEST-B" />
        </label>
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <button type="button" className="border border-white/20 px-4 py-2 font-mono text-xs uppercase" onClick={() => save()}>Save settings</button>
        <button type="button" className="border border-white/20 px-4 py-2 font-mono text-xs uppercase" onClick={() => post("/api/strategy/reset")}>Reset to defaults</button>
        <button type="button" className="border border-white/20 px-4 py-2 font-mono text-xs uppercase" onClick={() => post("/api/strategy/pause")}>Pause strategy</button>
        <button type="button" className="border border-white/20 px-4 py-2 font-mono text-xs uppercase" onClick={() => post("/api/strategy/resume")}>Resume strategy</button>
        <button type="button" className="border border-white/20 px-4 py-2 font-mono text-xs uppercase" onClick={() => post("/api/strategy/stop")}>Stop all auto trading</button>
        <button type="button" className="border border-white/20 px-4 py-2 font-mono text-xs uppercase" onClick={() => setCloseConfirm(true)}>Close all positions</button>
      </div>
      <p className="mt-2 font-mono text-xs text-mist/50">{saved}</p>
      {confirmOpen && (
        <div className="mt-3 border border-white/15 p-3">
          <p className="font-mono text-sm">Apply to current position?</p>
          <div className="mt-2 flex gap-2">
            <button type="button" className="border border-white/20 px-3 py-2 font-mono text-xs" onClick={() => (confirmOpen === "reset" ? post("/api/strategy/reset", { apply_to_open: true }) : save(true))}>Yes</button>
            <button type="button" className="border border-white/20 px-3 py-2 font-mono text-xs" onClick={() => (confirmOpen === "reset" ? post("/api/strategy/reset", { apply_to_open: false }) : save(false))}>No</button>
          </div>
        </div>
      )}
      {closeConfirm && (
        <div className="mt-3 border border-white/15 p-3">
          <p className="font-mono text-sm">Close all positions? This sends exit orders.</p>
          <div className="mt-2 flex gap-2">
            <button type="button" className="border border-white/20 px-3 py-2 font-mono text-xs" onClick={() => { setCloseConfirm(false); post("/api/strategy/close-all", { confirm: true }); }}>Confirm</button>
            <button type="button" className="border border-white/20 px-3 py-2 font-mono text-xs" onClick={() => setCloseConfirm(false)}>Cancel</button>
          </div>
        </div>
      )}
      <div className="mt-6">
        <h3 className="font-mono text-xs uppercase tracking-widest text-mist/50">Open positions</h3>
        {positions.length === 0 ? (
          <p className="mt-2 text-sm text-mist/50">No open position.</p>
        ) : (
          positions.map((position) => (
            <div key={position.position_id} className="mt-3 border border-white/10 p-3 font-mono text-xs">
              <div>{position.player} · {position.side} · {position.status}</div>
              <div>Bet ${position.bet_amount} · Entry {position.entry_price ?? "—"}¢ · Exit {position.current_exit_price ?? "—"}¢</div>
              <div>P&L {position.pnl_percent}% · ${position.pnl_dollars}</div>
              <div>Peak {position.peak_price ?? "—"}¢ · Target {position.take_profit_target ?? "—"}¢ · Stop {position.stop_loss_level ?? "—"}¢ · Trail {position.trailing_stop_level ?? "—"}¢</div>
            </div>
          ))
        )}
        {reasons.map((reason) => (
          <p key={`${reason.market_ticker}-${reason.detail}`} className="mt-2 text-xs text-mist/50">{reason.market_ticker}: {reason.detail}</p>
        ))}
      </div>
    </section>
  );
}
