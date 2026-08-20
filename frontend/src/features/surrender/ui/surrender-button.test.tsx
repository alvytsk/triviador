import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderWithApp } from "../../../../testing/render";
import { SurrenderButton } from "./surrender-button";

describe("SurrenderButton", () => {
  it("sends nothing on the first click — it only reveals a confirmation", async () => {
    const harness = renderWithApp(<SurrenderButton gameId="g1" />);
    act(() => harness.socket.last().open());

    await userEvent.click(screen.getByRole("button", { name: "Surrender" }));

    expect(harness.socket.last().frames()).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Confirm surrender" })).toBeInTheDocument();
  });

  it("cancel returns to the unconfirmed state and sends nothing", async () => {
    const harness = renderWithApp(<SurrenderButton gameId="g1" />);
    act(() => harness.socket.last().open());

    await userEvent.click(screen.getByRole("button", { name: "Surrender" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("button", { name: "Surrender" })).toBeInTheDocument();
    expect(harness.socket.last().frames()).toHaveLength(0);
  });

  it("sends a windowless surrender frame — no deadline_id, no payload — only on the second click", async () => {
    const harness = renderWithApp(<SurrenderButton gameId="g1" />);
    act(() => harness.socket.last().open());

    await userEvent.click(screen.getByRole("button", { name: "Surrender" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm surrender" }));

    const frames = harness.socket.last().frames();
    expect(frames).toHaveLength(1);
    expect(frames[0]).toMatchObject({ type: "surrender", game_id: "g1" });
    expect(frames[0]).not.toHaveProperty("deadline_id");
    expect(frames[0]).not.toHaveProperty("payload");
  });

  it("renders the server's message for a rejected surrender", async () => {
    const harness = renderWithApp(<SurrenderButton gameId="g1" />);
    act(() => harness.socket.last().open());

    await userEvent.click(screen.getByRole("button", { name: "Surrender" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm surrender" }));
    const commandId = harness.socket.last().frames()[0]?.command_id as string;

    act(() =>
      harness.socket.last().deliver({
        type: "error",
        command_id: commandId,
        code: "not_a_participant",
        message: "You are not in this game.",
      }),
    );

    expect(await screen.findByRole("status")).toHaveTextContent("You are not in this game.");
  });
});
