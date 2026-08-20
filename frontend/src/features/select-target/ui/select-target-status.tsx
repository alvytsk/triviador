import type { CommandFailure } from "@/shared/api";
import { Banner } from "@/shared/ui";

/**
 * The one place a rejected `select_attack_target` is shown — `not_adjacent`
 * and `own_territory` above all. Always the server's own code and message
 * (decision 2).
 */
export function SelectTargetStatus({ failure }: { failure: CommandFailure | null }) {
  if (failure === null) return null;
  return (
    <Banner tone="bad" code={failure.code}>
      {failure.message}
    </Banner>
  );
}
