import { createFileRoute } from "@tanstack/react-router";

/**
 * Placeholder. `_authed` is a pathless layout route, and the router plugin
 * needs at least one child under it to generate a clean route tree — this is
 * that child. Task 10 replaces it with the lobby.
 */
export const Route = createFileRoute("/_authed/")({
  component: () => null,
});
