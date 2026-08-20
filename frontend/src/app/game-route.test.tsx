import { createMemoryHistory, createRouter, RouterProvider } from "@tanstack/react-router";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { deadline, gameState, player, question, snapshot } from "../../testing/factories";
import { FakeSocket } from "../../testing/fake-socket";
import { server } from "../../testing/msw";
import { Providers } from "./app-providers";
import { createQueryClient } from "./query-client";
import { routeTree } from "./routes/routeTree.gen";

/**
 * Every earlier test of this screen (`pages/game/ui/game-page.test.tsx`,
 * `pages/game/ui/full-game.test.tsx`) substitutes a hand-rolled, non-merging
 * `queryFn` for `gameQueryOptions` and passes `resolvedQuestion` in as a
 * prop — both documented, deliberate substitutes for exercising `GamePage`
 * on its own. Neither exercises this *route file*: its loader running
 * `gameQueryOptions` in situ, or the `question_resolved`/`question_presented`
 * narration threading it alone owns. That gap is exactly the seam Important
 * #3 (a stale `question_resolved` reveal surviving a gapped update) slipped
 * through undetected — so this file drives the real route, through the real
 * router, the real `Providers`, and a real (fake-transport) socket.
 *
 * `WebSocket` is stubbed with a subclass of `FakeSocket` that records every
 * instance constructed, the same pattern `login-next-navigation.test.tsx`
 * uses for `Providers`-rooted trees that cannot accept an injected socket
 * client the way `renderWithApp` can.
 */
let createdSockets: FakeSocket[] = [];
class TrackedSocket extends FakeSocket {
  constructor(url: string) {
    super(url);
    createdSockets.push(this);
  }
}

