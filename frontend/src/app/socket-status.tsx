import { useParams } from "@tanstack/react-router";
import { useResyncGame } from "@/entities/game";
import { Banner } from "@/shared/ui";
import { useSocket } from "./socket-provider";

/**
 * §11.7's socket-status banner. Silent while the socket is open and nothing
 * has gone wrong — a permanent "connected" badge trains people to ignore
 * the strip the one evening it says something else.
 *
 * `connectionError` takes priority over the plain connection-status
 * branches below: a `not_found` on a refused `subscribe`/`resync` can land
 * while `status` is still `"open"` (the *transport* is fine; a specific
 * topic was refused), and that is exactly the "quietly stops updating"
 * case this banner exists to make loud — see `SocketProvider`'s
 * `connectionError` doc comment.
 *
 * The "Resync" trigger wires up §11.7's stated recovery ("any client-side
 * desync has exactly one resolution: take a fresh snapshot",
 * `useResyncGame`) — otherwise that recovery is unreachable from the
 * shipped UI. `useParams({ strict: false, shouldThrow: false })` reads
 * `gameId` if the current route happens to be a game screen; this banner
 * is mounted once, above every route (`_authed.tsx`), so it often has no
 * matched route at all to read from — a plain render outside a matched
 * `<Outlet>` (every test in `socket-provider.test.tsx`, which renders this
 * component on its own) has *no* nearest match, and `useMatch` (which
 * `useParams` is built on) throws in that case unless told not to.
 */
export function SocketStatusBanner() {
  const { status, connectionError } = useSocket();
  const params = useParams({ strict: false, shouldThrow: false });
  const gameId = params?.gameId;
  const resync = useResyncGame(gameId ?? "");

  if (connectionError !== null) {
    return (
      <Banner tone="bad" code={connectionError.code}>
        <span className="flex items-center gap-3">
          {connectionError.message}
          {gameId !== undefined && (
            <button
              type="button"
              onClick={resync}
              className="shrink-0 text-[11px] font-semibold uppercase tracking-[0.14em] text-gold underline underline-offset-2"
            >
              Resync
            </button>
          )}
        </span>
      </Banner>
    );
  }
  if (status === "open") return null;
  if (status === "closed") {
    return <Banner tone="quiet">Not connected. Reload to rejoin.</Banner>;
  }
  return <Banner tone="warn">Reconnecting — the board may be a moment behind.</Banner>;
}
