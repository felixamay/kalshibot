"use client";

import type { ConnectionStatus } from "@/lib/types";

export function ConnectionBadge({
  kalshi,
  ws,
}: {
  kalshi: ConnectionStatus;
  ws: ConnectionStatus;
}) {
  const status = kalshi === "CONNECTED" && ws !== "DISCONNECTED" ? kalshi : kalshi;
  const color =
    status === "CONNECTED"
      ? "bg-signal-lime"
      : status === "RECONNECTING"
        ? "bg-signal-amber"
        : "bg-signal-coral";

  return (
    <div className="flex items-center gap-4 font-mono text-xs uppercase tracking-widest">
      <div className="flex items-center gap-2">
        <span className={`inline-block h-2 w-2 rounded-full ${color}`} />
        <span>Kalshi {status}</span>
      </div>
      <div className="text-mist/40">WS {ws}</div>
    </div>
  );
}
