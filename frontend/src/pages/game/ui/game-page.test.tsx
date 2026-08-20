import { useQuery } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { gameKey } from "@/entities/game";
import { apiFetch, gameSnapshotSchema } from "@/shared/api";
import { deadline, gameState, question, snapshot } from "../../../../testing/factories";
import { server } from "../../../../testing/msw";
import { renderWithApp } from "../../../../testing/render";
import { GamePage } from "./game-page";

const MAP_HANDLERS = [
  http.get("/api/maps/czechia", () =>
    HttpResponse.json({
      map_id: "czechia",
      svg_url: "/maps/czechia/map.svg",
      regions: [{ region_id: "praha", display_name: "Praha" }],
    }),
  ),
  http.get(
    "/maps/czechia/map.svg",
    () =>
      new HttpResponse(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path id="praha" d="M0 0h1v1z"/></svg>',
        { headers: { "content-type": "image/svg+xml" } },
      ),
  ),
];

/**
 * `GamePage` no longer runs `gameQueryOptions` itself — that function lives
 * in `app/game-query.ts` because its queryFn calls `writeGame`, and `pages`
 * may not import from `app` (steiger's `fsd/forbidden-imports`, the same
 * wall `useGameSubscription` hit and was moved to `entities/game` for). In
 * production the route runs the query and hands the live result down; this
 * harness plays the route's part for a standalone render, with a queryFn
 * that does a plain fetch and no merge.
 *
 * That's a deliberate, narrower substitute — proof that this harness is not
 * quietly weaker than production: the dispatcher's own cache write, driven
 * by a real `game.update` frame, is genuine production code running inside
 * `renderWithApp`'s real `SocketProvider`, and `useQuery`'s subscription to
 * the same `["game", id]` key is what lets `GamePage` see it land. Only the
 * *REST-arriving-late-after-a-newer-socket-write* race depends on the merge
 * itself (`writeGame`'s `seq >=` rule), and that is proven directly against
 * `gameQueryOptions` in `app/game-query.test.ts`, where the function
 * actually lives.
 */
function GameHarness({ gameId }: { gameId: string }) {
  const game = useQuery({
    queryKey: gameKey(gameId),
    queryFn: () => apiFetch(`/api/games/${gameId}`, gameSnapshotSchema),
  });
  return <GamePage gameId={gameId} game={game} />;
}

describe("GamePage", () => {
  it("paints from the REST snapshot before the socket opens", async () => {
    server.use(http.get("/api/games/g1", () => HttpResponse.json(snapshot(1, { round_no: 1 }))));
    renderWithApp(<GameHarness gameId="g1" />);
    expect(await screen.findByText(/round 1/i)).toBeInTheDocument();
  });

  it("re-renders from a game.update over the socket without a second fetch", async () => {
    let getCalls = 0;
    server.use(
      http.get("/api/games/g1", () => {
        getCalls++;
        return HttpResponse.json(snapshot(1, { round_no: 1 }));
      }),
    );
    const harness = renderWithApp(<GameHarness gameId="g1" />);
    await screen.findByText(/round 1/i);

    act(() => harness.socket.last().open());
    act(() =>
      harness.socket.last().deliver({
        type: "game.update",
        game_id: "g1",
        seq: 2,
        base_seq: 1,
        events: [],
        state: gameState({ phase: "expansion", round_no: 2 }),
      }),
    );

    expect(await screen.findByText(/round 2/i)).toBeInTheDocument();
    expect(getCalls).toBe(1);
  });

  it("keeps the stage's fixed geometry whether or not the open question has an image", async () => {
    server.use(
      http.get("/api/games/g1", () =>
        HttpResponse.json(
          snapshot(1, {
            phase: "battle",
            round_no: 1,
            turn: {
              kind: "battle_target_select",
              attacker_id: "u1",
              deadline_at: deadline(),
              deadline_id: 1,
              your_options: { pick: [], attack: [] },
            },
          }),
        ),
      ),
      ...MAP_HANDLERS,
    );
    const harness = renderWithApp(<GameHarness gameId="g1" />);
    await screen.findByRole("img", { name: "Game map" });
    const stageBefore = screen.getByTestId("game-stage").className;

    act(() => harness.socket.last().open());
    act(() =>
      harness.socket.last().deliver({
        type: "game.update",
        game_id: "g1",
        seq: 2,
        base_seq: 1,
        events: [],
        state: gameState({
          phase: "battle",
          round_no: 1,
          turn: {
            kind: "battle_duel",
            attacker_id: "u1",
            defender_id: "u2",
            region_id: "praha",
            deadline_at: deadline(),
            deadline_id: 1,
            tiebreak: false,
            answered: [],
            your_answer: null,
            your_options: { pick: [], attack: [] },
            question: question({ media_url: "/api/media/abc" }),
          },
        }),
      }),
    );

    await screen.findByRole("img", { name: "Question media" });
    const stageAfter = screen.getByTestId("game-stage").className;

    expect(stageAfter).toBe(stageBefore);
  });

  it("prefetches every url in media_prefetch, one Image() each", async () => {
    const ImageSpy = vi.fn();
    vi.stubGlobal("Image", ImageSpy);
    server.use(
      http.get("/api/games/g1", () =>
        HttpResponse.json(snapshot(1, { media_prefetch: ["/api/media/a", "/api/media/b"] })),
      ),
    );

    renderWithApp(<GameHarness gameId="g1" />);

    await waitFor(() => expect(ImageSpy).toHaveBeenCalledTimes(2));
  });

  it("renders the room with an enabled Start for a seated player when phase is lobby", async () => {
    server.use(
      http.get("/api/games/g1", () =>
        HttpResponse.json(snapshot(1, { phase: "lobby", turn: null })),
      ),
    );
    renderWithApp(<GameHarness gameId="g1" />);
    expect(await screen.findByRole("button", { name: /start game/i })).toBeEnabled();
  });
});
