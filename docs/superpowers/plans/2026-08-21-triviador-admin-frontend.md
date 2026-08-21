# Triviador Plan 7B — Admin Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render Plan 7A's admin backend. An admin signs in, edits the question bank, uploads media, bulk-imports a zip, manages invites, users and presets, and reads a coverage indicator that tells them whether a game can start — all behind a route tree a player never downloads. After this plan Spec 1 is complete end to end; Plan 8 deploys it.

**Architecture:** A lazily-loaded `/admin` route tree guarded on `role === 'admin'` (Spec 1B §9), built from `contracts/admin.schema.json` via the generated `admin.ts` Zod module — the same generate-and-validate discipline Plan 6 uses for the player surface. shadcn/ui arrives here, where tables, dialogs, selects and toasts earn it; Plan 6 deliberately deferred it (its Decision 4) rather than importing a component library to restyle away.

**Tech Stack:** React 19 · TypeScript · TanStack Router (file-based) · TanStack Query · TanStack Form · Zod · Tailwind 4 · **shadcn/ui + Radix + class-variance-authority** (new) · Vitest + Testing Library + MSW

**Spec:** `docs/superpowers/specs/2026-08-07-triviador-spec1-design.md` §9.7 (screens) and §10 (admin behaviour); `docs/superpowers/specs/2026-08-16-triviador-app-architecture-design.md` §9 (lazy, role-guarded tree), §6.1 (routes), §6.3 (error envelope), §8.4 (routing).

**Predecessor:** `docs/superpowers/plans/2026-08-20-triviador-admin-backend.md` (Plan 7A). Every route this plan calls exists and is tested there; every DTO is in `frontend/src/shared/api/generated/admin.ts`, which nothing imports yet.

---

## Global Constraints

- **TypeScript strict; `pnpm check` must pass** — biome, `tsc --noEmit`, and `steiger ./src` (Feature-Sliced Design). Run from `frontend/`.
- **`pnpm codegen:check` must stay green.** Never hand-edit anything under `src/shared/api/generated/`; if a DTO is missing, the fix is in the backend's `ADMIN_MODELS`, not here.
- **Types come from the generated Zod schemas.** No hand-written interface duplicating a DTO. `z.infer` is the only source.
- **Every admin response is parsed** through its generated schema by `apiFetch`, exactly as the player surface does. An unparsed `any` from the network is the bug this whole contract pipeline exists to prevent.
- **The admin tree is lazily loaded, and that is asserted, not assumed** (§9). A player must never download admin code or construct its schemas.
- **FSD layers hold.** `steiger` is the gate: `shared` → `entities` → `features` → `widgets` → `pages` → `app`. Admin screens are pages composed of features and widgets, not one file each.
- **Errors are rendered by code, never by status alone.** The envelope's `code` is a closed union (`src/shared/api/generated/errors.ts`); Plan 7A added six members this plan must handle: `media_rejected`, `import_not_confirmable`, `slug_taken`, `default_preset`, `last_admin`, `self_target`.
- **No new backend route.** If a screen seems to need one, stop and report — Plan 7A's surface is fixed and reviewed.
- **Tests assert behaviour through the DOM**, with MSW serving contract-shaped responses. A test that asserts a component called a mock is not a test of the screen.

---

## File Structure

```
frontend/src/
├── app/routes/
│   ├── _authed.admin.tsx                 CREATE  role guard + layout shell (eager, tiny)
│   ├── _authed.admin.lazy.tsx            CREATE  the shell's component (lazy)
│   ├── _authed.admin.index.tsx           CREATE  redirect to /admin/questions
│   ├── _authed.admin.questions.tsx       CREATE  + .lazy.tsx — list
│   ├── _authed.admin.questions.$id.tsx   CREATE  + .lazy.tsx — editor
│   ├── _authed.admin.questions.import.tsx CREATE + .lazy.tsx — two-phase import
│   ├── _authed.admin.invites.tsx         CREATE  + .lazy.tsx
│   ├── _authed.admin.users.tsx           CREATE  + .lazy.tsx
│   └── _authed.admin.presets.tsx         CREATE  + .lazy.tsx
├── entities/admin/
│   ├── api/{questions,categories,media,imports,invites,users,presets}.ts  CREATE
│   ├── model/keys.ts                     CREATE  query-key factory
│   └── index.ts                          CREATE
├── features/
│   ├── edit-question/                    CREATE  form + media upload
│   ├── import-questions/                 CREATE  dry-run → confirm wizard
│   ├── manage-invites/                   CREATE
│   ├── manage-users/                     CREATE
│   ├── manage-presets/                   CREATE
│   └── create-game/ui/                   MODIFY  preset picker replaces the fixed line
├── pages/admin/
│   ├── questions/                        CREATE  list page
│   ├── question-editor/                  CREATE
│   ├── import/                           CREATE
│   ├── invites/  users/  presets/        CREATE
├── shared/ui/                            MODIFY  shadcn primitives land here
│   └── {table,dialog,select,toast,...}.tsx
└── shared/lib/admin-errors.ts            CREATE  code → message, exhaustive
```

