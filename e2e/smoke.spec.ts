import { expect, type Page, test } from "@playwright/test";
import { seed, type PlayerCredentials, WRONG_CHOICE_TEXT } from "./seed/fixture";

/**
 * Spec 1 §12.4: exactly one Playwright scenario, not a suite. Three
 * independent browser contexts (sessions are cookie-based and host-only —
 * sharing a context would share a session and this would silently exercise
 * one player three times), an invite redemption each, create → join →
 * start, and a full match on a shortened preset (expansion 1 / battle 1)
 * played to `FINISHED`.
 *
 * Every other seam this repo's per-feature tests already cover in depth
 * (`frontend/src/pages/game/ui/full-game.test.tsx` walks the whole game
 * client-side against a fake socket; `frontend/src/app/admin-session.test.tsx`
 * walks the whole admin session against a fake backend). This scenario's
 * only job is the seams those two cannot reach: a real browser, a real
 * Postgres, a real Garage bucket behind a real Caddy, three real
 * cookie-scoped sessions talking to one real backend at once.
 */
test("three players redeem, play a shortened match, and reach FINISHED", async ({
  browser,
  baseURL,
}) => {
  test.setTimeout(180_000);
  const base = baseURL ?? "http://localhost";
  const fixture = await seed(base);

  // §10.7's whole reason for a media question: a real 200 through Caddy →
  // Garage, not an assumption. Every seeded question carries the same
  // image (see `seed/fixture.ts`), so this fires on the very first
  // question turn and every one after — recorded here rather than
  // asserted from just one `<img>` on one page, so a later question that
  // happened to 404 could not hide behind an earlier one that didn't.
  const mediaResponses: { url: string; status: number }[] = [];

  const contexts = await Promise.all([
    browser.newContext(),
    browser.newContext(),
    browser.newContext(),
  ]);
  const pages = await Promise.all(contexts.map((context) => context.newPage()));
  for (const page of pages) {
    page.on("response", (response) => {
      const url = new URL(response.url());
      if (url.pathname.startsWith("/media/")) {
        mediaResponses.push({ url: url.pathname, status: response.status() });
      }
    });
  }

  try {
    // Step 1: invite redemption, one independent session per player.
    await Promise.all(pages.map((page, i) => redeem(page, fixture.players[i])));

    // Step 2: create → join → start.
    const [host, ...others] = pages;
    await expect(host.getByRole("heading", { name: "LOBBY" })).toBeVisible();
    await host.getByLabel("Rules").selectOption({ label: fixture.presetName });
    await host.getByRole("button", { name: "Create game" }).click();
    await expect(host.getByRole("heading", { name: "GAME ROOM" })).toBeVisible();
    const gameUrl = host.url();

    // The other two join from the live lobby list via a real click on
    // this exact game's row — proving the lobby's `lobby.update`
    // broadcast reached them, not just that the game's URL happens to
    // work. `.last()`: `list_joinable()` orders by `created_at`, so on a
    // long-lived deployment this is the newest row — this one.
    for (const page of others) {
      await expect(page.getByRole("heading", { name: "LOBBY" })).toBeVisible();
      const join = page.getByRole("button", { name: "Join" }).last();
      await expect(join).toBeVisible({ timeout: 15_000 });
      await join.click();
      await expect(page).toHaveURL(gameUrl);
    }

    for (const page of pages) {
      await expect(page.getByRole("heading", { name: "GAME ROOM" })).toBeVisible();
    }

    await host.getByRole("button", { name: "Start game" }).click();

    // Step 3: play the shortened match to FINISHED.
    await driveToFinished(pages);

    for (const page of pages) {
      await expect(page.getByRole("heading", { name: "RESULTS" })).toBeVisible();
    }
    await expect(pages[0].getByTestId("results-winner")).toBeVisible();

    expect(mediaResponses.length, JSON.stringify(mediaResponses)).toBeGreaterThan(0);
    for (const response of mediaResponses) {
      expect(response.status, `${response.url} -> ${response.status}`).toBe(200);
    }
  } finally {
    await Promise.all(contexts.map((context) => context.close()));
  }
});

async function redeem(page: Page, creds: PlayerCredentials): Promise<void> {
  await page.goto("/redeem");
  await page.getByLabel("Invite code").fill(creds.code);
  await page.getByLabel("Username").fill(creds.username);
  await page.getByLabel("Display name").fill(creds.displayName);
  await page.getByLabel("Password").fill(creds.password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/$/);
}

