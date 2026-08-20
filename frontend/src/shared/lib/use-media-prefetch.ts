import { useEffect } from "react";

/**
 * §9.6. The whole match's pool is drawn at `GameStarted`, so every image the
 * game will ever show is known on entry and can be in cache before any timer
 * starts. The URLs are content-addressed and opaque (`/api/media/a3f9c1…`),
 * so prefetching them leaks neither question text nor answers.
 *
 * Four at a time: twenty-nine parallel requests on a phone on shared Wi-Fi is
 * how you turn a fairness fix into a fairness problem.
 *
 * Lives in `shared/lib` rather than `app/` (where the brief first drafted
 * it): the hook takes plain URLs and touches nothing app-specific — no
 * cache, no socket, no dispatcher — so nothing stops it living at the
 * bottom layer, and `GamePage` (`pages/game`) needs to call it directly.
 * `pages` may not import from `app` (steiger's `fsd/forbidden-imports`), the
 * same wall `useGameSubscription` hit; moving the parts that have no
 * app-only dependency is the same fix applied a second time.
 */
export function useMediaPrefetch(urls: readonly string[]): void {
  useEffect(() => {
    if (urls.length === 0) return;
    let cancelled = false;
    const queue = [...urls];

    const pump = (): void => {
      const url = queue.shift();
      if (url === undefined || cancelled) return;
      const image = new Image();
      image.onload = pump;
      image.onerror = pump; // a missing image must not stall the queue
      image.src = url;
    };
    // `lanes` is captured once, before any `pump()` call: the loop's own
    // condition re-reading `queue.length` here would shrink on every
    // iteration (`pump` shifts the queue synchronously), so a 2-URL prefetch
    // would open one lane, see `queue.length` drop to 1, and stop — never
    // opening the second lane at all. Capturing the width up front is what
    // actually starts `min(4, urls.length)` lanes concurrently, matching
    // "four at a time" above.
    const lanes = Math.min(4, urls.length);
    for (let lane = 0; lane < lanes; lane++) pump();

    return () => {
      cancelled = true;
    };
  }, [urls]);
}
