// Mechanical proof of Spec 1B §9: "/admin/* is a lazily-loaded route tree
// guarded on role === 'admin', so players never download it and never
// construct its schemas."
//
// Run after `vite build` (wired as `pnpm check:bundle`). Reads
// `dist/.vite/manifest.json` — emitted because `vite.config.ts` sets
// `build.manifest: true` for exactly this purpose — and follows only its
// `imports` edges (never `dynamicImports`) from every entry chunk to build
// the true "a player's browser executes this without ever clicking
// anything" set. Anything reachable only by crossing a `dynamicImports`
// edge is the lazy set: code fetched on demand, the first time someone
// actually navigates into `/admin`.
//
// The check greps built chunk *source text* for literal strings, not
// identifiers. `questionWriteRequestSchema` (Plan 7A's example of an
// "admin-only identifier") is a plausible-sounding target, but it is also
// exactly the kind of top-level binding name a production minifier is
// free to rename. String *literals* are never renamed by a minifier, and
// `AdminShell`'s five nav hrefs already are real, permanent, string
// literals that exist for no reason other than "the admin nav points at
// admin-only screens" — `/admin/questions` itself is excluded because
// `_authed.admin.index.tsx`'s redirect target is eager route code (only a
// route's `component` is lazy-split, never its `beforeLoad`) and so
// legitimately also contains that one substring; the other four do not
// appear anywhere outside the admin tree.
//
// `duplicate_of` is Task 2's addition, and it is schema content rather
// than routing content: it is the one object key in the 27 schemas
// `entities/admin` now imports from `generated/admin.ts`
// (`questionSavedSchema`'s `duplicate_of` field) that appears nowhere
// else in this codebase — confirmed with
// `grep -rn '"duplicate_of"' frontend/src` before it was chosen. A Zod
// `z.object({...})` key is a string literal in the emitted source the same
// way `AdminShell`'s hrefs are, so it survives minification the same way,
// and — unlike a bare identifier — it can only appear in a chunk that
// actually constructs `questionSavedSchema`, not merely one that imports
// something that happens to share a binding name.
//
// As of Task 2, `duplicate_of` is not actually found in either the eager
// or the lazy set: `entities/admin/` exists and is fully tested, but no
// screen imports it yet (Tasks 3+ own the question editor that calls
// `createQuestion`/`updateQuestion`, the only functions that touch
// `questionSavedSchema`). It is listed now, ahead of that wiring, so the
// day a screen does import it, this check starts enforcing the split on
// real schema content immediately rather than needing a second edit here.
// The four href markers below still make check 2 ("something is in the
// lazy set") non-vacuous today; Task 2's report documents a manual,
// temporary-import verification that `duplicate_of` itself behaves
// correctly (found only in a lazy chunk when wired in; leaks into the
// eager set and fails check 1 when hoisted into a player-reachable file)
// without leaving that wiring in the tree.
const ADMIN_ONLY_MARKERS = [
  "/admin/questions/import",
  "/admin/invites",
  "/admin/users",
  "/admin/presets",
  "duplicate_of",
];

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const distDir = resolve(here, "../dist");
const manifestPath = resolve(distDir, ".vite/manifest.json");

let manifestRaw;
try {
  manifestRaw = readFileSync(manifestPath, "utf-8");
} catch (error) {
  console.error(
    `assert-admin-split: no manifest at ${manifestPath}. Run \`vite build\` first ` +
      "(this script is meant to run after it, as `check:bundle` does).",
  );
  throw error;
}
const manifest = JSON.parse(manifestRaw);

/** BFS over `imports` only — `dynamicImports` edges are exactly the
 *  boundary a player's initial page load never crosses. */
function reachableStatically(startKeys) {
  const seen = new Set();
  const queue = [...startKeys];
  while (queue.length > 0) {
    const key = queue.shift();
    if (seen.has(key) || !(key in manifest)) continue;
    seen.add(key);
    for (const next of manifest[key].imports ?? []) queue.push(next);
  }
  return seen;
}

const entryKeys = Object.keys(manifest).filter((key) => manifest[key].isEntry);
if (entryKeys.length === 0) {
  throw new Error("assert-admin-split: no entry chunk found in the manifest.");
}

const eagerKeys = reachableStatically(entryKeys);
const allChunkKeys = Object.keys(manifest).filter((key) => manifest[key].file?.endsWith(".js"));
const lazyOnlyKeys = allChunkKeys.filter((key) => !eagerKeys.has(key));

function readChunk(key) {
  return readFileSync(resolve(distDir, manifest[key].file), "utf-8");
}

// 1. No chunk a player's first load executes may contain admin-only
//    content.
const leaks = [];
for (const key of eagerKeys) {
  if (!allChunkKeys.includes(key)) continue;
  const source = readChunk(key);
  for (const marker of ADMIN_ONLY_MARKERS) {
    if (source.includes(marker)) leaks.push({ key, file: manifest[key].file, marker });
  }
}
if (leaks.length > 0) {
  console.error("assert-admin-split: FAILED — admin-only content reachable without a redirect:");
  for (const leak of leaks) {
    console.error(`  ${leak.marker} found in ${leak.file} (${leak.key}), part of the entry graph`);
  }
  process.exit(1);
}

// 2. Some lazy chunk must actually carry that content — a check that
//    passes vacuously (nothing anywhere, eager or lazy) proves nothing.
const found = [];
for (const key of lazyOnlyKeys) {
  const source = readChunk(key);
  for (const marker of ADMIN_ONLY_MARKERS) {
    if (source.includes(marker)) found.push({ key, file: manifest[key].file, marker });
  }
}
if (found.length === 0) {
  console.error(
    "assert-admin-split: FAILED — no lazily-loaded chunk contains any admin-only marker. " +
      "Either the split regressed (AdminShell is reachable from the entry — see the leak " +
      "check above, which would also have fired) or nothing built the admin tree at all.",
  );
  process.exit(1);
}

console.log("assert-admin-split: OK");
console.log(`  entry chunks: ${entryKeys.length}, eager chunks: ${eagerKeys.size}`);
console.log(`  lazy-only chunks: ${lazyOnlyKeys.length}`);
for (const hit of found) {
  console.log(`  ${hit.marker} found only in ${hit.file} (${hit.key})`);
}
