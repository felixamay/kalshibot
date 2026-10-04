/** Shared context is unlocked by the sound button, before any automated alert. */
import type { LiveSignal } from "./types";

/** Same YES episode cannot replay its sound inside this window. The first YES does not wait. */
export const ALERT_COOLDOWN_MS = 20000;

const yesAlerts = new Map<string, { active: boolean; lastPlayedAt: number }>();

export function resetYesAlerts() {
  yesAlerts.clear();
}

/** True only for a new YES episode whose last sound is at least 20 seconds old. */
export function considerYesAlert(alertKey: string, isYes: boolean, now: number): boolean {
  const matchId = alertKey.split("|")[0];
  for (const [other, state] of yesAlerts) {
    if (other.startsWith(`${matchId}|`) && other !== alertKey) state.active = false;
  }
  const prev = yesAlerts.get(alertKey) ?? { active: false, lastPlayedAt: Number.NEGATIVE_INFINITY };
  if (!isYes) {
    yesAlerts.set(alertKey, { active: false, lastPlayedAt: prev.lastPlayedAt });
    return false;
  }
  if (prev.active) return false;
  const play = now - prev.lastPlayedAt >= ALERT_COOLDOWN_MS;
  yesAlerts.set(alertKey, { active: true, lastPlayedAt: play ? now : prev.lastPlayedAt });
  return play;
}

/** Alert identity excludes quote and countdown refreshes. */
export function signalAlertKey(signal: Pick<LiveSignal, "signal_id" | "signal_version" | "signal_type" | "status" | "expiration_reason">): string {
  return JSON.stringify([signal.signal_id, signal.signal_version, signal.signal_type, signal.status, signal.expiration_reason ?? null]);
}

let context: AudioContext | null = null;
const entries = new Set<string>();
const warnings = new Set<string>();

export async function enableSignalAudio(): Promise<boolean> {
  if (typeof window === "undefined" || !window.AudioContext) return false;
  try {
    context ??= new window.AudioContext();
    if (context.state !== "running") await context.resume();
    return context.state === "running";
  } catch {
    return false;
  }
}

function tone(frequency: number, delay: number, duration: number) {
  if (!context || context.state !== "running") return;
  const oscillator = context.createOscillator();
  const volume = context.createGain();
  const start = context.currentTime + delay;
  oscillator.frequency.value = frequency;
  volume.gain.setValueAtTime(0.08, start);
  volume.gain.exponentialRampToValueAtTime(0.001, start + duration);
  oscillator.connect(volume);
  volume.connect(context.destination);
  oscillator.start(start);
  oscillator.stop(start + duration);
}

export function playSignalAudio(id: string, warning = false): boolean {
  if (!context || context.state !== "running") return false;
  const heard = warning ? warnings : entries;
  if (heard.has(id)) return false;
  heard.add(id);
  // Bound session memory without allowing repeated sounds on card remounts.
  if (heard.size > 2000) heard.delete(heard.values().next().value!);
  tone(warning ? 440 : 880, 0, warning ? 0.25 : 0.15);
  if (!warning) tone(1175, 0.12, 0.18);
  return true;
}
