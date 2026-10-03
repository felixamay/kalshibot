"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ClockSynchronizer } from "@/lib/clock";
import type { DashboardPayload, LiveSignal } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ||
  API_URL.replace(/^http/, "ws") + "/ws";

export function useLiveFeed() {
  const [dashboard, setDashboard] = useState<DashboardPayload | null>(null);
  const [signals, setSignals] = useState<Record<string, LiveSignal>>({});
  const [connection, setConnection] = useState<
    "CONNECTED" | "RECONNECTING" | "DISCONNECTED"
  >("DISCONNECTED");
  const [wsState, setWsState] = useState<
    "CONNECTED" | "RECONNECTING" | "DISCONNECTED"
  >("DISCONNECTED");
  const clockRef = useRef(new ClockSynchronizer());
  const wsRef = useRef<WebSocket | null>(null);
  const [serverNow, setServerNow] = useState(() => Date.now());

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
          if (msg.type === "clock_sync" || msg.type === "pong") {
            const p = msg.payload;
            clockRef.current.recordSync(
              p.client_send_ms ?? recv,
              p.server_time_ms,
              recv
            );
            setServerNow(clockRef.current.serverNow());
          } else if (msg.type === "dashboard") {
            setDashboard(msg.payload);
            setConnection(msg.payload.connection_status);
            for (const s of msg.payload.actionable_signals || []) {
              upsertSignal(s);
            }
          } else if (msg.type === "signal") {
            upsertSignal(msg.payload);
          } else if (msg.type === "connection") {
            setConnection(msg.payload.status);
          } else if (msg.type === "heartbeat") {
            if (msg.payload?.server_time_ms) {
              clockRef.current.offsetMs =
                msg.payload.server_time_ms - Date.now();
              setServerNow(clockRef.current.serverNow());
            }
            if (msg.payload?.connection_status) {
              setConnection(msg.payload.connection_status);
            }
          }
        } catch {
          /* ignore */
        }
      };

      ws.onclose = () => {
        setWsState("DISCONNECTED");
        if (heartbeatTimer) clearInterval(heartbeatTimer);
        const delay = Math.min(1000 * 2 ** retry, 15000);
        retry += 1;
        setTimeout(connect, delay);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    // REST fallback for initial load
    fetch(`${API_URL}/api/dashboard`)
      .then((r) => r.json())
      .then((d: DashboardPayload) => {
        setDashboard(d);
        setConnection(d.connection_status);
        for (const s of d.actionable_signals || []) upsertSignal(s);
      })
      .catch(() => undefined);

    return () => {
      stopped = true;
      if (heartbeatTimer) clearInterval(heartbeatTimer);
      wsRef.current?.close();
    };
  }, [upsertSignal]);

  // High-frequency server-now tick for countdowns (100ms)
  useEffect(() => {
    const id = setInterval(() => {
      setServerNow(clockRef.current.serverNow());
    }, 100);
    return () => clearInterval(id);
  }, []);

  // Visibility: recalculate immediately when tab returns
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "visible") {
        setServerNow(clockRef.current.serverNow());
        wsRef.current?.send(
          JSON.stringify({ type: "get_dashboard", client_send_ms: Date.now() })
        );
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

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