---

## Design decisions this plan makes that the spec does not state

1. **shadcn/ui is vendored into `shared/ui/`, not installed as a dependency tree.** shadcn is a copy-in component set by design; its files become ours and are styled to the dark broadcast look Plan 6 established. That keeps `steiger`'s layer rules intact (`shared/ui` is where primitives already live) and avoids a second visual vocabulary. Radix and `class-variance-authority` are the only new runtime dependencies; `clsx` and `tailwind-merge` are already present from Plan 6 precisely so these drop in unchanged.

2. **Lazy loading is proven by a build assertion, not by a comment.** §9's claim — "players never download it and never construct its schemas" — is testable: build, then assert no player-reachable chunk contains an admin schema identifier. A `.lazy.tsx` split that silently regresses is otherwise invisible until someone inspects a bundle. See Task 1.

3. **One error-message map, exhaustively typed.** `shared/lib/admin-errors.ts` maps `ErrorCode` to admin-facing copy with a `satisfies Record<AdminErrorCode, string>` so a new backend code fails the build here rather than rendering "something went wrong". The six codes Plan 7A added each get a sentence that says what to do next.

4. **Media upload posts the raw file as the body.** Plan 7A's `POST /api/admin/media` takes the image as the request body with its own `Content-Type` — not multipart (7A's Task 3 documents why). The uploader therefore sends the `File` directly. The import upload likewise posts raw bytes with `X-Filename`.

5. **An issued invite code is shown once, and the UI says so.** The backend stores only a digest; the plaintext exists in exactly one response. The issue dialog therefore presents codes as copyable text with an explicit "these will not be shown again" line, and the listing never asks for them.

6. **The coverage readout renders `informative` as a sentence, not a badge.** Spec 1 §10.6: the indicator is informative, not authoritative — an admin can deactivate a question between reading it and starting a game, and `StartGame` is the authoritative check. The screen states that in words next to the numbers.

7. **Server-side pagination stays server-side.** The question list passes `limit`/`offset` through to the URL as search params, so a filtered view is linkable and a refresh does not silently reset to page 1. TanStack Router's typed search params are the mechanism.

---

## Task 1: The lazy, role-guarded admin shell — and proof that players never download it

**Files:**
- Create: `frontend/src/app/routes/_authed.admin.tsx`, `_authed.admin.lazy.tsx`, `_authed.admin.index.tsx`
- Create: `frontend/src/pages/admin/shell/ui/admin-shell.tsx`, `.../index.ts`
- Create: `frontend/src/shared/lib/admin-errors.ts`
- Test: `frontend/src/app/admin-guard.test.tsx`, `frontend/scripts/assert-admin-split.mjs`
- Modify: `frontend/package.json` (a `check:bundle` script)

**Interfaces:**
- Produces: the `/admin` route with `beforeLoad` refusing a non-admin; `AdminShell` (nav + `<Outlet/>`); `adminErrorMessage(code): string`; `pnpm check:bundle`.

- [ ] **Step 1: Write the failing guard test**

`src/app/admin-guard.test.tsx` — mirror `authed-guard.test.tsx`'s shape (memory history + real router + MSW), asserting three things:

```tsx
it("sends a player away from /admin", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ ...ME, role: "player" })));
  const router = createRouter({ routeTree, context: { queryClient }, history: memory("/admin/questions") });
  render(<RouterProvider router={router} />);
  await waitFor(() => expect(router.state.location.pathname).toBe("/"));
});

it("lets an admin in", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ ...ME, role: "admin" })));
  // ...asserts the admin nav is on screen
});

it("sends an anonymous visitor to /login, not to /", async () => {
  // 401 from /api/auth/me — the `_authed` parent guard owns this, and the
  // admin guard must not shadow it with its own redirect.
});
```

The third case is the one worth writing carefully: `/admin` sits under `_authed`, so an anonymous visitor must reach `/login` (with `next`), not be bounced to `/` by a role check that ran on a `me` query that never resolved.

- [ ] **Step 2: Run it, watch it fail**

