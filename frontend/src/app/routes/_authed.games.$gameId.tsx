import { createFileRoute } from "@tanstack/react-router";

/**
 * Placeholder, added here rather than in Task 12 because it already has to
 * exist for this task's own code to type-check: `useCreateGame` and
 * `useJoinGame` (`src/features/create-game`, `src/features/join-game`) both
 * `navigate({ to: "/games/$gameId", params: { gameId } })` on success, and
 * TanStack Router's typed `navigate` needs `/games/$gameId` to be a real
 * route — with this exact param shape — for that call to compile. Same
 * pattern Task 8 used for `/login` ahead of Task 9's `redirect`.
 *
 * Task 12 replaces the component (and adds the loader that fetches the
 * game's snapshot through `gameQueryOptions` / `writeGame`'s one merge
 * rule) — the route id and the `$gameId` param are expected to survive
 * unchanged, since Task 10's navigation calls already depend on them.
 */
export const Route = createFileRoute("/_authed/games/$gameId")({
  component: () => null,
});