beforeEach(() => {
  createdSockets = [];
  vi.stubGlobal("WebSocket", TrackedSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function lastSocket(): FakeSocket {
  const socket = createdSockets.at(-1);
  if (socket === undefined) throw new Error("no socket was created");
  return socket;
}

/**
 * `SocketProvider` builds its `SocketClient` asynchronously here — unlike
 * `renderWithApp`'s harness, which injects an already-built one on the very
 * first render — so the effect that wires `client.onMessage` up to the
 * dispatcher runs in a *later* commit than the one that constructs the
 * client. Opening the fake socket and immediately delivering a message (as
 * every `renderWithApp`-based test does safely) can outrun that wiring here.
 * A real browser never hits this: a genuine WebSocket handshake takes far
 * longer than one more React render, so the listener is always registered
 * first. Waiting for the game subscription — sent by that very effect,
 * right where it registers the listener — is the deterministic stand-in for
 * that real-world head start.
 */
async function openAndWire() {
  act(() => lastSocket().open());
  await waitFor(() =>
    expect(
      lastSocket()
        .frames()
        .some((f) => f.type === "subscribe" && f.topic === "game:g1"),
    ).toBe(true),
  );
}

function deliver(message: Record<string, unknown>) {
  act(() => lastSocket().deliver(message));
}

function renderRouter(initial: string) {
  const queryClient = createQueryClient();
  const history = createMemoryHistory({ initialEntries: [initial] });
  const router = createRouter({ routeTree, context: { queryClient }, history });
  const view = render(
    <Providers queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return { router, queryClient, ...view };
}

const ME = { user_id: "u1", username: "alexey", display_name: "Alexey", role: "player" };

function meOk() {
  return http.get("/api/auth/me", () => HttpResponse.json(ME));
}

const U1 = player({ player_id: "u1", display_name: "Alexey", seat: 0 });
const U2 = player({ player_id: "u2", display_name: "Petra", seat: 1 });

// `media_url` set on purpose: it makes `GameStage` render the question's
// image instead of `MapBoard`, so this file needs no `/api/maps/*` handler
// at all — this suite is about narration threading, not the map.
const QUESTION_A = question({ question_id: "qa", media_url: "/api/media/a" });
const QUESTION_TURN_A = {
  kind: "expansion_question" as const,
  question: QUESTION_A,
  answered: [] as readonly string[],
  your_answer: null,
  deadline_at: deadline(),
  deadline_id: 10,
  your_options: { pick: [] as readonly string[], attack: [] as readonly string[] },
};

const RESOLVED_A = {
  type: "question_resolved" as const,
  correct_choice_index: 1,
  correct_players: ["u1"],
  correct_value: null,
  ranking: ["u1", "u2"],
};

describe("the game route ($gameId), end to end", () => {
  it("runs gameQueryOptions' loader for real and subscribes over the real socket", async () => {
    let getCalls = 0;
    server.use(
      meOk(),
      http.get("/api/games/g1", () => {
        getCalls++;
        return HttpResponse.json(
          snapshot(1, {
            phase: "expansion",
            round_no: 1,
            players: [U1, U2],
            turn: QUESTION_TURN_A,
          }),
        );
      }),
    );

    renderRouter("/games/g1");

    // The loader (`Route.options.loader`, calling `gameQueryOptions`) is what
    // paints this — not a substitute `queryFn` reimplemented in a test file.
    expect(await screen.findByText(QUESTION_A.prompt)).toBeInTheDocument();
    expect(getCalls).toBe(1);

    await openAndWire();
  });

  it("reveals question_resolved's correct choice, then clears it when question_presented opens the next one", async () => {
    server.use(
      meOk(),
      http.get("/api/games/g1", () =>
        HttpResponse.json(
          snapshot(1, {
            phase: "expansion",
            round_no: 1,
            players: [U1, U2],
            turn: QUESTION_TURN_A,
          }),
        ),
      ),
    );
    renderRouter("/games/g1");
    await screen.findByText(QUESTION_A.prompt);
    await openAndWire();

    deliver({
      type: "game.update",
      game_id: "g1",
      seq: 2,
      base_seq: 1,
      events: [RESOLVED_A],
      state: gameState({
        phase: "expansion",
        round_no: 1,
        players: [U1, U2],
        turn: QUESTION_TURN_A,
      }),
    });
    expect(await screen.findByTestId("choice-correct")).toBeInTheDocument();

    const QUESTION_B = question({ question_id: "qb", media_url: "/api/media/b" });
    const QUESTION_TURN_B = {
      ...QUESTION_TURN_A,
      question: QUESTION_B,
      deadline_id: 11,
      your_answer: null,
    };
    deliver({
      type: "game.update",
      game_id: "g1",
      seq: 3,
      base_seq: 2,
      events: [{ type: "question_presented", deadline_id: 11 }],
      state: gameState({
        phase: "expansion",
        round_no: 1,
        players: [U1, U2],
        turn: QUESTION_TURN_B,
      }),
    });

    await screen.findByText(QUESTION_B.prompt);
    expect(screen.queryByTestId("choice-correct")).not.toBeInTheDocument();
  });

  it("drops a stale question_resolved reveal across a GAPPED update opening a new question — no question_presented event fires", async () => {
    // Spec 1B §8.2 suppresses narration events on a gap, so the only signal
    // available that the question changed is the state's own deadline_id
    // moving — this is Important #3 from the whole-branch review, reproduced
    // end to end through the real route rather than at the route's own unit
    // level.
    server.use(
      meOk(),
      http.get("/api/games/g1", () =>
        HttpResponse.json(
          snapshot(1, {
            phase: "expansion",
            round_no: 1,
            players: [U1, U2],
            turn: QUESTION_TURN_A,
          }),
        ),
      ),
    );
    renderRouter("/games/g1");
    await screen.findByText(QUESTION_A.prompt);
    await openAndWire();

    deliver({
      type: "game.update",
      game_id: "g1",
      seq: 2,
      base_seq: 1,
      events: [RESOLVED_A],
      state: gameState({
        phase: "expansion",
        round_no: 1,
        players: [U1, U2],
        turn: QUESTION_TURN_A,
      }),
    });
    expect(await screen.findByTestId("choice-correct")).toBeInTheDocument();

    const QUESTION_B = question({ question_id: "qb", media_url: "/api/media/b" });
    const QUESTION_TURN_B = {
      ...QUESTION_TURN_A,
      question: QUESTION_B,
      deadline_id: 99,
      your_answer: null,
    };
    // seq 6 / base_seq 5 does not match the cache's seq (2) — a real gap.
    deliver({
      type: "game.update",
      game_id: "g1",
      seq: 6,
      base_seq: 5,
      events: [],
      state: gameState({
        phase: "expansion",
        round_no: 1,
        players: [U1, U2],
        turn: QUESTION_TURN_B,
      }),
    });

    await screen.findByText(QUESTION_B.prompt);
    expect(screen.queryByTestId("choice-correct")).not.toBeInTheDocument();
    expect(screen.queryByTestId("choice-incorrect")).not.toBeInTheDocument();
  });

  it("surfaces a refused subscription via the connection-error banner, with a working Resync trigger", async () => {
    server.use(
      meOk(),
      http.get("/api/games/g1", () =>
        HttpResponse.json(
          snapshot(1, {
            phase: "expansion",
            round_no: 1,
            players: [U1, U2],
            turn: QUESTION_TURN_A,
          }),
        ),
      ),
    );
    renderRouter("/games/g1");
    await screen.findByText(QUESTION_A.prompt);
    await openAndWire();

    // The `not_found` the backend emits for a refused subscribe or resync —
    // `command_id: null`, so nothing correlates it to a command.
    deliver({
      type: "error",
      command_id: null,
      code: "not_found",
      message: "That subscription does not exist.",
    });

    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent("not_found");
    expect(banner).toHaveTextContent("That subscription does not exist.");

    await userEvent.click(screen.getByRole("button", { name: "Resync" }));
    expect(
      lastSocket()
        .frames()
        .some((f) => f.type === "resync" && f.topic === "game:g1"),
    ).toBe(true);
  });
});
