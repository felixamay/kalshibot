"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ClockSynchronizer } from "@/lib/clock";
import type { DashboardPayload, LiveSignal } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ||
  API_URL.replace(/^http/, "ws") + "/ws";
const BOARD_KEY = "kt_board";

function readCachedBoard(): DashboardPayload | null {
  try {
    const raw = sessionStorage.getItem(BOARD_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as DashboardPayload;
    if (!parsed || !Array.isArray(parsed.matches)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function useLiveFeed() {
  const [dashboard, setDashboard] = useState<DashboardPayload | null>(null);
  const [signals, setSignals] = useState<Record<string, LiveSignal>>({});
  // A refresh used to paint DISCONNECTED until the full board downloaded.
  const [connection, setConnection] = useState<
    "CONNECTED" | "RECONNECTING" | "DISCONNECTED"
  >("RECONNECTING");
  const [wsState, setWsState] = useState<
    "CONNECTED" | "RECONNECTING" | "DISCONNECTED"
  >("RECONNECTING");
  const clockRef = useRef(new ClockSynchronizer());
  const wsRef = useRef<WebSocket | null>(null);
  const boardSaveAt = useRef(0);
  const lastMessageAt = useRef(Date.now());
  const [serverNow, setServerNow] = useState(() => Date.now());

  const rememberBoard = useCallback((payload: DashboardPayload, force = false) => {
    const now = Date.now();
    if (!force && now - boardSaveAt.current < 4000) return;
    boardSaveAt.current = now;
    try {
      sessionStorage.setItem(BOARD_KEY, JSON.stringify(payload));
    } catch {
      /* quota or private mode */
    }
  }, []);

  const upsertSignal = useCallback((sig: LiveSignal) => {
    setSignals((prev) => {
      const existing = prev[sig.signal_id];
      // Newer version wins; ignore older versions
      if (existing && existing.signal_version > sig.signal_version) {
        return prev;
      }
      // If another signal same market with higher version, invalidate older
      const next = { ...prev, [sig.signal_id]: sig };
      for (const [id, s] of Object.entries(next)) {
        if (
          id !== sig.signal_id &&
          s.market_ticker === sig.market_ticker &&
          s.signal_version < sig.signal_version &&
          s.actionable
        ) {
          next[id] = {
            ...s,
            actionable: false,
            status: "SUPERSEDED",
            display_label: "SIGNAL EXPIRED",
          };
        }
      }
      return next;
    });
  }, []);

  const applyDashboard = useCallback(
    (payload: DashboardPayload) => {
      setDashboard(payload);
      if (payload.connection_status) setConnection(payload.connection_status);
      if (clockRef.current.lastSyncAt === 0 && payload.server_time_ms) {
        const recv = Date.now();
        clockRef.current.offsetMs = payload.server_time_ms - recv;
        clockRef.current.lastSyncAt = recv;
        setServerNow(clockRef.current.serverNow());
      }
      const incoming = payload.actionable_signals || [];
      const snapshotAt = payload.server_time_ms || 0;
      setSignals((prev) => {
        const next: Record<string, LiveSignal> = {};
        for (const sig of incoming) {
          const existing = prev[sig.signal_id];
          next[sig.signal_id] =
            existing && existing.signal_version > sig.signal_version ? existing : sig;
        }
        // A socket alert can arrive after this snapshot was built. Keep that one.
        for (const sig of Object.values(prev)) {
          if (next[sig.signal_id]) continue;
          if (sig.actionable && sig.created_at_ms > snapshotAt) next[sig.signal_id] = sig;
        }
        return next;
      });
      rememberBoard(payload);
    },
    [rememberBoard]
  );

  useEffect(() => {
    let stopped = false;
    let retry = 0;
    let heartbeatTimer: ReturnType<typeof setInterval> | null = null;

    const connect = () => {
      if (stopped) return;
      setWsState("RECONNECTING");
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        retry = 0;
        setWsState("CONNECTED");
        const sendMs = Date.now();
        ws.send(JSON.stringify({ type: "ping", client_send_ms: sendMs }));
        heartbeatTimer = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(
              JSON.stringify({ type: "ping", client_send_ms: Date.now() })
            );
          }
        }, 5000);
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          const recv = Date.now();
          lastMessageAt.current = recv;
          if (msg.type === "clock_sync" || msg.type === "pong") {
            const p = msg.payload;
            clockRef.current.recordSync(
              p.client_send_ms ?? recv,
              p.server_time_ms,
              recv
            );
            setServerNow(clockRef.current.serverNow());
          } else if (msg.type === "dashboard") {
            applyDashboard(msg.payload);
          } else if (msg.type === "signal") {
            upsertSignal(msg.payload);
          } else if (msg.type === "connection") {
            setConnection(msg.payload.status);
          } else if (msg.type === "heartbeat") {
            if (msg.payload?.connection_status) {
              setConnection(msg.payload.connection_status);
            }
          }
        } catch {
          /* ignore */
        }
      };

      ws.onclose = () => {
        if (heartbeatTimer) clearInterval(heartbeatTimer);
        if (stopped) return;
        setWsState("RECONNECTING");
        const delay = Math.min(500 * 2 ** retry, 8000);
        retry += 1;
        setTimeout(connect, delay);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    fetch(`${API_URL}/api/health`)
      .then((r) => r.json())
      .then((health: { connection_status?: DashboardPayload["connection_status"] }) => {
        if (!stopped && health.connection_status) setConnection(health.connection_status);
      })
      .catch(() => undefined);

    fetch(`${API_URL}/api/dashboard`)
      .then((r) => r.json())
      .then((d: DashboardPayload) => {
        if (!stopped) applyDashboard(d);
      })
      .catch(() => undefined);

    return () => {
      stopped = true;
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      wsRef.current?.close();
    };
  }, [applyDashboard, upsertSignal]);

  // Paint the previous board before the browser shows a blank, disconnected page.
  useLayoutEffect(() => {
    const cached = readCachedBoard();
    if (!cached) return;
    setDashboard(cached);
    if (cached.connection_status) setConnection(cached.connection_status);
  }, []);

  // High-frequency server-now tick for countdowns (100ms).
  // Match timers recompute from absolute end timestamps on this tick.
  useEffect(() => {
    const id = setInterval(() => {
      setServerNow(clockRef.current.serverNow());
    }, 100);
    return () => clearInterval(id);
  }, []);

  // Refresh match state about once a second. The countdown itself does not
  // wait on this — it runs from observation_ends_ms and serverNow.
  useEffect(() => {
    const id = setInterval(() => {
      const ws = wsRef.current;
      const quiet = Date.now() - lastMessageAt.current > 8000;
      if (!quiet && ws && ws.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({ type: "get_dashboard", client_send_ms: Date.now() })
        );
        return;
      }
      fetch(`${API_URL}/api/dashboard`)
        .then((r) => r.json())
        .then((d: DashboardPayload) => applyDashboard(d))
        .catch(() => undefined);
    }, 3000);
    return () => clearInterval(id);
  }, [applyDashboard]);

  // Visibility: save the board when leaving, and pull a fresh one when returning.
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "hidden" && dashboard) {
        rememberBoard(dashboard, true);
        return;
      }
      if (document.visibilityState !== "visible") return;
      setServerNow(clockRef.current.serverNow());
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "get_dashboard", client_send_ms: Date.now() }));
        return;
      }
      fetch(`${API_URL}/api/dashboard`)
        .then((r) => r.json())
        .then((d: DashboardPayload) => applyDashboard(d))
        .catch(() => undefined);
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [applyDashboard, dashboard, rememberBoard]);

  return {
    dashboard,
    signals: Object.values(signals),
    connection,
    wsState,
    serverNow,
    clock: clockRef.current,
    apiUrl: API_URL,
  };
}
