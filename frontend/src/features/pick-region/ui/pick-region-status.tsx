import type { CommandFailure } from "@/shared/api";
import { Banner } from "@/shared/ui";

/**
 * The one place a rejected `pick_region` is shown — `region_not_free` above
 * all, but also `unknown_region`, `not_your_turn` and `wrong_turn_state`.
 * Always the server's own code and message (decision 2): this component
 * never re-derives a reason the reducer already decided.
 */
export function PickRegionStatus({ failure }: { failure: CommandFailure | null }) {
  if (failure === null) return null;
  return (
    <Banner tone="bad" code={failure.code}>
      {failure.message}
    </Banner>
  );
}