`cd frontend && pnpm test src/app/admin-guard.test.tsx` — fails: no `/admin` route.

- [ ] **Step 3: The route pair**

`_authed.admin.tsx` stays tiny and eager — it holds only the guard, so the decision to refuse costs no admin JavaScript:

```tsx
import { createFileRoute, redirect } from "@tanstack/react-router";
import { meQueryOptions } from "@/entities/game";

export const Route = createFileRoute("/_authed/admin")({
  // The parent `_authed` route has already ensured `me` (and redirected to
  // /login on 401), so this read is a cache hit and cannot 401 here. Its
  // only job is the role check — §9's "guarded on role === 'admin'".
  beforeLoad: async ({ context }) => {
    const me = await context.queryClient.ensureQueryData(meQueryOptions());
    if (me.role !== "admin") {
      // Home, not /login: signing in again would not help, and a login
      // form shown to someone already signed in is a dead end.
      throw redirect({ to: "/" });
    }
  },
});
```

`_authed.admin.lazy.tsx` carries the component, which is what keeps the tree out of the player bundle:

```tsx
import { createLazyFileRoute } from "@tanstack/react-router";
import { AdminShell } from "@/pages/admin/shell";

export const Route = createLazyFileRoute("/_authed/admin")({ component: AdminShell });
```

`_authed.admin.index.tsx` redirects `/admin` to `/admin/questions` (§9.7 lists no bare `/admin` screen).

`AdminShell` renders the six nav links and an `<Outlet/>`.

- [ ] **Step 4: The error map**

`src/shared/lib/admin-errors.ts`:

```ts
import type { ErrorCode } from "@/shared/api/generated/errors";

/** The codes Plan 7A added, each with the next action spelled out.
 *
 * `satisfies` rather than a plain annotation: a seventh admin code added
 * to the backend fails this file's type-check instead of rendering a
 * generic apology at the one moment an admin needs to know what to do. */
const ADMIN_MESSAGES = {
  media_rejected: "That image cannot be used — check the format and size, then try another.",
  import_not_confirmable: "This upload can no longer be applied. Run the dry-run again.",
  slug_taken: "A category with that slug already exists.",
  default_preset: "That is the default preset. Make another preset the default first.",
  last_admin: "This is the last administrator. Promote someone else first.",
  self_target: "You cannot do that to your own account. Ask another administrator.",
} satisfies Partial<Record<ErrorCode, string>>;
```

...plus `adminErrorMessage(code, fallback)`.

- [ ] **Step 5: Prove the split**

`frontend/scripts/assert-admin-split.mjs` — build, then read the emitted chunks and assert that no chunk reachable from the entry contains an admin-only identifier (e.g. `questionWriteRequestSchema`), while some lazily-loaded chunk does. Wire it as `"check:bundle": "vite build && node scripts/assert-admin-split.mjs"`.

The assertion must fail loudly if the split regresses. Prove it does: temporarily import `AdminShell` directly in `_authed.admin.tsx` (eager), run `pnpm check:bundle`, watch it fail, revert. Record that output in your report — this is the whole point of the task.

- [ ] **Step 6: Gate and commit**

`pnpm check && pnpm test && pnpm check:bundle`.

```bash
git add frontend/src frontend/scripts frontend/package.json
git commit -m "feat(admin-ui): lazy role-guarded /admin shell, with a bundle assertion that proves the split"
```

---

## Task 2: The admin data layer

**Files:**
- Create: `frontend/src/entities/admin/model/keys.ts`, `frontend/src/entities/admin/api/*.ts`, `frontend/src/entities/admin/index.ts`
- Test: `frontend/src/entities/admin/api/questions.test.ts` and siblings

**Interfaces:**
- Produces: query options and mutation functions for every Plan 7A route, each parsing through its generated schema.

- [ ] **Step 1: Keys first**

```ts
export const adminKeys = {
  questions: (filters: QuestionFilters, page: Page) => ["admin", "questions", filters, page] as const,
  question: (id: string) => ["admin", "question", id] as const,
  categories: () => ["admin", "categories"] as const,
  invites: () => ["admin", "invites"] as const,
  users: () => ["admin", "users"] as const,
  presets: () => ["admin", "presets"] as const,
  coverage: (id: string) => ["admin", "preset-coverage", id] as const,
} as const;
```

Filters and page are *in* the question list key: a filtered page is a different resource, and folding them out would make a filter change reuse the previous page's data.

- [ ] **Step 2: One module per resource**

Each follows the pattern `src/entities/game/api/me.ts` already sets — `queryOptions` + `apiFetch(path, schema)` with the generated schema. For example:

