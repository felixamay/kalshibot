import { describe, test } from "node:test";
import assert from "node:assert/strict";
const expect = (actual: unknown) => ({ toBe: (value: unknown) => assert.equal(actual, value), toBeLessThan: (value: number) => assert.ok(Number(actual) < value) });
/**
 * @jest-environment jsdom
 *
 * Countdown accuracy unit tests (pure timestamp logic).
 * Run via: npx tsx --test or include in vitest/jest.
 * Also mirrored in backend pytest suite.
 */

import {
  formatCountdown,
  formatMatchTimer,
  matchClockEndsMs,
  remainingFromTimestamps,
  urgencyFromRemaining,
  ClockSynchronizer,
} from "./clock.ts";

describe("countdown timestamps", () => {
  test("starts with correct remaining", () => {
    expect(remainingFromTimestamps(1_008_000, 1_000_000)).toBe(8000);
  });

  test("network delay reduces remaining", () => {
    const created = 1_000_000;
    const ttl = 8000;
    const receiveDelay = 450;
    expect(remainingFromTimestamps(created + ttl, created + receiveDelay)).toBe(
      7550
    );
  });

  test("freeze does not drift", () => {
    // showed 6.4s (1600 elapsed), froze 3s → 3.4s
    expect(remainingFromTimestamps(1_008_000, 1_000_000 + 1600 + 3000)).toBe(
      3400
    );
  });

  test("never negative", () => {
    expect(remainingFromTimestamps(1000, 5000)).toBe(0);
  });

  test("urgency stages", () => {
    expect(urgencyFromRemaining(6000)).toBe("NORMAL");
    expect(urgencyFromRemaining(3000)).toBe("CAUTION");
    expect(urgencyFromRemaining(1500)).toBe("FINAL");
    expect(urgencyFromRemaining(0)).toBe("EXPIRED");
  });

  test("format final second", () => {
    expect(formatCountdown(900)).toBe("0.9");
    expect(formatCountdown(7400)).toBe("7.4");
  });

  test("clock sync offset", () => {
    const c = new ClockSynchronizer();
    c.recordSync(1000, 1100, 1050);
    expect(Math.abs(c.offsetMs - 75)).toBeLessThan(1);
  });

  test("match timer ticks from absolute end, including tenths", () => {
    const ends = 300_000;
    expect(formatMatchTimer(remainingFromTimestamps(ends, 0))).toBe("05:00.0");
    expect(formatMatchTimer(remainingFromTimestamps(ends, 100))).toBe("04:59.9");
    expect(formatMatchTimer(remainingFromTimestamps(ends, 272_400))).toBe("00:27.6");
    expect(formatMatchTimer(remainingFromTimestamps(ends, 400_000))).toBe("00:00.0");
  });

  test("match clock end is stable across later server times", () => {
    const ends = matchClockEndsMs(1_000_000 + 300_000, 1_000_000, 300_000);
    expect(ends).toBe(1_300_000);
    expect(remainingFromTimestamps(ends, 1_001_500)).toBe(298_500);
    const reconstructed = matchClockEndsMs(undefined, 1_000_000, 300_000);
    expect(reconstructed).toBe(1_300_000);
    expect(remainingFromTimestamps(reconstructed, 1_000_000 + 2_000)).toBe(298_000);
  });
});
