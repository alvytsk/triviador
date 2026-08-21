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
 * an operator actually uses it — sign in, create a category, add a
 * question, run an import, issue an invite, retire a preset — asserting
 * that screens agree with each other rather than that each works alone
 * (the per-feature tests already cover that).
 *
 * This file adds no product code. Where the story could not be walked by
 * clicking, that is recorded in a comment at the exact point it happened,
 * not patched around — see Task 10's brief and the three findings below,
 * which are the actual point of this file existing.
 *
 * ---
 *
 * FINDING 1 — AdminShell's own nav is unclickable in this environment.
 * `pages/admin/shell/ui/admin-shell.tsx` still renders its five section
 * links as plain `<a href>` elements (a forward-reference workaround from
 * Task 1, never upgraded once Tasks 3–8 registered the real routes — its
 * own comment is now stale, still saying "point at routes that do not
 * exist yet"). A plain `<a>` is real browser navigation; jsdom does not
 * implement navigation and a click on one is a silent no-op here — proven
 * below by clicking "Invites" from the Questions screen and observing the
 * router never moves. In a real browser the click *would* eventually land
 * on the target screen, but only via a full document reload (losing every
 * bit of SPA state) rather than a client-side transition — the exact
 * defect Task 5's and Task 8's reports already flagged as deferred, now
 * demonstrated rather than asserted. The very next step below clicks a
 * genuine `<Link>` (`QuestionsPage`'s own "Import" entry point, Task 5) in
 * the same environment and it works immediately, which is the contrast
 * that proves this is AdminShell's defect, not a jsdom limitation this
 * whole file is subject to.
 *
 * Because of Finding 1, this test cannot walk from one admin screen to
 * the next by clicking through AdminShell — there is no other way to
 * reach Invites, Users or Presets at all (nothing else in the rendered
 * app links to them either — see Finding 2). Per the brief, the fix is
 * not to call the router directly to fake the click succeeding. Instead,
 * each such screen below is entered by mounting a fresh `renderRoute` at
 * its URL — the same thing a real operator would be forced to do today
 * (bookmark or retype the address, because the in-app link only manages a
 * full reload) and the identical pattern every other admin page test in
 * this codebase already uses to reach its screen. State created on one
 * screen is proven to reach another through this file's own stateful MSW
 * backend below (shared, mutable, closed over by every handler), not
 * through anything a single React Query cache could be quietly carrying
 * for free — a fresh `renderRoute` gets a fresh `QueryClient` every time.
 *
 * FINDING 2 — two of the story's own entry points do not exist yet.
 *   - There is no control anywhere in the rendered app that creates a
 *     category. `createCategory` exists in `entities/admin/api/
 *     categories.ts`, generated, tested at that layer, and exported from
 *     `entities/admin` — but no task ever wired it to a screen (checked:
 *     no button, link, dialog or route in `pages/` or `features/`
 *     mentions creating one; the question editor's Category `<Select>`
 *     only ever lists what `GET /api/admin/categories` already returns).
 *     A brand-new install has no click path to its first category, and
 *     therefore none to its first question either, which contradicts the
 *     backend's own "furnish a server from nothing" scenario
 *     (`test_admin_session.py`'s first two steps) — the backend can do
 *     it, the frontend cannot. This is a plan-level gap: no task's scope
 *     ever included a category screen. This file's "create a category"
 *     step below documents the gap in place and continues the rest of
 *     the story against an MSW-seeded category, exactly the way every
 *     other admin test in this codebase already has to.
 *   - `QuestionsPage` (Task 3) has no "New question" control at all — its
 *     only outbound link is "Import" (and, empty-state only, "Get
 *     started", which also points at Import). `/admin/questions/new`
 *     genuinely exists and works (Task 4), but nothing in the rendered
 *     app ever names it — confirmed by grep and by Task 4's own tests,
 *     which (like this one) can only reach it by mounting `renderRoute`
 *     there directly. Asserted below as an absence, at the exact screen
 *     where the control is missing.
 *
 * FINDING 3 — the row-level edit link has the same defect as Finding 1,
 * independently. `QuestionsPage`'s per-row link to `/admin/questions/$id`
 * (Task 3) is *also* a plain `<a href>`, not a `<Link>` — its own comment
 * says this was correct only until Task 4 registered that route, and
 * Task 4 never came back to convert it. Demonstrated below the same way
 * as Finding 1: click it, observe nothing happens.
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

    // FINDING 1, demonstrated: AdminShell's own "Invites" link is a
    // plain `<a>`. Click it and the router does not move.
    const adminNav = screen.getByRole("navigation", { name: "Admin" });
    const before = session.router.state.location.pathname;
    await userEvent.click(within(adminNav).getByRole("link", { name: "Invites" }));
    expect(session.router.state.location.pathname).toBe(before);
    expect(screen.queryByRole("button", { name: /issue invites/i })).not.toBeInTheDocument();

    // Contrast, same environment, same click mechanics: a genuine
    // `<Link>` (Task 5's own entry point for Import) works immediately.
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
    // a *different* screen visit — a fresh render, fresh QueryClient,
    // reached the only way available (Finding 1): re-enter the URL.
    // ---------------------------------------------------------------
    // `cleanup()` before each fresh URL entry below — not testing-library
    // hygiene, but the honest model of what Finding 1 costs a real operator:
    // a plain `<a>` click is a *full document reload*, which discards the
    // previous page's DOM entirely. Without this, unrelated elements from
    // the previous screen would still be mounted underneath the new one,
    // which is not what a real navigation (client-side or full-reload)
    // ever leaves behind, and would make later queries ambiguous for the
    // wrong reason.
    cleanup();
    const questionsAfterImport = renderRoute("/admin/questions");
    expect(
      await screen.findByText("Which river flows through Prague?", {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(screen.getByText("In which year did the Velvet Revolution begin?")).toBeInTheDocument();

    // FINDING 2, question half, asserted at the screen it is missing
    // from: still nothing here offers to create a brand-new question.
    expect(screen.queryByRole("link", { name: /new question/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /new question/i })).not.toBeInTheDocument();

    // FINDING 3, demonstrated the same way as Finding 1: the row link
    // to an existing question is also a plain `<a>`.
    const rowBefore = questionsAfterImport.router.state.location.pathname;
    await userEvent.click(screen.getByRole("link", { name: "Which river flows through Prague?" }));
    expect(questionsAfterImport.router.state.location.pathname).toBe(rowBefore);
    expect(screen.queryByRole("heading", { name: "Edit question" })).not.toBeInTheDocument();

    // ---------------------------------------------------------------
    // Step 3: add a question by hand. No click path exists to
    // `/admin/questions/new` (Finding 2) — entered directly, exactly
    // as `question-editor-page.test.tsx` itself already has to.
    // ---------------------------------------------------------------
    cleanup();
    const editor = renderRoute("/admin/questions/new");
    expect(await screen.findByRole("heading", { name: "New question" })).toBeInTheDocument();

    // The category `<Select>` genuinely offers the one category this
    // fake backend seeded — the closest this file can get to proving
    // "a category is selectable when adding a question" without a way
    // to create one through the UI (Finding 2).
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
      expect(editor.router.state.location.pathname).toBe("/admin/questions/q-hand-typed"),
    );
    // The redirect is a real client-side transition (create -> canonical
    // id), and the page re-fetches at the new id rather than staying on
    // the stale create-mode form.
    expect(await screen.findByRole("heading", { name: "Edit question" })).toBeInTheDocument();

    // ---------------------------------------------------------------
    // Cross-screen check 2: the hand-typed question is visible from the
    // list too, alongside the imported ones — proving the list reads
    // the same backend state the editor just wrote, not a coincidence
    // of the editor's own cache.
    // ---------------------------------------------------------------
    cleanup();
    renderRoute("/admin/questions");
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
    // Step 4: issue an invite (Finding 1 again: reached by URL, not a
    // click, since AdminShell's own "Invites" link does not work here).
    // ---------------------------------------------------------------
    cleanup();
    renderRoute("/admin/invites");
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
    // Step 5: create and retire a preset — entirely on one screen, no
    // navigation needed for this half of the story.
    // ---------------------------------------------------------------
    cleanup();
    renderRoute("/admin/presets");
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
    // own closing scenario.
    // ---------------------------------------------------------------
    cleanup();
    renderRoute("/admin/users");
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
