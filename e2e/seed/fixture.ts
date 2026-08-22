import { execFileSync } from "node:child_process";
import { randomBytes } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Everything Task 13's scenario needs already sitting in the database
 * before a browser opens: an admin (bootstrapped the only way §10.1
 * allows — the `admin-create` CLI, not an HTTP route), a category, a
 * shortened preset (expansion 1 / battle 1, §12.4), a question bank that
 * covers it, and one invite per player.
 *
 * Driven entirely through the real admin API (`fetch` against the
 * deployed stack, the same origin a browser would use) rather than
 * inserting rows directly — a seed that bypasses the application can set
 * up a state the application cannot produce, and then the scenario proves
 * nothing about the seams it is supposed to exercise. The one exception is
 * `admin-create` itself: Spec 1 §10.1 deliberately gives bootstrapping no
 * HTTP surface at all (there is no "first admin" route to call), so this
 * is the one step that has to shell out to the CLI, the same way an
 * operator following the deploy docs would.
 *
 * Every named thing below (username, category slug, preset name, question
 * prompts) carries a random per-run suffix: this seeds a real, persistent
 * deployment (not a throwaway database), so a second run must not collide
 * with the first — `username_taken` for a re-redeemed player, `slug_taken`
 * for a re-created category, and so on.
 */

const REPO_ROOT = path.resolve(fileURLToPath(new URL(".", import.meta.url)), "../..");

// A 1x1 PNG. Its content, not its size, is what matters here — Pillow
// re-encodes whatever is uploaded, and the scenario's media assertion is
// about the URL Caddy/Garage resolve it to, not what it depicts.
const TEST_IMAGE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

export interface PlayerCredentials {
  code: string;
  username: string;
  password: string;
  displayName: string;
}

export interface Fixture {
  baseURL: string;
  presetName: string;
  players: readonly [PlayerCredentials, PlayerCredentials, PlayerCredentials];
}

function runSuffix(): string {
  return randomBytes(4).toString("hex");
}

/** `docker compose -f compose.yaml -f compose.prod.yaml exec -T backend ...`
 * — the exact stack `infra/deploy.sh` brings up, from the repo root, the
 * same way every other infra script in this repo invokes Compose. `-T`
 * disables pseudo-TTY allocation: this runs from a script, not a terminal,
 * and `docker compose exec` otherwise tries to allocate one and fails
 * noisily in a non-interactive shell. */
function composeExecBackend(args: string[]): string {
  return execFileSync(
    "docker",
    [
      "compose",
      "-f",
      "compose.yaml",
      "-f",
      "compose.prod.yaml",
      "exec",
      "-T",
      "backend",
      ...args,
    ],
    { cwd: REPO_ROOT, encoding: "utf-8" },
  );
}

async function bootstrapAdmin(username: string, password: string, displayName: string): Promise<void> {
  // §10.1's three outcomes: `created` and `already_exists` are both a
  // clean exit (this admin is now usable either way); `refused` means a
  // *different* admin already exists. On a fresh deploy there is no other
  // admin, so `--force` never changes what happens; on a long-lived
  // deployment this scenario has already run against (any earlier E2E run,
  // or an operator's own admin), it is what keeps this fixed, dedicated
  // username creatable every time rather than refused on the second run.
  const out = composeExecBackend([
    "triviador",
    "admin-create",
    "--username",
    username,
    "--password",
    password,
    "--display-name",
    displayName,
    "--force",
  ]);
  const outcome = out.trim();
  if (outcome !== "created" && outcome !== "already_exists") {
    throw new Error(`admin-create: unexpected outcome ${JSON.stringify(outcome)}`);
  }
}

class AdminClient {
  private cookie: string | null = null;

