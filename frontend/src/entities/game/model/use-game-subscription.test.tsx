import { act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TIMING } from "@/shared/config";
import { renderWithApp } from "../../../../testing/render";
import { useGameSubscription } from "./use-game-subscription";

function Watcher({ gameId }: { gameId: string }) {
  useGameSubscription(gameId);
  return null;
}

describe("useGameSubscription", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("subscribes once for two components watching the same game", () => {
    const harness = renderWithApp(
      <>
        <Watcher gameId="g1" />
        <Watcher gameId="g1" />
      </>,
    );
    act(() => harness.socket.last().open());
    const subscribes = harness.socket
      .last()
      .frames()
      .filter((f) => f.type === "subscribe");
    expect(subscribes).toEqual([{ type: "subscribe", topic: "game:g1" }]);
  });

  it("unsubscribes only when the last watcher unmounts", () => {
    const harness = renderWithApp(
      <>
        <Watcher gameId="g1" />
        <Watcher gameId="g1" />
      </>,
    );
    act(() => harness.socket.last().open());
    harness.rerender(<Watcher gameId="g1" />);
    expect(
      harness.socket
        .last()
        .frames()
        .some((f) => f.type === "unsubscribe"),
    ).toBe(false);
    harness.unmount();
    expect(harness.socket.last().frames().at(-1)).toEqual({
      type: "unsubscribe",
      topic: "game:g1",
    });
  });

  it("re-subscribes after a reconnect, because the server forgot", () => {
    const harness = renderWithApp(<Watcher gameId="g1" />);
    act(() => harness.socket.last().open());
    expect(harness.socket.created).toHaveLength(1);

    // A non-terminal close puts the client into "reconnecting" and schedules
    // a real `connect()` via `setTimeout(..., backoff)` — nothing happens
    // synchronously. Advancing past the backoff is what actually drives a
    // *new* socket into existence, which is the only way this test can tell
    // a genuine reconnect apart from a no-op.
    act(() => harness.socket.last().serverClose(1006));
    expect(harness.socket.created).toHaveLength(1); // still no new socket yet

    act(() => vi.advanceTimersByTime(TIMING.RECONNECT_BASE_MS));
    expect(harness.socket.created).toHaveLength(2); // the reconnect actually happened

    act(() => harness.socket.last().open());

    // The mount's own subscribe landed on the *first* socket, long gone by
    // now. Anything on this new socket can only be the reconnect-resubscribe
    // effect doing its job.
    const subscribes = harness.socket
      .last()
      .frames()
      .filter((f) => f.type === "subscribe");
    expect(subscribes).toEqual([{ type: "subscribe", topic: "game:g1" }]);
  });
});
