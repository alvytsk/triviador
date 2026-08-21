import { QueryClient } from "@tanstack/react-query";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { ApiFetchError } from "@/shared/api";
import { server } from "../../../../testing/msw";
import {
  adminPresetCoverageQueryOptions,
  adminPresetQueryOptions,
  adminPresetsQueryOptions,
  createPreset,
  deactivatePreset,
  publicPresetsQueryOptions,
  updatePreset,
} from "./presets";

const RULES = {
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
};

const PRESET_DETAIL = {
  id: "p1",
  name: "Standard",
  is_default: true,
  is_active: true,
  rules: RULES,
};

describe("adminPresetsQueryOptions", () => {
  it("fetches and parses the retired-inclusive preset list", async () => {
    server.use(http.get("/api/admin/presets", () => HttpResponse.json([PRESET_DETAIL])));
    const client = new QueryClient();
    await expect(client.fetchQuery(adminPresetsQueryOptions())).resolves.toEqual([PRESET_DETAIL]);
  });
});

describe("adminPresetQueryOptions", () => {
  it("fetches one preset by id, including a retired one", async () => {
    server.use(
      http.get("/api/admin/presets/p1", () =>
        HttpResponse.json({ ...PRESET_DETAIL, is_active: false }),
      ),
    );
    const client = new QueryClient();
    await expect(client.fetchQuery(adminPresetQueryOptions("p1"))).resolves.toEqual({
      ...PRESET_DETAIL,
      is_active: false,
    });
  });
});

describe("adminPresetCoverageQueryOptions", () => {
  it("fetches and parses the coverage readout", async () => {
    const coverage = {
      required: { numeric: 10, multiple_choice: 20 },
      bank: { numeric: 12, multiple_choice: 25 },
      sufficient: true,
      informative: true,
    };
    server.use(http.get("/api/admin/presets/p1/coverage", () => HttpResponse.json(coverage)));
    const client = new QueryClient();
    await expect(client.fetchQuery(adminPresetCoverageQueryOptions("p1"))).resolves.toEqual(
      coverage,
    );
  });
});

describe("createPreset / updatePreset", () => {
  it("posts a new preset and parses the created detail", async () => {
    let seenBody: unknown = null;
    server.use(
      http.post("/api/admin/presets", async ({ request }) => {
        seenBody = await request.json();
        return HttpResponse.json(PRESET_DETAIL, { status: 201 });
      }),
    );
    const body = { name: "Standard", is_default: true, rules: RULES };
    await expect(createPreset(body)).resolves.toEqual(PRESET_DETAIL);
    expect(seenBody).toEqual(body);
  });

  it("patches an existing preset by id", async () => {
    server.use(
      http.patch("/api/admin/presets/p1", () =>
        HttpResponse.json({ ...PRESET_DETAIL, name: "Renamed" }),
      ),
    );
    await expect(
      updatePreset("p1", { name: "Renamed", is_default: true, rules: RULES }),
    ).resolves.toEqual({ ...PRESET_DETAIL, name: "Renamed" });
  });

  it("surfaces default_preset as an envelope-kind ApiFetchError", async () => {
    server.use(
      http.patch("/api/admin/presets/p1", () =>
        HttpResponse.json(
          { code: "default_preset", message: "this is the default preset", details: null },
          { status: 409 },
        ),
      ),
    );
    const error = await updatePreset("p1", {
      name: "Standard",
      is_default: false,
      rules: RULES,
    }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiFetchError);
    expect((error as ApiFetchError).code).toBe("default_preset");
  });
});

describe("deactivatePreset", () => {
  it("sends a DELETE with no body and resolves on the 204", async () => {
    let seenMethod: string | null = null;
    let seenBody: string | null = null;
    server.use(
      http.delete("/api/admin/presets/p1", async ({ request }) => {
        seenMethod = request.method;
        seenBody = await request.text();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await expect(deactivatePreset("p1")).resolves.toBeUndefined();
    expect(seenMethod).toBe("DELETE");
    expect(seenBody).toBe("");
  });
});

describe("publicPresetsQueryOptions", () => {
  it("fetches the public (active-only) preset list from the non-admin route", async () => {
    const summary = { id: "p1", name: "Standard", is_default: true, rules: RULES };
    server.use(http.get("/api/presets", () => HttpResponse.json([summary])));
    const client = new QueryClient();
    await expect(client.fetchQuery(publicPresetsQueryOptions())).resolves.toEqual([summary]);
  });
});