```ts
export function adminQuestionsQueryOptions(search: AdminQuestionSearch) {
  return queryOptions({
    queryKey: adminKeys.questions(search.filters, search.page),
    queryFn: () => apiFetch(`/api/admin/questions?${toQuery(search)}`, questionPageViewSchema),
    // The list is a table an admin scans and re-filters; showing the last
    // page while the next loads is better than a spinner on every keystroke.
    placeholderData: keepPreviousData,
  });
}
```

Mutations are plain async functions returning parsed results; the screens own the `useMutation` wiring and cache invalidation, so the entity layer stays free of React (FSD: `entities` may not import `features`).

- [ ] **Step 3: The two raw-body uploads**

`uploadMedia(file: File)` posts the `File` as the body with `Content-Type: file.type`; `dryRunImport(file: File)` posts the bytes with `X-Filename`. Both parse their response through the generated schema. Neither uses `FormData` — Plan 7A's routes read the raw stream (its Task 3 explains why).

- [ ] **Step 4: Tests**

MSW-backed, asserting the request shape as well as the parse: the media call must send the file as the body (not a multipart part), the import call must carry `X-Filename`, and a malformed response must raise `ApiFetchError` with `kind: "transport"` rather than resolving.

- [ ] **Step 5: Gate and commit**

---

## Task 3: The question list

**Files:** `frontend/src/pages/admin/questions/**`, `frontend/src/app/routes/_authed.admin.questions.{tsx,lazy.tsx}`, shadcn `table`/`select`/`input` primitives into `shared/ui/`
**Spec:** §10.2 — server-side pagination and filters on `kind`, category, difficulty, `is_active`, `has_media`, plus prompt search.

- [ ] **Step 1: Failing test** — renders rows from a contract-shaped page; typing in search updates the URL and issues a new request with `q=`; changing a filter resets to offset 0; paging forward keeps the filter.
- [ ] **Step 2..n:** typed search params on the route (`validateSearch` with a Zod schema), a filter bar, and a table. The empty state distinguishes "no questions match this filter" from "the bank is empty" — the second is what a fresh deployment sees, and it should point at the import screen.
- [ ] **Gate and commit.**

---

## Task 4: The question editor

**Files:** `frontend/src/features/edit-question/**`, `frontend/src/pages/admin/question-editor/**`, route pair for `_authed.admin.questions.$id`
**Spec:** §10.2 — common fields plus kind-specific ones; exactly 4 choices and exactly 1 correct for MC; `correct_value` + optional unit for numeric; media upload inside the editor; duplicates warn and never block.

- [ ] **Failing test first**, then build. The assertions that matter:
  - switching kind swaps the field set without losing the prompt;
  - a fourth choice cannot be removed and a fifth cannot be added (four is fixed — §10.2: "a configurable count buys nothing and costs variability in the answer grid");
  - marking a second choice correct un-marks the first;
  - saving a prompt the bank already has shows the duplicate warning **and** the save succeeds — the response carries `duplicate_of` on a 201/200, never a 409;
  - a rejected image renders `media_rejected`'s sentence, and the rest of the form survives;
  - `deactivate`/`activate` flip the state without a full-page reload.
- [ ] **Gate and commit.**

---

## Task 5: The two-phase import

**Files:** `frontend/src/features/import-questions/**`, `frontend/src/pages/admin/import/**`, route pair
**Spec:** §10.3 — dry-run reports per row; CONFIRM is enabled only when `rejected == 0`; the rejected rows download as CSV; confirming binds to that `import_id`.

- [ ] **Failing test first.** The load-bearing assertions:
  - the confirm button is disabled while `confirmable` is false, and **the screen reads `confirmable` from the response rather than recomputing `rejected_count === 0`** — the server also folds in status and expiry, and a client that re-derives it will eventually derive it differently;
  - rejections render by line number with their reason;
  - notices (duplicate prompts, in-file or against the bank) render as warnings that do **not** block confirm — §10.2's rule, and the place it is easiest to get wrong;
  - the rejected-rows CSV downloads from `GET /api/admin/questions/import/{id}/rejected.csv`;
  - a second confirm surfaces `import_not_confirmable`'s sentence rather than a raw failure.
- [ ] **Gate and commit.**

---

## Task 6: Invites

**Files:** `frontend/src/features/manage-invites/**`, `frontend/src/pages/admin/invites/**`, route pair
**Spec:** §10.5 — issue N codes with an expiry, list with status, revoke.

