import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  InviteView,
  IssuedInvite,
  PresetDetail,
  QuestionDetail,
  QuestionSummary,
  UserView,
} from "@/shared/api/generated/admin";
import { FakeSocket } from "../../testing/fake-socket";
import { server } from "../../testing/msw";
import { renderRoute } from "../../testing/render";

/**
 * The counterpart to `pages/game/ui/full-game.test.tsx` (Plan 6) and
 * `backend/tests/api/integration/test_admin_session.py` (Plan 7A): one
 * admin story, walked through the rendered app against MSW, in the order
 * an operator actually uses it — sign in, add a question, run an import,
 * issue an invite, retire a preset — asserting that screens agree with
 * each other rather than that each works alone (the per-feature tests
 * already cover that).
 *
 * This file adds no product code. Where the story still cannot be walked
 * by clicking, that is recorded in a comment at the exact point it
 * happened, not patched around.
 *
 * ---
 *
 * UPDATE (gap-closing commit `fix(admin-ui): navigation and entry
 * points`) — Task 10's three navigation findings are now fixed, and this
 * file was rewritten to click through them instead of working around
 * them:
 *
 *   - FINDING 1 (AdminShell's own nav was unclickable): `admin-shell.tsx`'s
 *     five section links are now typed `<Link>`s. Proven below by an
 *     explicit round trip — click "Invites" from Questions, confirm the
 *     router actually moved and the Invites screen rendered, click
 *     "Questions" to come back — rather than by the old no-op assertion,
 *     which would now be asserting a lie. From here on, every screen this
 *     story visits for the *first* time (Invites, Presets, Users) is
 *     reached by a real click through this nav, not a fresh `renderRoute`.
 *   - FINDING 3 (the question list's row link had the same defect,
 *     independently): also now a typed `<Link>`. Proven below by clicking
 *     an imported question's row and landing on its real edit screen.
 *   - FINDING 2, "New question" half (`QuestionsPage` had no entry point
 *     to `/admin/questions/new`): fixed — a "New question" link now sits
 *     next to "Import" in the page header. The hand-typed question below
 *     is now reached by clicking it instead of mounting `renderRoute`
 *     there directly.
 *
 * Two `renderRoute` mounts remain deliberate, NOT replaced by clicks, for
 * a reason independent of any of the above: `createQueryClient` sets
 * `staleTime: Infinity` (§9.3), so revisiting `/admin/questions` through
 * client-side nav after a mutation would serve the same `QueryClient`'s
 * already-cached (now stale) list rather than proving anything about the
 * backend. Both are marked "cross-screen check" below — a fresh
 * `renderRoute` gets a fresh `QueryClient`, so what it renders can only
 * have come from this file's own stateful MSW backend (shared, mutable,
 * closed over by every handler), not a warm cache. The very first mount
 * (sign-in) is also unchanged, for the obvious reason that there is no
 * prior screen to click from.
 *
 * FINDING 2's other half is UNCHANGED and still open: there is still no
 * control anywhere in the rendered app that creates a category.
 * `createCategory` exists in `entities/admin/api/categories.ts`,
 * generated, tested at that layer, and exported from `entities/admin` —
 * but as of this commit no screen wires it up yet (checked again: no
 * button, link, dialog or route in `pages/` or `features/` mentions
 * creating one). §9.7 lists no category screen and never will (see the
 * inline-creation commit that follows this one for the resolution scoped
 * to stay inside that constraint) — this file's assertions below that no
 * "new categor…" control exists on the Questions screen remain correct
 * and unchanged; the story still continues against an MSW-seeded
 * category, the same way every other admin test in this codebase already
 * has to.
 *
 * There is a fourth, milder observation, not treated as a finding because
 * no task ever scoped it: nothing in the signed-in player app (lobby,
 * header) links to `/admin` at all. An admin reaches it only by knowing
 * the URL — which is also true of every existing admin test in this repo,
 * including Task 1's own `admin-guard.test.tsx`. This file's own entry
 * (below) uses `/login`'s real `next` search param instead of a bare
 * direct mount, which *is* a genuine click path (the sign-in form
 * navigates to `next` on success) — the same mechanism a bookmarked
 * `/admin` URL plus a login wall would produce for a real operator.
 */

