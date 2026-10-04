"use client";

import { useCallback, useEffect, useRef } from "react";

/**
 * Plays ONE alert when BET NOW appears, and optionally ONE warning under 2s.
 * Never sounds on every countdown tick.
 */
export function useSignalSounds(
  signalId: string | null,
  actionable: boolean,
  remainingMs: number,
  enabled: boolean
) {
  const heardAppear = useRef<Set<string>>(new Set());
  const heardWarn = useRef<Set<string>>(new Set());
  const playing = useRef<Set<string>>(new Set());
  const ctxRef = useRef<AudioContext | null>(null);

  const beep = useCallback(async (freq: number, duration: number, gain = 0.08) => {
    if (typeof window === "undefined") return false;
    if (!ctxRef.current) {
      ctxRef.current = new AudioContext();
    }
    const ctx = ctxRef.current;
    if (ctx.state === "suspended") {
      try {
        await ctx.resume();
      } catch {
        return false;
      }
    }
    if (ctx.state !== "running") return false;
    const osc = ctx.createOscillator();
    const g = ctx.createGain();
    osc.frequency.value = freq;
    osc.type = "sine";
    g.gain.value = gain;
    osc.connect(g);
    g.connect(ctx.destination);
    osc.start();
    g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
    osc.stop(ctx.currentTime + duration);
    return true;
  }, []);

  useEffect(() => {
    if (!enabled || !signalId || !actionable) return;
    if (heardAppear.current.has(signalId) || playing.current.has(signalId)) return;
    let stop = false;
    const attempt = () => {
      if (stop || heardAppear.current.has(signalId)) return;
      playing.current.add(signalId);
      void beep(880, 0.15).then((ok) => {
        playing.current.delete(signalId);
        if (stop || !ok) {
          if (!stop) window.setTimeout(attempt, 1000);
          return;
        }
        heardAppear.current.add(signalId);
        window.setTimeout(() => void beep(1175, 0.18), 120);
      });
    };
    attempt();
    return () => {
      stop = true;
    };
  }, [signalId, actionable, enabled, beep]);

  useEffect(() => {
    if (!enabled || !signalId || !actionable) return;
    if (
      remainingMs <= 0 ||
      remainingMs >= 2000 ||
      heardWarn.current.has(signalId) ||
      playing.current.has(`${signalId}:warn`)
    ) {
      return;
    }
    let stop = false;
    playing.current.add(`${signalId}:warn`);
    void beep(440, 0.25, 0.1).then((ok) => {
      playing.current.delete(`${signalId}:warn`);
      if (!stop && ok) heardWarn.current.add(signalId);
    });
    return () => {
      stop = true;
    };
  }, [signalId, actionable, remainingMs, enabled, beep]);
}
