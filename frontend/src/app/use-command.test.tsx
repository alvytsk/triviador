import { act, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { gameState, snapshot } from "../../testing/factories";
import { renderWithApp } from "../../testing/render";
import { useCommand } from "./use-command";

/**
 * Exercises `useCommand` the way every real caller does: through a component
 * that calls `send`, never by reaching into the hook's internals.
 */
function Probe() {
  const command = useCommand();
  return (
    <div>
      <button
        type="button"
        onClick={() =>
          command.send((command_id) => ({
            type: "pick_region",
            command_id,
            game_id: "g1",
            deadline_id: 7,
            payload: { region_id: "praha" },
          }))
        }
      >
        send
      </button>
      <span data-testid="pending">{[...command.pending].sort().join(",")}</span>
      <span data-testid="failure">
        {command.failure !== null
          ? `${command.failure.commandId}:${command.failure.code}:${command.failure.message}`
          : ""}
      </span>
    </div>
  );
}

describe("useCommand", () => {
  it("sends a frame carrying a unique command_id and the given deadline_id", async () => {
    const harness = renderWithApp(<Probe />);
    act(() => harness.socket.last().open());

    await userEvent.click(screen.getByRole("button", { name: "send" }));
    await userEvent.click(screen.getByRole("button", { name: "send" }));

    const frames = harness.socket.last().frames();
    expect(frames).toHaveLength(2);
    for (const frame of frames) {
      expect(frame.deadline_id).toBe(7);
      expect(typeof frame.command_id).toBe("string");
      expect((frame.command_id as string).length).toBeGreaterThan(0);
    }
    expect(frames[0]?.command_id).not.toBe(frames[1]?.command_id);
  });

  it("clears the matching command and surfaces its code when the server rejects it", async () => {
    const harness = renderWithApp(<Probe />);
    act(() => harness.socket.last().open());
    await userEvent.click(screen.getByRole("button", { name: "send" }));
    const commandId = harness.socket.last().frames()[0]?.command_id as string;
    expect(screen.getByTestId("pending")).toHaveTextContent(commandId);

    act(() =>
      harness.socket.last().deliver({
        type: "error",
        command_id: commandId,
        code: "region_not_free",
        message: "That region is already taken.",
      }),
    );

    expect(screen.getByTestId("pending")).toHaveTextContent("");
    expect(screen.getByTestId("failure")).toHaveTextContent(
      `${commandId}:region_not_free:That region is already taken.`,
    );
  });

  it("does not clear a still-pending command when a different command_id errors", async () => {
    const harness = renderWithApp(<Probe />);
    act(() => harness.socket.last().open());
    await userEvent.click(screen.getByRole("button", { name: "send" }));
    const commandId = harness.socket.last().frames()[0]?.command_id as string;

    act(() =>
      harness.socket.last().deliver({
        type: "error",
        command_id: "some-other-command",
        code: "not_your_turn",
        message: "Not your turn.",
      }),
    );

    // The mismatched id never touched this command's own membership in
    // `pending` — `Set.delete` on an id that isn't there is a no-op.
    expect(screen.getByTestId("pending")).toHaveTextContent(commandId);
  });

  it("clears everything pending on a game.update", async () => {
    const harness = renderWithApp(<Probe />);
    act(() => harness.socket.last().open());
    await userEvent.click(screen.getByRole("button", { name: "send" }));
    await userEvent.click(screen.getByRole("button", { name: "send" }));
    expect(screen.getByTestId("pending")).not.toHaveTextContent("");

    act(() =>
      harness.socket.last().deliver({
        type: "game.update",
        game_id: "g1",
        seq: 2,
        base_seq: 1,
        events: [],
        state: gameState(),
      }),
    );

    expect(screen.getByTestId("pending")).toHaveTextContent("");
  });

  it("never re-sends a rejected command, even once time has passed", () => {
    vi.useFakeTimers();
    try {
      const harness = renderWithApp(<Probe />);
      act(() => harness.socket.last().open());
      // `fireEvent`, not `userEvent`, under fake timers: `userEvent`'s own
      // internal delay machinery needs real timers to resolve.
      act(() => fireEvent.click(screen.getByRole("button", { name: "send" })));
      const commandId = harness.socket.last().frames()[0]?.command_id as string;

      act(() =>
        harness.socket.last().deliver({
          type: "error",
          command_id: commandId,
          code: "region_not_free",
          message: "That region is already taken.",
        }),
      );

      act(() => vi.advanceTimersByTime(60_000));

      expect(
        harness.socket
          .last()
          .frames()
          .filter((f) => f.type === "pick_region"),
      ).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("also clears pending on a game.snapshot", async () => {
    const harness = renderWithApp(<Probe />);
    act(() => harness.socket.last().open());
    await userEvent.click(screen.getByRole("button", { name: "send" }));
    expect(screen.getByTestId("pending")).not.toHaveTextContent("");

    act(() =>
      harness.socket.last().deliver({ type: "game.snapshot", game_id: "g1", ...snapshot(1) }),
    );

    expect(screen.getByTestId("pending")).toHaveTextContent("");
  });
});
