"use client";

/**
 * Authoritative countdown from absolute timestamps — no interval subtraction.
 * Survives browser freezes and background tab throttling.
 */

import { useEffect, useState } from "react";
import {
  formatCountdown,
  remainingFromTimestamps,
  urgencyFromRemaining,
  type Urgency,
} from "@/lib/clock";

export interface CountdownState {
  remainingMs: number;
  remainingDisplay: string;
  progress: number; // remaining / original_ttl
  urgency: Urgency;
  expired: boolean;
}

export function useSignalCountdown(
  expiresAtMs: number,
  originalTtlMs: number,
  serverNowMs: number,
  active: boolean
): CountdownState {
  // Recalculate purely from timestamps every render driven by serverNowMs
  const remainingMs = active
    ? remainingFromTimestamps(expiresAtMs, serverNowMs)
    : 0;
  const progress =
    originalTtlMs > 0 ? Math.max(0, Math.min(1, remainingMs / originalTtlMs)) : 0;
  const urgency = urgencyFromRemaining(remainingMs);
  const expired = !active || remainingMs <= 0;

  return {
    remainingMs,
    remainingDisplay: formatCountdown(remainingMs),
    progress,
    urgency,
    expired,
  };
}

/** Optional rAF-backed smooth tick for progress bar polish — still uses timestamps. */
export function useSmoothServerNow(
  getServerNow: () => number,
  refreshMs = 100
): number {
  const [now, setNow] = useState(() => getServerNow());

  useEffect(() => {
    let frame = 0;
    let last = performance.now();

    const loop = (t: number) => {
      if (t - last >= refreshMs) {
        setNow(getServerNow());
        last = t;
      }
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);

    const onVis = () => {
      if (document.visibilityState === "visible") {
        setNow(getServerNow());
      }
    };
    document.addEventListener("visibilitychange", onVis);

    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [getServerNow, refreshMs]);

  return now;
}