  constructor(private readonly baseURL: string) {}

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    const headers: Record<string, string> = { Origin: this.baseURL, ...extra };
    if (this.cookie !== null) headers.Cookie = this.cookie;
    return headers;
  }

  async login(username: string, password: string): Promise<void> {
    const response = await fetch(`${this.baseURL}/api/auth/login`, {
      method: "POST",
      headers: this.headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ username, password }),
    });
    if (!response.ok) {
      throw new Error(`admin login failed: ${response.status} ${await response.text()}`);
    }
    const setCookie = response.headers.getSetCookie();
    if (setCookie.length === 0) throw new Error("admin login: no Set-Cookie in the response");
    // Only the name=value pair travels back out — Path/HttpOnly/SameSite
    // are directives to the browser that sent this request, meaningless
    // echoed back as request headers.
    this.cookie = setCookie.map((c) => c.split(";", 1)[0]).join("; ");
  }

  async postJson<T>(pathname: string, body: unknown): Promise<T> {
    const response = await fetch(`${this.baseURL}${pathname}`, {
      method: "POST",
      headers: this.headers({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      throw new Error(`POST ${pathname} -> ${response.status}: ${await response.text()}`);
    }
    return (await response.json()) as T;
  }

  async uploadMedia(bytes: Buffer, contentType: string): Promise<{ id: string; url: string }> {
    const response = await fetch(`${this.baseURL}/api/admin/media`, {
      method: "POST",
      headers: this.headers({ "Content-Type": contentType }),
      body: new Uint8Array(bytes),
    });
    if (!response.ok) {
      throw new Error(`POST /api/admin/media -> ${response.status}: ${await response.text()}`);
    }
    return (await response.json()) as { id: string; url: string };
  }
}

interface RulesBody {
  player_count: number;
  expansion_rounds: number;
  battle_rounds: number;
  base_hp: number;
  answer_timeout_ms: number;
  pick_timeout_ms: number;
  warmup_ms: number;
  claims_by_rank: number[];
  pts_base: number;
  pts_territory: number;
  pts_conquered: number;
  pts_defense: number;
}

// §12.4's shortened preset: one expansion round, one battle round, still a
// full 3-player match. Timeouts are short (but >= the domain's 3s floor)
// so the scenario stays fast without ever racing a real deadline —
// Playwright reacts to every window well inside it.
const SHORTENED_RULES: RulesBody = {
  player_count: 3,
  expansion_rounds: 1,
  battle_rounds: 1,
  base_hp: 3,
  answer_timeout_ms: 15_000,
  pick_timeout_ms: 15_000,
  warmup_ms: 3_000,
  claims_by_rank: [2, 1, 0],
  pts_base: 1000,
  pts_territory: 200,
  pts_conquered: 400,
  pts_defense: 100,
};

// player_count(3) * battle_rounds(1) = 3 duels, each of which may escalate
// to a numeric tiebreak; plus one numeric per expansion round and one for
// the final score tiebreak (`required_question_budget`,
// `domain/game/rules.py`). Seeded a little above the bare minimum so
// `StartGame`'s pool draw never comes up short.
const NUMERIC_QUESTION_COUNT = 6;
const MULTIPLE_CHOICE_QUESTION_COUNT = 4;

// Every seeded multiple-choice question uses this exact shape: the same
// four choice texts, always in the same order, always with idx 0 correct.
// The scenario's driver (`smoke.spec.ts`) exploits both facts — it always
// clicks "Choice B" by its accessible name (never by position), which is
// therefore always wrong, so no duel this match ever ties (`attacker_right
// and defender_right`) and needs a numeric tiebreak question at all.
export const WRONG_CHOICE_TEXT = "Choice B";
const CHOICES = [
  { text: "Choice A", is_correct: true },
  { text: "Choice B", is_correct: false },
  { text: "Choice C", is_correct: false },
  { text: "Choice D", is_correct: false },
];

export async function seed(baseURL: string): Promise<Fixture> {
  const suffix = runSuffix();

  // Fixed, not suffixed: `--force` (above) is what makes bootstrapping the
  // same admin safe on every run, so there is nothing here for a random
  // suffix to protect — unlike the category/preset/player names below,
  // which have no such escape hatch and collide for real on a second run
  // against the same deployment.
  const adminUsername = "e2e-admin";
  const adminPassword = "E2e-Admin-Passw0rd!";
  await bootstrapAdmin(adminUsername, adminPassword, "E2E Admin");

  const admin = new AdminClient(baseURL);
  await admin.login(adminUsername, adminPassword);

  const category = await admin.postJson<{ id: string }>("/api/admin/categories", {
    slug: `e2e-geo-${suffix}`,
    name: `E2E Geography ${suffix}`,
  });

  const media = await admin.uploadMedia(TEST_IMAGE_PNG, "image/png");

  const presetName = `E2E Shortened ${suffix}`;
  await admin.postJson("/api/admin/presets", {
    name: presetName,
    is_default: false,
    rules: SHORTENED_RULES,
  });

  for (let i = 0; i < NUMERIC_QUESTION_COUNT; i++) {
    await admin.postJson("/api/admin/questions", {
      kind: "numeric",
      prompt: `E2E ${suffix}: how many regions does the map have (Q${i})?`,
      category_id: category.id,
      difficulty: "easy",
      media_asset_id: media.id,
      numeric_answer: "42",
    });
  }
  for (let i = 0; i < MULTIPLE_CHOICE_QUESTION_COUNT; i++) {
    await admin.postJson("/api/admin/questions", {
      kind: "multiple_choice",
      prompt: `E2E ${suffix}: which of these is correct (Q${i})?`,
      category_id: category.id,
      difficulty: "easy",
      media_asset_id: media.id,
      choices: CHOICES,
    });
  }

  const issued = await admin.postJson<Array<{ code: string }>>("/api/admin/invites", {
    count: 3,
    expires_in_hours: 1,
  });

  const players = issued.map((invite, i) => ({
    code: invite.code,
    username: `e2e-player${i + 1}-${suffix}`,
    password: "E2e-Player-Passw0rd!",
    displayName: `E2E Player ${i + 1}`,
  })) as [PlayerCredentials, PlayerCredentials, PlayerCredentials];

  return { baseURL, presetName, players };
}
