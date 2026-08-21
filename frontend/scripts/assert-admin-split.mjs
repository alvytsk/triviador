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
// admin-only screens" — except that once a path is actually registered
// as a route, its `fullPath` also lands, unconditionally, in
// `routeTree.gen.ts`'s own route-info map, and that file is not
// lazy-split at all: it is the eager route tree every entry chunk
// imports to build the router in the first place (only a route's
// `component` is ever code-split — `_authed.admin.tsx`'s own comment).
// `/admin/questions` was excluded from this list for exactly that reason
// (Task 3's report attributed it to `_authed.admin.index.tsx`'s redirect
// target, which is also eager route code and also true, but
// `routeTree.gen.ts`'s `fullPath: '/admin/questions'` alone would have
// forced the same exclusion). `/admin/questions/import` joins it here as
// of Task 5, for the identical reason, confirmed the expensive way:
// leaving it in this list and running `pnpm check:bundle` after wiring
// the route failed check 1 with `/admin/questions/import found in
// assets/index-*.js, part of the entry graph` — not from anything this
// screen imports, but from that route's own entry in `routeTree.gen.ts`'s
// route-info map, `fullPath: '/admin/questions/import'`. `/admin/invites`
// joins it here as of Task 6, same mechanism, same confirmation: wiring
// `_authed.admin.invites.tsx` and leaving the href in this list made
// `pnpm check:bundle` fail check 1 with `/admin/invites found in
// assets/index-*.js, part of the entry graph`, traced to
// `routeTree.gen.ts`'s new `fullPath: '/admin/invites'` entry, not to
// anything `InvitesPage` itself imports. `/admin/users` joins it here as
// of Task 7, same mechanism, same confirmation: wiring
// `_authed.admin.users.tsx` and leaving the href in this list made
// `pnpm check:bundle` fail check 1 with `/admin/users found in
// assets/index-*.js, part of the entry graph`, traced to
// `routeTree.gen.ts`'s new `fullPath: '/admin/users'` entry, not to
// anything `UsersPage` itself imports. `/admin/presets` — the last of
// AdminShell's five nav hrefs — is REMOVED as of Task 8, same mechanism:
// wiring `_authed.admin.presets.tsx` puts `fullPath: '/admin/presets'`
// into `routeTree.gen.ts`'s route-info map the instant the route is
// registered, regardless of anything `PresetsPage` itself imports, so
// leaving it in this list would fail check 1 on infrastructure this
// check was never meant to flag — exactly as the four before it did.
// With all five nav hrefs gone, this list's only remaining members are
// the two schema markers below; Task 8 adds no third. Ruling carried in
// from Task 6's mutation-test finding: `generated/admin.ts` is retained
// WHOLE by Rollup once any single export is used (proven empirically —
// see that task's report — by importing `importSummarySchema` alone into
// an eager file and observing BOTH `duplicate_of` and `used_by` leak, not
// just one), so a leak of `presetDetailSchema`/`presetCoverageSchema`
// content trips the existing two markers exactly as any other admin
// schema leak would. A third marker drawn from a presets-only field
// would only ever be redundant with that finding, not additionally
// protective.
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
// The three href markers below still make check 2 ("something is in the
// lazy set") non-vacuous today; Task 2's report documents a manual,
// temporary-import verification that `duplicate_of` itself behaves
// correctly (found only in a lazy chunk when wired in; leaks into the
// eager set and fails check 1 when hoisted into a player-reachable file)
// without leaving that wiring in the tree.
//
// `used_by` is Task 6's addition, and — like `duplicate_of` — it is
// schema content, drawn this time from an invites-only DTO rather than
// the questions subtree `duplicate_of` already covers: it is the one
// object key of `inviteViewSchema` (`InviteView`'s `status`/`expires_at`/
// `id`/`used_by`) that appears nowhere else in `generated/admin.ts` and
// nowhere in this codebase outside that file and `entities/admin`'s own
// test fixture — confirmed with `grep -rn '"used_by"' frontend/src` (only
// `generated/admin.ts` and `invites.test.ts`, the latter not shipped)
// before it was chosen. `duplicate_of` alone would stay silent forever
// about a hypothetical future screen that imports admin schemas without
// ever touching `questionSavedSchema` — `used_by` is that second,
// independent witness, actually constructed at runtime by
// `InviteTable`/`InvitesPage` (`inviteViewSchema` parses every response
// `adminInvitesQueryOptions`/`revokeInvite` return, unlike
// `issueInvitesRequestSchema`'s `expires_in_hours`, whose *type* — not
// its schema — is all `issueInvites` imports, so that schema is never
// constructed at runtime and would not have worked as a marker). Found
// only in `_authed.admin.invites.lazy-*.js` when this task wired the
// route (see this task's report for the `pnpm check:bundle` output).
const ADMIN_ONLY_MARKERS = ["duplicate_of", "used_by"];

