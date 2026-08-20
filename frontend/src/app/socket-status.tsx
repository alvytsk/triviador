import { Banner } from "@/shared/ui";
import { useSocket } from "./socket-provider";

/**
 * §11.7's socket-status banner. Silent while the socket is open — a
 * permanent "connected" badge trains people to ignore the strip the one
 * evening it says something else.
 */
export function SocketStatusBanner() {
  const { status } = useSocket();
  if (status === "open") return null;
  if (status === "closed") {
    return <Banner tone="quiet">Not connected. Reload to rejoin.</Banner>;
  }
  return <Banner tone="warn">Reconnecting — the board may be a moment behind.</Banner>;
}
