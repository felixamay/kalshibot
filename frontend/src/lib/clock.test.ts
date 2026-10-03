/**
 * @jest-environment jsdom
 *
 * Countdown accuracy unit tests (pure timestamp logic).
 * Run via: npx tsx --test or include in vitest/jest.
 * Also mirrored in backend pytest suite.
 */

import {
  formatCountdown,
  remainingFromTimestamps,
  urgencyFromRemaining,
  ClockSynchronizer,
} from "../lib/clock";

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
});
