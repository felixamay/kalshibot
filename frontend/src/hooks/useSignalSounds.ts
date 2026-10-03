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
  const ctxRef = useRef<AudioContext | null>(null);

  const beep = useCallback((freq: number, duration: number, gain = 0.08) => {
    if (typeof window === "undefined") return;
    if (!ctxRef.current) {
      ctxRef.current = new AudioContext();
    }
    const ctx = ctxRef.current;
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
  }, []);

  useEffect(() => {
    if (!enabled || !signalId || !actionable) return;
    if (!heardAppear.current.has(signalId)) {
      heardAppear.current.add(signalId);
      beep(880, 0.15);
      setTimeout(() => beep(1175, 0.18), 120);
    }
  }, [signalId, actionable, enabled, beep]);

  useEffect(() => {
    if (!enabled || !signalId || !actionable) return;
    if (remainingMs > 0 && remainingMs < 2000 && !heardWarn.current.has(signalId)) {
      heardWarn.current.add(signalId);
      beep(440, 0.25, 0.1);
    }
  }, [signalId, actionable, remainingMs, enabled, beep]);
}
