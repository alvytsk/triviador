/** §8.1: fill comes from `territories[id].owner_id` mapped to a per-seat CSS
 *  custom property. This is the only place that mapping is written down. */
export const SEAT_COUNT = 4;

export function seatVar(seat: number): string {
  return `var(--seat-${seat % SEAT_COUNT})`;
}
