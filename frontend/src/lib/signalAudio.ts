/** Shared context is unlocked by the sound button, before any automated alert. */
import type { LiveSignal } from "./types";

/** Same YES episode cannot replay its sound inside this window. The first YES does not wait. */
export const ALERT_COOLDOWN_MS = 20000;

const yesAlerts = new Map<string, { active: boolean; lastPlayedAt: number }>();

export function resetYesAlerts() {
  yesAlerts.clear();
}

const strategyAlerts = new Map<string, number>();

export function resetStrategyAlerts() {
  strategyAlerts.clear();
}

/** First strategy alert plays immediately. The same alert waits 20 seconds. */
export function considerStrategyAlert(alertKey: string, now: number): boolean {
  const last = strategyAlerts.get(alertKey) ?? Number.NEGATIVE_INFINITY;
  if (now - last < ALERT_COOLDOWN_MS) return false;
  strategyAlerts.set(alertKey, now);
  return true;
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
let htmlAlert: HTMLAudioElement | null = null;
const entries = new Set<string>();
const warnings = new Set<string>();

type AudioContextCtor = new () => AudioContext;

function audioConstructor(): AudioContextCtor | null {
  if (typeof window === "undefined") return null;
  const vendor = window as Window & { webkitAudioContext?: AudioContextCtor };
  return window.AudioContext ?? vendor.webkitAudioContext ?? null;
}

/** Clears playback state so tests can cover each browser path. */
export function resetSignalPlayback() {
  context = null;
  htmlAlert = null;
  entries.clear();
  warnings.clear();
}

function buzzClip(): string {
  const rate = 8000;
  const samples = Math.floor(rate * 0.28);
  const data = new Uint8Array(44 + samples);
  const view = new DataView(data.buffer);
  data.set([82, 73, 70, 70], 0);
  view.setUint32(4, 36 + samples, true);
  data.set([87, 65, 86, 69, 102, 109, 116, 32], 8);
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate, true);
  view.setUint16(32, 1, true);
  view.setUint16(34, 8, true);
  data.set([100, 97, 116, 97], 36);
  view.setUint32(40, samples, true);
  for (let i = 0; i < samples; i++) {
    const t = i / rate;
    const pulsing = Math.floor(t * 16) % 2 === 0;
    data[44 + i] = pulsing && Math.sin(2 * Math.PI * 180 * t) >= 0 ? 210 : 128;
  }
  let binary = "";
  for (let i = 0; i < data.length; i++) binary += String.fromCharCode(data[i]);
  const encode = typeof btoa === "function" ? btoa : (value: string) => Buffer.from(value, "binary").toString("base64");
  return `data:audio/wav;base64,${encode(binary)}`;
}

function ensureHtmlAlert(): HTMLAudioElement | null {
  if (typeof Audio === "undefined") return null;
  htmlAlert ??= new Audio(buzzClip());
  return htmlAlert;
}

export async function enableSignalAudio(): Promise<boolean> {
  const Ctor = audioConstructor();
  let web = false;
  if (Ctor) {
    try {
      context ??= new Ctor();
      if (context.state !== "running") await context.resume();
      try {
        const buffer = context.createBuffer(1, 1, 22050);
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(context.destination);
        source.start(0);
      } catch {
        // A missing buffer API still leaves the context usable.
      }
      web = context.state === "running";
    } catch {
      web = false;
    }
  }
  const html = ensureHtmlAlert();
  if (!html) return web;
  try {
    html.muted = true;
    await html.play();
    html.pause();
    html.currentTime = 0;
    html.muted = false;
    return true;
  } catch {
    return web;
  }
}

function tone(frequency: number, delay: number, duration: number, kind: OscillatorType = "sine") {
  if (!context || context.state !== "running") return;
  const oscillator = context.createOscillator();
  const volume = context.createGain();
  const start = context.currentTime + delay;
  oscillator.type = kind;
  oscillator.frequency.value = frequency;
  volume.gain.setValueAtTime(kind === "square" ? 0.14 : 0.08, start);
  volume.gain.exponentialRampToValueAtTime(0.001, start + duration);
  oscillator.connect(volume);
  volume.connect(context.destination);
  oscillator.start(start);
  oscillator.stop(start + duration);
}

function playWebTone(warning: boolean): boolean {
  if (!context || context.state !== "running") {
    if (context && context.state !== "closed") void context.resume();
    return false;
  }
  // A low square pulse is the vibration browsers can always play as sound.
  tone(warning ? 90 : 70, 0, warning ? 0.28 : 0.2, "square");
  tone(warning ? 440 : 880, 0, warning ? 0.25 : 0.15);
  if (!warning) tone(1175, 0.12, 0.18);
  return true;
}

function playHtmlAlert(): boolean {
  const html = ensureHtmlAlert();
  if (!html) return false;
  try {
    html.currentTime = 0;
    const pending = html.play();
    if (pending && typeof pending.catch === "function") pending.catch(() => undefined);
    return true;
  } catch {
    return false;
  }
}

function pulseVibration(warning: boolean) {
  const pattern = warning ? [100, 50, 100, 50, 160] : [35, 25, 35, 25, 70];
  try {
    const nav = typeof navigator === "undefined" ? undefined : navigator;
    if (typeof nav?.vibrate === "function") nav.vibrate(pattern);
  } catch {
    // Desktop Safari and iOS have no vibration motor API.
  }
}

/** BUY rises, SELL falls, STRONG BUY adds a third tone, STOP LOSS uses the low buzz. */
export function playStrategySound(kind: string): boolean {
  const normalized = kind.toUpperCase();
  if (!context || context.state !== "running") {
    const sounded = playHtmlAlert();
    if (sounded) pulseVibration(normalized.includes("STOP"));
    return sounded;
  }
  if (normalized.includes("STOP")) {
    tone(90, 0, 0.28, "square");
    tone(220, 0.08, 0.24);
    pulseVibration(true);
    return true;
  }
  if (normalized.startsWith("SELL")) {
    tone(520, 0, 0.16);
    tone(330, 0.14, 0.22);
    pulseVibration(false);
    return true;
  }
  if (normalized.includes("STRONG")) {
    tone(880, 0, 0.12);
    tone(1175, 0.1, 0.12);
    tone(1568, 0.2, 0.16);
    pulseVibration(false);
    return true;
  }
  tone(660, 0, 0.14);
  tone(880, 0.12, 0.16);
  pulseVibration(false);
  return true;
}

export function playSignalAudio(id: string, warning = false): boolean {
  const heard = warning ? warnings : entries;
  if (heard.has(id)) return false;
  const sounded = playWebTone(warning) || playHtmlAlert();
  if (!sounded) return false;
  heard.add(id);
  // Bound session memory without allowing repeated sounds on card remounts.
  if (heard.size > 2000) heard.delete(heard.values().next().value!);
  pulseVibration(warning);
  return true;
}
