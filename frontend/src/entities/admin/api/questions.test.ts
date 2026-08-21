import { keepPreviousData, QueryClient } from "@tanstack/react-query";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { ApiFetchError } from "@/shared/api";
import { server } from "../../../../testing/msw";
import {
  activateQuestion,
  adminQuestionQueryOptions,
  adminQuestionsQueryOptions,
  createQuestion,
  deactivateQuestion,
  updateQuestion,
} from "./questions";

const SUMMARY = {
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
};

const DETAIL = {
  id: "q1",
  kind: "multiple_choice",
  prompt: "Which river flows through Prague?",
  category_id: "cat1",
  category_slug: "geography",
  difficulty: "easy",
  is_active: true,
  version: 1,
  media_asset_id: null,
  choices: [{ idx: 0, text: "Vltava", is_correct: true, media_asset_id: null }],
  numeric_answer: null,
  unit: null,
};

const WRITE_BODY = {
  kind: "multiple_choice" as const,
  prompt: "Which river flows through Prague?",
  category_id: "cat1",
  difficulty: "easy" as const,
  media_asset_id: null,
  choices: [{ text: "Vltava", is_correct: true }],
  numeric_answer: null,
  unit: null,
};

describe("adminQuestionsQueryOptions", () => {
  it("sends every filter and the page as query params, and parses the page view", async () => {
    let seenUrl: string | null = null;
    server.use(
      http.get("/api/admin/questions", ({ request }) => {
        seenUrl = request.url;
        return HttpResponse.json({ items: [SUMMARY], total: 1, limit: 20, offset: 0 });
      }),
    );
    const client = new QueryClient();
    const options = adminQuestionsQueryOptions({
      filters: {
        kind: "multiple_choice",
        categoryId: "cat1",
        difficulty: "easy",
        isActive: true,
        hasMedia: false,
        q: "prague",
      },
      page: { limit: 20, offset: 0 },
    });
    await expect(client.fetchQuery(options)).resolves.toEqual({
      items: [SUMMARY],
      total: 1,
      limit: 20,
      offset: 0,
    });
    const params = seenUrl === null ? null : new URL(seenUrl).searchParams;
    expect(params?.get("kind")).toBe("multiple_choice");
    expect(params?.get("category_id")).toBe("cat1");
    expect(params?.get("difficulty")).toBe("easy");
    expect(params?.get("is_active")).toBe("true");
    expect(params?.get("has_media")).toBe("false");
    expect(params?.get("q")).toBe("prague");
    expect(params?.get("limit")).toBe("20");
    expect(params?.get("offset")).toBe("0");
  });

  it("omits filters that were not given", async () => {
    let seenUrl: string | null = null;
    server.use(
      http.get("/api/admin/questions", ({ request }) => {
        seenUrl = request.url;
        return HttpResponse.json({ items: [], total: 0, limit: 50, offset: 0 });
      }),
    );
    const client = new QueryClient();
    await client.fetchQuery(
      adminQuestionsQueryOptions({ filters: {}, page: { limit: 50, offset: 0 } }),
    );
    const params = seenUrl === null ? null : new URL(seenUrl).searchParams;
    expect(params?.has("kind")).toBe(false);
    expect(params?.has("q")).toBe(false);
  });

  it("keeps the previous page's data as placeholder data", () => {
    const options = adminQuestionsQueryOptions({ filters: {}, page: { limit: 50, offset: 0 } });
    expect(options.placeholderData).toBe(keepPreviousData);
  });

  it("keys a different filter set as a different resource", () => {
    const a = adminQuestionsQueryOptions({ filters: {}, page: { limit: 50, offset: 0 } });
    const b = adminQuestionsQueryOptions({
      filters: { isActive: false },
      page: { limit: 50, offset: 0 },
    });
    expect(a.queryKey).not.toEqual(b.queryKey);
  });
});

describe("adminQuestionQueryOptions", () => {
  it("fetches and parses one question's detail", async () => {
    server.use(http.get("/api/admin/questions/q1", () => HttpResponse.json(DETAIL)));
    const client = new QueryClient();
    await expect(client.fetchQuery(adminQuestionQueryOptions("q1"))).resolves.toEqual(DETAIL);
  });
});

describe("createQuestion / updateQuestion", () => {
  it("posts the write request and parses the saved question plus duplicates", async () => {
    let seenBody: unknown = null;
    server.use(
      http.post("/api/admin/questions", async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json({ question: DETAIL, duplicate_of: [] }, { status: 201 });
      }),
    );
    await expect(createQuestion(WRITE_BODY)).resolves.toEqual({
      question: DETAIL,
      duplicate_of: [],
    });
    expect(seenBody).toEqual(WRITE_BODY);
  });

  it("patches an existing question by id", async () => {
    server.use(
      http.patch("/api/admin/questions/q1", () =>
        HttpResponse.json({ question: DETAIL, duplicate_of: ["q9"] }),
      ),
    );
    await expect(updateQuestion("q1", WRITE_BODY)).resolves.toEqual({
      question: DETAIL,
      duplicate_of: ["q9"],
    });
  });
});

describe("activateQuestion / deactivateQuestion", () => {
  it("deactivates by id and parses the resulting detail", async () => {
    server.use(
      http.post("/api/admin/questions/q1/deactivate", () =>
        HttpResponse.json({ ...DETAIL, is_active: false }),
      ),
    );
    await expect(deactivateQuestion("q1")).resolves.toEqual({ ...DETAIL, is_active: false });
  });

  it("activates by id and parses the resulting detail", async () => {
    server.use(http.post("/api/admin/questions/q1/activate", () => HttpResponse.json(DETAIL)));
    await expect(activateQuestion("q1")).resolves.toEqual(DETAIL);
  });
});

describe("malformed responses", () => {
  it("raise a transport ApiFetchError rather than resolving", async () => {
    server.use(http.get("/api/admin/questions/q1", () => HttpResponse.json({ id: "q1" })));
    const client = new QueryClient();
    const error = await client.fetchQuery(adminQuestionQueryOptions("q1")).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiFetchError);
    expect((error as ApiFetchError).kind).toBe("transport");
  });
});