const ME_ADMIN = { user_id: "u1", username: "admin", display_name: "Admin", role: "admin" };

function unauthenticated() {
  return HttpResponse.json(
    { code: "unauthenticated", message: "no session", details: null },
    { status: 401 },
  );
}

const CATEGORY = { id: "cat-geo", slug: "geography", name: "Geography" };

/** §6.1's usable-default invariant (Task 9 settled this: zero presets is
 *  unreachable) — a preset the story must NOT be able to retire, since
 *  the backend's own `default_preset` refusal exists for exactly this. */
function defaultPreset(): PresetDetail {
  return {
    id: "preset-default",
    name: "Classic",
    is_default: true,
    is_active: true,
    rules: {
      player_count: 3,
      expansion_rounds: 4,
      battle_rounds: 4,
      base_hp: 3,
      answer_timeout_ms: 20_000,
      pick_timeout_ms: 15_000,
      warmup_ms: 5_000,
      claims_by_rank: [2, 1, 0],
      pts_base: 1000,
      pts_territory: 200,
      pts_conquered: 400,
      pts_defense: 100,
    },
  };
}

function csvFile() {
  // Same header shape `test_admin_session.py`'s `bank_zip()` uses, so this
  // walk is exercising the same-shaped upload the backend counterpart
  // proves against a real parser — this file only needs the *dry-run
  // response* to be self-consistent, since MSW answers it, not `imports.py`.
  return new File(
    ["prompt,answer\nWhich river flows through Prague?,Vltava\nWhen was 1989?,1989\n"],
    "bank.csv",
    { type: "text/csv" },
  );
}

/**
 * Every render below goes through the real `Providers` (via `renderRoute`),
 * which mounts `SocketWhenSignedIn` unconditionally the moment a screen is
 * signed in — stubbing `WebSocket` keeps that from racing a real (refused)
 * connection, same reasoning as `admin-guard.test.tsx`.
 */
