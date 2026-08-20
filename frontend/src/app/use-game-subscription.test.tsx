import { act } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderWithApp } from "../../testing/render";
import { useGameSubscription } from "./use-game-subscription";

function Watcher({ gameId }: { gameId: string }) {
  useGameSubscription(gameId);
  return null;
}

describe("useGameSubscription", () => {
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
    act(() => harness.socket.last().serverClose(1006));
    act(() => {
      harness.socket.created.at(-1)?.open();
    });
    const subscribes = harness.socket
      .last()
      .frames()
      .filter((f) => f.type === "subscribe");
    expect(subscribes.length).toBeGreaterThanOrEqual(1);
  });
});
