import { describe, expect, it } from "vitest";
import { createClockOffset } from "./clock";

describe("createClockOffset", () => {
  it("is zero before any pong", () => {
    expect(createClockOffset().offsetMs()).toBe(0);
  });

  it("estimates the server's clock as halfway through the round trip", () => {
    const clock = createClockOffset();
    // sent at 1000, received at 1100, server said 2050 → offset 1000
    clock.record(1000, 2050, 1100);
    expect(clock.offsetMs()).toBe(1000);
  });

  it("is not moved by a single delayed packet", () => {
    const clock = createClockOffset(5);
    for (let i = 0; i < 4; i++) clock.record(0, 1000, 0);
    clock.record(0, 9000, 0); // one packet queued behind something
    expect(clock.offsetMs()).toBe(1000);
  });

  it("keeps only the most recent samples", () => {
    const clock = createClockOffset(3);
    for (let i = 0; i < 3; i++) clock.record(0, 1000, 0);
    for (let i = 0; i < 3; i++) clock.record(0, 5000, 0);
    expect(clock.offsetMs()).toBe(5000);
  });
});