beforeEach(() => {
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the whole admin session, click by click", () => {
  it("signs in, furnishes the bank, and leaves the traces every other screen can see", async () => {
    // A stateful fake backend, not a fixed fixture: every handler below
    // closes over these same mutable arrays, so a POST from one screen
    // is what a GET from a *different* screen's *own* fresh render
    // (fresh QueryClient, no shared cache) actually reads back. This is
    // what makes the cross-screen assertions below real rather than
    // React Query coincidentally carrying a warm cache forward.
    const questions: QuestionSummary[] = [];
    const questionDetails = new Map<string, QuestionDetail>();
    const invites: InviteView[] = [];
    const presets: PresetDetail[] = [defaultPreset()];
    const users: UserView[] = [
      { id: "u1", username: "admin", display_name: "Admin", is_active: true, role: "admin" },
    ];

    server.use(
      // Starts unauthenticated — the sign-in step below is a real
      // credentials check, not a shortcut past it.
      http.get("/api/auth/me", () => unauthenticated()),
      http.post("/api/auth/login", () => HttpResponse.json(ME_ADMIN)),
      http.get("/api/admin/categories", () => HttpResponse.json([CATEGORY])),
      http.get("/api/admin/questions", () =>
        HttpResponse.json({ items: questions, total: questions.length, limit: 50, offset: 0 }),
      ),
      http.get("/api/admin/questions/:id", ({ params }) => {
        const detail = questionDetails.get(params.id as string);
        return detail === undefined
          ? HttpResponse.json(
              { code: "not_found", message: "no such question", details: null },
              { status: 404 },
            )
          : HttpResponse.json(detail);
      }),
      http.post("/api/admin/questions/import/dry-run", () =>
        HttpResponse.json(
          {
            import_id: "imp1",
            upload_sha256: "a".repeat(64),
            filename: "bank.csv",
            staged_key: "imp1/bank.csv",
            row_count: 2,
            rejected_count: 0,
            rejections: [],
            notices: [],
            status: "validated",
            confirmable: true,
            expires_at: "2026-08-22T00:00:00Z",
          },
          { status: 201 },
        ),
      ),
      http.post("/api/admin/questions/import/imp1/confirm", () => {
        // What the two-phase confirm actually does: writes rows to the
        // bank. `ImportSummary` never carries the rows themselves (no
        // such field on the contract) — the only way this is visible
        // anywhere else is a later `GET /api/admin/questions`, which is
        // exactly what "run an import" -> "appears in the list" tests.
        questions.push(
          {
            id: "q-imported-1",
            kind: "multiple_choice",
            prompt: "Which river flows through Prague?",
            category_id: CATEGORY.id,
            category_slug: CATEGORY.slug,
            difficulty: "easy",
            is_active: true,
            has_media: false,
            version: 1,
            updated_at: "2026-08-21T00:00:00Z",
          },
          {
            id: "q-imported-2",
            kind: "numeric",
            prompt: "In which year did the Velvet Revolution begin?",
            category_id: CATEGORY.id,
            category_slug: CATEGORY.slug,
            difficulty: "easy",
            is_active: true,
            has_media: false,
            version: 1,
            updated_at: "2026-08-21T00:00:00Z",
          },
        );
        // The row link's target (Task 10's Finding 3, fixed by
        // `fix(admin-ui): navigation and entry points`) is a real
        // client-side `<Link>` now, so this walk actually clicks through
        // to `GET /api/admin/questions/:id` — that route needs a detail
        // to answer with, not just the summary the list itself reads.
        questionDetails.set("q-imported-1", {
          id: "q-imported-1",
          kind: "multiple_choice",
          prompt: "Which river flows through Prague?",
          category_id: CATEGORY.id,
          category_slug: CATEGORY.slug,
          difficulty: "easy",
          is_active: true,
          media_asset_id: null,
          choices: [
            { idx: 0, text: "Vltava", is_correct: true, media_asset_id: null },
            { idx: 1, text: "Labe", is_correct: false, media_asset_id: null },
            { idx: 2, text: "Morava", is_correct: false, media_asset_id: null },
            { idx: 3, text: "Odra", is_correct: false, media_asset_id: null },
          ],
          numeric_answer: null,
          unit: null,
          version: 1,
        });
        questionDetails.set("q-imported-2", {
          id: "q-imported-2",
          kind: "numeric",
          prompt: "In which year did the Velvet Revolution begin?",
          category_id: CATEGORY.id,
          category_slug: CATEGORY.slug,
          difficulty: "easy",
          is_active: true,
          media_asset_id: null,
          choices: null,
          numeric_answer: "1989",
          unit: null,
          version: 1,
        });
        return HttpResponse.json({
          import_id: "imp1",
          upload_sha256: "a".repeat(64),
          filename: "bank.csv",
          staged_key: "imp1/bank.csv",
          row_count: 2,
          rejected_count: 0,
          rejections: [],
          notices: [],
          status: "confirmed",
          confirmable: false,
          expires_at: "2026-08-22T00:00:00Z",
        });
      }),
      http.post("/api/admin/questions", async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        const detail: QuestionDetail = {
          id: "q-hand-typed",
          kind: body.kind as QuestionDetail["kind"],
          prompt: body.prompt as string,
          category_id: body.category_id as string,
          category_slug: CATEGORY.slug,
          difficulty: body.difficulty as QuestionDetail["difficulty"],
          is_active: true,
          media_asset_id: null,
          numeric_answer: (body.numeric_answer as string | null) ?? null,
          unit: (body.unit as string | null) ?? null,
          choices: null,
          version: 1,
        };
        questionDetails.set(detail.id, detail);
        questions.push({
          id: detail.id,
          kind: detail.kind,
          prompt: detail.prompt,
          category_id: detail.category_id,
          category_slug: detail.category_slug,
          difficulty: detail.difficulty,
          is_active: true,
          has_media: false,
          version: 1,
          updated_at: "2026-08-21T00:00:00Z",
        });
        return HttpResponse.json({ question: detail, duplicate_of: [] }, { status: 201 });
      }),
      http.get("/api/admin/invites", () => HttpResponse.json(invites)),
      http.post("/api/admin/invites", async ({ request }) => {
        const body = (await request.json()) as { count: number };
        const issued: IssuedInvite[] = [];
        for (let i = 0; i < body.count; i++) {
          const id = `invite-${invites.length + i + 1}`;
          issued.push({ id, code: `CODE-${id}`, expires_at: "2026-08-28T00:00:00Z" });
          invites.push({
            id,
            status: "pending",
            expires_at: "2026-08-28T00:00:00Z",
            used_by: null,
          });
        }
        return HttpResponse.json(issued, { status: 201 });
      }),
      http.get("/api/admin/presets", () => HttpResponse.json(presets)),
      http.post("/api/admin/presets", async ({ request }) => {
        const body = (await request.json()) as {
          name: string;
          is_default: boolean;
          rules: unknown;
        };
        const created: PresetDetail = {
          id: "preset-new",
          name: body.name,
          is_default: body.is_default,
          is_active: true,
          rules: body.rules as PresetDetail["rules"],
        };
        presets.push(created);
        return HttpResponse.json(created, { status: 201 });
      }),
      http.delete("/api/admin/presets/preset-new", () => {
        const target = presets.find((preset) => preset.id === "preset-new");
        if (target !== undefined) target.is_active = false;
        return new HttpResponse(null, { status: 204 });
      }),
      http.get("/api/admin/users", () => HttpResponse.json(users)),
      // The one refusal `DeactivateControl`'s own comment names as the
      // route's only possible one: `self_target`. There is deliberately
      // only one admin in this fake backend, matching
      // `test_admin_session.py`'s own final scenario ("the admin cannot
      // remove themselves") — proven here through the UI instead of
      // through the API directly.
      http.post("/api/admin/users/u1/deactivate", () =>
        HttpResponse.json(
          { code: "self_target", message: "you cannot deactivate yourself", details: null },
          { status: 409 },
        ),
      ),
    );

    // ---------------------------------------------------------------
    // Step 1: sign in as an admin, landing on /admin/questions via a
    // real click — `next` is what a bookmarked admin URL behind the
    // login wall would produce, and `LoginPage.onDone` navigates there
    // on success (see this file's header comment on why this, rather
    // than a bare direct mount, is the honest click path in).
    // ---------------------------------------------------------------
    const session = renderRoute("/login?next=%2Fadmin%2Fquestions");

    await userEvent.type(await screen.findByLabelText("USERNAME"), "admin");
    await userEvent.type(screen.getByLabelText("PASSWORD"), "hunter2hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(session.router.state.location.pathname).toBe("/admin/questions"));
    // From here on, a fresh render's own `/api/auth/me` also needs to
    // answer as this same admin — the sign-in mutation only warmed
    // *this* render's cache.
    server.use(http.get("/api/auth/me", () => HttpResponse.json(ME_ADMIN)));

    expect(await screen.findByRole("heading", { name: "Questions" })).toBeInTheDocument();
    expect(
      await screen.findByText("No questions yet — import a starter set to get going."),
    ).toBeInTheDocument();

    // FINDING 2, category half: nothing on this screen, or the editor
    // it leads to, offers to create a category. Asserted here as an
    // absence rather than skipped silently.
    expect(screen.queryByRole("link", { name: /new categor/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /new categor/i })).not.toBeInTheDocument();

    // FINDING 1, now fixed: a real round trip through AdminShell's own
    // nav. "Invites" genuinely moves the router and renders that screen,
    // and "Questions" genuinely moves it back — a client-side transition
    // both ways, not the full-reload-only no-op this used to be.
    const adminNav = () => screen.getByRole("navigation", { name: "Admin" });
    await userEvent.click(within(adminNav()).getByRole("link", { name: "Invites" }));
    await waitFor(() => expect(session.router.state.location.pathname).toBe("/admin/invites"));
    expect(await screen.findByRole("heading", { name: "Invites" })).toBeInTheDocument();

    await userEvent.click(within(adminNav()).getByRole("link", { name: "Questions" }));
    await waitFor(() => expect(session.router.state.location.pathname).toBe("/admin/questions"));
    expect(await screen.findByRole("heading", { name: "Questions" })).toBeInTheDocument();

    // A genuine `<Link>` (Task 5's own entry point for Import), unchanged.
    await userEvent.click(within(screen.getByRole("main")).getByRole("link", { name: "Import" }));
    await waitFor(() =>
      expect(session.router.state.location.pathname).toBe("/admin/questions/import"),
    );

    // ---------------------------------------------------------------
    // Step 2: run a bulk import — the dry-run, then confirm.
    // ---------------------------------------------------------------
    expect(await screen.findByRole("heading", { name: "Import questions" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/upload questions file/i), {
      target: { files: [csvFile()] },
    });

    const confirmButton = await screen.findByRole("button", { name: /confirm import/i });
    expect(confirmButton).toBeEnabled();
    await userEvent.click(confirmButton);
    expect(
      await screen.findByText("Confirmed — the questions were added to the bank."),
    ).toBeInTheDocument();

    // ---------------------------------------------------------------
    // Cross-screen check 1: the two imported questions are visible from
    // a *different* screen visit — a fresh render, fresh QueryClient
    // (deliberately NOT a nav click: `createQueryClient`'s `staleTime:
    // Infinity` means a click-through revisit on the SAME QueryClient
    // would just serve back whatever it had cached before the import,
    // proving nothing about the backend). See this file's header comment
    // on why these two checks alone stay `renderRoute` mounts.
    // ---------------------------------------------------------------
    cleanup();
    const questionsAfterImport = renderRoute("/admin/questions");
    expect(
      await screen.findByText("Which river flows through Prague?", {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(screen.getByText("In which year did the Velvet Revolution begin?")).toBeInTheDocument();

    // FINDING 3, now fixed: the row link to an existing question is a
    // real `<Link>`. Click it and land on that question's own edit
    // screen, pre-filled with the record the import actually wrote.
    await userEvent.click(screen.getByRole("link", { name: "Which river flows through Prague?" }));
    await waitFor(() =>
      expect(questionsAfterImport.router.state.location.pathname).toBe(
        "/admin/questions/q-imported-1",
      ),
    );
    expect(await screen.findByRole("heading", { name: "Edit question" })).toBeInTheDocument();
    expect(screen.getByLabelText("Prompt")).toHaveValue("Which river flows through Prague?");

    // Back to the list the same way a real operator now can — AdminShell's
    // own nav — to reach the entry point Step 3 needs.
    await userEvent.click(within(adminNav()).getByRole("link", { name: "Questions" }));
    await waitFor(() =>
      expect(questionsAfterImport.router.state.location.pathname).toBe("/admin/questions"),
    );
    expect(await screen.findByRole("heading", { name: "Questions" })).toBeInTheDocument();

    // ---------------------------------------------------------------
    // Step 3: add a question by hand. FINDING 2's "New question" half is
    // now fixed too — click the page's own entry point instead of
    // mounting `renderRoute("/admin/questions/new")` directly.
    // ---------------------------------------------------------------
    await userEvent.click(
      within(screen.getByRole("main")).getByRole("link", { name: /new question/i }),
    );
    await waitFor(() =>
      expect(questionsAfterImport.router.state.location.pathname).toBe("/admin/questions/new"),
    );
    expect(await screen.findByRole("heading", { name: "New question" })).toBeInTheDocument();

    // The category `<Select>` genuinely offers the one category this
    // fake backend seeded — the closest this file can get to proving "a
    // category is selectable when adding a question" without a click
    // path to CREATE one (FINDING 2's category half, still open — see
    // the header comment).
    await userEvent.click(screen.getByRole("combobox", { name: "Category" }));
    await userEvent.click(await screen.findByRole("option", { name: "Geography" }));

    await userEvent.type(
      screen.getByLabelText("Prompt"),
      "How many bridges cross the Vltava in Prague?",
    );
    await userEvent.click(screen.getByRole("combobox", { name: "Kind" }));
    await userEvent.click(await screen.findByRole("option", { name: "Numeric" }));
    await userEvent.type(screen.getByLabelText("Correct value"), "18");

    await userEvent.click(screen.getByRole("button", { name: /create question/i }));

    await waitFor(() =>
      expect(questionsAfterImport.router.state.location.pathname).toBe(
        "/admin/questions/q-hand-typed",
      ),
    );
    // The redirect is a real client-side transition (create -> canonical
    // id), and the page re-fetches at the new id rather than staying on
    // the stale create-mode form.
    expect(await screen.findByRole("heading", { name: "Edit question" })).toBeInTheDocument();

    // ---------------------------------------------------------------
    // Cross-screen check 2: the hand-typed question is visible from the
    // list too, alongside the imported ones — proving the list reads
    // the same backend state the editor just wrote, not a coincidence
    // of the editor's own cache. Fresh `renderRoute` again, for the same
    // `staleTime: Infinity` reason as cross-screen check 1 — this is a
    // genuinely new write, so a nav-click revisit on the same
    // `QueryClient` would still show the pre-write cache.
    // ---------------------------------------------------------------
    cleanup();
    const finalCheck = renderRoute("/admin/questions");
    expect(
      await screen.findByText(
        "How many bridges cross the Vltava in Prague?",
        {},
        { timeout: 3000 },
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Which river flows through Prague?")).toBeInTheDocument();
    expect(screen.getByText("In which year did the Velvet Revolution begin?")).toBeInTheDocument();

    // ---------------------------------------------------------------
    // Step 4: issue an invite. FINDING 1 is fixed, and this is the
    // *first* visit to Invites in this render's `QueryClient`, so a real
    // nav click carries no staleness risk — no fresh `renderRoute`
    // needed here the way Cross-screen checks 1 and 2 above still do.
    // ---------------------------------------------------------------
    await userEvent.click(within(adminNav()).getByRole("link", { name: "Invites" }));
    await waitFor(() => expect(finalCheck.router.state.location.pathname).toBe("/admin/invites"));
    expect(await screen.findByRole("heading", { name: "Invites" })).toBeInTheDocument();
    expect(
      await screen.findByText("No invites yet — issue some to get started."),
    ).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /issue invites/i }));
    const countField = screen.getByLabelText(/count/i);
    await userEvent.clear(countField);
    await userEvent.type(countField, "1");
    await userEvent.click(screen.getByRole("button", { name: /^issue$/i }));

    const issuedCode = await screen.findByText(/^CODE-invite-1$/);
    expect(issuedCode).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^done$/i }));

    // Cross-component agreement on the *same* screen: the invite the
    // dialog just issued shows up in the table a sibling component
    // reads through its own query — this is the brief's own example
    // ("an issued invite appears in the invite listing").
    expect(await screen.findByText("Pending")).toBeInTheDocument();

    // ---------------------------------------------------------------
    // Step 5: create and retire a preset. First visit to Presets in this
    // `QueryClient`, so — same reasoning as Step 4 — a real nav click.
    // ---------------------------------------------------------------
    await userEvent.click(within(adminNav()).getByRole("link", { name: "Presets" }));
    await waitFor(() => expect(finalCheck.router.state.location.pathname).toBe("/admin/presets"));
    expect(await screen.findByRole("heading", { name: "Presets" })).toBeInTheDocument();
    expect(await screen.findByText("Classic")).toBeInTheDocument();
    expect(screen.getByText("Default")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /new preset/i }));
    await userEvent.type(screen.getByLabelText(/^name$/i), "Speed round");
    await userEvent.click(screen.getByRole("button", { name: /create preset/i }));

    const speedRow = (await screen.findByRole("button", { name: /open speed round/i })).closest(
      "tr",
    );
    expect(speedRow).not.toBeNull();
    expect(within(speedRow as HTMLElement).getByText("Active")).toBeInTheDocument();

    await userEvent.click(
      within(speedRow as HTMLElement).getByRole("button", { name: /^retire$/i }),
    );
    await userEvent.click(
      within(speedRow as HTMLElement).getByRole("button", { name: /^confirm retire$/i }),
    );

    await waitFor(() =>
      expect(within(speedRow as HTMLElement).getByText("Retired")).toBeInTheDocument(),
    );
    expect(
      within(speedRow as HTMLElement).queryByRole("button", { name: /^retire$/i }),
    ).not.toBeInTheDocument();

    // The default preset is untouched and still open-able — retiring
    // one preset does not disturb another (§6.1's soft delete is
    // per-row, not a screen-wide state).
    const classicRow = screen.getByRole("button", { name: /open classic/i }).closest("tr");
    expect(within(classicRow as HTMLElement).getByText("Active")).toBeInTheDocument();

    // ---------------------------------------------------------------
    // Step 6: visit Users (the eighth and last screen) and confirm the
    // one refusal this session's own shape makes reachable — the admin
    // cannot deactivate themselves. Mirrors `test_admin_session.py`'s
    // own closing scenario. First visit to Users, same reasoning as
    // Steps 4 and 5: a real nav click.
    // ---------------------------------------------------------------
    await userEvent.click(within(adminNav()).getByRole("link", { name: "Users" }));
    await waitFor(() => expect(finalCheck.router.state.location.pathname).toBe("/admin/users"));
    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
    const adminRow = (await screen.findByText("admin")).closest("tr") as HTMLElement;

    await userEvent.click(within(adminRow).getByRole("button", { name: /^deactivate$/i }));
    await userEvent.click(within(adminRow).getByRole("button", { name: /^confirm deactivate$/i }));

    // `adminErrorMessage` overrides the server's own sentence with its
    // fixed one for `self_target` (Task 1's error map) — the assertion
    // is on that fixed sentence, not the raw message this fake backend
    // sent, which is what actually reaches the screen. Scoped to this
    // row: `SocketStatusBanner` also renders `role="status"`, which
    // makes an unscoped query for the role ambiguous.
    expect(await within(adminRow).findByRole("status")).toHaveTextContent(
      "You cannot do that to your own account. Ask another administrator.",
    );
    // Refused, not silently ignored — the row is still active.
    expect(within(adminRow).getByText("Active")).toBeInTheDocument();
  }, 20_000);
});

