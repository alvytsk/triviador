import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";
import { RedeemPage } from "@/pages/redeem";

/** No search params: unlike `/login`, nothing sends a player here with a
 *  `next` to return to — an invite redeems into a fresh session, and the
 *  post-redeem destination is always the lobby. */
export const Route = createFileRoute("/redeem")({
  validateSearch: z.object({}),
  component: RedeemPage,
});
