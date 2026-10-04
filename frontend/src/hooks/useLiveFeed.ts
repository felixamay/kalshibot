"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ClockSynchronizer } from "@/lib/clock";
import type { DashboardPayload, LiveSignal } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ||
  API_URL.replace(/^http/, "ws") + "/ws";
const BOARD_KEY = "kt_board_v2";
/** A reload may paint the last board only for this long. Older saves are finished matches. */
const BOARD_KEEP_MS = 15_000;

function readCachedBoard(): DashboardPayload | null {
  try {
    const raw = sessionStorage.getItem(BOARD_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { savedAt?: number; payload?: DashboardPayload };
    if (!parsed || typeof parsed.savedAt !== "number" || !parsed.payload) return null;
    if (!Array.isArray(parsed.payload.matches)) return null;
    if (Date.now() - parsed.savedAt > BOARD_KEEP_MS) return null;
    return parsed.payload;
  } catch {
    return null;
  }
}

function forgetBoard() {
  try {
    sessionStorage.removeItem(BOARD_KEY);
    sessionStorage.removeItem("kt_board");
  } catch {
    /* private mode */
  }
}

export function useLiveFeed() {
  const [dashboard, setDashboard] = useState<DashboardPayload | null>(null);
  const [signals, setSignals] = useState<Record<string, LiveSignal>>({});
  // A refresh paints this static page before the live check. Keep both lights
  // on. A missed fetch is not a Kalshi outage.
  const [connection, setConnection] = useState<
    "CONNECTED" | "RECONNECTING" | "DISCONNECTED"
  >("CONNECTED");
  const [wsState, setWsState] = useState<
    "CONNECTED" | "RECONNECTING" | "DISCONNECTED"
  >("CONNECTED");
  const clockRef = useRef(new ClockSynchronizer());
  const wsRef = useRef<WebSocket | null>(null);
  const [feedDown, setFeedDown] = useState(false);
  const boardSaveAt = useRef(0);
  const lastBoardAt = useRef(0);
  const lastGoodAt = useRef(0);
  const [serverNow, setServerNow] = useState(() => Date.now());

  const rememberBoard = useCallback((payload: DashboardPayload, force = false) => {
    const now = Date.now();
    if (!force && now - boardSaveAt.current < 4000) return;
    boardSaveAt.current = now;
    try {
      sessionStorage.setItem(BOARD_KEY, JSON.stringify({ savedAt: now, payload }));
    } catch {
      /* quota or private mode */
    }
  }, []);

  const noteFailure = useCallback(() => {
    const age = Date.now() - lastGoodAt.current;
    if (lastGoodAt.current !== 0 && age <= BOARD_KEEP_MS) return;
    setDashboard(null);
    setSignals({});
    setFeedDown(true);
    forgetBoard();
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
      lastGoodAt.current = Date.now();
      setFeedDown(false);
      setDashboard(payload);
      if (payload.connection_status === "CONNECTED") setConnection("CONNECTED");
      if (clockRef.current.lastSyncAt === 0 && payload.server_time_ms) {
        const recv = Date.now();
        clockRef.current.offsetMs = payload.server_time_ms - recv;
        clockRef.current.lastSyncAt = recv;
        setServerNow(clockRef.current.serverNow());
      }
      lastBoardAt.current = Date.now();
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
          if (msg.type === "dashboard" || msg.type === "signal") {
            lastBoardAt.current = recv;
          }
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
            if (msg.payload?.status === "CONNECTED") setConnection("CONNECTED");
          } else if (msg.type === "heartbeat") {
            if (msg.payload?.connection_status === "CONNECTED") {
              setConnection("CONNECTED");
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

    const loadJson = (path: string) =>
      fetch(`${API_URL}${path}`, { cache: "no-store", signal: AbortSignal.timeout(8000) }).then(
        (r) => {
          if (!r.ok) throw new Error(String(r.status));
          return r.json();
        }
      );

    loadJson("/api/health")
      .then((health: { connection_status?: DashboardPayload["connection_status"] }) => {
        if (!stopped && health.connection_status === "CONNECTED") {
          setConnection("CONNECTED");
        }
      })
      .catch(() => {
        /* A missed health check is not a Kalshi outage. Keep the last status. */
      });

    loadJson("/api/dashboard")
      .then((d: DashboardPayload) => {
        if (!stopped) applyDashboard(d);
      })
      .catch(() => {
        if (!stopped) noteFailure();
      });

    return () => {
      stopped = true;
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      wsRef.current?.close();
    };
  }, [applyDashboard, noteFailure, upsertSignal]);

  // Paint the previous board before the browser shows a blank, disconnected page.
  useLayoutEffect(() => {
    const cached = readCachedBoard();
    if (!cached) return;
    setDashboard(cached);
    if (cached.connection_status === "CONNECTED") setConnection(cached.connection_status);
  }, []);

  // High-frequency server-now tick for countdowns (100ms).
  // Match timers recompute from absolute end timestamps on this tick.
  useEffect(() => {
    const id = setInterval(() => {
      setServerNow(clockRef.current.serverNow());
    }, 100);
    return () => clearInterval(id);
  }, []);

  // The socket pushes each new alert. Asking it for the full board every few
  // seconds stalled that push, so the cards and the sound froze until refresh.
  // If no alert or board arrives, fetch the board over HTTP instead.
  useEffect(() => {
    const pull = () => {
      fetch(`${API_URL}/api/dashboard`, { cache: "no-store", signal: AbortSignal.timeout(8000) })
        .then((r) => {
          if (!r.ok) throw new Error(String(r.status));
          return r.json();
        })
        .then((d: DashboardPayload) => {
          lastBoardAt.current = Date.now();
          applyDashboard(d);
        })
        .catch(() => noteFailure());
    };
    const id = setInterval(() => {
      const ws = wsRef.current;
      const stale = Date.now() - lastBoardAt.current > 6000;
      if (stale || !ws || ws.readyState !== WebSocket.OPEN) pull();
    }, 3000);
    return () => clearInterval(id);
  }, [applyDashboard, noteFailure]);

  // Visibility: save the board when leaving, and pull a fresh one when returning.
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "hidden" && dashboard) {
        rememberBoard(dashboard, true);
        return;
      }
      if (document.visibilityState !== "visible") return;
      setServerNow(clockRef.current.serverNow());
      fetch(`${API_URL}/api/dashboard`, { cache: "no-store", signal: AbortSignal.timeout(8000) })
        .then((r) => {
          if (!r.ok) throw new Error(String(r.status));
          return r.json();
        })
        .then((d: DashboardPayload) => {
          lastBoardAt.current = Date.now();
          applyDashboard(d);
        })
        .catch(() => noteFailure());
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [applyDashboard, dashboard, noteFailure, rememberBoard]);

  return {
    dashboard,
    signals: Object.values(signals),
    connection,
    wsState,
    feedDown,
    serverNow,
    clock: clockRef.current,
    apiUrl: API_URL,
  };
}
