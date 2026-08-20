import { useQuery } from "@tanstack/react-query";
import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { gameKey } from "@/entities/game";
import { apiFetch, gameSnapshotSchema } from "@/shared/api";
import { deadline, gameState, player, question, snapshot } from "../../../../testing/factories";
import { server } from "../../../../testing/msw";
import { renderWithApp } from "../../../../testing/render";
import { GamePage } from "./game-page";

const MAP_DETAIL = {
  map_id: "czechia",
  svg_url: "/maps/czechia/map.svg",
  regions: [
    { region_id: "praha", display_name: "Praha" },
    { region_id: "stredocesky", display_name: "Středočeský" },
    { region_id: "plzensky", display_name: "Plzeňský" },
  ],
};

const MAP_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">' +
  '<path id="praha" d="M0 0h1v1z"/>' +
  '<path id="stredocesky" d="M2 2h1v1z"/>' +
  '<path id="plzensky" d="M4 4h1v1z"/>' +
  "</svg>";

const MAP_HANDLERS = [
  http.get("/api/maps/czechia", () => HttpResponse.json(MAP_DETAIL)),
  http.get("/maps/czechia/map.svg", () => HttpResponse.text(MAP_SVG)),
];

const U1 = player({ player_id: "u1", display_name: "Alexey", seat: 0, base_region: null });
const U2 = player({
  player_id: "u2",
  display_name: "Petra",
  seat: 1,
  base_region: null,
});
const U3 = player({
  player_id: "u3",
  display_name: "Tomáš",
  seat: 2,
  base_region: null,
});

const QUESTION = question();
const QUESTION_TURN = {
  kind: "expansion_question" as const,
  question: QUESTION,
  answered: [] as readonly string[],
  your_answer: null,
  deadline_at: deadline(),
  deadline_id: 10,
  your_options: { pick: [] as readonly string[], attack: [] as readonly string[] },
};

/**
 * Same substitute `game-page.test.tsx` already documents: `GamePage` cannot
 * run `gameQueryOptions` or `useNarration` itself (both are `app/`-only,
 * `pages` may not import from `app` — steiger's `fsd/forbidden-imports`), so
 * this harness plays the route's part for a standalone render. Everything
 * this test needs beyond that — whether the real dispatcher applied a gapped
 * update's state and suppressed its narration — is proven directly against
 * `harness.bus`, the real `EventBus` `SocketProvider`'s own dispatcher emits
 * onto (see `SocketProvider`'s `bus` prop), not against anything reimplemented
 * in this file.
 */
function GameHarness({ gameId }: { gameId: string }) {
  const game = useQuery({
    queryKey: gameKey(gameId),
    queryFn: () => apiFetch(`/api/games/${gameId}`, gameSnapshotSchema),
  });
  return <GamePage gameId={gameId} game={game} />;
}

function deliver(harness: ReturnType<typeof renderWithApp>, message: Record<string, unknown>) {
  act(() => harness.socket.last().deliver(message));
}

/** The frames a *command* sent — `useGameSubscription`'s own `subscribe`
 *  (queued on mount, flushed the moment the fake socket opens) is real
 *  production traffic on the same socket and would otherwise throw off
 *  every "exactly one frame" assertion below. */
function commandFrames(harness: ReturnType<typeof renderWithApp>) {
  return harness.socket
    .last()
    .frames()
    .filter((frame) => frame.type !== "subscribe" && frame.type !== "unsubscribe");
}