describe("the questions list sees its own writes on a same-router revisit", () => {
  /**
   * Regression test for the whole-branch review's CRITICAL finding: no
   * question mutation (create, update, activate/deactivate,
   * import-confirm) invalidated `adminKeys.questions(...)`, so a list the
   * admin had already visited kept showing pre-mutation rows for the rest
   * of the session — `createQueryClient` sets `staleTime: Infinity` with
   * no window-focus/reconnect refetch (`app/query-client.ts`), so nothing
   * ever forced a second look.
   *
   * WHY THIS NEEDS ITS OWN TEST, NOT A TWEAK TO THE ONE ABOVE: every
   * post-mutation assertion in `admin-session.test.tsx`'s big scenario
   * (the "cross-screen check" comments) deliberately uses a FRESH
   * `renderRoute` call — a fresh `QueryClient` — specifically so the
   * assertion proves a write reached the BACKEND rather than a warm
   * client cache. That is the right test for that job. But it is
   * structurally incapable of catching THIS bug: a fresh `QueryClient` has
   * no stale entry to serve, so it would render correctly whether or not
   * `invalidateQueries` was ever called. The exact reasoning that makes
   * those checks sound against the backend is what made them blind to a
   * cache that never invalidates — eight per-feature reviews and the
   * full end-to-end walk above all passed with the bug live. Proving THIS
   * bug requires the opposite setup: one router, one `QueryClient`, visit
   * the list once to warm its cache, mutate, and revisit on the SAME
   * client without remounting anything.
   */
  it("shows an edited prompt (and drops the old one) after navigating back on the same router", async () => {
    const ME_ADMIN = { user_id: "u1", username: "admin", display_name: "Admin", role: "admin" };
    const CATEGORY = { id: "cat-geo", slug: "geography", name: "Geography" };

    const summary = {
      id: "q1",
      kind: "multiple_choice",
      prompt: "OLD PROMPT",
      category_id: CATEGORY.id,
      category_slug: CATEGORY.slug,
      difficulty: "easy",
      is_active: true,
      has_media: false,
      version: 1,
      updated_at: "2026-08-21T00:00:00Z",
    };
    const detail = {
      id: "q1",
      kind: "multiple_choice",
      prompt: "OLD PROMPT",
      category_id: CATEGORY.id,
      category_slug: CATEGORY.slug,
      difficulty: "easy",
      is_active: true,
      media_asset_id: null,
      choices: [
        { idx: 0, text: "Vltava", is_correct: true, media_asset_id: null },
        { idx: 1, text: "Labe", is_correct: false, media_asset_id: null },
        { idx: 2, text: "Morava", is_correct: false, media_asset_id: null },
        { idx: 3, text: "Odra", is_correct: false, media_asset_id: null },
      ],
      numeric_answer: null,
      unit: null,
      version: 1,
    };

    server.use(
      http.get("/api/auth/me", () => HttpResponse.json(ME_ADMIN)),
      http.get("/api/admin/categories", () => HttpResponse.json([CATEGORY])),
      http.get("/api/admin/questions", () =>
        HttpResponse.json({ items: [summary], total: 1, limit: 50, offset: 0 }),
      ),
      http.get("/api/admin/questions/q1", () => HttpResponse.json(detail)),
      http.patch("/api/admin/questions/q1", async ({ request }) => {
        const body = (await request.json()) as { prompt: string };
        // Mutate in place — the same stateful-backend shape the big
        // scenario above uses, so a fix that merely made THIS test's own
        // fixture object identity change (rather than reading the query
        // cache correctly) couldn't accidentally pass it.
        detail.prompt = body.prompt;
        summary.prompt = body.prompt;
        return HttpResponse.json({ question: detail, duplicate_of: [] });
      }),
    );

    // Visit 1: warm this router's QueryClient with the pre-edit list.
    const session = renderRoute("/admin/questions");
    expect(await screen.findByText("OLD PROMPT", {}, { timeout: 3000 })).toBeInTheDocument();

    // Into the editor via a real click — same shape as the big scenario's
    // own row-link check.
    await userEvent.click(screen.getByRole("link", { name: "OLD PROMPT" }));
    await waitFor(() => expect(session.router.state.location.pathname).toBe("/admin/questions/q1"));
    expect(await screen.findByDisplayValue("OLD PROMPT")).toBeInTheDocument();

    const promptField = screen.getByLabelText("Prompt");
    await userEvent.clear(promptField);
    await userEvent.type(promptField, "NEW PROMPT");
    await userEvent.click(screen.getByRole("button", { name: /save changes/i }));

    // The round trip actually finished — not just that the click fired.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /save changes/i })).toBeEnabled(),
    );

    // Back to the list on the SAME router — no `cleanup()`, no fresh
    // `renderRoute`. This is the one step the bug hid behind: a stale
    // cache renders exactly as if nothing were wrong.
    const adminNav = () => screen.getByRole("navigation", { name: "Admin" });
    await userEvent.click(within(adminNav()).getByRole("link", { name: "Questions" }));
    await waitFor(() => expect(session.router.state.location.pathname).toBe("/admin/questions"));

    expect(await screen.findByText("NEW PROMPT", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(screen.queryByText("OLD PROMPT")).not.toBeInTheDocument();
  }, 20_000);
});