- [ ] Issue dialog takes a count and an expiry; the response's plaintext codes are shown once, copyable, with an explicit line saying they will not be shown again (Decision 5).
- [ ] The listing shows status (pending / used / revoked / expired) and never a code.
- [ ] Revoking twice is not an error — the second click must not surface a failure.
- [ ] **Gate and commit.**

---

## Task 7: Users

**Files:** `frontend/src/features/manage-users/**`, `frontend/src/pages/admin/users/**`, route pair
**Spec:** §10.5 — list, deactivate, grant/revoke admin; cannot deactivate self; cannot demote the last admin.

- [ ] Both refusals render their own sentence: `self_target` and `last_admin`. A generic toast here is the failure this task exists to avoid — these are the two moments an admin most needs to be told exactly what happened.
- [ ] Deactivation is presented as what it is: immediate, and it signs that user out of every session. The copy should say so.
- [ ] **Gate and commit.**

---

## Task 8: Presets, and the coverage readout

**Files:** `frontend/src/features/manage-presets/**`, `frontend/src/pages/admin/presets/**`, route pair
**Spec:** §10.6 — CRUD over `GameRules`, validation on save, coverage from `required_question_budget`, and the explicit note that editing a preset does not affect running games.

- [ ] The rules form surfaces the server's validation messages verbatim — `validate_rules` is the single definition of a legal ruleset and the client must not restate its bounds.
- [ ] Coverage renders need-vs-bank per kind with §10.6's sentence about being informative (Decision 6), and the page states that editing a preset does not affect games already running.
- [ ] The two default refusals (`default_preset` for clearing the default and for promoting a retired preset) render their sentence.
- [ ] A retired preset is visible and openable — Plan 7A added `get_including_retired` precisely so this screen can show one.
- [ ] **Gate and commit.**

---

## Task 9: The lobby preset picker

**Files:** `frontend/src/features/create-game/ui/**` (MODIFY)

Plan 6 shipped a fixed line: `Default rules — presets are configurable from the admin screens.` Plan 7A added `GET /api/presets` (its Decision 1) so this becomes a real select.

- [ ] Replace the line with a picker over `GET /api/presets`, defaulting to the preset flagged `is_default`, and send its id as `preset_id` instead of `null`.
- [ ] The rules readout reflects the selected preset.
- [ ] A player with one preset available sees a sensible non-choice rather than an empty select.
- [ ] **Gate and commit.**

---

## Task 10: One admin session through the UI

**Files:** `frontend/src/app/admin-session.test.tsx`

The counterpart to Plan 6's `full-game.test.tsx` and Plan 7A's `test_admin_session.py`: one test that walks the whole admin story through the rendered app against MSW — sign in as admin, create a category, add a question, run an import, issue an invite, retire a preset — asserting the screens agree with each other (a question added on one screen appears in the list on another).

This task adds no source file. If it needs one, an earlier task is incomplete.

- [ ] **Gate:** `pnpm check && pnpm test && pnpm check:bundle && pnpm codegen:check`, and the backend suite untouched and green.
- [ ] **Commit.**

---

## What this plan deliberately does not do

- **No new backend route.** Plan 7A's surface is fixed; a screen that seems to need more is a finding to report, not a route to add.
- **No media browser.** Spec 1 §10.4: upload happens inside the question editor, and there is no separate asset library in Spec 1.
- **No preset reactivation.** Plan 7A ships no route for it (recorded there as a deliberate decision); this plan renders retired presets as retired.
- **No Playwright.** Spec 1 §12.4's single end-to-end smoke and the compose file it runs against are Plan 8's.
- **No spectating, match history or analytics.** Spec 2.

## Self-review

**Spec coverage.** §9.7's admin screens → Tasks 3-8. Spec 1B §9's "lazily-loaded, role-guarded" → Task 1, asserted by a bundle check rather than assumed. §10.2 → Tasks 3 and 4. §10.3 → Task 5. §10.4's upload → Task 4 (inside the editor, per the spec). §10.5 → Tasks 6 and 7. §10.6 → Task 8. §6.3's envelope → Task 1's exhaustive error map, used by every later task. Plan 7A's Decision 1 (`GET /api/presets`) → Task 9.

**Two things a reviewer should push on before execution starts.**
1. **Task 1's bundle assertion is the only mechanical proof of §9's central claim.** If it turns out to be impractical against this Vite config, say so before Task 3 — every later task inherits the split, and discovering it does not hold after eight screens is expensive.
2. **shadcn components are vendored into `shared/ui/` (Decision 1).** That is a deliberate deviation from installing a component library, and it is cheap to reverse now and expensive after six screens of styling.
