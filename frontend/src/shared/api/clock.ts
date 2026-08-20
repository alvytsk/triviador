// `TIMING` lives in `shared/config` — a different segment of the same
// sliceless `shared` layer, so `fsd/import-locality` wants it relative, not
// `@/shared/config` (the same correction `shared/ui` needed for `shared/lib`).
import { TIMING } from "../config";

/**
 * §8.6: the client refines its clock offset from ping/pong, not from
 * `hello.server_time` — that one carries whatever one-way delay its packet
 * met. Median of the last five round trips (decision 11): one sample is at
 * the mercy of a single queued packet, and a mean is at the mercy of the same
 * packet forever.
 */
export function createClockOffset(samples: number = TIMING.CLOCK_SAMPLES) {
  const observed: number[] = [];
  return {
    /** `serverTime` is the pong's `server_time` in epoch milliseconds. The
     *  server's instant is assumed to sit halfway through the round trip,
     *  which is the standard estimate and is wrong only by the asymmetry of
     *  the two legs. */
    record(sentAt: number, serverTime: number, receivedAt: number): void {
      observed.push(serverTime - (sentAt + receivedAt) / 2);
      if (observed.length > samples) observed.shift();
    },
    /** Add this to a local `Date.now()` to get the server's clock. Zero until
     *  the first pong, which is correct: an unmeasured offset is best assumed
     *  to be none rather than guessed from `hello`. */
    offsetMs(): number {
      if (observed.length === 0) return 0;
      const sorted = [...observed].sort((a, b) => a - b);
      const middle = Math.floor(sorted.length / 2);
      return sorted.length % 2 === 1
        ? (sorted[middle] as number)
        : ((sorted[middle - 1] as number) + (sorted[middle] as number)) / 2;
    },
  };
}
