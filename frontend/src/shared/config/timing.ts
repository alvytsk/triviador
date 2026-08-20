export const TIMING = {
  /** §8.6: "ping every 15 s, socket considered dead after 30 s of silence." */
  PING_INTERVAL_MS: 15_000,
  /** How many round trips the clock offset is a median of (decision 11). */
  CLOCK_SAMPLES: 5,
  RECONNECT_BASE_MS: 500,
  RECONNECT_MAX_MS: 10_000,
  /** Below this the clock turns red. Presentation only. */
  TIMER_URGENT_MS: 8_000,
} as const;