// Whole-branch review finding: the two marker-based checks above prove no
// eager chunk *constructs an admin schema* — a proxy for Spec 1B §9's
// actual claim ("players never download it"), not the claim itself. Proof
// of the gap: importing `AdminShell` (which pulls in only
// `@tanstack/react-router` and `@/shared/lib` — no `entities/admin`, no
// `generated/admin`, so it trips neither marker) into the eager
// `_authed.admin.tsx` landed its own `"Back to lobby"` literal in the
// entry chunk while both marker checks kept reporting OK. This list is the
// direct fix: every source directory that is admin-only *code*, regardless
// of whether it happens to construct a generated schema. `src/pages/admin`
// covers the shell and all five admin screens; the five feature slices
// cover the admin-only mutations/forms those screens use that a future
// screen elsewhere in `pages/` could in principle also import by mistake.
// Matched against `emitModuleIds()`'s (`vite.config.ts`) absolute module
// ids, so a path fragment is enough — no need to reconstruct the project
// root here.
const ADMIN_ONLY_SOURCE_DIRS = [
  "/src/pages/admin/",
  "/src/features/manage-invites/",
  "/src/features/manage-presets/",
  "/src/features/manage-users/",
  "/src/features/edit-question/",
  "/src/features/import-questions/",
];

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const distDir = resolve(here, "../dist");
const manifestPath = resolve(distDir, ".vite/manifest.json");
const moduleIdsPath = resolve(distDir, ".vite/module-ids.json");

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

let moduleIdsRaw;
try {
  moduleIdsRaw = readFileSync(moduleIdsPath, "utf-8");
} catch (error) {
  console.error(
    `assert-admin-split: no module-id map at ${moduleIdsPath}. This is written by ` +
      "`emitModuleIds()` in `vite.config.ts` — if that plugin was removed, check 3 " +
      "below (the module-content check) has no data to run against.",
  );
  throw error;
}
/** Keyed by output `fileName` (matches `manifest[key].file`), each value
 *  the full list of source module ids Rollup folded into that chunk. */
const moduleIdsByFile = JSON.parse(moduleIdsRaw);

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

// 3. No chunk a player's first load executes may contain a MODULE from an
//    admin-only source directory — the check that closes the blind spot
//    checks 1 and 2 have: content that is admin-only CODE but constructs no
//    admin SCHEMA (the `AdminShell` mutation above) trips neither marker,
//    but it does still show up as a module id here, because Rollup's own
//    bundle metadata (not a source-text grep) is what `moduleIdsByFile`
//    comes from.
const codeLeaks = [];
for (const key of eagerKeys) {
  if (!allChunkKeys.includes(key)) continue;
  const file = manifest[key].file;
  const moduleIds = moduleIdsByFile[file] ?? [];
  for (const moduleId of moduleIds) {
    const dir = ADMIN_ONLY_SOURCE_DIRS.find((prefix) => moduleId.includes(prefix));
    if (dir !== undefined) codeLeaks.push({ key, file, moduleId, dir });
  }
}
if (codeLeaks.length > 0) {
  console.error(
    "assert-admin-split: FAILED — an admin-only source module is part of the entry graph:",
  );
  for (const leak of codeLeaks) {
    console.error(
      `  ${leak.moduleId} (matches ${leak.dir}) is bundled into ${leak.file} (${leak.key})`,
    );
  }
  process.exit(1);
}

// Same non-vacuity requirement as check 2, applied to check 3's own
// evidence: some lazy chunk must actually contain a module from one of
// these directories, or this check would pass just as happily if the
// admin tree were never built at all.
const codeFound = [];
for (const key of lazyOnlyKeys) {
  const file = manifest[key].file;
  const moduleIds = moduleIdsByFile[file] ?? [];
  for (const moduleId of moduleIds) {
    const dir = ADMIN_ONLY_SOURCE_DIRS.find((prefix) => moduleId.includes(prefix));
    if (dir !== undefined) codeFound.push({ key, file, dir });
  }
}
if (codeFound.length === 0) {
  console.error(
    "assert-admin-split: FAILED — no lazily-loaded chunk contains a module from any admin-only " +
      "source directory. Either the split regressed or none of pages/admin/** or the admin " +
      "feature slices were built at all.",
  );
  process.exit(1);
}

console.log("assert-admin-split: OK");
console.log(`  entry chunks: ${entryKeys.length}, eager chunks: ${eagerKeys.size}`);
console.log(`  lazy-only chunks: ${lazyOnlyKeys.length}`);
for (const hit of found) {
  console.log(`  ${hit.marker} found only in ${hit.file} (${hit.key})`);
}
const codeFoundDirs = [...new Set(codeFound.map((hit) => hit.dir))];
for (const dir of codeFoundDirs) {
  const count = codeFound.filter((hit) => hit.dir === dir).length;
  console.log(`  ${count} module(s) under ${dir} found, all confined to lazy chunks`);
}
