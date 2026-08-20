import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { createPresenceStore, PresenceProvider, usePresence } from "./use-presence";

describe("createPresenceStore", () => {
  it("returns null for a game with no presence message yet — distinct from an empty roster", () => {
    const store = createPresenceStore();
    expect(store.snapshot("g1")).toBeNull();
  });

  it("returns exactly what the last update said, including an explicit empty roster", () => {
    const store = createPresenceStore();
    store.update("g1", ["u1", "u2"]);
    expect(store.snapshot("g1")).toEqual(["u1", "u2"]);
    store.update("g1", []);
    expect(store.snapshot("g1")).toEqual([]);
  });

  it("keeps two games independent", () => {
    const store = createPresenceStore();
    store.update("g1", ["u1"]);
    expect(store.snapshot("g2")).toBeNull();
  });

  it("notifies a subscriber of that game only, and stops after unsubscribe", () => {
    const store = createPresenceStore();
    let seenG1 = 0;
    let seenG2 = 0;
    store.subscribe("g1", () => {
      seenG1++;
    });
    const offG2 = store.subscribe("g2", () => {
      seenG2++;
    });

    store.update("g1", ["u1"]);
    expect(seenG1).toBe(1);
    expect(seenG2).toBe(0);

    offG2();
    store.update("g2", ["u2"]);
    expect(seenG2).toBe(0);
  });
});

function Roster({ gameId }: { gameId: string }) {
  const connected = usePresence(gameId);
  if (connected === null) return <span>unknown</span>;
  return <span>{connected.length === 0 ? "empty" : connected.join(",")}</span>;
}

describe("usePresence", () => {
  it("throws outside a PresenceProvider", () => {
    // The hook is only ever reachable from `app/`, behind
    // `fsd/forbidden-imports` — this proves it also fails loudly rather
    // than silently if that ever changes. React logs the thrown error to
    // the console on its way out; silenced here since the throw itself is
    // the assertion.
    vi.spyOn(console, "error").mockImplementation(() => {});
    const spy = () => render(<Roster gameId="g1" />);
    expect(spy).toThrow(/PresenceProvider/);
  });

  it("renders 'unknown' before the first game.presence and updates once one lands", () => {
    const store = createPresenceStore();
    render(
      <PresenceProvider value={store}>
        <Roster gameId="g1" />
      </PresenceProvider>,
    );
    expect(screen.getByText("unknown")).toBeInTheDocument();

    act(() => store.update("g1", ["u1", "u2"]));
    expect(screen.getByText("u1,u2")).toBeInTheDocument();
  });

  it("does not re-render a component subscribed to a different game", () => {
    const store = createPresenceStore();
    render(
      <PresenceProvider value={store}>
        <Roster gameId="g1" />
      </PresenceProvider>,
    );
    act(() => store.update("g2", ["u9"]));
    expect(screen.getByText("unknown")).toBeInTheDocument();
  });
});