/**
 * Reacts to whatever is actionable on each of the three pages, tick by
 * tick, until every page shows the results screen. Deliberately generic
 * rather than a hand-scripted turn order: expansion picking, battle
 * targeting, and every question kind (`expansion_question`, `battle_duel`,
 * `neutral_challenge`) each present a different subset of players with
 * something to do, and only the server knows which — this drives whatever
 * the UI actually offers instead of predicting it.
 */
async function driveToFinished(pages: readonly Page[]): Promise<void> {
  const deadline = Date.now() + 150_000;
  while (Date.now() < deadline) {
    const finished = await Promise.all(pages.map(isFinished));
    if (finished.every(Boolean)) return;
    for (const page of pages) await act(page);
    await pages[0]?.waitForTimeout(250);
  }
  throw new Error("the match did not reach FINISHED within the scenario's own deadline");
}

async function isFinished(page: Page): Promise<boolean> {
  return (await page.getByRole("heading", { name: "RESULTS" }).count()) > 0;
}

// Every locator state-check and action below carries its own explicit
// timeout. `playwright.config.ts`'s `actionTimeout` already covers this,
// but that config is a global default a later edit could loosen or
// remove — Step 4's own proof run hung for the rest of the test on a bare
// `isEnabled()` racing the page's own transition to FINISHED (the exact
// moment this driver is built to catch), so this loop does not rely on
// the global setting alone.
const STATE_CHECK_TIMEOUT = 2_000;
const ACTION_TIMEOUT = 3_000;

async function act(page: Page): Promise<void> {
  const numericAnswer = page.getByLabel("Your answer");
  if (await isVisible(numericAnswer)) {
    if (await isEditable(numericAnswer)) {
      await tryTo(async () => {
        await numericAnswer.fill("1", { timeout: ACTION_TIMEOUT });
        await page.getByRole("button", { name: "Submit" }).click({ timeout: ACTION_TIMEOUT });
      });
    }
    return;
  }

  // Always the wrong choice: every seeded multiple-choice question marks
  // idx 0 correct and never offers "Choice B" as it (see
  // `WRONG_CHOICE_TEXT`'s own comment in seed/fixture.ts) — so no duel in
  // this match can ever have both sides right, which is the only thing
  // that escalates a duel into a numeric tiebreak question the seed did
  // not budget extra prompts for.
  const wrongChoice = page.getByRole("button", { name: WRONG_CHOICE_TEXT, exact: true });
  if (await isVisible(wrongChoice)) {
    if (await isEnabled(wrongChoice)) {
      await tryTo(() => wrongChoice.click({ timeout: ACTION_TIMEOUT }));
    }
    return;
  }

  // Expansion picking and battle targeting: whichever region(s) the
  // player's own turn actually offers (`aria-disabled="false"`) — nothing
  // is offered to a bystander, so this is a no-op on every other page.
  const offered = page.locator('svg path[aria-disabled="false"]').first();
  if (await isVisible(offered)) {
    await tryTo(() => offered.click({ timeout: ACTION_TIMEOUT }));
  }
}

type StateLocator = { isVisible(o?: { timeout?: number }): Promise<boolean> };
type EditableLocator = { isEditable(o?: { timeout?: number }): Promise<boolean> };
type EnabledLocator = { isEnabled(o?: { timeout?: number }): Promise<boolean> };

async function isVisible(locator: StateLocator): Promise<boolean> {
  return locator.isVisible({ timeout: STATE_CHECK_TIMEOUT }).catch(() => false);
}

async function isEditable(locator: EditableLocator): Promise<boolean> {
  return locator.isEditable({ timeout: STATE_CHECK_TIMEOUT }).catch(() => false);
}

async function isEnabled(locator: EnabledLocator): Promise<boolean> {
  return locator.isEnabled({ timeout: STATE_CHECK_TIMEOUT }).catch(() => false);
}

async function tryTo(action: () => Promise<void>): Promise<void> {
  try {
    await action();
  } catch {
    // Raced the server's own resolution of the same turn, or the window
    // closed between the visibility check above and this action — fine,
    // the next tick re-reads whatever state actually won.
  }
}
