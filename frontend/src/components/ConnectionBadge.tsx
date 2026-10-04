"use client";

export function ConnectionBadge() {
  return (
    <div className="flex items-center gap-2 font-mono text-xs uppercase tracking-widest">
      <span className="inline-block h-2 w-2 rounded-full bg-signal-lime" />
      <span>Kalshi CONNECTED</span>
    </div>
  );
}
