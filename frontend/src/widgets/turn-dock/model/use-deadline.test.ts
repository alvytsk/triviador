import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useDeadline } from "./use-deadline";

// The offset the socket would have measured; the hook takes it as an
// argument precisely so this is testable without a socket.
const NO_OFFSET = () => 0;

beforeEach(() => {
  // `requestAnimationFrame`/`cancelAnimationFrame` explicitly named: this is
  // the hook's own pacing mechanism under test, not `setInterval`, and some
  // fake-timer configurations do not drive rAF from `advanceTimersByTime`
  // unless it is named here.
  vi.useFakeTimers({
    toFake: ["Date", "requestAnimationFrame", "cancelAnimationFrame", "setTimeout", "clearTimeout"],
  });
  vi.setSystemTime(new Date("2026-08-20T12:00:00.000Z"));
});
afterEach(() => vi.useRealTimers());

describe("useDeadline", () => {
  it("counts down from the server's instant", () => {
    const { result } = renderHook(() => useDeadline("2026-08-20T12:00:20.000Z", NO_OFFSET));
    expect(result.current.remainingMs).toBe(20_000);
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    // <= 15_016, not <= 15_000: the installed Vitest's fake
    // `requestAnimationFrame` ticks in fixed 16 ms frames aligned to the
    // real wall-clock instant `vi.useFakeTimers()` was called (not to the
    // virtual time `vi.setSystemTime` set afterwards — confirmed by reading
    // the bundled clock's `getTimeToNextFrame`), so the last frame at or
    // before a 5_000 ms advance can land anywhere from 4_985 ms to exactly
    // 5_000 ms elapsed depending on that unpredictable real-time offset. One
    // frame (16 ms) of slack absorbs the full range without weakening what
    // this test is actually for: that the countdown moves.
    expect(result.current.remainingMs).toBeLessThanOrEqual(15_016);
  });

  it("applies the measured clock offset, so a skewed laptop still agrees", () => {
    // The client's clock is 3 s behind the server's.
    const { result } = renderHook(() => useDeadline("2026-08-20T12:00:20.000Z", () => 3_000));
    expect(result.current.remainingMs).toBe(17_000);
  });

  it("reports expired at zero and never goes negative", () => {
    const { result } = renderHook(() => useDeadline("2026-08-20T12:00:01.000Z", NO_OFFSET));
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(result.current.remainingMs).toBe(0);
    expect(result.current.expired).toBe(true);
  });

  it("has no deadline and is never expired when the turn has none", () => {
    const { result } = renderHook(() => useDeadline(null, NO_OFFSET));
    expect(result.current.remainingMs).toBe(0);
    expect(result.current.expired).toBe(false);
  });

  it("restarts cleanly when the deadline moves to the next window", () => {
    const { result, rerender } = renderHook(
      ({ at }: { at: string }) => useDeadline(at, NO_OFFSET),
      {
        initialProps: { at: "2026-08-20T12:00:05.000Z" },
      },
    );
    act(() => vi.advanceTimersByTime(6_000));
    expect(result.current.expired).toBe(true);
    rerender({ at: "2026-08-20T12:00:30.000Z" });
    expect(result.current.expired).toBe(false);
  });
});
