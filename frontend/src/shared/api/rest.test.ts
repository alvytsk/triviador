import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { server } from "../../../testing/msw";
import { ApiFetchError } from "./errors";
import { apiFetch, apiSend } from "./rest";

const bodySchema = z.object({ ok: z.boolean() });

describe("apiFetch", () => {
  it("parses a success body through the schema", async () => {
    server.use(http.get("/api/thing", () => HttpResponse.json({ ok: true })));
    await expect(apiFetch("/api/thing", bodySchema)).resolves.toEqual({ ok: true });
  });

  it("turns an error envelope into an envelope-kind failure carrying the server's code", async () => {
    server.use(
      http.get("/api/thing", () =>
        HttpResponse.json(
          { code: "credentials_invalid", message: "invalid username or password", details: null },
          { status: 401 },
        ),
      ),
    );
    const error = await apiFetch("/api/thing", bodySchema).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiFetchError);
    expect(error).toMatchObject({
      kind: "envelope",
      status: 401,
      code: "credentials_invalid",
      message: "invalid username or password",
    });
  });

  it("carries details through when the envelope has them", async () => {
    server.use(
      http.get("/api/thing", () =>
        HttpResponse.json(
          { code: "validation_failed", message: "bad", details: { field: "username" } },
          { status: 422 },
        ),
      ),
    );
    const error = (await apiFetch("/api/thing", bodySchema).catch(
      (e: unknown) => e,
    )) as ApiFetchError;
    expect(error.details).toEqual({ field: "username" });
  });

  it("classifies an HTML error page as transport, not as a server code", async () => {
    server.use(
      http.get("/api/thing", () =>
        HttpResponse.html("<html><body>502 Bad Gateway</body></html>", { status: 502 }),
      ),
    );
    const error = (await apiFetch("/api/thing", bodySchema).catch(
      (e: unknown) => e,
    )) as ApiFetchError;
    expect(error.kind).toBe("transport");
    expect(error.code).toBeNull();
    expect(error.status).toBe(502);
  });

  it("classifies a truncated body as transport", async () => {
    server.use(http.get("/api/thing", () => new HttpResponse('{"ok": tr', { status: 200 })));
    const error = (await apiFetch("/api/thing", bodySchema).catch(
      (e: unknown) => e,
    )) as ApiFetchError;
    expect(error.kind).toBe("transport");
  });

  it("classifies a 2xx whose shape the schema rejects as transport", async () => {
    server.use(http.get("/api/thing", () => HttpResponse.json({ ok: "yes please" })));
    const error = (await apiFetch("/api/thing", bodySchema).catch(
      (e: unknown) => e,
    )) as ApiFetchError;
    expect(error.kind).toBe("transport");
    expect(error.code).toBeNull();
  });

  it("classifies a dead network as transport with no status", async () => {
    server.use(http.get("/api/thing", () => HttpResponse.error()));
    const error = (await apiFetch("/api/thing", bodySchema).catch(
      (e: unknown) => e,
    )) as ApiFetchError;
    expect(error.kind).toBe("transport");
    expect(error.status).toBeNull();
  });

  it("classifies a 204 with no body as success when the schema accepts void", async () => {
    server.use(http.post("/api/logout", () => new HttpResponse(null, { status: 204 })));
    await expect(apiSend("/api/logout", z.void(), undefined)).resolves.toBeUndefined();
  });

  it("flags unauthenticated so a guard can redirect without matching strings", async () => {
    server.use(
      http.get("/api/thing", () =>
        HttpResponse.json(
          { code: "unauthenticated", message: "not signed in", details: null },
          { status: 401 },
        ),
      ),
    );
    const error = (await apiFetch("/api/thing", bodySchema).catch(
      (e: unknown) => e,
    )) as ApiFetchError;
    expect(error.isUnauthenticated).toBe(true);
  });

  it("sends a JSON body and keeps same-origin credentials", async () => {
    let seen: { body: unknown; credentials: string } | null = null;
    server.use(
      http.post("/api/thing", async ({ request }) => {
        seen = { body: await request.json(), credentials: request.credentials };
        return HttpResponse.json({ ok: true });
      }),
    );
    await apiSend("/api/thing", bodySchema, { username: "alexey" });
    expect(seen).toEqual({ body: { username: "alexey" }, credentials: "same-origin" });
  });
});
