import { readdirSync, readFileSync } from "node:fs";
import { dirname, extname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// `fileURLToPath(import.meta.url)` rather than `new URL(".", import.meta.url)`:
// this file runs under vitest's jsdom environment, whose global `URL` shadows
// Node's and rejects that construction ("The URL must be of scheme file").
// `fileURLToPath` is a plain `node:url` function, not the global constructor,
// so it is unaffected.
const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_ROOT = join(HERE, "..");

/**
 * `setQueryData(gameKey(` — with or without a generic type argument
 * (`setQueryData<GameSnapshot>(gameKey(...`) and however it's spaced or
 * wrapped across lines. This is the literal write the one-merge-rule
 * (`writeGame`, `app/dispatcher.ts`) exists to be the only path for.
 *
 * `biome.json`'s `noRestrictedImports` on `@/app/dispatcher` stops someone
 * *importing* `writeGame` from outside `app/` — but it has nothing to say
 * about a hand-rolled `queryClient.setQueryData(gameKey(id), ...)`, which
 * reaches the exact same cache entry without importing anything that gate
 * watches. This test is the check that actually covers the write, not just
 * one way of getting there.
 */
const WRITES_GAME_CACHE = /setQueryData\s*(?:<[^>]*>)?\s*\(\s*gameKey\(/;

function sourceFilesUnder(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      files.push(...sourceFilesUnder(full));
    } else if (
      entry.isFile() &&
      (extname(entry.name) === ".ts" || extname(entry.name) === ".tsx")
    ) {
      files.push(full);
    }
  }
  return files;
}

describe("the game cache's one writer", () => {
  it("is never written to by anything outside app/", () => {
    const offenders = sourceFilesUnder(SRC_ROOT)
      .filter((file) => relative(SRC_ROOT, file).split("/")[0] !== "app")
      .filter((file) => WRITES_GAME_CACHE.test(readFileSync(file, "utf8")))
      .map((file) => relative(SRC_ROOT, file));

    expect(
      offenders,
      `${offenders.join(", ")} ${offenders.length === 1 ? "writes" : "write"} ["game", id] ` +
        "directly via setQueryData(gameKey(...)) instead of going through writeGame's one merge " +
        "rule (src/app/dispatcher.ts). A screen that needs a game to change sends a command over " +
        "the socket and lets the server answer, or — for a REST response like a create/join " +
        "mutation's GameSnapshot — navigates and lets the route's own loader write it through " +
        "writeGame. Writing the cache from a second place is exactly the bug this test exists to " +
        "catch: biome's noRestrictedImports on @/app/dispatcher does not catch it, because this " +
        "path imports nothing that gate watches.",
    ).toEqual([]);
  });
});