describe("the whole game, client-side", () => {
  it(
    "goes from a two-of-three lobby to FINISHED through the real socket wiring — " +
      "Spec 1 §12.4's scenario, no browser, no backend",
    async () => {
      server.use(
        http.get("/api/games/g1", () =>
          HttpResponse.json(
            snapshot(1, { phase: "lobby", round_no: 0, turn: null, players: [U1, U2] }),
          ),
        ),
        ...MAP_HANDLERS,
      );

      // Step 1: lobby, two of three seats filled.
      const harness = renderWithApp(<GameHarness gameId="g1" />);
      expect(await screen.findByRole("heading", { name: "GAME ROOM" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /start game/i })).toBeEnabled();
      expect(screen.getByText("Empty seat")).toBeInTheDocument();

      act(() => harness.socket.last().open());

      // Step 2: a game.update seats the third player.
      deliver(harness, {
        type: "game.update",
        game_id: "g1",
        seq: 2,
        base_seq: 1,
        events: [],
        state: gameState({ phase: "lobby", round_no: 0, turn: null, players: [U1, U2, U3] }),
      });
      await screen.findByText("Tomáš");
      expect(screen.queryByText("Empty seat")).not.toBeInTheDocument();

      // Step 3: expansion begins with a multiple-choice question. The dock
      // shows the prompt and four choices, and the map is on the stage.
      deliver(harness, {
        type: "game.update",
        game_id: "g1",
        seq: 3,
        base_seq: 2,
        events: [],
        state: gameState({
          phase: "expansion",
          round_no: 1,
          players: [U1, U2, U3],
          turn: QUESTION_TURN,
        }),
      });
      expect(await screen.findByText(QUESTION.prompt)).toBeInTheDocument();
      for (const choice of ["Vltava", "Labe", "Morava", "Odra"]) {
        expect(screen.getByRole("button", { name: choice })).toBeInTheDocument();
      }
      expect(await screen.findByRole("img", { name: "Game map" })).toBeInTheDocument();

      // Step 4: click a choice — exactly one submit_answer, right deadline_id.
      await userEvent.click(screen.getByRole("button", { name: "Labe" }));
      const afterAnswer = commandFrames(harness);
      expect(afterAnswer).toHaveLength(1);
      expect(afterAnswer[0]).toMatchObject({
        type: "submit_answer",
        deadline_id: 10,
        payload: { kind: "choice", idx: 1 },
      });

      // Step 5: the server echoes your_answer back — choices disable.
      deliver(harness, {
        type: "game.update",
        game_id: "g1",
        seq: 4,
        base_seq: 3,
        events: [],
        state: gameState({
          phase: "expansion",
          round_no: 1,
          players: [U1, U2, U3],
          turn: {
            ...QUESTION_TURN,
            your_answer: { kind: "choice", idx: 1, value: null },
            answered: ["u1"],
          },
        }),
      });
      expect(await screen.findByText("Answer sent.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Labe" })).toBeDisabled();

      // Step 6: an expansion_picking turn offers exactly two regions.
      deliver(harness, {
        type: "game.update",
        game_id: "g1",
        seq: 5,
        base_seq: 4,
        events: [],
        state: gameState({
          phase: "expansion",
          round_no: 1,
          players: [U1, U2, U3],
          turn: {
            kind: "expansion_picking",
            current_picker: "u1",
            deadline_at: deadline(),
            deadline_id: 11,
            grants_remaining: {},
            pick_order: ["u1", "u2", "u3"],
            your_options: { pick: ["praha", "stredocesky"], attack: [] },
          },
        }),
      });
      await screen.findByLabelText("praha");
      const clickable = harness.container.querySelectorAll('path[aria-disabled="false"]');
      const offeredLabels = [...clickable].map((el) => el.getAttribute("aria-label")).sort();
      expect(offeredLabels).toEqual(["praha", "stredocesky"]);
      expect(screen.getByLabelText("plzensky")).toHaveAttribute("aria-disabled", "true");

      await userEvent.click(screen.getByLabelText("praha"));
      const afterPick = commandFrames(harness);
      expect(afterPick).toHaveLength(2);
      expect(afterPick[1]).toMatchObject({
        type: "pick_region",
        deadline_id: 11,
        payload: { region_id: "praha" },
      });

      // Step 7: a *gapped* update — base_seq (7) does not match the cache's
      // seq (5) — carrying phase: "battle". The board must still update, and
      // no narration may fire: Spec 1B §8.2's gap rule, proven against the
      // real dispatcher's real bus, not a reimplementation of the rule in
      // this test.
      const narrated: unknown[] = [];
      harness.bus.subscribe("g1", (event) => narrated.push(event));

      deliver(harness, {
        type: "game.update",
        game_id: "g1",
        seq: 8,
        base_seq: 7,
        events: [{ type: "round_started", phase: "battle", round_no: 1 }],
        state: gameState({
          phase: "battle",
          round_no: 1,
          players: [U1, U2, U3],
          turn: null,
        }),
      });
      expect(await screen.findByText(/battle/i)).toBeInTheDocument();
      expect(narrated).toEqual([]);

      // Step 8: phase: "finished" with a winner_id. The results widget names
      // the winner and the dock is gone.
      deliver(harness, {
        type: "game.update",
        game_id: "g1",
        seq: 9,
        base_seq: 8,
        events: [],
        state: gameState({
          phase: "finished",
          round_no: 1,
          winner_id: "u1",
          turn: null,
          players: [
            { ...U1, score: 1800, bonus_score: 200 },
            { ...U2, score: 1500, bonus_score: 100 },
            { ...U3, score: 900, bonus_score: 0, is_eliminated: true },
          ],
        }),
      });

      expect(await screen.findByTestId("results-winner")).toHaveTextContent("Alexey");
      expect(screen.queryByTestId("game-stage")).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Surrender" })).not.toBeInTheDocument();
      expect(screen.queryByText(/^round \d/i)).not.toBeInTheDocument();
    },
  );
});
