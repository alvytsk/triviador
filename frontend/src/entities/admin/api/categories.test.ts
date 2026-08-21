import { QueryClient } from "@tanstack/react-query";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { ApiFetchError } from "@/shared/api";
import { server } from "../../../../testing/msw";
import { adminCategoriesQueryOptions, createCategory, renameCategory } from "./categories";

const CATEGORY = { id: "c1", slug: "geography", name: "Geography" };

describe("adminCategoriesQueryOptions", () => {
  it("parses the category list through the generated schema", async () => {
    server.use(http.get("/api/admin/categories", () => HttpResponse.json([CATEGORY])));
    const client = new QueryClient();
    await expect(client.fetchQuery(adminCategoriesQueryOptions())).resolves.toEqual([CATEGORY]);
  });

  it("raises a transport ApiFetchError when a category in the list is malformed", async () => {
    server.use(
      http.get("/api/admin/categories", () => HttpResponse.json([{ id: "c1", slug: "x" }])),
    );
    const client = new QueryClient();
    const error = await client.fetchQuery(adminCategoriesQueryOptions()).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiFetchError);
    expect((error as ApiFetchError).kind).toBe("transport");
  });
});

describe("createCategory", () => {
  it("posts the request body and parses the created category", async () => {
    let seenBody: unknown = null;
    server.use(
      http.post("/api/admin/categories", async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json(CATEGORY, { status: 201 });
      }),
    );
    await expect(createCategory({ slug: "geography", name: "Geography" })).resolves.toEqual(
      CATEGORY,
    );
    expect(seenBody).toEqual({ slug: "geography", name: "Geography" });
  });

  it("surfaces the server's slug_taken envelope as an envelope-kind ApiFetchError", async () => {
    server.use(
      http.post("/api/admin/categories", () =>
        HttpResponse.json(
          {
            code: "slug_taken",
            message: "a category with slug 'geography' already exists",
            details: null,
          },
          { status: 409 },
        ),
      ),
    );
    const error = await createCategory({ slug: "geography", name: "Geography" }).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ApiFetchError);
    expect((error as ApiFetchError).code).toBe("slug_taken");
  });
});

describe("renameCategory", () => {
  it("patches the category and parses the renamed result", async () => {
    let seenBody: unknown = null;
    server.use(
      http.patch("/api/admin/categories/c1", async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json({ ...CATEGORY, name: "World Geography" });
      }),
    );
    await expect(renameCategory("c1", { name: "World Geography" })).resolves.toEqual({
      ...CATEGORY,
      name: "World Geography",
    });
    expect(seenBody).toEqual({ name: "World Geography" });
  });
});
