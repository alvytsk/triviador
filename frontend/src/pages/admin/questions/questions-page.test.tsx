import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../../../testing/msw";
import { renderRoute } from "../../../../testing/render";

const ME_ADMIN = { user_id: "u1", username: "admin", display_name: "Admin", role: "admin" };

const CATEGORIES = [{ id: "cat1", name: "Geography", slug: "geography" }];

function summary(overrides: Record<string, unknown> = {}) {
  return {
    id: "q1",
    kind: "multiple_choice",
    prompt: "Which river flows through Prague?",
    category_id: "cat1",
    category_slug: "geography",
    difficulty: "easy",
    is_active: true,
    has_media: false,
    version: 1,
    updated_at: "2026-08-21T00:00:00Z",
    ...overrides,
  };
}

function withMe() {
  server.use(http.get("/api/auth/me", () => HttpResponse.json(ME_ADMIN)));
}

function withCategories() {
  server.use(http.get("/api/admin/categories", () => HttpResponse.json(CATEGORIES)));
}

describe("QuestionsPage", () => {
  it("renders a page of questions", async () => {
    withMe();
    withCategories();
    server.use(
      http.get("/api/admin/questions", () =>
        HttpResponse.json({ items: [summary()], total: 1, limit: 50, offset: 0 }),
      ),
    );

    renderRoute("/admin/questions");

    expect(
      await screen.findByText("Which river flows through Prague?", {}, { timeout: 3000 }),
    ).toBeInTheDocument();
    expect(screen.getByText("geography")).toBeInTheDocument();
    expect(screen.getByText(/multiple choice/i)).toBeInTheDocument();
    expect(screen.getByText("Easy")).toBeInTheDocument();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("puts the search in the URL, and sends it as q=", async () => {
    withMe();
    withCategories();
    let seenUrl: string | null = null;
    server.use(
      http.get("/api/admin/questions", ({ request }) => {
        seenUrl = request.url;
        return HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 });
      }),
    );

    const { router } = renderRoute("/admin/questions");
    await screen.findByLabelText("Search prompts");

    await userEvent.type(screen.getByLabelText("Search prompts"), "velvet");

    await waitFor(() => {
      const url = seenUrl;
      expect(url).not.toBeNull();
      expect(new URL(url as string).searchParams.get("q")).toBe("velvet");
    });
    await waitFor(() => expect(router.state.location.search).toMatchObject({ q: "velvet" }));
  });

  it("resets to the first page when a filter changes", async () => {
    withMe();
    withCategories();
    const seenOffsets: number[] = [];
    server.use(
      http.get("/api/admin/questions", ({ request }) => {
        const offset = Number(new URL(request.url).searchParams.get("offset"));
        seenOffsets.push(offset);
        return HttpResponse.json({ items: [summary()], total: 120, limit: 50, offset });
      }),
    );

    renderRoute("/admin/questions");
    await screen.findByText("Which river flows through Prague?");

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(seenOffsets).toContain(50));

    await userEvent.click(screen.getByRole("combobox", { name: "Kind" }));
    await userEvent.click(await screen.findByRole("option", { name: "Numeric" }));

    await waitFor(() => expect(seenOffsets.at(-1)).toBe(0));
  });

  it("keeps the filter when paging", async () => {
    withMe();
    withCategories();
    const seenParams: URLSearchParams[] = [];
    server.use(
      http.get("/api/admin/questions", ({ request }) => {
        const params = new URL(request.url).searchParams;
        seenParams.push(params);
        return HttpResponse.json({
          items: [summary()],
          total: 120,
          limit: 50,
          offset: Number(params.get("offset") ?? "0"),
        });
      }),
    );

    renderRoute("/admin/questions");
    await screen.findByText("Which river flows through Prague?");

    await userEvent.click(screen.getByRole("combobox", { name: "Kind" }));
    await userEvent.click(await screen.findByRole("option", { name: "Numeric" }));
    await waitFor(() => expect(seenParams.at(-1)?.get("kind")).toBe("numeric"));

    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(seenParams.at(-1)?.get("offset")).toBe("50"));
    expect(seenParams.at(-1)?.get("kind")).toBe("numeric");
  });

  it("points a brand-new, filterless bank at the import screen", async () => {
    withMe();
    withCategories();
    server.use(
      http.get("/api/admin/questions", () =>
        HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
      ),
    );

    renderRoute("/admin/questions");

    expect(await screen.findByText(/import a starter set/i)).toBeInTheDocument();
    expect(screen.queryByText(/no questions match/i)).not.toBeInTheDocument();
  });

  it("tells a filtered-out result apart from an empty bank, and offers to clear it", async () => {
    withMe();
    withCategories();
    server.use(
      http.get("/api/admin/questions", () =>
        HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 }),
      ),
    );

    renderRoute("/admin/questions?kind=numeric");

    expect(await screen.findByText(/no questions match these filters/i)).toBeInTheDocument();
    expect(screen.queryByText(/import a starter set/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /clear filters/i })).toBeInTheDocument();
  });
});
