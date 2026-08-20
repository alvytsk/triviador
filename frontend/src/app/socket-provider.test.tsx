import { act } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { gameKey, meKey } from "@/entities/game";
import type { GameSnapshot } from "@/shared/api";
import { snapshot } from "../../testing/factories";
import { renderWithApp } from "../../testing/render";
import { SocketStatusBanner } from "./socket-status";

describe("SocketProvider", () => {
  it("routes an incoming snapshot into the cache through the dispatcher", () => {
    const harness = renderWithApp(<SocketStatusBanner />);
    act(() => harness.socket.last().open());
    act(() =>
      harness.socket.last().deliver({
        type: "game.snapshot",
        game_id: "g1",
        seq: 4,
        state: snapshot(4).state,
      }),
    );
    expect(harness.queryClient.getQueryData<GameSnapshot>(gameKey("g1"))?.seq).toBe(4);
  });

  it("clears the session when the socket is closed with 4401", () => {
    const harness = renderWithApp(<SocketStatusBanner />, {
      seed: ({ queryClient }) => queryClient.setQueryData(meKey(), { user_id: "u1" }),
    });
    act(() => harness.socket.last().open());
    act(() => harness.socket.last().serverClose(4401));
    expect(harness.queryClient.getQueryData(meKey())).toBeNull();
  });

  it("says nothing while the socket is open and speaks up when it is not", () => {
    const harness = renderWithApp(<SocketStatusBanner />);
    act(() => harness.socket.last().open());
    expect(harness.queryByRole("status")).toBeNull();
    act(() => harness.socket.last().serverClose(1006));
    expect(harness.getByRole("status")).toHaveTextContent("Reconnecting");
  });

  it("shows the quiet 'not connected' banner on a terminal close", () => {
    const harness = renderWithApp(<SocketStatusBanner />);
    act(() => harness.socket.last().open());
    // 4401/4403 are terminal — the client gives up on reconnecting entirely
    // (§11.1), which is the one state this banner exists to make unmissable.
    act(() => harness.socket.last().serverClose(4401));
    const banner = harness.getByRole("status");
    expect(banner).toHaveTextContent("Not connected");
    expect(banner.className).toContain("border-ink-dim"); // the "quiet" tone
  });
});
