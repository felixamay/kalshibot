/**
 * Clock synchronization — never trust client clock alone for signal expiry.
 */

export class ClockSynchronizer {
  offsetMs = 0;
  latencyMs = 0;
  lastSyncAt = 0;

  /** estimated_server_now = client_now + offset */
  serverNow(_clientNow?: number): number {
    // Use Date.now() based wall clock for absolute timestamps matching server epoch ms
    void _clientNow;
    return Date.now() + this.offsetMs;
  }

  recordSync(clientSendMs: number, serverTimeMs: number, clientRecvMs: number) {
    const rtt = clientRecvMs - clientSendMs;
    const latency = rtt / 2;
    const midClient = clientSendMs + latency;
    const offset = serverTimeMs - midClient;
    // EMA smooth
    if (this.lastSyncAt === 0) {
      this.offsetMs = offset;
      this.latencyMs = latency;
    } else {
      this.offsetMs = this.offsetMs * 0.7 + offset * 0.3;
      this.latencyMs = this.latencyMs * 0.7 + latency * 0.3;
    }
    this.lastSyncAt = clientRecvMs;
  }
}

export function remainingFromTimestamps(
  expiresAtMs: number,
  serverNowMs: number
): number {
  return Math.max(0, expiresAtMs - serverNowMs);
}

export function urgencyFromRemaining(remainingMs: number): Urgency {
  if (remainingMs <= 0) return "EXPIRED";
  if (remainingMs < 2000) return "FINAL";
  if (remainingMs <= 5000) return "CAUTION";
  return "NORMAL";
}

export type Urgency = "NORMAL" | "CAUTION" | "FINAL" | "EXPIRED";

export function formatCountdown(remainingMs: number): string {
  if (remainingMs <= 0) return "0.0";
  if (remainingMs < 1000) {
    return (remainingMs / 1000).toFixed(1);
  }
  return (remainingMs / 1000).toFixed(1);
}

/** MM:SS.t from an absolute end timestamp. Tenths update every 100ms. Never negative. */
export function formatMatchTimer(remainingMs: number): string {
  const ms = Math.max(0, remainingMs);
  const totalTenths = Math.floor(ms / 100);
  const tenths = totalTenths % 10;
  const totalSeconds = Math.floor(totalTenths / 10);
  const seconds = totalSeconds % 60;
  const minutes = Math.floor(totalSeconds / 60);
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${tenths}`;
}

/** End timestamp for a match clock. Prefer the server absolute; otherwise reconstruct once from the snapshot. */
export function matchClockEndsMs(
  absoluteEndsMs: number | undefined,
  snapshotServerTimeMs: number,
  remainingAtSnapshotMs: number
): number {
  if (absoluteEndsMs && absoluteEndsMs > 0) return absoluteEndsMs;
  return snapshotServerTimeMs + Math.max(0, remainingAtSnapshotMs || 0);
}
