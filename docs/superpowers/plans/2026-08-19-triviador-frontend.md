# Triviador Plan 6 — Frontend Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the backend. Three people in one room open a browser, redeem an invite, sign in, create and join a game, and play it to `FINISHED` on a real map — with the client never folding an event into state, never learning a rule, and never counting a deadline it is allowed to be wrong about. After this plan Spec 1's player-facing product is complete end to end; Plan 7 adds the admin half, Plan 8 deploys it.

**Architecture:** Plan 5 left a machine-checked contract and two transports for the same projection. This plan consumes both and adds exactly three things that are not plumbing. **The dispatcher** is the single writer of the game cache — one function, three cases, living in `app/` because it is the only place allowed to know both the socket and the cache. **The map** is parsed from an SVG at runtime and re-validated in the browser against the same contract the build enforces, because the asset is fetched rather than bundled and a fetched asset is an input. **The clock** is drawn from `deadline_at` plus a ping/pong offset and disables input locally, while the server stays the only thing that decides whether an answer arrived in time. Everything else is a screen over a query cache.

**Tech Stack:** Vite 7 · React 19 · TypeScript 5.7 (`strict`) · Tailwind CSS v4 · TanStack Router (file-based) / Query / Form · Zod 3 (the generated schemas) · Zustand 5 · Biome 2 · `steiger` 0.6 + `@feature-sliced/steiger-plugin` 0.7 · Vitest 3 · Testing Library · MSW 2 · pnpm 9 · Node 22+ · `svgo` 4 (one-shot, for map normalization) · Python 3.13 / `uv` (Tasks 1–2 only)

**Spec:** `docs/superpowers/specs/2026-08-07-triviador-spec1-design.md` §4 (repository layout, the map contract, the type contract), §8.3 (message envelope), §8.5 (reconnect), §8.6 (clock, heartbeat, backpressure), §8.7–§8.8 (projection and affordances), §9 in full (frontend: state ownership, first paint and the write race, FSD layers, game-screen layout, media and timer fairness, screens), §10.1 (admin bootstrap, for the seeded bank's prerequisites), §11.7 (frontend error handling), §12.4–§12.6 (the E2E that is *not* here, and the CI gates), §14.1–§14.3 (the three open items this plan closes) · `docs/superpowers/specs/2026-08-16-triviador-app-architecture-design.md` §7 (contracts and codegen), §8 in full (map rendering, the dispatcher and its gap rule, the timer, routing), §9 (the admin tree this plan deliberately does not build), §10.2 (where `map.svg` is served from), §12 (plan sequence)

**Design canvas:** the approved visual direction and every screen state — https://claude.ai/code/artifact/6d869bdb-10ab-485e-b225-c4e69b6a7447 . The `The system` artboard is the source of the palette, type rules and component states Task 3 turns into tokens; the remaining artboards are the reference for Tasks 9–14. Where this plan and the canvas disagree, this plan wins — it is the one that was checked against the contracts.

---

## Global Constraints

Every task's requirements implicitly include this section.

- **The client never folds an event into state** (§9.1). `msg.state` is the only thing that may reach the query cache; `msg.events` may only reach the ephemeral bus. A reducer, a merge, or a `setQueryData` that reads `events` is a plan violation, not a style preference. Task 7's tests assert it directly.
- **There is exactly one writer of `["game", id]`** (§9.3): `writeGame` in `app/`. No component, hook, feature or entity may call `queryClient.setQueryData(gameKey(...))`. Task 7 adds a lint gate that proves it.
- **The client never learns a rule.** Adjacency, claim counts, eligibility and legality come from `turn.your_options` and nothing else (§8.8). The frontend has no adjacency list — `MapDetail` deliberately does not carry one — and must never derive one from the regions it can see.
- **The pre-resolution question DTO has no answer fields, and the client must not invent them.** No optimistic "you were right", no client-side scoring, no comparing a submitted value to anything (§8.7, §12.3).
- **Identity comes from the socket's principal.** No frame carries an actor; every generated frame schema is `.strict()` and would reject one (§6.5). `state.you.player_id` is who you are *in a game*, and is the only correct way to answer "is this me" — never `["me"].user_id` compared against the player list (§8.7).
- **Every server value crossing into the app is parsed by a generated Zod schema** before anything reads a field. Hand-written types for wire data are forbidden; the generated modules are the single source (§4 "Type contract", §7).
- **Generated files are never hand-edited.** `pnpm codegen:check` regenerates, byte-diffs and evaluates them; it stays green on every commit.
- **The server's clock is authoritative.** The local timer is presentation: it may disable input, it may never claim an answer was late, and it never sends anything at expiry (§8.3 of Spec 1B, §8.6 of Spec 1).
- **A numeric answer is a string on the wire, end to end.** It is typed as a string, validated as a finite decimal, and sent as a string. Parsing it to a JavaScript `number` anywhere — including for display — is forbidden: `Decimal("0.1")` does not survive the round trip (`SubmittedValue`'s own contract note).
- **FSD layer direction is enforced by `steiger`, not by discipline.** `shared` imports nothing above it; `entities` imports only `shared`; `features` only `entities`/`shared`; `widgets` only `features`/`entities`/`shared`; `pages` only below; `app` may import everything. Biome does not check this, which is why `steiger` is a CI gate (§14.2, closed by Task 3).
- **`shared/api/ws` is dumb.** It connects, sends typed frames, and emits typed messages. It must not import `@tanstack/react-query`, must not know a cache key exists, and must not import from any layer above `shared` (§9.4).
- **Components never see the socket.** The only public surface is `useGameSubscription(gameId)` (refcounted) and `useGame(gameId)` (§9.4).
- **Zustand holds only** `selectedRegionId`, `mapZoom`, `mapPan`, `openPanel`, `soundEnabled`. Territory ownership, scores, the round, the current question and the timer are *never* in Zustand (§9.2). Task 6's test enumerates the store's keys and fails on a sixth.
- **Query config for game and lobby data is** `staleTime: Infinity`, `refetchOnWindowFocus: false`, `refetchOnReconnect: false` (§9.3). The socket is the refresh mechanism; a refetch racing an update is the bug this prevents.
- **TypeScript is `strict`, plus `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes` and `verbatimModuleSyntax`.** `any` is banned outside the generated modules (which the codegen emits and Biome ignores). `tsc --noEmit` passes on every commit.
- **No test waits on wall-clock time.** Timer and reconnect tests drive `vi.useFakeTimers()` and an injected clock; a `setTimeout(..., 1000)` in a test is a plan violation.
- **Every test that renders a component renders it through the real providers** (`renderWithApp` from Task 8), so a component that quietly depends on a provider fails in the test rather than in the browser.
- **No Playwright, no compose, no Caddy, no Docker.** Those are Plan 8 (Spec 1B §12). This plan's tests run with `pnpm test` and nothing else running.
- **No `/admin/*` route, component, schema or link.** Plan 7 owns the admin tree, lazily loaded and role-guarded (§9 of Spec 1B). A stub route now is a promise in the router.
- Line length 100 for Python (unchanged). Biome owns formatting for TypeScript; `pnpm check` must pass on every commit.
- **Backend changes (Tasks 1–2) keep every existing gate green:** `ruff check`, `ruff format --check`, `mypy --strict`, the full `pytest` run, and `reducer.py` / `db/codec/`'s 100 % branch coverage.

---

## File Structure

```
data/
├── maps/czechia/
│   ├── map.svg                       CREATE  Task 1 — sourced, normalized, licence recorded
│   ├── map.json                       —      unchanged; its 14 region ids are the contract
│   └── LICENSE                       MODIFY  Task 1 — the SVG's real terms replace the TODO
└── seeds/
    └── questions.csv                 CREATE  Task 2 — 17 numeric + 12 MC, closes §14.3

backend/
├── pyproject.toml                    MODIFY  Task 1 (defusedxml), Task 2 (CLI command)
└── src/triviador/
    ├── maps/
    │   ├── validator.py              CREATE  Task 1 — the §8.1 SVG contract, build-time half
    │   └── registry.py               MODIFY  Task 1 — load_with_digest validates the SVG too
    ├── db/
    │   └── repositories/questions.py MODIFY  Task 2 — QuestionWriter, for the seed only
    └── cli.py                        MODIFY  Task 2 — `seed-questions`

frontend/
├── package.json                      MODIFY  Task 3 — the app's dependencies and scripts
├── vite.config.ts                    CREATE  Task 3 — plugins, aliases, /api + /ws proxy, /maps
├── tsconfig.json                     CREATE  Task 3 — strict, aliases
├── biome.json                        CREATE  Task 3
├── steiger.config.ts                 CREATE  Task 3 — closes §14.2
├── vitest.config.ts                  CREATE  Task 3
├── index.html                        CREATE  Task 3
├── scripts/codegen.mjs               MODIFY  Task 3 — output moves under src/
├── scripts/verify-generated.mjs      MODIFY  Task 3 — same
└── src/
    ├── main.tsx                      CREATE  Task 8   the entry point
    ├── styles.css                    CREATE  Task 3   Tailwind v4 + the design tokens
    ├── app/
    │   ├── providers.tsx             CREATE  Task 8   QueryClient, router, socket
    │   ├── query-client.ts           CREATE  Task 8   the §9.3 defaults, in one place
    │   ├── error-boundary.tsx        CREATE  Task 8   §11.7, per route
    │   ├── socket-status.tsx         CREATE  Task 8   §11.7's banner
    │   ├── dispatcher.ts             CREATE  Task 7   writeGame + the A-3 gap rule
    │   ├── event-bus.ts              CREATE  Task 7   ephemeral narration, never the cache
    │   ├── socket-provider.tsx       CREATE  Task 7   one socket per tab, status, offset
    │   ├── use-game-subscription.ts  CREATE  Task 7   refcounted subscribe/unsubscribe
    │   ├── use-media-prefetch.ts     CREATE  Task 12  §9.6
    │   └── routes/                   CREATE  Task 8   file-based route tree
    │       ├── __root.tsx                     Task 8
    │       ├── login.tsx                      Task 9
    │       ├── redeem.tsx                     Task 9
    │       ├── index.tsx                      Task 10  the lobby
    │       └── games.$gameId.tsx              Task 12
    ├── pages/
    │   ├── login/                    CREATE  Task 9
    │   ├── redeem/                   CREATE  Task 9
    │   ├── lobby/                    CREATE  Task 10
    │   └── game/                     CREATE  Task 12  room + board, one page
    ├── widgets/
    │   ├── player-strip/             CREATE  Task 12
    │   ├── game-stage/               CREATE  Task 12  the fixed-height stage
    │   ├── question-dock/            CREATE  Task 13  question, answers, timer
    │   ├── turn-dock/                CREATE  Task 13  the non-question docks
    │   └── results/                  CREATE  Task 14  full time
    ├── features/
    │   ├── create-game/              CREATE  Task 10
    │   ├── join-game/                CREATE  Task 10
    │   ├── sign-in/                  CREATE  Task 9
    │   ├── redeem-invite/            CREATE  Task 9
    │   ├── submit-answer/            CREATE  Task 13
    │   ├── pick-region/              CREATE  Task 13
    │   ├── select-target/            CREATE  Task 13
    │   └── surrender/                CREATE  Task 14
    ├── entities/
    │   ├── game/                     CREATE  Task 6   keys, selectors, seat colours
    │   ├── player/                   CREATE  Task 6
    │   ├── question/                 CREATE  Task 6
    │   ├── territory/                CREATE  Task 6
    │   └── map/                      CREATE  Task 11  the parsed map and its validator
    └── shared/
        ├── api/
        │   ├── generated/            MOVED   Task 3   from frontend/shared/api/generated
        │   ├── rest.ts               CREATE  Task 4   apiFetch and the envelope
        │   ├── errors.ts             CREATE  Task 4   ApiFetchError
        │   ├── ws.ts                 CREATE  Task 5   the dumb socket client
        │   ├── clock.ts              CREATE  Task 5   ping/pong offset
        │   └── index.ts              CREATE  Task 4   the segment's public API
        ├── ui/                       CREATE  Task 3   button, field, banner — ours, not shadcn
        ├── lib/                      CREATE  Task 3   cn(), invariant(), test render
        └── config/                   CREATE  Task 3   seat colours, timings
```

---

## Design decisions this plan makes that the spec does not state

1. **The generated Zod is used outbound as well as inbound.** `contracts/ws.schema.json` exports `SubscribeFrame`, `SubmitAnswerFrame`, `PickRegionFrame`, `SelectTargetFrame`, `SurrenderFrame`, `ResyncFrame`, `UnsubscribeFrame` and `PingFrame` — the client frames, not just the server messages. Every outbound frame is therefore built and `.parse()`d through its generated schema before `send`. The alternative is hand-writing the frame shape on the client, which is the exact failure §4's type contract exists to prevent, and `.strict()` means a stray key is caught here rather than as a silent `validation_failed` with no `command_id` to correlate it to.

2. **A transport failure never wears a server error code.** `errorCodeSchema` is a closed union of two disjoint server enums. A proxy 502, an HTML error page, a truncated body or a dead network is none of them, and inventing a `"network_error"` member would corrupt a union the backend asserts the shape of. So `ApiFetchError` carries a discriminant: `kind: "envelope"` (the server answered in its envelope; `code` is a real `ErrorCode`) or `kind: "transport"` (nobody answered, or what answered was not this API). Spec 1B §6.3's last paragraph names this as the frontend's half of the guarantee; this is that half.

3. **`/api`, `/ws` and `/maps` are proxied by the Vite dev server**, so the browser sees one origin in development and the session cookie — `triviador_session`, `SameSite=Lax`, `path=/` — behaves exactly as it will behind Caddy in Plan 8. `/maps` is not a proxy but a static mount: nothing serves `data/maps` in development, and `MapDetail.svg_url` is `/maps/<id>/map.svg`. A twelve-line Vite plugin serves that directory; Plan 8 replaces it with Caddy and no client code changes.

4. **shadcn/ui is deferred to Plan 7, and `shared/ui` is four small components of our own.** Spec 1 §4 names shadcn/ui in the stack. Plan 6's screens need a button, a text field, a banner and a chip — the approved visual direction is a dark broadcast look that shares no vocabulary with shadcn's defaults, so installing it here means importing Radix, `class-variance-authority` and a component library in order to restyle all of it away. Plan 7's admin surface is where shadcn earns its keep: tables with server-side pagination, dialogs, selects, comboboxes, toasts. `cn()` (`clsx` + `tailwind-merge`) is added now so those components drop in unchanged later. **This is a deliberate deviation from the spec's stack list and should be rejected here if you disagree — it is cheap to reverse now and expensive after fourteen tasks of styling.**

5. **`steiger`'s rule set (§14.2, the open item this closes).** `fsd.configs.recommended`, with four deviations, each of which is a rule fighting something the spec mandates rather than a rule we find inconvenient:
   - `fsd/insignificant-slice` **off under `entities/**`**. Spec 1 §9.4 names four entity slices; `question` and `territory` are each referenced from exactly one widget, which is what this rule flags. The spec's decomposition wins.
   - `fsd/import-locality` **on** (it ships disabled). Same-slice imports relative, cross-slice absolute. It costs nothing and makes every cross-slice edge visible in a diff.
   - `fsd/no-cross-imports` and `fsd/no-higher-level-imports` stay folded inside `fsd/forbidden-imports` (their default) — enabling them separately double-reports.
   - Everything else, including `fsd/public-api` on `shared`, is left at `recommended`. `shared/api` gets a real `index.ts` rather than an exemption.

6. **`app/` has no `ui` segment.** `fsd/no-ui-in-app` forbids one, and the error boundary and socket banner are genuinely app-level. They live as `app/error-boundary.tsx` and `app/socket-status.tsx` — files at the layer root, not a segment.

7. **The generated modules move to `frontend/src/shared/api/generated`.** Plan 5 put them at `frontend/shared/...` because there was no app and therefore no `src/`. `steiger` reads the FSD root (`./src`), and a `shared` layer outside it is invisible to the gate. Task 3 moves the directory, updates both scripts, and proves the move byte-neutral by regenerating.

8. **One socket per tab, owned by a provider, not by a page.** §8.1 says one multiplexed socket per browser tab. It is opened when a signed-in session exists and closed on sign-out — not on navigation, so moving lobby → game → lobby does not reconnect. `useGameSubscription` refcounts topic subscriptions on top of it.

9. **Reconnect resyncs; it does not resubscribe.** §8.5's recovery is a fresh snapshot. On reopen the client sends `resync` for every game topic it held (the server answers with a snapshot without re-adding the subscription, then re-subscribes it) — precisely, it sends `subscribe` first for topics it is no longer subscribed to server-side (the server dropped the connection, so all of them), which already answers with a snapshot. `resync` is reserved for the case where the socket is still open but the client believes it has desynced (§11.7's "one resolution: take a fresh snapshot"). Both paths are exercised in Task 7.

10. **A pending command is tracked by `command_id`, and nothing else is optimistic.** Submitting an answer, a pick or a target sets a local "sending" flag keyed by the `command_id` we generated. It clears when a `game.update` arrives whose state reflects it (`turn.your_answer` non-null, or the turn moved on) or when an `error` arrives carrying that same `command_id` (§8.3: `command_id` is transport correlation, never a retry key). No answer is ever drawn as accepted before the server says so, and nothing is retried automatically.

11. **The clock offset is the median of the last five ping/pong round trips**, not the latest. §8.6 makes the client refine its offset from ping/pong rather than from `hello.server_time`; a single sample carries whatever queueing delay that one packet met. Median of five is one line and is not fooled by an outlier.

12. **The map fails closed and says so.** If `map.svg` fails the browser-side contract — a `<script>`, a `transform`, an id that is not in `map.json`, a missing id — the game screen renders an error state naming the map, and no partial board. §8.1 calls the client-side check defence in depth precisely because the asset is fetched; defence that degrades quietly is not defence.

13. **Seat colour is a CSS custom property indexed by `ClientPlayer.seat`**, set once on the game screen root: `--seat-0` … `--seat-3`. Region fill reads `var(--seat-N)`; nothing stores a colour. This is what makes §8.1's "region appearance is derived, never stored" true in the DOM and not just in the store.

14. **The seed bank is written by a CLI command, not by a migration.** Questions are content, and content in a migration is content that cannot be corrected without another migration (`db/seed.py`'s own rule). `triviador seed-questions` is idempotent on `prompt_hash`, so running it twice is a no-op and re-running it after editing the CSV updates nothing already drawn into a live game's pool.

---

## Task 1: A real map, and the SVG contract that keeps it renderable

Spec 1 §14.1 has been open since Plan 1: `data/maps/czechia/` has 14 regions, a symmetric adjacency graph and a `LICENSE` that says the SVG is missing. Nothing can render a board without it, and Spec 1B §8.1 says the contract on that file is enforced in two places. This task closes the open item and builds the first half.

**Files:**
- Create: `data/maps/czechia/map.svg`
- Modify: `data/maps/czechia/LICENSE`
- Create: `backend/src/triviador/maps/validator.py`
- Modify: `backend/pyproject.toml` (add `defusedxml>=0.7`)
- Create: `backend/tests/maps/test_svg_validator.py`
- Create: `frontend/svgo.config.mjs` (one-shot normalization, kept so the next map is reproducible)

**Interfaces:**
- Consumes: `triviador.domain.ids.RegionId`; `data/maps/czechia/map.json`'s 14 region ids.
- Produces: `validate_svg(source: str, region_ids: Collection[RegionId]) -> tuple[str, ...]` and `validate_map_directory(root: Path, map_id: str, region_ids: Collection[RegionId]) -> tuple[str, ...]` — both return an empty tuple for a valid map and one string per problem otherwise, matching `domain.maps.validation.validate_map`'s existing shape. Task 11 reimplements the same contract in TypeScript and its test asserts the two agree on the shipped file.

- [ ] **Step 1: Source an SVG whose licence permits redistribution — and stop if you cannot**

Find an SVG of the 14 Czech regions (NUTS-3 / kraje). Known sources, in the order worth trying:

| Source | Typical licence | Note |
|---|---|---|
| Wikimedia Commons — "Czech Republic regions" maps | often CC0, CC BY-SA, or PD | check the file page's licence box, not the thumbnail |
| `simplemaps.com` free SVG maps | MIT for the free tier | attribution required in the file or docs |
| Natural Earth (via a GeoJSON→SVG step) | public domain | no attribution required; needs a conversion step |
| amCharts `svg-maps` | CC BY 4.0 (per repo LICENSE) | attribution required |

Requirements the file must meet, in order of how expensive they are to fix:
1. **The licence must permit redistribution**, and you must be able to point at the exact statement.
2. It must have **one path per region** — a single merged outline is unusable.
3. Its ids must be **recoverable** — either the region name, an ISO 3166-2:CZ code, or a documented order. Meaningless ids (`path4382`) are fixable by hand against a rendered picture, but that is manual work, so prefer a file that has them.

**STOP RULE.** If you cannot find a file whose licence you can quote, do not commit one, do not hand-draw a substitute, and do not proceed to Step 2. Report back with the candidates you found and their licence text, and let the human decide. A map asset with an unverifiable licence is the one thing in this plan that cannot be fixed later by editing code.

Record the outcome by rewriting `data/maps/czechia/LICENSE` in full — the current text is a TODO addressed to Plan 4 and must not survive:

```
map.json
--------
Topology (region ids, display names, adjacency) is hand-authored from public
administrative boundaries and is released under CC0 1.0.

map.svg
-------
Source:    <exact URL the file was downloaded from>
Author:    <as stated by the source>
Licence:   <SPDX identifier or the licence's own name>
Statement: <the sentence on the source page that grants redistribution>
Retrieved: 2026-08-19
Changes:   normalized with svgo (see frontend/svgo.config.mjs) — group
           transforms flattened into path data, metadata and styling
           stripped; path ids renamed to this directory's map.json region ids.
```

If the licence requires attribution in the rendered product rather than in a file, say so on an `Attribution:` line here and add it to Task 12's game-screen footer — do not silently satisfy it with this file alone.

- [ ] **Step 2: Normalize it**

`frontend/svgo.config.mjs` — the normalization is one-shot but the config is committed, because the next map has to be normalized the same way:

```javascript
// One-shot map normalization (Spec 1B §8.1). Not part of the build: run it
// by hand when a new map.svg is sourced.
//
//   pnpm dlx svgo@4 --config svgo.config.mjs -i raw.svg -o ../data/maps/<id>/map.svg
//
// The point is `convertPathData.applyTransforms` plus `collapseGroups`:
// §8.1 accepts exactly one transform contract — flattened, top-level paths
// — because supporting "top-level paths *or* composed ancestors" would mean
// two transform engines, the validator's and the browser's, with room to
// disagree.
export default {
  multipass: true,
  plugins: [
    {
      name: "preset-default",
      params: {
        overrides: {
          // Ids are the contract: they must survive to match map.json.
          cleanupIds: false,
          // A viewBox is required by §8.1 and by every consumer of this file.
          removeViewBox: false,
        },
      },
    },
    "convertStyleToAttrs",
    "removeStyleElement",
    "removeScripts",
    "removeDimensions",
    { name: "removeAttrs", params: { attrs: "(style|class|transform|fill|stroke|stroke-width|opacity)" } },
  ],
};
```

Run it against the sourced file. `removeDimensions` drops `width`/`height` in favour of the `viewBox`, which is what lets the React component size the map; `removeAttrs` strips the presentational attributes that would otherwise fight the seat-colour custom properties in Task 11.

- [ ] **Step 3: Rename the path ids to `map.json`'s region ids**

Sourced files identify regions by ISO 3166-2:CZ code or by Czech name. Both forms map to this directory's ids as follows — the alpha codes appear in amCharts and simplemaps output, the numeric ones in Wikimedia and Eurostat NUTS files:

| Region | `map.json` id | ISO alpha | ISO numeric | NUTS-3 |
|---|---|---|---|---|
| Praha | `praha` | CZ-PR | CZ-10 | CZ010 |
| Středočeský | `stredocesky` | CZ-ST | CZ-20 | CZ020 |
| Jihočeský | `jihocesky` | CZ-JC | CZ-31 | CZ031 |
| Plzeňský | `plzensky` | CZ-PL | CZ-32 | CZ032 |
| Karlovarský | `karlovarsky` | CZ-KA | CZ-41 | CZ041 |
| Ústecký | `ustecky` | CZ-US | CZ-42 | CZ042 |
| Liberecký | `liberecky` | CZ-LI | CZ-51 | CZ051 |
| Královéhradecký | `kralovehradecky` | CZ-KR | CZ-52 | CZ052 |
| Pardubický | `pardubicky` | CZ-PA | CZ-53 | CZ053 |
| Vysočina | `vysocina` | CZ-VY | CZ-63 | CZ063 |
| Jihomoravský | `jihomoravsky` | CZ-JM | CZ-64 | CZ064 |
| Olomoucký | `olomoucky` | CZ-OL | CZ-71 | CZ071 |
| Zlínský | `zlinsky` | CZ-ZL | CZ-72 | CZ072 |
| Moravskoslezský | `moravskoslezsky` | CZ-MO | CZ-80 | CZ080 |

Rename them with a throwaway script — do not commit it, and do not hand-edit a 14-path file in an editor where a typo is invisible:

```bash
cd /home/alexey/projects/sandbox/triviador
python3 - <<'PY'
import re
from pathlib import Path

# Fill this in from the file you actually sourced: whatever it calls each
# region on the left, this directory's id on the right.
RENAME = {
    "CZ-PR": "praha", "CZ-ST": "stredocesky", "CZ-JC": "jihocesky",
    "CZ-PL": "plzensky", "CZ-KA": "karlovarsky", "CZ-US": "ustecky",
    "CZ-LI": "liberecky", "CZ-KR": "kralovehradecky", "CZ-PA": "pardubicky",
    "CZ-VY": "vysocina", "CZ-JM": "jihomoravsky", "CZ-OL": "olomoucky",
    "CZ-ZL": "zlinsky", "CZ-MO": "moravskoslezsky",
}
path = Path("data/maps/czechia/map.svg")
svg = path.read_text(encoding="utf-8")
for old, new in RENAME.items():
    before = svg
    svg = re.sub(rf'id="{re.escape(old)}"', f'id="{new}"', svg)
    assert svg != before, f"no path had id={old!r}"
path.write_text(svg, encoding="utf-8")
print("renamed", len(RENAME))
PY
```

The assert is the point: a source file that silently lacks one of the fourteen would otherwise produce a map with a hole in it, and Step 8's test would tell you *that* a region is missing but not which rename failed.

- [ ] **Step 4: Add the parser dependency**

```bash
cd backend && uv add defusedxml
```

`defusedxml` rather than the standard library: §8.1 rejects `DOCTYPE` and entities, and the honest way to reject an entity expansion is a parser that refuses to perform one, not a regex over the source looking for `<!ENTITY`.

- [ ] **Step 5: Write the failing test**

`backend/tests/maps/test_svg_validator.py`:

```python
"""Spec 1B §8.1's contract, one test per line of it.

`_svg` builds a document that passes, so each test can break exactly one
rule and assert on that rule alone. A test that constructs its own whole
document per case drifts: the "rejects transform" test ends up also
missing a viewBox, passes for the wrong reason, and keeps passing after
the transform check is deleted.
"""

import json
from pathlib import Path

import pytest

from triviador.domain.ids import RegionId
from triviador.maps.validator import validate_map_directory, validate_svg

REPO_MAPS = Path(__file__).resolve().parents[3] / "data" / "maps"
REGIONS = (RegionId("a"), RegionId("b"))


def _svg(paths: str = "", root_attrs: str = 'viewBox="0 0 100 100"') -> str:
    body = paths or '<path id="a" d="M0 0h1v1z"/><path id="b" d="M2 2h1v1z"/>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" {root_attrs}>{body}</svg>'


def test_a_flat_two_path_document_is_valid() -> None:
    assert validate_svg(_svg(), REGIONS) == ()


def test_missing_viewbox_is_a_problem() -> None:
    problems = validate_svg(_svg(root_attrs=""), REGIONS)
    assert any("viewBox" in p for p in problems)


@pytest.mark.parametrize(
    "element",
    [
        "<script>alert(1)</script>",
        "<foreignObject><div/></foreignObject>",
        '<use href="#a"/>',
        '<image href="x.png"/>',
        "<style>path{fill:red}</style>",
    ],
)
def test_forbidden_elements_are_rejected(element: str) -> None:
    doc = _svg(paths=f'<path id="a" d="M0 0h1v1z"/><path id="b" d="M2 2h1v1z"/>{element}')
    assert validate_svg(doc, REGIONS) != ()


def test_a_group_wrapper_is_rejected_even_without_a_transform() -> None:
    """§8.1 accepts one transform contract. A group is rejected structurally
    rather than only when it carries a transform, because the property that
    has to hold is "the browser and the validator cannot disagree", and a
    group is where they could."""
    doc = _svg(paths='<g><path id="a" d="M0 0h1v1z"/><path id="b" d="M2 2h1v1z"/></g>')
    assert validate_svg(doc, REGIONS) != ()


@pytest.mark.parametrize(
    "attr",
    ['transform="translate(5,5)"', 'href="#x"', 'style="fill:red"', 'onclick="x()"'],
)
def test_disallowed_path_attributes_are_rejected(attr: str) -> None:
    doc = _svg(paths=f'<path id="a" d="M0 0h1v1z" {attr}/><path id="b" d="M2 2h1v1z"/>')
    problems = validate_svg(doc, REGIONS)
    assert any("disallowed attribute" in p for p in problems)


def test_fill_rule_and_clip_rule_are_allowed() -> None:
    doc = _svg(
        paths=(
            '<path id="a" d="M0 0h1v1z" fill-rule="evenodd" clip-rule="evenodd"/>'
            '<path id="b" d="M2 2h1v1z"/>'
        )
    )
    assert validate_svg(doc, REGIONS) == ()


def test_a_path_missing_from_map_json_is_reported() -> None:
    doc = _svg(
        paths=(
            '<path id="a" d="M0 0h1v1z"/><path id="b" d="M2 2h1v1z"/><path id="c" d="M4 4h1v1z"/>'
        )
    )
    problems = validate_svg(doc, REGIONS)
    assert any("no region in map.json" in p for p in problems)


def test_a_region_with_no_path_is_reported() -> None:
    problems = validate_svg(_svg(paths='<path id="a" d="M0 0h1v1z"/>'), REGIONS)
    assert any("no path" in p for p in problems)


def test_duplicate_ids_are_reported() -> None:
    doc = _svg(
        paths=(
            '<path id="a" d="M0 0h1v1z"/><path id="a" d="M2 2h1v1z"/><path id="b" d="M4 4h1v1z"/>'
        )
    )
    problems = validate_svg(doc, REGIONS)
    assert any("duplicate" in p for p in problems)


def test_a_doctype_is_refused_by_the_parser() -> None:
    doc = '<!DOCTYPE svg><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>'
    assert validate_svg(doc, REGIONS) != ()


def test_an_entity_declaration_is_refused_by_the_parser() -> None:
    doc = (
        '<!DOCTYPE svg [<!ENTITY x "boom">]>'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1">'
        '<path id="a" d="&x;"/></svg>'
    )
    assert validate_svg(doc, REGIONS) != ()


def test_unparseable_input_is_a_problem_not_an_exception() -> None:
    assert validate_svg("not xml at all", REGIONS) != ()


def test_the_shipped_czechia_map_satisfies_the_contract() -> None:
    """The build-time half of §8.1. This is the gate that makes "a map is a
    two-file drop" safe: the drop is checked here, in the repository, rather
    than discovered by a player looking at a blank board."""
    source = json.loads((REPO_MAPS / "czechia" / "map.json").read_text(encoding="utf-8"))
    regions = [RegionId(r["id"]) for r in source["regions"]]
    assert validate_map_directory(REPO_MAPS, "czechia", regions) == ()


def test_a_map_directory_without_an_svg_is_reported() -> None:
    assert validate_map_directory(REPO_MAPS, "no-such-map", REGIONS) != ()
```

- [ ] **Step 6: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/maps/test_svg_validator.py -v --no-cov`
Expected: a collection error — `ModuleNotFoundError: No module named 'triviador.maps.validator'`.

- [ ] **Step 7: Write the validator**

`backend/src/triviador/maps/validator.py`:

```python
"""Spec 1B §8.1's SVG contract, enforced against the file.

The frontend enforces the same contract on the bytes it fetches
(`entities/map`, Task 11), because `map.svg` is dropped into a directory
rather than passed through a build. This module is the half that runs
against the repository, so a map that could never render is a red test
instead of a blank board.

**Why the whitelist is a whitelist.** `href`, `xlink:href`, `transform`,
`style` and `onclick` are all rejected by *not appearing* in `PATH_ATTRS`
rather than by being enumerated as forbidden. That is the only form of this
rule that stays correct as SVG grows a new attribute.

**Why a group is rejected structurally.** §8.1 accepts exactly one
transform contract — flattened, top-level paths — because supporting
"top-level paths *or* composed ancestors" would mean two transform engines,
this one's and the browser's, with room to disagree. So nesting is rejected
whether or not the wrapper carries a transform: the property that has to
hold is that there is nothing to disagree about.
"""

from collections.abc import Collection
from pathlib import Path
from xml.etree.ElementTree import Element

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from triviador.domain.ids import RegionId

SVG_NS = "http://www.w3.org/2000/svg"

PATH_ATTRS = frozenset({"id", "d", "fill-rule", "clip-rule"})
# `xmlns` never appears here: ElementTree folds namespace declarations into
# the tag name rather than reporting them as attributes.
ROOT_ATTRS = frozenset({"viewBox", "width", "height"})


def _split(tag: object) -> tuple[str, str]:
    """`{ns}local` → `(ns, local)`. A comment or PI has a callable tag."""
    if not isinstance(tag, str):
        return ("", "<non-element>")
    if tag.startswith("{"):
        namespace, _, local = tag[1:].partition("}")
        return (namespace, local)
    return ("", tag)


def validate_svg(source: str, region_ids: Collection[RegionId]) -> tuple[str, ...]:
    """Every problem, not the first one. An operator fixing a map wants the
    whole list, the same way `startup_problems` hands a deployment all of
    its misconfigurations at once."""
    try:
        root = fromstring(source, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        return (f"refused by the hardened parser: {exc}",)
    except ParseError as exc:
        return (f"not parseable as XML: {exc}",)

    problems: list[str] = []
    namespace, local = _split(root.tag)
    if local != "svg" or namespace not in (SVG_NS, ""):
        problems.append(f"root element is <{local}> in namespace {namespace!r}, not an SVG <svg>")
    if "viewBox" not in root.attrib:
        problems.append("root <svg> has no viewBox")
    for name in sorted(set(root.attrib) - ROOT_ATTRS):
        problems.append(f"root <svg> carries a disallowed attribute: {name}")

    seen: list[str] = []
    for child in root:
        child_ns, child_tag = _split(child.tag)
        if child_tag != "path" or child_ns not in (SVG_NS, ""):
            problems.append(f"<{child_tag}> is not allowed: every region is a top-level <path>")
            continue
        problems.extend(_path_problems(child, seen))
        for descendant in child:
            deep = _split(descendant.tag)[1]
            problems.append(f"<path> has a child <{deep}>; the file must be flat")

    duplicates = sorted({i for i in seen if seen.count(i) > 1})
    if duplicates:
        problems.append(f"duplicate path ids: {duplicates}")

    wanted = {str(r) for r in region_ids}
    got = set(seen)
    if missing := sorted(wanted - got):
        problems.append(f"regions in map.json with no path: {missing}")
    if extra := sorted(got - wanted):
        problems.append(f"paths with no region in map.json: {extra}")

    return tuple(problems)


def _path_problems(element: Element, seen: list[str]) -> list[str]:
    problems: list[str] = []
    for name in sorted(set(element.attrib) - PATH_ATTRS):
        problems.append(f"<path> carries a disallowed attribute: {name}")
    identifier = element.attrib.get("id")
    if identifier is None:
        problems.append("a <path> has no id")
        return problems
    if not element.attrib.get("d"):
        problems.append(f"path {identifier!r} has no d")
    seen.append(identifier)
    return problems


def validate_map_directory(
    root: Path, map_id: str, region_ids: Collection[RegionId]
) -> tuple[str, ...]:
    path = root / map_id / "map.svg"
    if not path.is_file():
        return (f"no map.svg at {path}",)
    return validate_svg(path.read_text(encoding="utf-8"), region_ids)
```

- [ ] **Step 8: Run the tests until the shipped map passes**

Run: `cd backend && uv run pytest tests/maps/ -v --no-cov`
Expected: every test PASSES, including `test_the_shipped_czechia_map_satisfies_the_contract`.

If the shipped-map test fails, the failure names the exact problem — a leftover `transform`, a `<g>` that `collapseGroups` could not remove, an id that did not get renamed. Fix the *asset* (re-run Step 2 with the offending plugin added, or fix the rename table in Step 3). Do not loosen the validator to accept the file you happen to have: this contract is what the browser enforces in Task 11, and a validator that agrees with a bad file has only moved the failure into someone's browser.

- [ ] **Step 9: Confirm nothing else moved**

Run: `cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q`
Expected: all green. `MapRegistry` is deliberately untouched — `load_with_digest` runs on every game creation and every recovery replay, and parsing a 100 KB SVG there to re-prove a property the repository already gates would be waste. The residual risk is an operator dropping a map into `/data/maps` in production with no `map.svg`; that surfaces as Task 11's named map-error state rather than a blank board, which is decision 12.

- [ ] **Step 10: Commit**

```bash
git add data/maps/czechia/map.svg data/maps/czechia/LICENSE \
        backend/src/triviador/maps/validator.py backend/tests/maps/test_svg_validator.py \
        backend/pyproject.toml backend/uv.lock frontend/svgo.config.mjs
git commit -m "feat(maps): a real Czechia map.svg, and the §8.1 contract that gates it"
```

---

## Task 2: A question bank, so a game can actually start

`required_question_budget(DEFAULT_RULES)` is `numeric=17, multiple_choice=12` — four expansion rounds plus twelve duels plus the final tiebreak, and twelve duel questions. The bank is empty, so `StartGame` raises `InsufficientQuestions` and nothing in Plan 6 can be exercised against a real backend. Spec 1 §14.3 is the open item; this closes it, and it does so with a command rather than a migration because content in a migration is content that cannot be corrected without another migration (`db/seed.py`'s own rule).

**Files:**
- Create: `data/seeds/questions.csv`
- Modify: `backend/src/triviador/db/repositories/questions.py` (add `SeedQuestion`, `QuestionSeeder`, `prompt_digest`)
- Modify: `backend/src/triviador/cli.py` (add `parse_seed_csv` and the `seed-questions` command)
- Create: `backend/tests/test_seed_csv.py` (pure — no database)
- Create: `backend/tests/db/test_seed_questions.py` (integration — real PostgreSQL)

**Interfaces:**
- Consumes: `db.models.content.{Category,Question,QuestionChoice,QuestionNumeric}`, `domain.questions.types.{QuestionKind,Difficulty}`, `domain.game.rules.required_question_budget`, `db.repositories.presets.PresetRepository.get_default`, `db.engine.{engine_for,sessionmaker_for}`.
- Produces: `prompt_digest(prompt: str) -> str`; `SeedQuestion` (frozen dataclass: `kind`, `category_slug`, `category_name`, `difficulty`, `prompt`, `unit: str | None`, `correct_value: Decimal | None`, `choices: tuple[str, ...]`, `correct_index: int | None`); `QuestionSeeder(session).ensure(q) -> bool` and `.active_counts() -> dict[QuestionKind, int]`; `parse_seed_csv(text: str) -> tuple[SeedQuestion, ...]`; the shell command `uv run triviador seed-questions --csv data/seeds/questions.csv`.

- [ ] **Step 1: Write the seed CSV**

`data/seeds/questions.csv` — 18 numeric and 14 multiple-choice, one above each floor so that deactivating a single question does not make the default preset unstartable:

```csv
kind,category_slug,category_name,difficulty,prompt,unit,answer,choice_1,choice_2,choice_3,choice_4,correct_index
numeric,history,History,easy,"In which year did the Velvet Revolution begin?",,1989,,,,,
numeric,history,History,medium,"In which year was Charles University in Prague founded?",,1348,,,,,
numeric,history,History,easy,"In which year did the Second World War end in Europe?",,1945,,,,,
numeric,history,History,medium,"In which year did Czechoslovakia split into two countries?",,1993,,,,,
numeric,history,History,medium,"In which year was the Eiffel Tower completed?",,1889,,,,,
numeric,history,History,easy,"In which year did a human first walk on the Moon?",,1969,,,,,
numeric,geography,Geography,easy,"How many regions does the Czech Republic have, counting Prague?",,14,,,,,
numeric,geography,Geography,medium,"How many countries share a land border with the Czech Republic?",,4,,,,,
numeric,geography,Geography,hard,"How high is Sněžka, the highest peak in the Czech Republic?",m,1603,,,,,
numeric,geography,Geography,hard,"How long is the river Vltava?",km,430,,,,,
numeric,geography,Geography,hard,"What is the area of the Czech Republic?",km²,78866,,,,,
numeric,science,Science,easy,"At what temperature does water boil at sea level?",°C,100,,,,,
numeric,science,Science,easy,"At what temperature does water freeze, in degrees Fahrenheit?",°F,32,,,,,
numeric,science,Science,medium,"How many bones are there in an adult human body?",,206,,,,,
numeric,science,Science,medium,"How many elements does the periodic table contain?",,118,,,,,
numeric,culture,Culture,medium,"How many keys does a standard full-size piano have?",,88,,,,,
numeric,culture,Culture,easy,"How many strings does a violin have?",,4,,,,,
numeric,sport,Sport,easy,"How many players from one team are on the pitch in a football match?",,11,,,,,
multiple_choice,geography,Geography,easy,"Which river flows through Prague?",,,Vltava,Labe,Morava,Odra,0
multiple_choice,geography,Geography,easy,"What is the capital of Slovakia?",,,Bratislava,Brno,Vienna,Kraków,0
multiple_choice,geography,Geography,medium,"Which Czech region has Brno as its capital?",,,Jihomoravský,Zlínský,Olomoucký,Vysočina,0
multiple_choice,geography,Geography,easy,"Which is the largest ocean?",,,Pacific,Atlantic,Indian,Arctic,0
multiple_choice,geography,Geography,medium,"Which mountain range is the traditional boundary between Europe and Asia?",,,Ural,Caucasus,Alps,Carpathians,0
multiple_choice,geography,Geography,easy,"Which Czech city is home to the Pilsner Urquell brewery?",,,Plzeň,České Budějovice,Brno,Olomouc,0
multiple_choice,science,Science,medium,"Which of these is a noble gas?",,,Argon,Nitrogen,Oxygen,Chlorine,0
multiple_choice,science,Science,easy,"Which planet orbits closest to the Sun?",,,Mercury,Venus,Mars,Earth,0
multiple_choice,science,Science,medium,"Which metal is liquid at room temperature?",,,Mercury,Lead,Tin,Zinc,0
multiple_choice,science,Science,easy,"What is the chemical symbol for gold?",,,Au,Ag,Go,Gd,0
multiple_choice,culture,Culture,medium,"Who composed the opera Rusalka?",,,Antonín Dvořák,Bedřich Smetana,Leoš Janáček,Bohuslav Martinů,0
multiple_choice,culture,Culture,hard,"Who painted the Slav Epic?",,,Alfons Mucha,Josef Lada,Max Švabinský,František Kupka,0
multiple_choice,sport,Sport,easy,"In which sport is the Stanley Cup awarded?",,,Ice hockey,Football,Basketball,Tennis,0
multiple_choice,sport,Sport,medium,"Which country hosted the 2016 Summer Olympics?",,,Brazil,China,United Kingdom,Japan,0
```

**The correct answer is always `choice_1` in this file, and that is a bug waiting to happen** — a player who notices wins every multiple-choice question. `parse_seed_csv` therefore does *not* preserve CSV order: it shuffles each question's choices with a seed derived from the prompt digest, so the position is stable across re-runs (the same question always lands the same way, so re-seeding is still a no-op) but unrelated to the file. Authoring is easy, playing is not rigged. Step 4's parser does this; Step 3's test asserts it.

- [ ] **Step 2: Write the failing pure test**

`backend/tests/test_seed_csv.py`:

```python
"""The seed file's format, without a database in sight."""

from decimal import Decimal

import pytest

from triviador.cli import parse_seed_csv
from triviador.db.repositories.questions import prompt_digest
from triviador.domain.questions.types import Difficulty, QuestionKind

HEADER = (
    "kind,category_slug,category_name,difficulty,prompt,unit,answer,"
    "choice_1,choice_2,choice_3,choice_4,correct_index\n"
)
NUMERIC = 'numeric,science,Science,easy,"How hot is it?",°C,100,,,,,\n'
CHOICE = 'multiple_choice,science,Science,easy,"Which one?",,,Right,Wrong,Also wrong,Still wrong,0\n'


def test_a_numeric_row_parses() -> None:
    (question,) = parse_seed_csv(HEADER + NUMERIC)
    assert question.kind is QuestionKind.NUMERIC
    assert question.difficulty is Difficulty.EASY
    assert question.correct_value == Decimal("100")
    assert question.unit == "°C"
    assert question.choices == ()
    assert question.correct_index is None


def test_a_choice_row_parses_and_keeps_exactly_one_correct_answer() -> None:
    (question,) = parse_seed_csv(HEADER + CHOICE)
    assert question.kind is QuestionKind.MULTIPLE_CHOICE
    assert sorted(question.choices) == sorted(["Right", "Wrong", "Also wrong", "Still wrong"])
    assert question.correct_index is not None
    assert question.choices[question.correct_index] == "Right"
    assert question.correct_value is None
    assert question.unit is None


def test_choices_are_shuffled_away_from_the_authored_order() -> None:
    """Every row in the shipped file authors the correct answer first. If
    the parser preserved that, the game would be trivially winnable."""
    prompts = [f'multiple_choice,c,C,easy,"Question {i}?",,,Right,B,C,D,0' for i in range(30)]
    questions = parse_seed_csv(HEADER + "\n".join(prompts) + "\n")
    first_is_correct = [q.correct_index == 0 for q in questions]
    assert sum(first_is_correct) < len(questions)


def test_the_shuffle_is_stable_for_the_same_prompt() -> None:
    """Re-running the seed must be a no-op, which it cannot be if the same
    question comes out with its answers in a different order each time."""
    once = parse_seed_csv(HEADER + CHOICE)[0]
    twice = parse_seed_csv(HEADER + CHOICE)[0]
    assert once.choices == twice.choices
    assert once.correct_index == twice.correct_index


@pytest.mark.parametrize(
    "row",
    [
        'numeric,s,S,easy,"No answer",,,,,,,',
        'numeric,s,S,easy,"Not a number",,abc,,,,,',
        'multiple_choice,s,S,easy,"Only two",,,A,B,,,0',
        'multiple_choice,s,S,easy,"Index out of range",,,A,B,C,D,9',
        'multiple_choice,s,S,easy,"Numeric fields set",u,5,A,B,C,D,0',
        'sideways,s,S,easy,"Unknown kind",,1,,,,,',
        'numeric,s,S,tepid,"Unknown difficulty",,1,,,,,',
    ],
)
def test_a_malformed_row_names_its_line(row: str) -> None:
    with pytest.raises(ValueError, match="line 2"):
        parse_seed_csv(HEADER + row + "\n")


def test_a_duplicate_prompt_in_one_file_is_refused() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        parse_seed_csv(HEADER + NUMERIC + NUMERIC)


def test_prompt_digest_ignores_whitespace_and_case() -> None:
    assert prompt_digest("How  hot\nis it?") == prompt_digest("how hot is it?")


def test_the_shipped_seed_file_meets_the_default_preset_budget() -> None:
    """Spec 1 §14.3, as a test rather than a promise. `required_question_budget`
    of the default rules is numeric=17, multiple_choice=12."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "data" / "seeds" / "questions.csv"
    questions = parse_seed_csv(path.read_text(encoding="utf-8"))
    numeric = [q for q in questions if q.kind is QuestionKind.NUMERIC]
    choice = [q for q in questions if q.kind is QuestionKind.MULTIPLE_CHOICE]
    assert len(numeric) >= 17
    assert len(choice) >= 12
```

- [ ] **Step 3: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/test_seed_csv.py -v --no-cov`
Expected: `ImportError: cannot import name 'parse_seed_csv' from 'triviador.cli'`.

- [ ] **Step 4: Write the seeder and the parser**

Append to `backend/src/triviador/db/repositories/questions.py`:

```python
# --- seeding -----------------------------------------------------------------
# Deliberately *not* the admin write path. Plan 7 owns question editing, and
# with it the rule that every semantic edit bumps `questions.version` — the
# invariant this module's `FOR SHARE` lock depends on. `QuestionSeeder` only
# ever inserts whole new questions, so there is no edit for that rule to
# govern, and it must stay that way: an `UPDATE` added here is Plan 7's admin
# path written in the wrong module and outside the rule that keeps the lock
# meaningful.


def prompt_digest(prompt: str) -> str:
    """Whitespace- and case-insensitive.

    Re-running the seed after reflowing a line in the CSV must not insert a
    second copy of a question the bank already has, and `questions.prompt_hash`
    is the only column that could tell the two apart.
    """
    return hashlib.sha256(" ".join(prompt.split()).casefold().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SeedQuestion:
    kind: QuestionKind
    category_slug: str
    category_name: str
    difficulty: Difficulty
    prompt: str
    unit: str | None
    correct_value: Decimal | None
    choices: tuple[str, ...]
    correct_index: int | None


class QuestionSeeder:
    """Wraps one `AsyncSession` belonging to the caller's open transaction,
    exactly as `QuestionBank` does. Never opens or commits one itself."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_category(self, slug: str, name: str) -> str:
        existing = await self._session.scalar(select(Category).where(Category.slug == slug))
        if existing is not None:
            return existing.id
        category = Category(id=str(uuid4()), slug=slug, name=name)
        self._session.add(category)
        await self._session.flush()
        return category.id

    async def ensure(self, question: SeedQuestion) -> bool:
        """True if it was inserted, False if the bank already had it."""
        digest = prompt_digest(question.prompt)
        if await self._session.scalar(select(Question.id).where(Question.prompt_hash == digest)):
            return False

        row = Question(
            id=str(uuid4()),
            version=1,
            kind=question.kind.value,
            prompt=question.prompt,
            category_id=await self.ensure_category(question.category_slug, question.category_name),
            difficulty=question.difficulty.value,
            media_asset_id=None,
            is_active=True,
            prompt_hash=digest,
        )
        self._session.add(row)
        await self._session.flush()

        if question.kind is QuestionKind.NUMERIC:
            self._session.add(
                QuestionNumeric(
                    question_id=row.id, correct_value=question.correct_value, unit=question.unit
                )
            )
        else:
            for idx, text in enumerate(question.choices):
                self._session.add(
                    QuestionChoice(
                        question_id=row.id,
                        idx=idx,
                        text=text,
                        is_correct=idx == question.correct_index,
                        media_asset_id=None,
                    )
                )
        return True

    async def active_counts(self) -> dict[QuestionKind, int]:
        rows = await self._session.execute(
            select(Question.kind, func.count())
            .where(Question.is_active.is_(True))
            .group_by(Question.kind)
        )
        counts = dict.fromkeys(QuestionKind, 0)
        for kind, count in rows.all():
            counts[QuestionKind(kind)] = count
        return counts
```

Add to that module's imports: `hashlib`, `from dataclasses import dataclass`, `from decimal import Decimal`, `from uuid import uuid4`, and `Difficulty` (already imported) — `func` and `select` are already there.

Append to `backend/src/triviador/cli.py`:

```python
SEED_COLUMNS = (
    "kind",
    "category_slug",
    "category_name",
    "difficulty",
    "prompt",
    "unit",
    "answer",
    "choice_1",
    "choice_2",
    "choice_3",
    "choice_4",
    "correct_index",
)


def parse_seed_csv(text: str) -> tuple[SeedQuestion, ...]:
    """Every problem names its line, because a 32-row file with one bad cell
    is otherwise a `ValueError` pointing at nothing.

    Choices are shuffled by a seed derived from the prompt digest rather
    than kept in file order: authoring is easier when the correct answer is
    always written first, and a game in which it is always first is not a
    game. Deriving the seed from the prompt keeps the shuffle stable, which
    is what lets `seed-questions` stay idempotent.
    """
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != SEED_COLUMNS:
        raise ValueError(f"header must be exactly {','.join(SEED_COLUMNS)}")

    questions: list[SeedQuestion] = []
    seen: set[str] = set()
    for line, row in enumerate(reader, start=2):
        try:
            question = _parse_seed_row(row)
        except ValueError as exc:
            raise ValueError(f"line {line}: {exc}") from exc
        digest = prompt_digest(question.prompt)
        if digest in seen:
            raise ValueError(f"line {line}: duplicate prompt")
        seen.add(digest)
        questions.append(question)
    return tuple(questions)


def _parse_seed_row(row: dict[str, str]) -> SeedQuestion:
    kind_raw = (row["kind"] or "").strip()
    if kind_raw not in {k.value for k in QuestionKind}:
        raise ValueError(f"unknown kind {kind_raw!r}")
    difficulty_raw = (row["difficulty"] or "").strip()
    if difficulty_raw not in {d.value for d in Difficulty}:
        raise ValueError(f"unknown difficulty {difficulty_raw!r}")
    prompt = (row["prompt"] or "").strip()
    if not prompt:
        raise ValueError("empty prompt")

    kind = QuestionKind(kind_raw)
    choices = tuple(c.strip() for c in (row[f"choice_{i}"] or "" for i in (1, 2, 3, 4)) if c.strip())
    answer = (row["answer"] or "").strip()
    unit = (row["unit"] or "").strip() or None
    index_raw = (row["correct_index"] or "").strip()

    if kind is QuestionKind.NUMERIC:
        if choices or index_raw:
            raise ValueError("a numeric question must not carry choices")
        if not answer:
            raise ValueError("a numeric question needs an answer")
        try:
            value = Decimal(answer)
        except InvalidOperation as exc:
            raise ValueError(f"answer {answer!r} is not a decimal number") from exc
        if not value.is_finite():
            raise ValueError("answer must be finite")
        return SeedQuestion(kind, row["category_slug"].strip(), row["category_name"].strip(),
                            Difficulty(difficulty_raw), prompt, unit, value, (), None)

    if answer or unit:
        raise ValueError("a multiple-choice question must not carry answer or unit")
    if len(choices) < 3:
        raise ValueError(f"a multiple-choice question needs at least 3 choices, got {len(choices)}")
    if not index_raw.isdigit() or int(index_raw) >= len(choices):
        raise ValueError(f"correct_index {index_raw!r} is not one of {len(choices)} choices")

    ordered, correct = _shuffle_choices(choices, int(index_raw), prompt)
    return SeedQuestion(kind, row["category_slug"].strip(), row["category_name"].strip(),
                        Difficulty(difficulty_raw), prompt, None, None, ordered, correct)


def _shuffle_choices(choices: tuple[str, ...], correct: int, prompt: str) -> tuple[tuple[str, ...], int]:
    rng = random.Random(prompt_digest(prompt))
    order = list(range(len(choices)))
    rng.shuffle(order)
    return tuple(choices[i] for i in order), order.index(correct)


async def _seed_questions_command(args: argparse.Namespace) -> int:
    questions = parse_seed_csv(args.csv.read_text(encoding="utf-8"))
    settings = get_settings()
    async with engine_for(settings.database_url) as engine:
        sessionmaker = sessionmaker_for(engine)
        async with sessionmaker() as session, session.begin():
            seeder = QuestionSeeder(session)
            inserted = sum([await seeder.ensure(q) for q in questions])
            counts = await seeder.active_counts()
        preset = await PresetRepository(sessionmaker).get_default()

    print(f"inserted {inserted}, unchanged {len(questions) - inserted}")
    for kind, count in sorted(counts.items()):
        print(f"active {kind.value}: {count}")
    if preset is None:
        print("no default preset: cannot check the question budget")
        return 0

    budget = required_question_budget(preset.rules)
    short = [
        f"{kind.value} needs {need}, bank has {counts[kind]}"
        for kind, need in (
            (QuestionKind.NUMERIC, budget.numeric),
            (QuestionKind.MULTIPLE_CHOICE, budget.multiple_choice),
        )
        if counts[kind] < need
    ]
    for line in short:
        print(f"SHORT: {line}")
    # Non-zero rather than a warning: a deployment script that seeds a bank
    # too small for its own default preset has produced a server on which
    # `StartGame` fails, and finding that out from a player is worse than
    # finding it out from the exit code.
    return 1 if short else 0
```

Wire the subcommand into `main`, alongside the existing two:

```python
    seed = commands.add_parser("seed-questions")
    seed.add_argument("--csv", type=Path, required=True)
```

and extend the dispatch at the end of `main`:

```python
    args = parser.parse_args(argv)
    if args.command == "export-contracts":
        export_contracts(args.out)
        return 0
    if args.command == "seed-questions":
        return asyncio.run(_seed_questions_command(args))
    return asyncio.run(_admin_create_command(args))
```

New imports for `cli.py`: `csv`, `io`, `random`, `from decimal import Decimal, InvalidOperation`, `from triviador.db.repositories.presets import PresetRepository`, `from triviador.db.repositories.questions import QuestionSeeder, SeedQuestion, prompt_digest`, `from triviador.domain.game.rules import required_question_budget`, `from triviador.domain.questions.types import Difficulty, QuestionKind`.

- [ ] **Step 5: Run the pure tests until they pass**

Run: `cd backend && uv run pytest tests/test_seed_csv.py -v --no-cov`
Expected: all PASS.

- [ ] **Step 6: Write the integration test**

`backend/tests/db/test_seed_questions.py`:

```python
"""The seeder, against real PostgreSQL — the only place the idempotency
claim can actually be checked, since it is a uniqueness property of rows."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.content import Question, QuestionChoice, QuestionNumeric
from triviador.db.repositories.questions import QuestionBank, QuestionSeeder, SeedQuestion
from triviador.domain.questions.types import Difficulty, QuestionBudget, QuestionKind

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _numeric(prompt: str) -> SeedQuestion:
    return SeedQuestion(
        kind=QuestionKind.NUMERIC,
        category_slug="science",
        category_name="Science",
        difficulty=Difficulty.EASY,
        prompt=prompt,
        unit="°C",
        correct_value=Decimal("100"),
        choices=(),
        correct_index=None,
    )


def _choice(prompt: str) -> SeedQuestion:
    return SeedQuestion(
        kind=QuestionKind.MULTIPLE_CHOICE,
        category_slug="science",
        category_name="Science",
        difficulty=Difficulty.EASY,
        prompt=prompt,
        unit=None,
        correct_value=None,
        choices=("A", "B", "C", "D"),
        correct_index=2,
    )


async def test_seeding_twice_inserts_once(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session, session.begin():
        assert await QuestionSeeder(session).ensure(_numeric("How hot?")) is True
    async with sessions() as session, session.begin():
        assert await QuestionSeeder(session).ensure(_numeric("how   hot?")) is False
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Question)) == 1


async def test_a_numeric_question_gets_its_child_row(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session, session.begin():
        await QuestionSeeder(session).ensure(_numeric("How hot?"))
    async with sessions() as session:
        row = await session.scalar(select(QuestionNumeric))
        assert row is not None
        assert row.correct_value == Decimal("100")
        assert row.unit == "°C"


async def test_a_choice_question_gets_four_choices_and_exactly_one_correct(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session, session.begin():
        await QuestionSeeder(session).ensure(_choice("Which one?"))
    async with sessions() as session:
        rows = (await session.scalars(select(QuestionChoice))).all()
        assert len(rows) == 4
        assert [r.idx for r in sorted(rows, key=lambda r: r.idx)] == [0, 1, 2, 3]
        correct = [r for r in rows if r.is_correct]
        assert len(correct) == 1
        assert correct[0].idx == 2


async def test_one_category_is_shared_by_every_question_that_names_it(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session, session.begin():
        seeder = QuestionSeeder(session)
        await seeder.ensure(_numeric("First?"))
        await seeder.ensure(_numeric("Second?"))
    async with sessions() as session:
        ids = set((await session.scalars(select(Question.category_id))).all())
        assert len(ids) == 1


async def test_active_counts_reports_per_kind(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as session, session.begin():
        seeder = QuestionSeeder(session)
        await seeder.ensure(_numeric("First?"))
        await seeder.ensure(_choice("Second?"))
        counts = await seeder.active_counts()
    assert counts[QuestionKind.NUMERIC] == 1
    assert counts[QuestionKind.MULTIPLE_CHOICE] == 1


async def test_a_seeded_bank_can_actually_be_drawn_from(
    clean_db: None, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """The claim this whole task exists to make true: after seeding, a pool
    can be drawn. `QuestionBank._materialize` is strict about shape, so this
    fails loudly if the seeder writes a question with no child rows."""
    async with sessions() as session, session.begin():
        seeder = QuestionSeeder(session)
        for i in range(3):
            await seeder.ensure(_numeric(f"Numeric {i}?"))
            await seeder.ensure(_choice(f"Choice {i}?"))
    async with sessions() as session, session.begin():
        pool = await QuestionBank(session).select_pool(QuestionBudget(numeric=3, multiple_choice=3))
    assert len(pool.numeric) == 3
    assert len(pool.multiple_choice) == 3
```

- [ ] **Step 7: Run the integration test**

```bash
cd backend
docker compose -f docker-compose.test.yml up -d
TRIVIADOR_DATABASE_URL=postgresql+asyncpg://triviador:triviador@127.0.0.1:5433/triviador_test \
  uv run pytest tests/db/test_seed_questions.py -v --no-cov
```
Expected: all PASS.

- [ ] **Step 8: Seed a real database and confirm a game can start**

```bash
cd backend
export TRIVIADOR_DATABASE_URL=postgresql+asyncpg://triviador:triviador@127.0.0.1:5433/triviador_test
uv run alembic upgrade head
uv run triviador seed-questions --csv ../data/seeds/questions.csv
```
Expected output, and an exit code of 0:
```
inserted 32, unchanged 0
active multiple_choice: 14
active numeric: 18
```
Run it a second time: `inserted 0, unchanged 32`, same counts, still 0. That second run is the whole idempotency claim, and it costs five seconds to check.

- [ ] **Step 9: Full backend gate**

Run: `cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest -q`
Expected: green, with `reducer.py` and `db/codec/` still at 100 % branch coverage (this task touches neither).

- [ ] **Step 10: Commit**

```bash
git add data/seeds/questions.csv backend/src/triviador/cli.py \
        backend/src/triviador/db/repositories/questions.py \
        backend/tests/test_seed_csv.py backend/tests/db/test_seed_questions.py
git commit -m "feat(seed): a startable question bank, and the command that installs it"
```

---

## Task 3: The toolchain, the tokens, and the layer gate

`frontend/` today is a contracts consumer: a `package.json` with two dev dependencies and two scripts. This task turns it into an application that builds, lints, type-checks, tests and enforces its own architecture — and closes Spec 1 §14.2, the `steiger` rule set, which has been open since Spec 1 was written.

**Files:**
- Modify: `frontend/package.json`
- Move: `frontend/shared/` → `frontend/src/shared/` (git mv; the generated modules move with it)
- Modify: `frontend/scripts/codegen.mjs`, `frontend/scripts/verify-generated.mjs`
- Create: `frontend/vite.config.ts`, `frontend/vitest.config.ts`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/biome.json`, `frontend/steiger.config.ts`, `frontend/index.html`
- Create: `frontend/testing/setup.ts`
- Create: `frontend/src/styles.css`
- Create: `frontend/src/shared/lib/{cn.ts,invariant.ts,index.ts}`
- Create: `frontend/src/shared/config/{seats.ts,timing.ts,index.ts}`
- Create: `frontend/src/shared/ui/{button.tsx,field.tsx,banner.tsx,chip.tsx,index.ts}`
- Create: `frontend/src/shared/api/index.ts`
- Create: `frontend/src/shared/ui/button.test.tsx`

**Interfaces:**
- Consumes: the three generated modules Plan 5 committed.
- Produces: `cn(...classes)`; `invariant(condition, message): asserts condition`; `SEAT_COUNT: 4` and `seatVar(seat: number): string`; `TIMING` (`{ PING_INTERVAL_MS: 15_000, RECONNECT_BASE_MS: 500, RECONNECT_MAX_MS: 10_000, TIMER_URGENT_MS: 8_000 }`); `<Button>`, `<Field>`, `<Banner>`, `<Chip>`; the `@/` path alias; the scripts `pnpm dev | build | test | check | codegen:check`.

**A correction to the File Structure block above:** test helpers live in `frontend/testing/`, *outside* `src/`, not under `shared/lib`. `steiger` scans the FSD root (`./src`); a `render.tsx` that imports `app/providers` is not an FSD violation, but it is a slice that exists only for tests and would have to be argued about on every `fsd/insignificant-slice` run. Keeping it outside the tree costs nothing and removes the argument. `shared/lib` holds `cn()` and `invariant()` only.

- [ ] **Step 1: Move the generated modules under `src/`**

```bash
cd /home/alexey/projects/sandbox/triviador/frontend
mkdir -p src
git mv shared src/shared
```

Then update both scripts — one line each:

- `scripts/codegen.mjs`: `const out = resolve(here, "../shared/api/generated");` becomes `resolve(here, "../src/shared/api/generated")`, and the `HEADER` comment's relative path `../../../../contracts` becomes `../../../../../contracts` (it is now one directory deeper).
- `scripts/verify-generated.mjs`: `const dir = resolve(import.meta.dirname, "../shared/api/generated");` becomes `resolve(import.meta.dirname, "../src/shared/api/generated")`.

- [ ] **Step 2: Prove the move was byte-neutral apart from the header**

```bash
cd frontend && pnpm codegen && git diff --stat -- src/shared/api/generated
```
Expected: exactly three files changed, one line each — the `HEADER` comment's path. If any schema line changed, the move broke something and must be fixed before anything is built on top of it.

- [ ] **Step 3: Install the application's dependencies**

```bash
cd frontend
pnpm add react react-dom @tanstack/react-router @tanstack/react-query @tanstack/react-form zustand clsx tailwind-merge @fontsource/bebas-neue @fontsource/barlow
pnpm add -D vite @vitejs/plugin-react typescript @types/react @types/react-dom \
            tailwindcss @tailwindcss/vite @tanstack/router-plugin @tanstack/react-router-devtools \
            @biomejs/biome steiger @feature-sliced/steiger-plugin \
            vitest @vitest/coverage-v8 jsdom @testing-library/react @testing-library/user-event @testing-library/jest-dom msw
```

**Fonts are self-hosted** (`@fontsource/*`), not linked from Google Fonts. Spec 1 §1.1 is a LAN deployment: a display face fetched from `fonts.googleapis.com` renders as Impact on the evening the internet is down, which is exactly the evening people are in one room playing a board game. The design canvas uses the Google-hosted versions because it runs in a browser tab with a network; the app does not.

**shadcn/ui is deliberately not installed here** — see decision 4. If that decision was rejected, this is the step to change, and Task 9 onward should use its `Button`/`Input`/`Label` in place of `shared/ui`.

- [ ] **Step 4: Write the configuration**

`frontend/package.json` — replace the `scripts` block and keep `packageManager`:

```json
{
  "name": "triviador-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest",
    "check": "biome check . && tsc --noEmit && steiger ./src",
    "fix": "biome check --write .",
    "codegen": "node scripts/codegen.mjs",
    "codegen:check": "node scripts/codegen.mjs && git diff --exit-code -- src/shared/api/generated && node --experimental-strip-types scripts/verify-generated.mjs"
  },
  "packageManager": "pnpm@9.15.0"
}
```

`frontend/vite.config.ts`:

```typescript
import { readFile } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { type Plugin, defineConfig } from "vite";

const here = fileURLToPath(new URL(".", import.meta.url));
const MAPS_ROOT = resolve(here, "../data/maps");

/**
 * Serves `data/maps` at `/maps` in development.
 *
 * `MapDetail.svg_url` is `${maps_public_base}/<id>/map.svg` — Caddy's job in
 * Plan 8 (Spec 1B §10.2), and nobody's job in development. This is twelve
 * lines rather than a `publicDir` copy so that dropping a new map in
 * `data/maps` is picked up without restarting anything, which is the whole
 * promise of "a map is a two-file drop".
 */
function serveMaps(): Plugin {
  const types: Record<string, string> = { ".svg": "image/svg+xml", ".json": "application/json" };
  return {
    name: "triviador-serve-maps",
    configureServer(server) {
      server.middlewares.use("/maps", (req, res, next) => {
        const path = (req.url ?? "/").split("?")[0] ?? "/";
        const file = join(MAPS_ROOT, normalize(decodeURIComponent(path)));
        // `normalize` alone does not stop `/maps/../../etc/passwd`: the
        // guard is that the resolved path is still inside MAPS_ROOT.
        if (!file.startsWith(MAPS_ROOT)) {
          res.statusCode = 403;
          res.end();
          return;
        }
        const type = types[extname(file)];
        if (type === undefined) {
          next();
          return;
        }
        readFile(file).then(
          (body) => {
            res.setHeader("content-type", type);
            res.end(body);
          },
          () => next(),
        );
      });
    },
  };
}

export default defineConfig({
  plugins: [
    tanstackRouter({ target: "react", autoCodeSplitting: true, routesDirectory: "./src/app/routes" }),
    react(),
    tailwindcss(),
    serveMaps(),
  ],
  resolve: { alias: { "@": resolve(here, "src") } },
  server: {
    port: 5173,
    proxy: {
      // `changeOrigin` stays false on purpose: the browser's Origin header
      // must arrive at the backend as `http://localhost:5173`, which is what
      // `TRIVIADOR_ALLOWED_ORIGINS` has to contain and what the socket
      // handshake checks (§6.4). Rewriting it would make development pass a
      // check that production performs differently.
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/media": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true, changeOrigin: false },
    },
  },
});
```

`frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "types": ["vite/client", "vitest/globals", "@testing-library/jest-dom"],
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "exactOptionalPropertyTypes": true,
    "verbatimModuleSyntax": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noEmit": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "resolveJsonModule": true,
    "baseUrl": ".",
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["src", "testing", "vite.config.ts", "vitest.config.ts", "steiger.config.ts"]
}
```

`frontend/biome.json`:

```json
{
  "$schema": "https://biomejs.dev/schemas/2.2.0/schema.json",
  "vcs": { "enabled": true, "clientKind": "git", "useIgnoreFile": true },
  "files": { "ignoreUnknown": true, "includes": ["**", "!src/shared/api/generated/**", "!src/app/routes/routeTree.gen.ts"] },
  "formatter": { "enabled": true, "indentStyle": "space", "indentWidth": 2, "lineWidth": 100 },
  "linter": { "enabled": true, "rules": { "recommended": true, "suspicious": { "noExplicitAny": "error" } } },
  "javascript": { "formatter": { "quoteStyle": "double", "semicolons": "always" } }
}
```

The two exclusions are both generated: `codegen.mjs` writes one and the router plugin writes the other, and formatting a file a generator owns is a diff that comes back on the next run.

`frontend/steiger.config.ts` — **this is Spec 1 §14.2, closed.** Every deviation from `recommended` is a rule fighting something the spec mandates, and says so:

```typescript
import fsd from "@feature-sliced/steiger-plugin";
import { defineConfig } from "steiger";

export default defineConfig([
  ...fsd.configs.recommended,
  {
    // Generated. `fsd/public-api` and the naming rules have opinions about
    // files nobody writes by hand.
    ignores: ["**/api/generated/**", "**/routeTree.gen.ts"],
  },
  {
    rules: {
      // Ships disabled. Same-slice imports relative, cross-slice absolute:
      // it costs nothing and makes every cross-slice edge visible in a diff
      // rather than hidden behind a `./`.
      "fsd/import-locality": "error",
    },
  },
  {
    files: ["./src/entities/**"],
    rules: {
      // Spec 1 §9.4 names four entity slices. `question` and `territory` are
      // each referenced from exactly one widget, which is precisely what
      // this rule flags. The spec's decomposition wins: these slices exist
      // so that the *types and selectors* for a concept have one home, not
      // because two consumers demanded them.
      "fsd/insignificant-slice": "off",
    },
  },
]);
```

Left at `recommended` on purpose, including the ones it would be tempting to switch off: `fsd/forbidden-imports` (the layer-direction rule Biome cannot express — the reason `steiger` is here at all), `fsd/public-api` including on `shared` (so `shared/api` gets a real `index.ts` rather than an exemption), `fsd/no-ui-in-app` (which is why the error boundary and socket banner are files at the `app/` root rather than an `app/ui/` segment), and `fsd/no-public-api-sidestep`.

`frontend/vitest.config.ts` — separate from `vite.config.ts` so the router plugin's code generation does not run on every test:

```typescript
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const here = fileURLToPath(new URL(".", import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": resolve(here, "src") } },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./testing/setup.ts"],
    // A test that leaks a timer, a socket or an MSW handler into the next
    // test is the failure mode this whole suite is most exposed to, because
    // almost everything here is stateful. Isolate rather than debug it later.
    restoreMocks: true,
    clearMocks: true,
  },
});
```

`frontend/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Triviador</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/testing/setup.ts`:

```typescript
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});
```

MSW's server is added to this file in Task 4, once there is something to mock.

- [ ] **Step 5: Write the tokens**

`frontend/src/styles.css` — the design canvas's system artboard, as Tailwind v4 theme variables. Everything a component reaches for is here; a hex literal in a `.tsx` file is a review comment.

```css
@import "tailwindcss";
@import "@fontsource/bebas-neue/400.css";
@import "@fontsource/barlow/400.css";
@import "@fontsource/barlow/500.css";
@import "@fontsource/barlow/600.css";

@theme {
  /* Bebas names and counts — players, scores, the clock, buttons.
     Barlow says anything a player has to read — prompts, answers, hints.
     All-caps condensed at 24px over a Czech question sentence is a
     readability problem, and the question is the one thing on screen that
     must never be hard to read. */
  --font-display: "Bebas Neue", Impact, sans-serif;
  --font-sans: "Barlow", "Helvetica Neue", sans-serif;

  --color-base: #0b0b1a;
  --color-stage: #0e0e20;
  --color-panel: #15152c;
  --color-panel-you: #1b1b36;
  --color-raised: #1c1c38;
  --color-line: #2b2b52;
  --color-line-strong: #3a3a63;
  --color-track: #24243f;
  --color-ink: #f7f5ee;
  --color-ink-dim: #7a7a99;
  --color-ink-faint: #6f6f92;
  --color-gold: #ffd24a;
  --color-gold-bright: #ffe68a;
  --color-good: #7de08a;
  --color-bad: #ff5c7a;
  --color-region-free: #252544;
  --color-region-out: #1a1a30;
}

:root {
  /* §8.1: region appearance is derived, never stored. A territory's fill is
     `var(--seat-N)` where N is `ClientPlayer.seat` — nothing anywhere holds
     a colour, so a re-seat or a re-render cannot disagree with the strip.
     Four hues far enough apart in both hue and lightness to survive
     deuteranopia; colour is never the only signal, because the player strip
     always names every player. */
  --seat-0: #ff4757;
  --seat-1: #22d3ee;
  --seat-2: #a78bfa;
  --seat-3: #a3e635;
}

body {
  margin: 0;
  background: var(--color-base);
  color: var(--color-ink);
  font-family: var(--font-sans);
}
```

- [ ] **Step 6: Write `shared/lib`, `shared/config` and `shared/ui`**

`frontend/src/shared/lib/cn.ts`:

```typescript
import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...classes: ClassValue[]): string {
  return twMerge(clsx(classes));
}
```

`frontend/src/shared/lib/invariant.ts`:

```typescript
/**
 * Narrowing that throws rather than a comment that hopes.
 *
 * Used for facts the projection guarantees but the generated types cannot
 * express — e.g. a `questionTurn` always carries a `question`, but a viewer
 * holding `ClientGameState["turn"]` has a seven-member union until something
 * narrows it.
 */
export function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(`invariant: ${message}`);
  }
}
```

`frontend/src/shared/lib/index.ts`:

```typescript
export { cn } from "./cn";
export { invariant } from "./invariant";
```

`frontend/src/shared/config/seats.ts`:

```typescript
/** §8.1: fill comes from `territories[id].owner_id` mapped to a per-seat CSS
 *  custom property. This is the only place that mapping is written down. */
export const SEAT_COUNT = 4;

export function seatVar(seat: number): string {
  return `var(--seat-${seat % SEAT_COUNT})`;
}
```

`frontend/src/shared/config/timing.ts`:

```typescript
export const TIMING = {
  /** §8.6: "ping every 15 s, socket considered dead after 30 s of silence." */
  PING_INTERVAL_MS: 15_000,
  /** How many round trips the clock offset is a median of (decision 11). */
  CLOCK_SAMPLES: 5,
  RECONNECT_BASE_MS: 500,
  RECONNECT_MAX_MS: 10_000,
  /** Below this the clock turns red. Presentation only. */
  TIMER_URGENT_MS: 8_000,
} as const;
```

`frontend/src/shared/config/index.ts`:

```typescript
export { SEAT_COUNT, seatVar } from "./seats";
export { TIMING } from "./timing";
```

`frontend/src/shared/ui/button.tsx`:

```typescript
import type { ButtonHTMLAttributes } from "react";
import { cn } from "@/shared/lib";

type Variant = "primary" | "ghost";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

const VARIANTS: Record<Variant, string> = {
  primary: "bg-gold text-base hover:bg-gold-bright disabled:bg-track disabled:text-ink-faint",
  ghost: "border-2 border-line text-ink hover:border-line-strong disabled:text-ink-faint",
};

export function Button({ variant = "primary", className, ...props }: ButtonProps) {
  return (
    <button
      type="button"
      className={cn(
        "font-display text-xl tracking-wider px-6 h-12 inline-flex items-center justify-center",
        "transition-colors disabled:cursor-not-allowed",
        VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
}
```

`frontend/src/shared/ui/field.tsx`:

```typescript
import type { InputHTMLAttributes, ReactNode } from "react";
import { cn } from "@/shared/lib";

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: ReactNode;
  error?: string | undefined;
}

export function Field({ label, hint, error, className, id, ...props }: FieldProps) {
  const inputId = id ?? `field-${label.toLowerCase().replace(/\s+/g, "-")}`;
  return (
    <div className="flex flex-col gap-2">
      <label htmlFor={inputId} className="text-[10px] font-semibold tracking-[0.14em] text-ink-dim">
        {label.toUpperCase()}
      </label>
      <input
        id={inputId}
        aria-invalid={error !== undefined}
        aria-errormessage={error !== undefined ? `${inputId}-error` : undefined}
        className={cn(
          "bg-raised border-2 px-4 py-3 text-[15px] font-medium text-ink outline-none",
          error === undefined ? "border-line focus:border-gold" : "border-bad",
          className,
        )}
        {...props}
      />
      {error !== undefined ? (
        <p id={`${inputId}-error`} className="text-[11px] font-medium text-bad">
          {error}
        </p>
      ) : (
        hint !== undefined && <p className="text-[11px] text-ink-faint">{hint}</p>
      )}
    </div>
  );
}
```

`frontend/src/shared/ui/banner.tsx`:

```typescript
import type { ReactNode } from "react";
import { cn } from "@/shared/lib";

type Tone = "bad" | "warn" | "quiet";

const TONES: Record<Tone, string> = {
  bad: "bg-[#2a1220] border-bad",
  warn: "bg-[#2a2412] border-gold",
  quiet: "bg-[#221c2e] border-ink-dim",
};

/** §11.7's one shape for anything that went wrong: a code, a sentence, and
 *  nothing a player has to interpret. `code` is the server's — never one we
 *  invented (decision 2). */
export function Banner({ tone, code, children }: { tone: Tone; code?: string; children: ReactNode }) {
  return (
    <div role="status" className={cn("flex items-center gap-3 border-l-4 px-4 py-3", TONES[tone])}>
      {code !== undefined && (
        <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-dim">
          {code}
        </span>
      )}
      <span className="text-[13px] text-ink">{children}</span>
    </div>
  );
}
```

`frontend/src/shared/ui/chip.tsx`:

```typescript
import type { ReactNode } from "react";
import { cn } from "@/shared/lib";

export function Chip({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "px-2 py-[2px] text-[9px] font-semibold uppercase tracking-[0.14em]",
        "bg-gold text-base",
        className,
      )}
    >
      {children}
    </span>
  );
}
```

`frontend/src/shared/ui/index.ts`:

```typescript
export { Banner } from "./banner";
export { Button } from "./button";
export { Chip } from "./chip";
export { Field } from "./field";
```

`frontend/src/shared/api/index.ts` — the segment's public API, required by `fsd/public-api`. Tasks 4 and 5 extend it; for now it re-exports the generated modules, which is what makes "no layer above `shared` imports `generated/` directly" checkable:

```typescript
export * from "./generated/errors";
export * from "./generated/public";
export * from "./generated/ws";
```

- [ ] **Step 7: Write the smoke test**

`frontend/src/shared/ui/button.test.tsx`:

```typescript
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Button } from "./button";

describe("Button", () => {
  it("renders its label and calls onClick", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Start game</Button>);
    await userEvent.click(screen.getByRole("button", { name: "Start game" }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("does not call onClick when disabled", async () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        Start game
      </Button>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Start game" }));
    expect(onClick).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 8: Run the whole gate**

```bash
cd frontend
pnpm test
pnpm check
pnpm codegen:check
```

Expected: two tests pass; `biome check` clean; `tsc --noEmit` clean; `steiger ./src` reports no violations; `codegen:check` prints the three module summaries and exits 0.

`steiger` will complain about anything under `src/shared` that lacks a public API — that is the rule doing its job, and the fix is an `index.ts`, never an entry in `ignores`.

- [ ] **Step 9: Confirm the dev server actually serves a map**

```bash
cd frontend && pnpm dev
# in another shell:
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' http://localhost:5173/maps/czechia/map.svg
```
Expected: `200 image/svg+xml`. This is the one thing in the toolchain that has no test and would otherwise be discovered in Task 11 as "the map does not load".

- [ ] **Step 10: Commit**

```bash
git add frontend
git commit -m "feat(frontend): the toolchain, the design tokens, and the FSD layer gate"
```

---

## Task 4: `apiFetch`, and the half of the envelope guarantee that lives here

Spec 1B §6.3 makes the server's half absolute: every failure leaves through `{code, message, details?}`, including a 404 Starlette raised itself and an unhandled exception. Its last paragraph names the other half as the frontend's: a proxy 502, an HTML error page or a truncated body is *not* the server speaking, and a client that cannot tell those apart has lost the only fact that distinguishes "the backend answered" from "the backend was never reached". This task is that half.

**Files:**
- Create: `frontend/src/shared/api/errors.ts`
- Create: `frontend/src/shared/api/rest.ts`
- Modify: `frontend/src/shared/api/index.ts`
- Create: `frontend/testing/msw.ts`
- Modify: `frontend/testing/setup.ts`
- Create: `frontend/src/shared/api/rest.test.ts`

**Interfaces:**
- Consumes: `errorEnvelopeSchema`, `meSchema`, `errorCodeSchema` from `./generated`.
- Produces: `class ApiFetchError extends Error` with `kind: "envelope" | "transport"`, `status: number | null`, `code: ErrorCode | null`, `details: Record<string, unknown> | null`, and `isUnauthenticated: boolean`; `apiFetch<T>(path: string, schema: z.ZodType<T>, init?: RequestInit): Promise<T>`; `apiSend<T>(path, schema, body, init?)`. Every later task's REST call goes through these two and nothing else.

- [ ] **Step 1: Write the failing test**

`frontend/src/shared/api/rest.test.ts`:

```typescript
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
    const error = (await apiFetch("/api/thing", bodySchema).catch((e: unknown) => e)) as ApiFetchError;
    expect(error.details).toEqual({ field: "username" });
  });

  it("classifies an HTML error page as transport, not as a server code", async () => {
    server.use(
      http.get("/api/thing", () =>
        HttpResponse.html("<html><body>502 Bad Gateway</body></html>", { status: 502 }),
      ),
    );
    const error = (await apiFetch("/api/thing", bodySchema).catch((e: unknown) => e)) as ApiFetchError;
    expect(error.kind).toBe("transport");
    expect(error.code).toBeNull();
    expect(error.status).toBe(502);
  });

  it("classifies a truncated body as transport", async () => {
    server.use(http.get("/api/thing", () => new HttpResponse('{"ok": tr', { status: 200 })));
    const error = (await apiFetch("/api/thing", bodySchema).catch((e: unknown) => e)) as ApiFetchError;
    expect(error.kind).toBe("transport");
  });

  it("classifies a 2xx whose shape the schema rejects as transport", async () => {
    server.use(http.get("/api/thing", () => HttpResponse.json({ ok: "yes please" })));
    const error = (await apiFetch("/api/thing", bodySchema).catch((e: unknown) => e)) as ApiFetchError;
    expect(error.kind).toBe("transport");
    expect(error.code).toBeNull();
  });

  it("classifies a dead network as transport with no status", async () => {
    server.use(http.get("/api/thing", () => HttpResponse.error()));
    const error = (await apiFetch("/api/thing", bodySchema).catch((e: unknown) => e)) as ApiFetchError;
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
        HttpResponse.json({ code: "unauthenticated", message: "not signed in", details: null }, { status: 401 }),
      ),
    );
    const error = (await apiFetch("/api/thing", bodySchema).catch((e: unknown) => e)) as ApiFetchError;
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
```

- [ ] **Step 2: Write the MSW harness**

`frontend/testing/msw.ts`:

```typescript
import { setupServer } from "msw/node";

/** No default handlers on purpose: an unhandled request must fail the test
 *  loudly (`onUnhandledRequest: "error"` in setup.ts) rather than hang or
 *  quietly return undefined. Every test declares what it expects to be
 *  asked for. */
export const server = setupServer();
```

Replace `frontend/testing/setup.ts` with:

```typescript
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { server } from "./msw";

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
});

afterAll(() => {
  server.close();
});
```

- [ ] **Step 3: Run and watch it fail**

Run: `cd frontend && pnpm test src/shared/api/rest.test.ts`
Expected: FAIL — `Failed to resolve import "./errors"`.

- [ ] **Step 4: Write the error type**

`frontend/src/shared/api/errors.ts`:

```typescript
import type { ErrorCode } from "./generated/errors";

/**
 * Two kinds, and the distinction is load-bearing.
 *
 * `envelope` — the server answered in the shape §6.3 guarantees. `code` is a
 * real member of the closed union the backend asserts the shape of, and a
 * caller may switch on it.
 *
 * `transport` — nothing this API can be understood to have said: a dead
 * network, a proxy's HTML error page, a truncated body, or a 2xx whose shape
 * the generated schema rejects. `code` is `null`, and there is deliberately
 * no synthetic member like `"network_error"` to put there: `ErrorCode` is a
 * closed union of two disjoint server enums that a backend test asserts the
 * membership of, and inventing a value would corrupt the one type that makes
 * `switch` exhaustive.
 */
export type ApiFailureKind = "envelope" | "transport";

export class ApiFetchError extends Error {
  readonly kind: ApiFailureKind;
  readonly status: number | null;
  readonly code: ErrorCode | null;
  readonly details: Record<string, unknown> | null;

  constructor(init: {
    kind: ApiFailureKind;
    message: string;
    status: number | null;
    code?: ErrorCode | null;
    details?: Record<string, unknown> | null;
    cause?: unknown;
  }) {
    super(init.message, init.cause === undefined ? undefined : { cause: init.cause });
    this.name = "ApiFetchError";
    this.kind = init.kind;
    this.status = init.status;
    this.code = init.code ?? null;
    this.details = init.details ?? null;
  }

  /** The one code a guard reacts to structurally rather than by showing it
   *  (§9.4: "a 401 anywhere redirects to /login"). `credentials_invalid` is
   *  deliberately *not* included: that is a form telling you your password
   *  was wrong, not a session that expired. */
  get isUnauthenticated(): boolean {
    return this.kind === "envelope" && this.code === "unauthenticated";
  }
}
```

- [ ] **Step 5: Write `apiFetch`**

`frontend/src/shared/api/rest.ts`:

```typescript
import type { z } from "zod";
import { ApiFetchError } from "./errors";
import { errorEnvelopeSchema } from "./generated/public";

/**
 * Every REST call in the app. Two rules it exists to keep:
 *
 * 1. Nothing reads a field off a response until a generated schema has
 *    parsed it (§4's type contract). `schema` is not optional.
 * 2. A failure is classified before it is thrown, so no caller ever has to
 *    ask "is this a server code or a broken pipe" — see `ApiFetchError`.
 */
export async function apiFetch<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      // The session is a cookie (§7): `triviador_session`, HttpOnly,
      // SameSite=Lax, path=/. Same-origin because dev proxies /api and
      // production serves both from one origin behind Caddy — a
      // cross-origin credentialled fetch would need CORS the backend
      // deliberately does not grant.
      credentials: "same-origin",
      ...init,
      headers: { accept: "application/json", ...init?.headers },
    });
  } catch (cause) {
    throw new ApiFetchError({
      kind: "transport",
      message: "the server could not be reached",
      status: null,
      cause,
    });
  }

  const text = await response.text();

  if (!response.ok) {
    throw toFailure(response.status, text);
  }

  if (text === "") {
    // 204, and a schema that accepts it (`z.void()`) — logout's shape.
    const empty = schema.safeParse(undefined);
    if (empty.success) return empty.data;
    throw new ApiFetchError({
      kind: "transport",
      message: "the server sent an empty body where one was expected",
      status: response.status,
    });
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (cause) {
    throw new ApiFetchError({
      kind: "transport",
      message: "the server sent a body that is not JSON",
      status: response.status,
      cause,
    });
  }

  const result = schema.safeParse(parsed);
  if (!result.success) {
    // A 2xx in the wrong shape is not a server error — the server believes
    // it succeeded. It is this client and that server disagreeing about the
    // contract, which is the same class of problem as an HTML error page:
    // unusable, and not something to switch on.
    throw new ApiFetchError({
      kind: "transport",
      message: "the server sent a body this client does not understand",
      status: response.status,
      cause: result.error,
    });
  }
  return result.data;
}

/** POST/PUT with a JSON body. `body === undefined` sends no body at all,
 *  which is what `/api/auth/logout` wants. */
export async function apiSend<T>(
  path: string,
  schema: z.ZodType<T>,
  body: unknown,
  init?: RequestInit,
): Promise<T> {
  return apiFetch(path, schema, {
    method: "POST",
    ...init,
    ...(body === undefined
      ? {}
      : { body: JSON.stringify(body), headers: { "content-type": "application/json", ...init?.headers } }),
  });
}

function toFailure(status: number, text: string): ApiFetchError {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return new ApiFetchError({
      kind: "transport",
      message: `the server returned ${status} and a body that is not an error envelope`,
      status,
    });
  }
  const envelope = errorEnvelopeSchema.safeParse(parsed);
  if (!envelope.success) {
    return new ApiFetchError({
      kind: "transport",
      message: `the server returned ${status} and a body that is not an error envelope`,
      status,
    });
  }
  return new ApiFetchError({
    kind: "envelope",
    message: envelope.data.message,
    status,
    code: envelope.data.code,
    details: envelope.data.details,
  });
}
```

Extend `frontend/src/shared/api/index.ts`:

```typescript
export * from "./generated/errors";
export * from "./generated/public";
export * from "./generated/ws";
export { ApiFetchError, type ApiFailureKind } from "./errors";
export { apiFetch, apiSend } from "./rest";
```

- [ ] **Step 6: Run until green**

Run: `cd frontend && pnpm test src/shared/api/rest.test.ts`
Expected: all 10 PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/shared/api frontend/testing
git commit -m "feat(frontend): apiFetch, and a transport failure that never wears a server code"
```

---

## Task 5: The socket client, which knows nothing about a cache

§9.4: "The WS client in `shared/api/ws` is dumb — connect, subscribe, typed messages, no knowledge of the cache." This task builds exactly that, plus §8.6's clock offset, and nothing else. Everything that decides *what to do* with a message is Task 7's, one layer up.

**Files:**
- Create: `frontend/src/shared/api/messages.ts`
- Create: `frontend/src/shared/api/clock.ts`
- Create: `frontend/src/shared/api/ws.ts`
- Modify: `frontend/src/shared/api/index.ts`
- Create: `frontend/testing/fake-socket.ts`
- Create: `frontend/src/shared/api/ws.test.ts`, `frontend/src/shared/api/clock.test.ts`

**Interfaces:**
- Consumes: the generated frame and message schemas.
- Produces:
  - `type ServerMessage` and `parseServerMessage(raw: string): ServerMessage | null` (null = a `type` this build does not know, deliberately ignored).
  - `type ClientFrame` — the union of the seven generated client frames.
  - `createClockOffset(samples?: number)` → `{ record(sentAt: number, serverTime: number, receivedAt: number): void; offsetMs(): number }`.
  - `createSocketClient(options: { url: string; socketFactory?: (url: string) => SocketLike; now?: () => number; })` → `SocketClient` with `send(frame)`, `onMessage(fn): () => void`, `onStatus(fn): () => void`, `status()`, `offsetMs()`, `close()`.
  - `type SocketStatus = "connecting" | "open" | "reconnecting" | "closed"`, `type SocketClosed = { code: number }`.

- [ ] **Step 1: Write the message parser and its test**

There is no generated `ServerMessage` union — `contracts/ws.schema.json` exports each message as its own `$def`, because the Pydantic union is a discriminated `Annotated[...]` that JSON Schema flattens. So the union is assembled here, by hand, from generated parts.

**It is assembled as a lookup rather than a `z.discriminatedUnion`.** Two reasons, both concrete: the generated discriminators are `z.literal("x").default("x")`, and asking a union to unwrap a `ZodDefault` to find its discriminator is a bet on a library internal; and `lobby.snapshot`/`lobby.update` share one schema, so the mapping is many-to-one anyway. A lookup also lets an unknown `type` be *ignored* rather than thrown — which is what a client from Plan 6 must do when it meets a message Plan 7 added.

`frontend/src/shared/api/messages.ts`:

```typescript
import type { z } from "zod";
import {
  errorMessageSchema,
  helloMessageSchema,
  lobbyMessageSchema,
  pickRegionFrameSchema,
  pingFrameSchema,
  pongMessageSchema,
  presenceMessageSchema,
  resyncFrameSchema,
  selectTargetFrameSchema,
  snapshotMessageSchema,
  submitAnswerFrameSchema,
  subscribeFrameSchema,
  surrenderFrameSchema,
  unsubscribeFrameSchema,
  updateMessageSchema,
} from "./generated/ws";

export type ServerMessage =
  | z.infer<typeof helloMessageSchema>
  | z.infer<typeof pongMessageSchema>
  | z.infer<typeof lobbyMessageSchema>
  | z.infer<typeof snapshotMessageSchema>
  | z.infer<typeof updateMessageSchema>
  | z.infer<typeof presenceMessageSchema>
  | z.infer<typeof errorMessageSchema>;

export type ClientFrame =
  | z.infer<typeof subscribeFrameSchema>
  | z.infer<typeof unsubscribeFrameSchema>
  | z.infer<typeof resyncFrameSchema>
  | z.infer<typeof pingFrameSchema>
  | z.infer<typeof submitAnswerFrameSchema>
  | z.infer<typeof pickRegionFrameSchema>
  | z.infer<typeof selectTargetFrameSchema>
  | z.infer<typeof surrenderFrameSchema>;

const SERVER_SCHEMAS = {
  hello: helloMessageSchema,
  pong: pongMessageSchema,
  "lobby.snapshot": lobbyMessageSchema,
  "lobby.update": lobbyMessageSchema,
  "game.snapshot": snapshotMessageSchema,
  "game.update": updateMessageSchema,
  "game.presence": presenceMessageSchema,
  error: errorMessageSchema,
} as const;

const CLIENT_SCHEMAS = {
  subscribe: subscribeFrameSchema,
  unsubscribe: unsubscribeFrameSchema,
  resync: resyncFrameSchema,
  ping: pingFrameSchema,
  submit_answer: submitAnswerFrameSchema,
  pick_region: pickRegionFrameSchema,
  select_attack_target: selectTargetFrameSchema,
  surrender: surrenderFrameSchema,
} as const;

export class MessageParseError extends Error {}

/**
 * `null` means "a `type` this build does not know" — ignored on purpose, so a
 * Plan 6 client meeting a Plan 7 message keeps playing. Anything else that is
 * wrong throws: a *known* type whose payload fails its schema is the contract
 * breaking, and swallowing it would turn a loud bug into a board that quietly
 * stops updating.
 */
export function parseServerMessage(raw: string): ServerMessage | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (cause) {
    throw new MessageParseError("socket sent a frame that is not JSON", { cause });
  }
  if (typeof parsed !== "object" || parsed === null || !("type" in parsed)) {
    throw new MessageParseError("socket sent a frame with no type");
  }
  const type = (parsed as { type: unknown }).type;
  if (typeof type !== "string" || !(type in SERVER_SCHEMAS)) return null;
  const schema = SERVER_SCHEMAS[type as keyof typeof SERVER_SCHEMAS];
  const result = schema.safeParse(parsed);
  if (!result.success) {
    throw new MessageParseError(`socket sent a malformed ${type}`, { cause: result.error });
  }
  return result.data as ServerMessage;
}

/** Outbound frames go through the generated schema too (decision 1). Every
 *  frame schema is `.strict()`, so a stray key — `actor_id` above all — is
 *  caught here rather than arriving as a `validation_failed` with no
 *  `command_id` to correlate it to. */
export function encodeClientFrame(frame: ClientFrame): string {
  const schema = CLIENT_SCHEMAS[frame.type];
  return JSON.stringify(schema.parse(frame));
}
```

`frontend/src/shared/api/clock.ts`:

```typescript
import { TIMING } from "@/shared/config";

/**
 * §8.6: the client refines its clock offset from ping/pong, not from
 * `hello.server_time` — that one carries whatever one-way delay its packet
 * met. Median of the last five round trips (decision 11): one sample is at
 * the mercy of a single queued packet, and a mean is at the mercy of the same
 * packet forever.
 */
export function createClockOffset(samples: number = TIMING.CLOCK_SAMPLES) {
  const observed: number[] = [];
  return {
    /** `serverTime` is the pong's `server_time` in epoch milliseconds. The
     *  server's instant is assumed to sit halfway through the round trip,
     *  which is the standard estimate and is wrong only by the asymmetry of
     *  the two legs. */
    record(sentAt: number, serverTime: number, receivedAt: number): void {
      observed.push(serverTime - (sentAt + receivedAt) / 2);
      if (observed.length > samples) observed.shift();
    },
    /** Add this to a local `Date.now()` to get the server's clock. Zero until
     *  the first pong, which is correct: an unmeasured offset is best assumed
     *  to be none rather than guessed from `hello`. */
    offsetMs(): number {
      if (observed.length === 0) return 0;
      const sorted = [...observed].sort((a, b) => a - b);
      const middle = Math.floor(sorted.length / 2);
      return sorted.length % 2 === 1
        ? (sorted[middle] as number)
        : ((sorted[middle - 1] as number) + (sorted[middle] as number)) / 2;
    },
  };
}
```

`frontend/src/shared/api/clock.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { createClockOffset } from "./clock";

describe("createClockOffset", () => {
  it("is zero before any pong", () => {
    expect(createClockOffset().offsetMs()).toBe(0);
  });

  it("estimates the server's clock as halfway through the round trip", () => {
    const clock = createClockOffset();
    // sent at 1000, received at 1100, server said 2050 → offset 1000
    clock.record(1000, 2050, 1100);
    expect(clock.offsetMs()).toBe(1000);
  });

  it("is not moved by a single delayed packet", () => {
    const clock = createClockOffset(5);
    for (let i = 0; i < 4; i++) clock.record(0, 1000, 0);
    clock.record(0, 9000, 0); // one packet queued behind something
    expect(clock.offsetMs()).toBe(1000);
  });

  it("keeps only the most recent samples", () => {
    const clock = createClockOffset(3);
    for (let i = 0; i < 3; i++) clock.record(0, 1000, 0);
    for (let i = 0; i < 3; i++) clock.record(0, 5000, 0);
    expect(clock.offsetMs()).toBe(5000);
  });
});
```

- [ ] **Step 2: Write the fake socket**

`frontend/testing/fake-socket.ts` — the seam every socket test drives. It is deliberately not a `WebSocket` mock library: the client only uses six members, and a fake that implements exactly those cannot drift into testing a mock's behaviour.

```typescript
export interface SocketLike {
  send(data: string): void;
  close(code?: number, reason?: string): void;
  readyState: number;
  onopen: (() => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  onerror: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
}

export class FakeSocket implements SocketLike {
  static readonly OPEN = 1;
  static readonly CLOSED = 3;

  readonly sent: string[] = [];
  readyState = 0;
  onopen: (() => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;

  constructor(readonly url: string) {}

  send(data: string): void {
    this.sent.push(data);
  }

  close(code = 1000): void {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.({ code });
  }

  // --- test-side controls ---
  open(): void {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }

  deliver(message: unknown): void {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  serverClose(code: number): void {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.({ code });
  }

  /** The frames the client sent, parsed. */
  frames(): Array<Record<string, unknown>> {
    return this.sent.map((raw) => JSON.parse(raw) as Record<string, unknown>);
  }
}

/** Hands every constructed socket back to the test. */
export function fakeSocketFactory() {
  const created: FakeSocket[] = [];
  return {
    created,
    factory: (url: string): SocketLike => {
      const socket = new FakeSocket(url);
      created.push(socket);
      return socket;
    },
    last(): FakeSocket {
      const socket = created.at(-1);
      if (socket === undefined) throw new Error("no socket was created");
      return socket;
    },
  };
}
```

- [ ] **Step 3: Write the socket client's test**

`frontend/src/shared/api/ws.test.ts`:

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeSocketFactory } from "../../../testing/fake-socket";
import { TIMING } from "@/shared/config";
import { createSocketClient } from "./ws";

function setup() {
  const sockets = fakeSocketFactory();
  const client = createSocketClient({ url: "/ws", socketFactory: sockets.factory, now: () => Date.now() });
  return { sockets, client };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("createSocketClient", () => {
  it("reports connecting, then open", () => {
    const { sockets, client } = setup();
    const seen: string[] = [];
    client.onStatus((status) => seen.push(status));
    expect(client.status()).toBe("connecting");
    sockets.last().open();
    expect(seen).toContain("open");
    client.close();
  });

  it("delivers a parsed message to listeners", () => {
    const { sockets, client } = setup();
    const received: unknown[] = [];
    client.onMessage((message) => received.push(message));
    sockets.last().open();
    sockets.last().deliver({ type: "game.presence", game_id: "g1", connected: ["u1"] });
    expect(received).toEqual([{ type: "game.presence", game_id: "g1", connected: ["u1"] }]);
    client.close();
  });

  it("ignores a message type it does not know instead of throwing", () => {
    const { sockets, client } = setup();
    const received: unknown[] = [];
    client.onMessage((message) => received.push(message));
    sockets.last().open();
    expect(() => sockets.last().deliver({ type: "admin.something", x: 1 })).not.toThrow();
    expect(received).toEqual([]);
    client.close();
  });

  it("queues frames sent before open and flushes them on open", () => {
    const { sockets, client } = setup();
    client.send({ type: "subscribe", topic: "lobby" });
    expect(sockets.last().sent).toEqual([]);
    sockets.last().open();
    expect(sockets.last().frames()).toEqual([{ type: "subscribe", topic: "lobby" }]);
    client.close();
  });

  it("refuses to send a frame the generated schema rejects", () => {
    const { sockets, client } = setup();
    sockets.last().open();
    expect(() =>
      // @ts-expect-error — the point is that the runtime rejects it too
      client.send({ type: "subscribe", topic: "lobby", actor_id: "u1" }),
    ).toThrow();
    client.close();
  });

  it("pings on the heartbeat interval and folds the pong into the offset", () => {
    const { sockets, client } = setup();
    sockets.last().open();
    vi.advanceTimersByTime(TIMING.PING_INTERVAL_MS);
    expect(sockets.last().frames().at(-1)).toEqual({ type: "ping" });

    const serverTime = new Date(Date.now() + 5_000).toISOString();
    sockets.last().deliver({ type: "pong", server_time: serverTime });
    expect(client.offsetMs()).toBeGreaterThan(4_000);
    client.close();
  });

  it("reconnects with backoff after an unexpected close", () => {
    const { sockets, client } = setup();
    sockets.last().open();
    expect(sockets.created).toHaveLength(1);

    sockets.last().serverClose(1006);
    expect(client.status()).toBe("reconnecting");
    vi.advanceTimersByTime(TIMING.RECONNECT_BASE_MS);
    expect(sockets.created).toHaveLength(2);

    sockets.last().serverClose(1006);
    vi.advanceTimersByTime(TIMING.RECONNECT_BASE_MS);
    expect(sockets.created).toHaveLength(2); // not yet — the delay doubled
    vi.advanceTimersByTime(TIMING.RECONNECT_BASE_MS * 2);
    expect(sockets.created).toHaveLength(3);
    client.close();
  });

  it("resets the backoff once a connection stays open", () => {
    const { sockets, client } = setup();
    sockets.last().open();
    sockets.last().serverClose(1006);
    vi.advanceTimersByTime(TIMING.RECONNECT_BASE_MS);
    sockets.last().open();
    sockets.last().serverClose(1006);
    vi.advanceTimersByTime(TIMING.RECONNECT_BASE_MS);
    expect(sockets.created).toHaveLength(3);
    client.close();
  });

  it("does not reconnect after 4401 or 4403", () => {
    for (const code of [4401, 4403]) {
      const { sockets, client } = setup();
      sockets.last().open();
      sockets.last().serverClose(code);
      vi.advanceTimersByTime(TIMING.RECONNECT_MAX_MS * 4);
      expect(sockets.created).toHaveLength(1);
      expect(client.status()).toBe("closed");
      client.close();
    }
  });

  it("reports the close code to status listeners so a 4401 can sign the user out", () => {
    const { sockets, client } = setup();
    const codes: Array<number | undefined> = [];
    client.onStatus((_status, closed) => codes.push(closed?.code));
    sockets.last().open();
    sockets.last().serverClose(4401);
    expect(codes).toContain(4401);
    client.close();
  });

  it("stops pinging and never reconnects after close()", () => {
    const { sockets, client } = setup();
    sockets.last().open();
    client.close();
    vi.advanceTimersByTime(TIMING.PING_INTERVAL_MS * 5);
    expect(sockets.created).toHaveLength(1);
    expect(client.status()).toBe("closed");
  });
});
```

- [ ] **Step 4: Run and watch it fail**

Run: `cd frontend && pnpm test src/shared/api/ws.test.ts`
Expected: FAIL — `Failed to resolve import "./ws"`.

- [ ] **Step 5: Write the client**

`frontend/src/shared/api/ws.ts`:

```typescript
import { TIMING } from "@/shared/config";
import { createClockOffset } from "./clock";
import { type ClientFrame, type ServerMessage, encodeClientFrame, parseServerMessage } from "./messages";

export type SocketStatus = "connecting" | "open" | "reconnecting" | "closed";
export interface SocketClosed {
  code: number;
}

export interface SocketLike {
  send(data: string): void;
  close(code?: number, reason?: string): void;
  readyState: number;
  onopen: (() => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  onerror: (() => void) | null;
  onmessage: ((event: { data: string }) => void) | null;
}

export interface SocketClient {
  send(frame: ClientFrame): void;
  onMessage(listener: (message: ServerMessage) => void): () => void;
  onStatus(listener: (status: SocketStatus, closed?: SocketClosed) => void): () => void;
  status(): SocketStatus;
  offsetMs(): number;
  close(): void;
}

/**
 * §8.1's one multiplexed socket, and nothing else. It does not know a cache
 * exists, it does not know what a game is, and it must not learn: everything
 * that decides what a message *means* is `app/dispatcher.ts`, one layer up
 * (§9.4).
 *
 * Two close codes are terminal rather than retryable — `4401` (the session is
 * gone) and `4403` (this principal may not have that topic, or the origin was
 * refused). Reconnecting into either would be a client hammering a door it has
 * been told it does not have a key for; §11.1 gives each an explicit reaction,
 * and the reaction is not "try again".
 */
export function createSocketClient(options: {
  url: string;
  socketFactory?: (url: string) => SocketLike;
  now?: () => number;
}): SocketClient {
  const factory = options.socketFactory ?? ((url: string) => new WebSocket(url) as unknown as SocketLike);
  const now = options.now ?? Date.now;
  const clock = createClockOffset();

  const messageListeners = new Set<(message: ServerMessage) => void>();
  const statusListeners = new Set<(status: SocketStatus, closed?: SocketClosed) => void>();

  let socket: SocketLike | null = null;
  let current: SocketStatus = "connecting";
  let backoff = TIMING.RECONNECT_BASE_MS;
  let pending: ClientFrame[] = [];
  let pingTimer: ReturnType<typeof setInterval> | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | null = null;
  let pingSentAt = 0;
  let disposed = false;

  const TERMINAL_CLOSE_CODES = new Set([4401, 4403]);

  function setStatus(next: SocketStatus, closed?: SocketClosed): void {
    current = next;
    for (const listener of statusListeners) listener(next, closed);
  }

  function stopTimers(): void {
    if (pingTimer !== null) clearInterval(pingTimer);
    if (retryTimer !== null) clearTimeout(retryTimer);
    pingTimer = null;
    retryTimer = null;
  }

  function connect(): void {
    if (disposed) return;
    const opened = factory(options.url);
    socket = opened;

    opened.onopen = () => {
      backoff = TIMING.RECONNECT_BASE_MS;
      setStatus("open");
      const queued = pending;
      pending = [];
      for (const frame of queued) opened.send(encodeClientFrame(frame));
      pingTimer = setInterval(() => {
        pingSentAt = now();
        opened.send(encodeClientFrame({ type: "ping" }));
      }, TIMING.PING_INTERVAL_MS);
    };

    opened.onmessage = (event) => {
      const message = parseServerMessage(event.data);
      if (message === null) return;
      if (message.type === "pong") {
        clock.record(pingSentAt, Date.parse(message.server_time), now());
        return; // §8.6's heartbeat is not application data.
      }
      for (const listener of messageListeners) listener(message);
    };

    // A transport error is always followed by a close; handling both would
    // double-count and halve the backoff.
    opened.onerror = () => {};

    opened.onclose = (event) => {
      stopTimers();
      socket = null;
      if (disposed) {
        setStatus("closed", { code: event.code });
        return;
      }
      if (TERMINAL_CLOSE_CODES.has(event.code)) {
        disposed = true;
        setStatus("closed", { code: event.code });
        return;
      }
      setStatus("reconnecting", { code: event.code });
      retryTimer = setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, TIMING.RECONNECT_MAX_MS);
    };
  }

  connect();

  return {
    send(frame) {
      // Encoded — and therefore schema-checked — even when queued, so a
      // malformed frame throws at the call site rather than on reconnect.
      const encoded = encodeClientFrame(frame);
      if (socket !== null && current === "open") socket.send(encoded);
      else pending.push(frame);
    },
    onMessage(listener) {
      messageListeners.add(listener);
      return () => messageListeners.delete(listener);
    },
    onStatus(listener) {
      statusListeners.add(listener);
      return () => statusListeners.delete(listener);
    },
    status: () => current,
    offsetMs: () => clock.offsetMs(),
    close() {
      disposed = true;
      stopTimers();
      pending = [];
      const open = socket;
      socket = null;
      setStatus("closed");
      open?.close(1000);
    },
  };
}
```

Extend `frontend/src/shared/api/index.ts` with:

```typescript
export { createClockOffset } from "./clock";
export {
  type ClientFrame,
  type ServerMessage,
  MessageParseError,
  encodeClientFrame,
  parseServerMessage,
} from "./messages";
export {
  type SocketClient,
  type SocketClosed,
  type SocketLike,
  type SocketStatus,
  createSocketClient,
} from "./ws";
```

- [ ] **Step 6: Run until green**

Run: `cd frontend && pnpm test src/shared/api`
Expected: every test in `rest.test.ts`, `clock.test.ts` and `ws.test.ts` PASSES.

- [ ] **Step 7: Prove `shared` still imports nothing above it**

Run: `cd frontend && pnpm check`
Expected: `steiger` clean. If it reports a `fsd/forbidden-imports` violation here, something in `shared/api` reached for `entities` or above, and the fix is to move that logic to `app/`, never to add an exception.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/shared/api frontend/testing
git commit -m "feat(frontend): the dumb socket, its heartbeat, and a median clock offset"
```

---

## Task 6: The entities — cache keys, seat colours, and every question the screens will ask of a state

§9.4 names four entity slices. They hold types, selectors and cache keys — no components, no fetching, no React. Everything a widget wants to know about a `ClientGameState` is answered here, once, so that no two screens disagree about what "it is your turn" means.

**Files:**
- Create: `frontend/src/entities/game/model/{keys.ts,selectors.ts}`, `frontend/src/entities/game/index.ts`
- Create: `frontend/src/entities/player/model/seats.ts`, `frontend/src/entities/player/index.ts`
- Create: `frontend/src/entities/territory/model/ownership.ts`, `frontend/src/entities/territory/index.ts`
- Create: `frontend/src/entities/question/model/turn.ts`, `frontend/src/entities/question/index.ts`
- Create: `frontend/testing/factories.ts`
- Create: `frontend/src/entities/game/model/selectors.test.ts`, `frontend/src/entities/territory/model/ownership.test.ts`

**Interfaces:**
- Consumes: `ClientGameState`, `ClientPlayer`, `ClientTerritory`, `ClientQuestion`, `GameSnapshot` from `@/shared/api`; `seatVar` from `@/shared/config`.
- Produces:
  - keys: `meKey()`, `lobbyKey()`, `gameKey(id)`, `mapKey(mapId)` — all `readonly` tuples.
  - `entities/game`: `youPlayer(state)`, `isYourTurn(state)`, `deadlineOf(state)`, `deadlineIdOf(state)`, `yourOptions(state)`, `answeredBy(state)`, `yourAnswer(state)`, `turnKindOf(state)`.
  - `entities/player`: `seatColorOf(state, playerId)`, `playerById(state, id)`, `orderedPlayers(state)`.
  - `entities/territory`: `ownershipOf(state)` → `ReadonlyMap<string, TerritoryView>`; `type TerritoryView = { ownerSeat: number | null; isBase: boolean; baseHp: number | null }`.
  - `entities/question`: `questionOf(state)` → `ClientQuestion | null`, `isNumericTurn(state)`.

- [ ] **Step 1: Write the test factories**

`frontend/testing/factories.ts` — every later task builds its fixtures from these, so a projection field that changes shape breaks one file rather than twenty:

```typescript
import type {
  ClientGameState,
  ClientPlayer,
  ClientQuestion,
  ClientRules,
  ClientTerritory,
  GameSnapshot,
} from "@/shared/api";

export const RULES: ClientRules = {
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

export function player(overrides: Partial<ClientPlayer> = {}): ClientPlayer {
  return {
    player_id: "u1",
    display_name: "Alexey",
    seat: 0,
    score: 1000,
    bonus_score: 0,
    base_region: "plzensky",
    is_eliminated: false,
    ...overrides,
  };
}

export function territory(overrides: Partial<ClientTerritory> = {}): ClientTerritory {
  return {
    region_id: "praha",
    owner_id: null,
    kind: "normal",
    base_owner_id: null,
    base_hp: null,
    acquisition: null,
    ...overrides,
  };
}

export function question(overrides: Partial<ClientQuestion> = {}): ClientQuestion {
  return {
    question_id: "q1",
    kind: "multiple_choice",
    prompt: "Which river flows through Prague?",
    category: "Geography",
    difficulty: "easy",
    media_url: null,
    unit: null,
    choices: [
      { idx: 0, text: "Vltava", media_url: null },
      { idx: 1, text: "Labe", media_url: null },
      { idx: 2, text: "Morava", media_url: null },
      { idx: 3, text: "Odra", media_url: null },
    ],
    ...overrides,
  };
}

export function gameState(overrides: Partial<ClientGameState> = {}): ClientGameState {
  return {
    game_id: "g1",
    map_id: "czechia",
    phase: "expansion",
    round_no: 2,
    rules: RULES,
    players: [
      player(),
      player({ player_id: "u2", display_name: "Petra", seat: 1, base_region: "kralovehradecky" }),
      player({ player_id: "u3", display_name: "Tomáš", seat: 2, base_region: "jihomoravsky" }),
    ],
    territories: [territory()],
    turn: null,
    turn_order: ["u1", "u2", "u3"],
    winner_id: null,
    media_prefetch: [],
    you: { player_id: "u1", role: "player" },
    ...overrides,
  };
}

export function snapshot(seq: number, state: Partial<ClientGameState> = {}): GameSnapshot {
  return { seq, state: gameState(state) };
}

/** A deadline far enough out that a timer test never accidentally expires. */
export function deadline(msFromNow = 20_000): string {
  return new Date(Date.now() + msFromNow).toISOString();
}
```

- [ ] **Step 2: Write the failing selector tests**

`frontend/src/entities/game/model/selectors.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { deadline, gameState, question } from "../../../../testing/factories";
import { answeredBy, deadlineIdOf, deadlineOf, isYourTurn, yourAnswer, yourOptions, youPlayer } from "./selectors";

const pickingTurn = {
  kind: "expansion_picking" as const,
  current_picker: "u1",
  pick_order: ["u1", "u2", "u3"],
  grants_remaining: { u1: 2, u2: 1, u3: 0 },
  deadline_at: deadline(),
  deadline_id: 7,
  your_options: { pick: ["praha", "vysocina"], attack: [] },
};

const questionTurn = {
  kind: "expansion_question" as const,
  question: question(),
  answered: ["u3"],
  your_answer: null,
  deadline_at: deadline(),
  deadline_id: 9,
  your_options: { pick: [], attack: [] },
};

describe("game selectors", () => {
  it("finds you by state.you.player_id, not by comparing against /me", () => {
    const state = gameState();
    expect(youPlayer(state)?.display_name).toBe("Alexey");
  });

  it("returns null for a spectating admin who is in no seat", () => {
    const state = gameState({ you: { player_id: null, role: "admin" } });
    expect(youPlayer(state)).toBeNull();
  });

  it("says it is your turn when your_options offers something", () => {
    expect(isYourTurn(gameState({ turn: pickingTurn }))).toBe(true);
  });

  it("says it is not your turn when both option lists are empty", () => {
    const notYours = { ...pickingTurn, current_picker: "u2", your_options: { pick: [], attack: [] } };
    expect(isYourTurn(gameState({ turn: notYours }))).toBe(false);
  });

  it("treats an open question you have not answered as your turn", () => {
    expect(isYourTurn(gameState({ turn: questionTurn }))).toBe(true);
  });

  it("stops treating it as your turn once you have answered", () => {
    const answered = { ...questionTurn, your_answer: { kind: "choice" as const, idx: 1, value: null } };
    expect(isYourTurn(gameState({ turn: answered }))).toBe(false);
  });

  it("reads the deadline and its id off whichever turn is open", () => {
    const state = gameState({ turn: questionTurn });
    expect(deadlineOf(state)).toBe(questionTurn.deadline_at);
    expect(deadlineIdOf(state)).toBe(9);
  });

  it("has no deadline in a lobby", () => {
    expect(deadlineOf(gameState({ phase: "lobby", turn: null }))).toBeNull();
    expect(deadlineIdOf(gameState({ phase: "lobby", turn: null }))).toBeNull();
  });

  it("reports who has answered, and your own answer", () => {
    const state = gameState({ turn: questionTurn });
    expect(answeredBy(state)).toEqual(["u3"]);
    expect(yourAnswer(state)).toBeNull();
  });

  it("returns empty options rather than undefined for a turnless state", () => {
    expect(yourOptions(gameState({ turn: null }))).toEqual({ pick: [], attack: [] });
  });
});
```

`frontend/src/entities/territory/model/ownership.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { gameState, player, territory } from "../../../../testing/factories";
import { ownershipOf } from "./ownership";

describe("ownershipOf", () => {
  const state = gameState({
    players: [player(), player({ player_id: "u2", display_name: "Petra", seat: 1 })],
    territories: [
      territory({ region_id: "praha", owner_id: null }),
      territory({ region_id: "plzensky", owner_id: "u1", kind: "base", base_owner_id: "u1", base_hp: 2 }),
      territory({ region_id: "liberecky", owner_id: "u2" }),
    ],
  });

  it("maps an owner to a seat, because colour is derived from seat and nothing else", () => {
    expect(ownershipOf(state).get("liberecky")?.ownerSeat).toBe(1);
  });

  it("leaves a free region with no seat", () => {
    expect(ownershipOf(state).get("praha")?.ownerSeat).toBeNull();
  });

  it("carries a base and its remaining hit points", () => {
    expect(ownershipOf(state).get("plzensky")).toEqual({ ownerSeat: 0, isBase: true, baseHp: 2 });
  });

  it("has an entry for every territory the projection sent and no others", () => {
    expect([...ownershipOf(state).keys()].sort()).toEqual(["liberecky", "plzensky", "praha"]);
  });
});
```

- [ ] **Step 3: Run and watch them fail**

Run: `cd frontend && pnpm test src/entities`
Expected: FAIL — the two modules do not exist.

- [ ] **Step 4: Write the entities**

`frontend/src/entities/game/model/keys.ts`:

```typescript
/** Every cache key in the application. Written once so that a typo cannot
 *  produce a second, silently-empty cache entry — the failure mode where the
 *  socket updates `["game","g1"]` and a component reads `["games","g1"]`. */
export const meKey = () => ["me"] as const;
export const lobbyKey = () => ["lobby"] as const;
export const gameKey = (gameId: string) => ["game", gameId] as const;
export const mapKey = (mapId: string) => ["map", mapId] as const;
```

`frontend/src/entities/game/model/selectors.ts`:

```typescript
import type { ClientGameState, ClientPlayer, SubmittedValue, YourOptions } from "@/shared/api";

const NO_OPTIONS: YourOptions = { pick: [], attack: [] };

/** Who you are *in this game*. §8.7: the projection carries `you` precisely
 *  so the client never correlates `/api/auth/me` against the player list and
 *  gets it wrong for a spectating admin. */
export function youPlayer(state: ClientGameState): ClientPlayer | null {
  const id = state.you.player_id;
  if (id === null) return null;
  return state.players.find((p) => p.player_id === id) ?? null;
}

export function playerById(state: ClientGameState, playerId: string): ClientPlayer | null {
  return state.players.find((p) => p.player_id === playerId) ?? null;
}

export function turnKindOf(state: ClientGameState): string | null {
  return state.turn === null ? null : state.turn.kind;
}

export function yourOptions(state: ClientGameState): YourOptions {
  return state.turn?.your_options ?? NO_OPTIONS;
}

export function deadlineOf(state: ClientGameState): string | null {
  return state.turn?.deadline_at ?? null;
}

export function deadlineIdOf(state: ClientGameState): number | null {
  return state.turn?.deadline_id ?? null;
}

export function answeredBy(state: ClientGameState): readonly string[] {
  const turn = state.turn;
  return turn !== null && "answered" in turn ? turn.answered : [];
}

export function yourAnswer(state: ClientGameState): SubmittedValue | null {
  const turn = state.turn;
  return turn !== null && "your_answer" in turn ? turn.your_answer : null;
}

/**
 * The one definition of "you can act right now", so no two screens disagree.
 *
 * It is derived entirely from the projection's affordances (§8.8) plus
 * whether you have already answered — never from comparing `current_picker`
 * to your id, and never from a rule. A viewer who is offered nothing is
 * watching, whatever the turn says.
 */
export function isYourTurn(state: ClientGameState): boolean {
  const turn = state.turn;
  if (turn === null) return false;
  const options = yourOptions(state);
  if (options.pick.length > 0 || options.attack.length > 0) return true;
  if ("question" in turn && "your_answer" in turn) {
    if (turn.your_answer !== null) return false;
    // A question is only yours to answer if you are in it: `answered` lists
    // participants who have replied, and the projection only sends a
    // question to a viewer who may answer it or watch it. `you` being seated
    // is the honest test.
    return state.you.player_id !== null;
  }
  return false;
}
```

`frontend/src/entities/game/index.ts`:

```typescript
export { gameKey, lobbyKey, mapKey, meKey } from "./model/keys";
export {
  answeredBy,
  deadlineIdOf,
  deadlineOf,
  isYourTurn,
  playerById,
  turnKindOf,
  yourAnswer,
  yourOptions,
  youPlayer,
} from "./model/selectors";
```

`frontend/src/entities/player/model/seats.ts`:

```typescript
import { seatVar } from "@/shared/config";
import type { ClientGameState, ClientPlayer } from "@/shared/api";

/** §8.1: appearance is derived. This returns a CSS `var(...)` reference, not
 *  a colour — nothing in the app ever holds a hex value for a player. */
export function seatColorOf(state: ClientGameState, playerId: string | null): string | null {
  if (playerId === null) return null;
  const player = state.players.find((p) => p.player_id === playerId);
  return player === undefined ? null : seatVar(player.seat);
}

/** Seat order, which is turn order at the table and is stable for the whole
 *  game — unlike `turn_order`, which the server rotates. */
export function orderedPlayers(state: ClientGameState): readonly ClientPlayer[] {
  return [...state.players].sort((a, b) => a.seat - b.seat);
}
```

`frontend/src/entities/player/index.ts`:

```typescript
export { orderedPlayers, seatColorOf } from "./model/seats";
```

`frontend/src/entities/territory/model/ownership.ts`:

```typescript
import type { ClientGameState } from "@/shared/api";

export interface TerritoryView {
  ownerSeat: number | null;
  isBase: boolean;
  baseHp: number | null;
}

/**
 * The map's whole input, in one pass.
 *
 * Owner ids become *seats* here and nowhere else: §8.1 says fill comes from
 * `territories[id].owner_id` mapped to a per-seat custom property, and doing
 * that mapping in the renderer would mean every region re-scanning the player
 * list on every frame.
 */
export function ownershipOf(state: ClientGameState): ReadonlyMap<string, TerritoryView> {
  const seatOf = new Map(state.players.map((p) => [p.player_id, p.seat]));
  const view = new Map<string, TerritoryView>();
  for (const territory of state.territories) {
    view.set(territory.region_id, {
      ownerSeat: territory.owner_id === null ? null : (seatOf.get(territory.owner_id) ?? null),
      isBase: territory.kind === "base",
      baseHp: territory.base_hp,
    });
  }
  return view;
}
```

`frontend/src/entities/territory/index.ts`:

```typescript
export { type TerritoryView, ownershipOf } from "./model/ownership";
```

`frontend/src/entities/question/model/turn.ts`:

```typescript
import type { ClientGameState, ClientQuestion } from "@/shared/api";

/** The seven turn kinds are a union; four of them carry a question. This is
 *  the narrowing, written once. */
export function questionOf(state: ClientGameState): ClientQuestion | null {
  const turn = state.turn;
  return turn !== null && "question" in turn ? turn.question : null;
}

export function isNumericTurn(state: ClientGameState): boolean {
  return questionOf(state)?.kind === "numeric";
}
```

`frontend/src/entities/question/index.ts`:

```typescript
export { isNumericTurn, questionOf } from "./model/turn";
```

- [ ] **Step 5: Run until green, then run the layer gate**

Run: `cd frontend && pnpm test src/entities && pnpm check`
Expected: tests pass; `steiger` clean. `fsd/insignificant-slice` is off under `entities/**` (Task 3), so `question` and `territory` having one consumer each is not reported.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/entities frontend/testing/factories.ts
git commit -m "feat(frontend): the four entities, and one definition of whose turn it is"
```

---

## Task 7: The dispatcher — one merge rule, three cases, and a bus that never touches the cache

This is the task the plan exists for. §9.1 splits every `game.update` into two disjoint sinks; §9.3 makes `seq` the arbiter of a race between REST and the socket; Spec 1B §8.2 states the gap rule (A-3) in three lines. Get this wrong and the symptom is a board that is subtly, intermittently behind — the hardest class of bug in the product.

**Files:**
- Modify: `frontend/src/shared/api/messages.ts` (add `parseClientEvent`, `type Narration`)
- Create: `frontend/src/app/event-bus.ts`
- Create: `frontend/src/app/dispatcher.ts`
- Create: `frontend/src/app/dispatcher.test.ts`

**Interfaces:**
- Consumes: `gameKey`, `lobbyKey` from `@/entities/game`; `ServerMessage`, `GameSnapshot` from `@/shared/api`; `QueryClient` from `@tanstack/react-query`.
- Produces:
  - `type Narration` — the union of the 28 generated event types, plus `parseClientEvent(value: unknown): Narration | null`.
  - `createEventBus()` → `{ emit(gameId: string, events: readonly Narration[]): void; subscribe(gameId: string, fn: (event: Narration) => void): () => void }`.
  - `writeGame(queryClient, gameId, incoming: GameSnapshot): void` — **the merge rule**.
  - `createDispatcher({ queryClient, bus })` → `{ handle(message: ServerMessage): void }`.

- [ ] **Step 1: Add the narration parser**

`UpdateMessage.events` infers as `any[]`: the contract's event union is an `anyOf` that `json-schema-to-zod` renders as `z.any().superRefine(...)`, which validates but does not narrow. So the events are re-parsed into a real union here, by the same lookup pattern as `parseServerMessage` and for the same reason — an event type this build does not know is ignored, not thrown.

Append to `frontend/src/shared/api/messages.ts`:

```typescript
import {
  attackDeclaredEventSchema,
  baseDamagedEventSchema,
  baseDestroyedEventSchema,
  basesAssignedEventSchema,
  defenseHeldEventSchema,
  duelResolvedEventSchema,
  finalTiebreakStartedEventSchema,
  gameAbortedEventSchema,
  gameFinishedEventSchema,
  gameStartedEventSchema,
  neutralAttackFailedEventSchema,
  neutralCapturedEventSchema,
  picksGrantedEventSchema,
  playerAnsweredEventSchema,
  playerGoneEventSchema,
  playerJoinedEventSchema,
  playerLeftEventSchema,
  questionPresentedEventSchema,
  questionResolvedEventSchema,
  roundEventSchema,
  scoreChangedEventSchema,
  territoryCapturedEventSchema,
  territoryClaimedEventSchema,
  territoryNeutralizedEventSchema,
  tiebreakStartedEventSchema,
  turnEndedEventSchema,
  turnStartedEventSchema,
  warmupStartedEventSchema,
} from "./generated/ws";

const EVENT_SCHEMAS = {
  attack_declared: attackDeclaredEventSchema,
  base_damaged: baseDamagedEventSchema,
  base_destroyed: baseDestroyedEventSchema,
  bases_assigned: basesAssignedEventSchema,
  defense_held: defenseHeldEventSchema,
  duel_resolved: duelResolvedEventSchema,
  final_tiebreak_started: finalTiebreakStartedEventSchema,
  game_aborted: gameAbortedEventSchema,
  game_finished: gameFinishedEventSchema,
  game_started: gameStartedEventSchema,
  neutral_attack_failed: neutralAttackFailedEventSchema,
  neutral_captured: neutralCapturedEventSchema,
  picks_granted: picksGrantedEventSchema,
  player_answered: playerAnsweredEventSchema,
  player_gone: playerGoneEventSchema,
  player_joined: playerJoinedEventSchema,
  player_left: playerLeftEventSchema,
  question_presented: questionPresentedEventSchema,
  question_resolved: questionResolvedEventSchema,
  round: roundEventSchema,
  score_changed: scoreChangedEventSchema,
  territory_captured: territoryCapturedEventSchema,
  territory_claimed: territoryClaimedEventSchema,
  territory_neutralized: territoryNeutralizedEventSchema,
  tiebreak_started: tiebreakStartedEventSchema,
  turn_ended: turnEndedEventSchema,
  turn_started: turnStartedEventSchema,
  warmup_started: warmupStartedEventSchema,
} as const;

type EventSchemas = typeof EVENT_SCHEMAS;
export type Narration = { [K in keyof EventSchemas]: z.infer<EventSchemas[K]> }[keyof EventSchemas];

/** `null` for an event type this build does not know — narration is
 *  decoration, and a client that throws away an animation it has never heard
 *  of is behaving correctly. A *known* type with a malformed payload also
 *  returns null rather than throwing: unlike a `game.update`, losing one
 *  narration event costs nothing, and taking the whole board down for a bad
 *  toast would be the wrong trade. */
export function parseClientEvent(value: unknown): Narration | null {
  if (typeof value !== "object" || value === null || !("type" in value)) return null;
  const type = (value as { type: unknown }).type;
  if (typeof type !== "string" || !(type in EVENT_SCHEMAS)) return null;
  const result = EVENT_SCHEMAS[type as keyof EventSchemas].safeParse(value);
  return result.success ? (result.data as Narration) : null;
}
```

Export `Narration` and `parseClientEvent` from `shared/api/index.ts`.

- [ ] **Step 2: Write the failing dispatcher test**

`frontend/src/app/dispatcher.test.ts` — every line of §8.2's table, plus the race §9.3 exists for:

```typescript
import { QueryClient } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { snapshot } from "../../testing/factories";
import { gameKey, lobbyKey } from "@/entities/game";
import type { GameSnapshot, ServerMessage } from "@/shared/api";
import { createDispatcher, writeGame } from "./dispatcher";
import { createEventBus } from "./event-bus";

const CLAIMED = { type: "territory_claimed" as const, region_id: "praha", player_id: "u1" };
const CAPTURED = { type: "territory_captured" as const, region_id: "praha", attacker_id: "u1", defender_id: "u2" };

function setup() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const bus = createEventBus();
  const dispatcher = createDispatcher({ queryClient, bus });
  const narrated: unknown[] = [];
  bus.subscribe("g1", (event) => narrated.push(event));
  return { queryClient, bus, dispatcher, narrated };
}

function update(seq: number, baseSeq: number, events: unknown[] = []): ServerMessage {
  const { state } = snapshot(seq);
  return { type: "game.update", game_id: "g1", seq, base_seq: baseSeq, state, events } as ServerMessage;
}

function cached(queryClient: QueryClient): GameSnapshot | undefined {
  return queryClient.getQueryData<GameSnapshot>(gameKey("g1"));
}

describe("writeGame", () => {
  it("writes when the cache is empty", () => {
    const queryClient = new QueryClient();
    writeGame(queryClient, "g1", snapshot(3));
    expect(cached(queryClient)?.seq).toBe(3);
  });

  it("writes a newer seq over an older one", () => {
    const queryClient = new QueryClient();
    writeGame(queryClient, "g1", snapshot(3));
    writeGame(queryClient, "g1", snapshot(4));
    expect(cached(queryClient)?.seq).toBe(4);
  });

  it("refuses an older seq — §9.3's REST-lands-after-the-socket race", () => {
    const queryClient = new QueryClient();
    writeGame(queryClient, "g1", snapshot(7));
    writeGame(queryClient, "g1", snapshot(5));
    expect(cached(queryClient)?.seq).toBe(7);
  });

  it("accepts an equal seq, so a resync after a reconnect always lands", () => {
    const queryClient = new QueryClient();
    writeGame(queryClient, "g1", snapshot(7));
    const fresh = snapshot(7, { round_no: 3 });
    writeGame(queryClient, "g1", fresh);
    expect(cached(queryClient)?.state.round_no).toBe(3);
  });
});

describe("the dispatcher", () => {
  it("applies a snapshot and narrates nothing", () => {
    const { dispatcher, queryClient, narrated } = setup();
    dispatcher.handle({ type: "game.snapshot", game_id: "g1", seq: 5, state: snapshot(5).state } as ServerMessage);
    expect(cached(queryClient)?.seq).toBe(5);
    expect(narrated).toEqual([]);
  });

  it("applies an in-order update and narrates its events", () => {
    const { dispatcher, queryClient, narrated } = setup();
    dispatcher.handle({ type: "game.snapshot", game_id: "g1", seq: 5, state: snapshot(5).state } as ServerMessage);
    dispatcher.handle(update(6, 5, [CLAIMED]));
    expect(cached(queryClient)?.seq).toBe(6);
    expect(narrated).toEqual([CLAIMED]);
  });

  it("ignores a duplicate entirely — no write, no narration", () => {
    const { dispatcher, queryClient, narrated } = setup();
    dispatcher.handle({ type: "game.snapshot", game_id: "g1", seq: 6, state: snapshot(6).state } as ServerMessage);
    dispatcher.handle(update(6, 5, [CLAIMED]));
    expect(cached(queryClient)?.seq).toBe(6);
    expect(narrated).toEqual([]);
  });

  it("ignores an update older than the cache", () => {
    const { dispatcher, queryClient, narrated } = setup();
    dispatcher.handle({ type: "game.snapshot", game_id: "g1", seq: 9, state: snapshot(9).state } as ServerMessage);
    dispatcher.handle(update(7, 6, [CLAIMED]));
    expect(cached(queryClient)?.seq).toBe(9);
    expect(narrated).toEqual([]);
  });

  it("applies a gapped update's state but suppresses its events", () => {
    const { dispatcher, queryClient, narrated } = setup();
    dispatcher.handle({ type: "game.snapshot", game_id: "g1", seq: 5, state: snapshot(5).state } as ServerMessage);
    dispatcher.handle(update(9, 8, [CLAIMED, CAPTURED]));
    expect(cached(queryClient)?.seq).toBe(9);
    expect(narrated).toEqual([]);
  });

  it("narrates again once the sequence is contiguous after a gap", () => {
    const { dispatcher, narrated } = setup();
    dispatcher.handle({ type: "game.snapshot", game_id: "g1", seq: 5, state: snapshot(5).state } as ServerMessage);
    dispatcher.handle(update(9, 8, [CLAIMED]));
    dispatcher.handle(update(10, 9, [CAPTURED]));
    expect(narrated).toEqual([CAPTURED]);
  });

  it("suppresses events for the first update when there was no base at all", () => {
    const { dispatcher, queryClient, narrated } = setup();
    dispatcher.handle(update(4, 3, [CLAIMED]));
    expect(cached(queryClient)?.seq).toBe(4);
    expect(narrated).toEqual([]);
  });

  it("never lets an event reach the cache", () => {
    const { dispatcher, queryClient } = setup();
    dispatcher.handle({ type: "game.snapshot", game_id: "g1", seq: 5, state: snapshot(5).state } as ServerMessage);
    const before = cached(queryClient)?.state;
    dispatcher.handle(update(6, 5, [CLAIMED, CAPTURED]));
    // The only difference between the two states is what the *server* sent;
    // §9.1's whole point is that the client never folds an event in.
    expect(cached(queryClient)?.state).toEqual({ ...before, ...snapshot(6).state });
  });

  it("drops an event type it does not know without dropping the ones it does", () => {
    const { dispatcher, narrated } = setup();
    dispatcher.handle({ type: "game.snapshot", game_id: "g1", seq: 5, state: snapshot(5).state } as ServerMessage);
    dispatcher.handle(update(6, 5, [{ type: "invented_by_plan_9" }, CLAIMED]));
    expect(narrated).toEqual([CLAIMED]);
  });

  it("keeps two games' sequences independent", () => {
    const { dispatcher, queryClient } = setup();
    dispatcher.handle({ type: "game.snapshot", game_id: "g1", seq: 9, state: snapshot(9).state } as ServerMessage);
    dispatcher.handle({ type: "game.snapshot", game_id: "g2", seq: 2, state: snapshot(2).state } as ServerMessage);
    expect(queryClient.getQueryData<GameSnapshot>(gameKey("g1"))?.seq).toBe(9);
    expect(queryClient.getQueryData<GameSnapshot>(gameKey("g2"))?.seq).toBe(2);
  });

  it("writes a lobby message straight to the lobby key", () => {
    const { dispatcher, queryClient } = setup();
    const games = [{ game_id: "g1", host_id: "u1", map_id: "czechia", player_count: 2, max_players: 3, status: "lobby" }];
    dispatcher.handle({ type: "lobby.snapshot", games } as ServerMessage);
    expect(queryClient.getQueryData(lobbyKey())).toEqual(games);
  });

  it("ignores presence and error messages rather than writing them anywhere", () => {
    const { dispatcher, queryClient } = setup();
    dispatcher.handle({ type: "game.presence", game_id: "g1", connected: ["u1"] } as ServerMessage);
    dispatcher.handle({ type: "error", code: "not_your_turn", message: "no", command_id: "c1" } as ServerMessage);
    expect(cached(queryClient)).toBeUndefined();
  });
});

describe("the event bus", () => {
  it("delivers only to subscribers of that game", () => {
    const bus = createEventBus();
    const one: unknown[] = [];
    const two: unknown[] = [];
    bus.subscribe("g1", (e) => one.push(e));
    bus.subscribe("g2", (e) => two.push(e));
    bus.emit("g1", [CLAIMED]);
    expect(one).toEqual([CLAIMED]);
    expect(two).toEqual([]);
  });

  it("stops delivering after unsubscribe", () => {
    const bus = createEventBus();
    const seen: unknown[] = [];
    const off = bus.subscribe("g1", (e) => seen.push(e));
    off();
    bus.emit("g1", [CLAIMED]);
    expect(seen).toEqual([]);
  });

  it("keeps nothing: a subscriber that arrives late sees no history", () => {
    const bus = createEventBus();
    bus.emit("g1", [CLAIMED]);
    const seen: unknown[] = [];
    bus.subscribe("g1", (e) => seen.push(e));
    expect(seen).toEqual([]);
  });

  it("does not let one throwing subscriber rob the others", () => {
    const bus = createEventBus();
    const seen: unknown[] = [];
    bus.subscribe("g1", () => {
      throw new Error("a toast blew up");
    });
    bus.subscribe("g1", (e) => seen.push(e));
    expect(() => bus.emit("g1", [CLAIMED])).not.toThrow();
    expect(seen).toEqual([CLAIMED]);
  });
});
```

- [ ] **Step 3: Run and watch it fail**

Run: `cd frontend && pnpm test src/app/dispatcher.test.ts`
Expected: FAIL — `Failed to resolve import "./dispatcher"`.

- [ ] **Step 4: Write the bus**

`frontend/src/app/event-bus.ts`:

```typescript
import type { Narration } from "@/shared/api";

/**
 * §9.1's second sink. Narration — toasts, the capture animation, the battle
 * log — and nothing that outlives a frame.
 *
 * It has no history on purpose. An event bus that replays is a second store,
 * and a second store is the thing this whole design exists to not have. A
 * component that mounts late has missed the animation; it reads the state
 * for the facts.
 */
export interface EventBus {
  emit(gameId: string, events: readonly Narration[]): void;
  subscribe(gameId: string, listener: (event: Narration) => void): () => void;
}

export function createEventBus(): EventBus {
  const listeners = new Map<string, Set<(event: Narration) => void>>();

  return {
    emit(gameId, events) {
      const forGame = listeners.get(gameId);
      if (forGame === undefined) return;
      for (const event of events) {
        for (const listener of [...forGame]) {
          try {
            listener(event);
          } catch (error) {
            // A broken toast must not stop the battle log from rendering,
            // and must never propagate into the socket's message handler —
            // which is the call stack this runs on.
            console.error("narration listener threw", error);
          }
        }
      }
    },
    subscribe(gameId, listener) {
      const forGame = listeners.get(gameId) ?? new Set();
      forGame.add(listener);
      listeners.set(gameId, forGame);
      return () => {
        forGame.delete(listener);
        if (forGame.size === 0) listeners.delete(gameId);
      };
    },
  };
}
```

- [ ] **Step 5: Write the dispatcher**

`frontend/src/app/dispatcher.ts`:

```typescript
import type { QueryClient } from "@tanstack/react-query";
import { gameKey, lobbyKey } from "@/entities/game";
import { type GameSnapshot, type ServerMessage, parseClientEvent } from "@/shared/api";
import type { EventBus } from "./event-bus";

/**
 * §9.3's merge rule, and the only one in the application.
 *
 * Both paths into `["game", id]` — this, and the REST `queryFn` that paints
 * first — call it, so there is exactly one place where two versions of a game
 * are compared and exactly one answer to "which is newer". `>=` rather than
 * `>` on purpose: a resync after a reconnect can legitimately carry the seq
 * the cache already holds, and it must still land, because the *state* may
 * have been rebuilt while the seq stood still.
 */
export function writeGame(queryClient: QueryClient, gameId: string, incoming: GameSnapshot): void {
  queryClient.setQueryData<GameSnapshot>(gameKey(gameId), (previous) =>
    previous === undefined || incoming.seq >= previous.seq ? incoming : previous,
  );
}

/**
 * The ws→cache dispatcher §9.4 puts in `app/` — the one module allowed to
 * know both that a socket exists and that a cache does.
 *
 * Spec 1B §8.2's gap rule, verbatim:
 *
 *     base_seq == last_seq            apply state, emit narration events
 *     seq <= last_seq                 duplicate — ignore
 *     seq > last_seq, base mismatch   apply full state, suppress events
 *
 * `last_seq` is not a variable here: it is `cache[gameKey(id)].seq`. Keeping
 * it in a `Map` beside the cache would create two facts that can disagree —
 * and they would, the first time a REST first paint landed between two
 * updates. Deriving it means the REST race and the gap rule are settled by
 * the same number.
 */
export function createDispatcher(deps: { queryClient: QueryClient; bus: EventBus }) {
  const { queryClient, bus } = deps;

  function lastSeq(gameId: string): number | null {
    return queryClient.getQueryData<GameSnapshot>(gameKey(gameId))?.seq ?? null;
  }

  return {
    handle(message: ServerMessage): void {
      switch (message.type) {
        case "game.snapshot":
          // A snapshot is the truth as of `seq` and narrates nothing: §8.5's
          // recovery is "take a fresh state", not "replay what you missed".
          writeGame(queryClient, message.game_id, { seq: message.seq, state: message.state });
          return;

        case "game.update": {
          const last = lastSeq(message.game_id);
          if (last !== null && message.seq <= last) return; // duplicate

          const contiguous = last !== null && message.base_seq === last;
          writeGame(queryClient, message.game_id, { seq: message.seq, state: message.state });
          if (!contiguous) {
            // §8.2: because every update carries full state, a gap costs an
            // animation, not correctness — and does not require a resync.
            return;
          }
          const narration = message.events
            .map((event: unknown) => parseClientEvent(event))
            .filter((event): event is NonNullable<typeof event> => event !== null);
          bus.emit(message.game_id, narration);
          return;
        }

        case "lobby.snapshot":
        case "lobby.update":
          queryClient.setQueryData(lobbyKey(), message.games);
          return;

        case "hello":
        case "pong":
        case "game.presence":
        case "error":
          // Presence is rendered from a subscription of its own (Task 14) and
          // errors are correlated by `command_id` at the call site (Task 13).
          // Neither is state, and neither belongs in a cache.
          return;
      }
    },
  };
}
```

- [ ] **Step 6: Run until green**

Run: `cd frontend && pnpm test src/app/dispatcher.test.ts`
Expected: all 22 PASS.

- [ ] **Step 7: Add the one-writer lint gate**

The constraint "no component, hook, feature or entity writes `["game", id]`" is worth more than a comment. Add to `frontend/biome.json`'s linter block:

```json
    "rules": {
      "recommended": true,
      "suspicious": { "noExplicitAny": "error" },
      "nursery": {
        "noRestrictedImports": {
          "level": "error",
          "options": {
            "paths": {
              "@/app/dispatcher": "writeGame is app/'s. If a screen needs to change a game, send a command and let the server answer."
            }
          }
        }
      }
    }
```

That rule is scoped off for `app/` itself with a second `frontend/src/app/biome.json` override, or — simpler and what this plan does — by an `overrides` entry in the root config:

```json
  "overrides": [
    { "includes": ["src/app/**"], "linter": { "rules": { "nursery": { "noRestrictedImports": "off" } } } }
  ]
```

Run `pnpm check` and confirm it is clean. Then prove the gate bites: temporarily add `import { writeGame } from "@/app/dispatcher";` to `src/entities/game/model/keys.ts`, re-run `pnpm check`, confirm it fails with that message, and remove it. A gate nobody has seen fail is a gate nobody knows works.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/app frontend/src/shared/api/messages.ts frontend/biome.json
git commit -m "feat(frontend): the dispatcher, its gap rule, and a bus that cannot reach the cache"
```

---

## Task 8: The shell — providers, the guard, the socket, and the two things that tell you it broke

**Files:**
- Create: `frontend/src/app/query-client.ts`, `frontend/src/app/socket-provider.tsx`, `frontend/src/app/use-game-subscription.ts`, `frontend/src/app/error-boundary.tsx`, `frontend/src/app/socket-status.tsx`, `frontend/src/app/providers.tsx`, `frontend/src/main.tsx`
- Create: `frontend/src/app/routes/{__root.tsx,_authed.tsx}`
- Create: `frontend/src/entities/game/api/me.ts` (the `["me"]` query options)
- Create: `frontend/testing/render.tsx`
- Create: `frontend/src/app/use-game-subscription.test.tsx`, `frontend/src/app/socket-provider.test.tsx`

**A correction to the File Structure block:** the authenticated screens sit under a pathless layout route, so the guard is written once. The route files are `__root.tsx`, `login.tsx`, `redeem.tsx`, `_authed.tsx`, `_authed.index.tsx` (the lobby, Task 10) and `_authed.games.$gameId.tsx` (the game, Task 12).

**Interfaces:**
- Consumes: `createSocketClient`, `SocketStatus` from `@/shared/api`; `createDispatcher`, `createEventBus`, `EventBus` from `./dispatcher` / `./event-bus`; `meKey` from `@/entities/game`.
- Produces:
  - `createQueryClient(): QueryClient` — §9.3's defaults.
  - `meQueryOptions()` — `{ queryKey: ["me"], queryFn }`, used by the guard and by the header.
  - `<Providers>`, `<AppErrorBoundary>`, `<SocketStatusBanner>`.
  - `useSocket()` → `{ send, status, offsetMs, subscribeToEvents }`.
  - `useGameSubscription(gameId: string): void` — refcounted; subscribes on first mount, unsubscribes on last unmount, resyncs on reconnect.
  - `renderWithApp(ui, options?)` from `testing/render.tsx`.

- [ ] **Step 1: The query client and the `me` query**

`frontend/src/app/query-client.ts`:

```typescript
import { QueryClient } from "@tanstack/react-query";
import { ApiFetchError } from "@/shared/api";

/**
 * §9.3's defaults, in the one place they can be true everywhere.
 *
 * `staleTime: Infinity` and both refetch switches off because the socket is
 * the refresh mechanism. A background refetch racing a `game.update` is
 * exactly the race `writeGame`'s seq comparison exists to survive — but the
 * cheapest way to survive a race is not to start one.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: Number.POSITIVE_INFINITY,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        retry: (failureCount, error) => {
          // Retrying a refusal is noise: the server has answered, and it will
          // answer the same way. Only a transport failure is worth a retry,
          // and only twice.
          if (error instanceof ApiFetchError && error.kind === "envelope") return false;
          return failureCount < 2;
        },
      },
      mutations: { retry: false },
    },
  });
}
```

`frontend/src/entities/game/api/me.ts`:

```typescript
import { queryOptions } from "@tanstack/react-query";
import { apiFetch, meSchema } from "@/shared/api";
import { meKey } from "../model/keys";

export function meQueryOptions() {
  return queryOptions({
    queryKey: meKey(),
    queryFn: () => apiFetch("/api/auth/me", meSchema),
    // A 401 here is the answer, not a failure to retry.
    retry: false,
  });
}
```

Export it from `entities/game/index.ts`.

- [ ] **Step 2: The socket provider**

`frontend/src/app/socket-provider.tsx`:

```typescript
import { useQueryClient } from "@tanstack/react-query";
import {
  type ReactNode,
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { meKey } from "@/entities/game";
import {
  type ClientFrame,
  type Narration,
  type SocketClient,
  type SocketStatus,
  createSocketClient,
} from "@/shared/api";
import { createDispatcher } from "./dispatcher";
import { type EventBus, createEventBus } from "./event-bus";

interface SocketContextValue {
  send(frame: ClientFrame): void;
  status: SocketStatus;
  offsetMs(): number;
  bus: EventBus;
  client: SocketClient | null;
}

const SocketContext = createContext<SocketContextValue | null>(null);

/**
 * §8.1's one multiplexed socket per tab.
 *
 * It is opened once, above the router, and lives for the whole signed-in
 * session — navigating lobby → game → lobby must not reconnect, because a
 * reconnect costs a full resync of every topic and a visible hitch.
 *
 * `4401` is the one close code that changes who you are: the session is gone,
 * so `["me"]` is wrong and the guard has to see that. Clearing the cache
 * entry is enough — the next render sends the guard to `/login`.
 */
export function SocketProvider({
  children,
  enabled,
  client: injected,
}: {
  children: ReactNode;
  enabled: boolean;
  client?: SocketClient;
}) {
  const queryClient = useQueryClient();
  const bus = useMemo(() => createEventBus(), []);
  const dispatcher = useMemo(() => createDispatcher({ queryClient, bus }), [queryClient, bus]);
  const [client, setClient] = useState<SocketClient | null>(injected ?? null);
  const [status, setStatus] = useState<SocketStatus>(injected?.status() ?? "closed");

  useEffect(() => {
    if (injected !== undefined || !enabled) return;
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
    const socket = createSocketClient({ url });
    setClient(socket);
    return () => {
      socket.close();
      setClient(null);
    };
  }, [enabled, injected]);

  useEffect(() => {
    if (client === null) return;
    setStatus(client.status());
    const offMessage = client.onMessage((message) => dispatcher.handle(message));
    const offStatus = client.onStatus((next, closed) => {
      setStatus(next);
      if (closed?.code === 4401) queryClient.setQueryData(meKey(), null);
    });
    return () => {
      offMessage();
      offStatus();
    };
  }, [client, dispatcher, queryClient]);

  const value = useMemo<SocketContextValue>(
    () => ({
      send: (frame) => client?.send(frame),
      status,
      offsetMs: () => client?.offsetMs() ?? 0,
      bus,
      client,
    }),
    [client, status, bus],
  );

  return <SocketContext.Provider value={value}>{children}</SocketContext.Provider>;
}

export function useSocket(): SocketContextValue {
  const value = useContext(SocketContext);
  if (value === null) throw new Error("useSocket outside SocketProvider");
  return value;
}

/** Narration for one game. Components never touch the bus directly. */
export function useNarration(gameId: string, listener: (event: Narration) => void): void {
  const { bus } = useSocket();
  const stable = useRef(listener);
  stable.current = listener;
  useEffect(() => bus.subscribe(gameId, (event) => stable.current(event)), [bus, gameId]);
}
```

- [ ] **Step 3: The refcounted subscription**

`frontend/src/app/use-game-subscription.ts`:

```typescript
import { useEffect } from "react";
import { useSocket } from "./socket-provider";

/**
 * §9.4's refcounted subscription. Two widgets on one screen both wanting the
 * game must produce one `subscribe`, and the last of them to unmount must
 * produce the `unsubscribe` — a naive per-component effect would unsubscribe
 * the whole tab the moment any one of them re-rendered out.
 *
 * The refcount is module-level rather than per-provider because it counts
 * subscriptions on *the* socket, of which §8.1 says there is one.
 *
 * §8.5's reconnect: on reopen the server has forgotten every subscription, so
 * every held topic is re-`subscribe`d — which already answers with a fresh
 * snapshot. `resync` is reserved for a socket that is still open but whose
 * client believes it has desynced (§11.7's "one resolution: take a fresh
 * snapshot"), and is exposed separately as `resyncGame`.
 */
const counts = new Map<string, number>();

export function useGameSubscription(gameId: string): void {
  const { send, client, status } = useSocket();

  useEffect(() => {
    const topic = `game:${gameId}`;
    const held = counts.get(topic) ?? 0;
    counts.set(topic, held + 1);
    if (held === 0) send({ type: "subscribe", topic });

    return () => {
      const remaining = (counts.get(topic) ?? 1) - 1;
      if (remaining <= 0) {
        counts.delete(topic);
        send({ type: "unsubscribe", topic });
      } else {
        counts.set(topic, remaining);
      }
    };
  }, [gameId, send]);

  // Re-subscribe after a reconnect. `status` transitioning back to "open"
  // with a live refcount is exactly the moment the server knows nothing
  // about this tab's topics.
  useEffect(() => {
    if (status !== "open" || client === null) return;
    const topic = `game:${gameId}`;
    if ((counts.get(topic) ?? 0) > 0) send({ type: "subscribe", topic });
  }, [status, client, gameId, send]);
}

/** §11.7: any client-side desync has exactly one resolution. */
export function useResyncGame(gameId: string): () => void {
  const { send } = useSocket();
  return () => send({ type: "resync", topic: `game:${gameId}` });
}
```

- [ ] **Step 4: The two things that tell you it broke**

`frontend/src/app/error-boundary.tsx`:

```typescript
import { Component, type ErrorInfo, type ReactNode } from "react";
import { ApiFetchError } from "@/shared/api";
import { Banner, Button } from "@/shared/ui";

interface State {
  error: Error | null;
}

/**
 * §11.7: an error boundary per route.
 *
 * It shows the server's code when there is one and says "something broke"
 * when there is not — never a stack, and never a `code` this client invented
 * (decision 2). The only offered action is to reload, because every other
 * recovery this app has is "take a fresh snapshot", and by the time a render
 * has thrown, the component that would do that is gone.
 */
export class AppErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("route boundary caught", error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (error === null) return this.props.children;
    const code = error instanceof ApiFetchError && error.code !== null ? error.code : undefined;
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-6 p-8">
        <Banner tone="bad" code={code}>
          {error instanceof ApiFetchError ? error.message : "Something broke on this screen."}
        </Banner>
        <Button onClick={() => window.location.reload()}>Reload</Button>
      </div>
    );
  }
}
```

`frontend/src/app/socket-status.tsx`:

```typescript
import { Banner } from "@/shared/ui";
import { useSocket } from "./socket-provider";

/**
 * §11.7's socket-status banner. Silent while the socket is open — a
 * permanent "connected" badge trains people to ignore the strip the one
 * evening it says something else.
 */
export function SocketStatusBanner() {
  const { status } = useSocket();
  if (status === "open") return null;
  if (status === "closed") {
    return <Banner tone="quiet">Not connected. Reload to rejoin.</Banner>;
  }
  return <Banner tone="warn">Reconnecting — the board may be a moment behind.</Banner>;
}
```

- [ ] **Step 5: Providers, the routes, and the entry point**

`frontend/src/app/providers.tsx`:

```typescript
import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { meQueryOptions } from "@/entities/game";
import { AppErrorBoundary } from "./error-boundary";
import { SocketProvider } from "./socket-provider";

/** The socket is opened only once there is a session to open it with — an
 *  unauthenticated `/ws` handshake is closed with 4401 by the server, and
 *  reconnect-storming the login screen is not a good look. */
function SocketWhenSignedIn({ children }: { children: ReactNode }) {
  const me = useQuery(meQueryOptions());
  return <SocketProvider enabled={me.data != null}>{children}</SocketProvider>;
}

export function Providers({ queryClient, children }: { queryClient: QueryClient; children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <AppErrorBoundary>
        <SocketWhenSignedIn>{children}</SocketWhenSignedIn>
      </AppErrorBoundary>
    </QueryClientProvider>
  );
}
```

`frontend/src/app/routes/__root.tsx`:

```typescript
import type { QueryClient } from "@tanstack/react-query";
import { Outlet, createRootRouteWithContext } from "@tanstack/react-router";

export interface RouterContext {
  queryClient: QueryClient;
}

export const Route = createRootRouteWithContext<RouterContext>()({
  component: () => <Outlet />,
});
```

`frontend/src/app/routes/_authed.tsx` — §9.4's guard, written once:

```typescript
import { Outlet, createFileRoute, redirect } from "@tanstack/react-router";
import { meQueryOptions } from "@/entities/game";
import { ApiFetchError } from "@/shared/api";
import { SocketStatusBanner } from "../socket-status";

export const Route = createFileRoute("/_authed")({
  beforeLoad: async ({ context, location }) => {
    try {
      await context.queryClient.ensureQueryData(meQueryOptions());
    } catch (error) {
      if (error instanceof ApiFetchError && error.isUnauthenticated) {
        throw redirect({ to: "/login", search: { next: location.href } });
      }
      throw error;
    }
  },
  component: () => (
    <>
      <SocketStatusBanner />
      <Outlet />
    </>
  ),
});
```

`frontend/src/main.tsx`:

```typescript
import { RouterProvider, createRouter } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Providers } from "./app/providers";
import { createQueryClient } from "./app/query-client";
import { routeTree } from "./app/routes/routeTree.gen";
import "./styles.css";

const queryClient = createQueryClient();
const router = createRouter({ routeTree, context: { queryClient }, defaultPreload: "intent" });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const root = document.getElementById("root");
if (root === null) throw new Error("no #root");

createRoot(root).render(
  <StrictMode>
    <Providers queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>
  </StrictMode>,
);
```

- [ ] **Step 6: The test renderer every later task uses**

`frontend/testing/render.tsx`:

```typescript
import { QueryClientProvider } from "@tanstack/react-query";
import { type RenderResult, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { createQueryClient } from "@/app/query-client";
import { SocketProvider } from "@/app/socket-provider";
import { type SocketClient, createSocketClient } from "@/shared/api";
import { fakeSocketFactory } from "./fake-socket";

export interface AppHarness extends RenderResult {
  queryClient: ReturnType<typeof createQueryClient>;
  socket: ReturnType<typeof fakeSocketFactory>;
  client: SocketClient;
}

/**
 * Every component test renders through the real providers, so a component
 * that quietly depends on one fails here rather than in a browser. The socket
 * is real — `createSocketClient` with a fake transport — so the dispatcher,
 * the schemas and the frame encoding are all exercised rather than mocked
 * away, which is where the bugs actually are.
 */
export function renderWithApp(ui: ReactNode, options: { seed?: (harness: Omit<AppHarness, keyof RenderResult>) => void } = {}): AppHarness {
  const queryClient = createQueryClient();
  const socket = fakeSocketFactory();
  const client = createSocketClient({ url: "/ws", socketFactory: socket.factory });
  options.seed?.({ queryClient, socket, client });

  const result = render(
    <QueryClientProvider client={queryClient}>
      <SocketProvider enabled client={client}>
        {ui}
      </SocketProvider>
    </QueryClientProvider>,
  );
  return { ...result, queryClient, socket, client };
}
```

- [ ] **Step 7: Write the tests**

`frontend/src/app/use-game-subscription.test.tsx`:

```typescript
import { act } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { renderWithApp } from "../../testing/render";
import { useGameSubscription } from "./use-game-subscription";

function Watcher({ gameId }: { gameId: string }) {
  useGameSubscription(gameId);
  return null;
}

describe("useGameSubscription", () => {
  it("subscribes once for two components watching the same game", () => {
    const harness = renderWithApp(
      <>
        <Watcher gameId="g1" />
        <Watcher gameId="g1" />
      </>,
    );
    act(() => harness.socket.last().open());
    const subscribes = harness.socket.last().frames().filter((f) => f.type === "subscribe");
    expect(subscribes).toEqual([{ type: "subscribe", topic: "game:g1" }]);
  });

  it("unsubscribes only when the last watcher unmounts", () => {
    const harness = renderWithApp(
      <>
        <Watcher gameId="g1" />
        <Watcher gameId="g1" />
      </>,
    );
    act(() => harness.socket.last().open());
    harness.rerender(<Watcher gameId="g1" />);
    expect(harness.socket.last().frames().some((f) => f.type === "unsubscribe")).toBe(false);
    harness.unmount();
    expect(harness.socket.last().frames().at(-1)).toEqual({ type: "unsubscribe", topic: "game:g1" });
  });

  it("re-subscribes after a reconnect, because the server forgot", () => {
    const harness = renderWithApp(<Watcher gameId="g1" />);
    act(() => harness.socket.last().open());
    act(() => harness.socket.last().serverClose(1006));
    act(() => {
      harness.socket.created.at(-1)?.open();
    });
    const subscribes = harness.socket
      .last()
      .frames()
      .filter((f) => f.type === "subscribe");
    expect(subscribes.length).toBeGreaterThanOrEqual(1);
  });
});
```

`frontend/src/app/socket-provider.test.tsx`:

```typescript
import { act } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { meKey } from "@/entities/game";
import { gameKey } from "@/entities/game";
import type { GameSnapshot } from "@/shared/api";
import { snapshot } from "../../testing/factories";
import { renderWithApp } from "../../testing/render";
import { SocketStatusBanner } from "./socket-status";

describe("SocketProvider", () => {
  it("routes an incoming snapshot into the cache through the dispatcher", () => {
    const harness = renderWithApp(<SocketStatusBanner />);
    act(() => harness.socket.last().open());
    act(() =>
      harness.socket.last().deliver({
        type: "game.snapshot",
        game_id: "g1",
        seq: 4,
        state: snapshot(4).state,
      }),
    );
    expect(harness.queryClient.getQueryData<GameSnapshot>(gameKey("g1"))?.seq).toBe(4);
  });

  it("clears the session when the socket is closed with 4401", () => {
    const harness = renderWithApp(<SocketStatusBanner />, {
      seed: ({ queryClient }) => queryClient.setQueryData(meKey(), { user_id: "u1" }),
    });
    act(() => harness.socket.last().open());
    act(() => harness.socket.last().serverClose(4401));
    expect(harness.queryClient.getQueryData(meKey())).toBeNull();
  });

  it("says nothing while the socket is open and speaks up when it is not", () => {
    const harness = renderWithApp(<SocketStatusBanner />);
    act(() => harness.socket.last().open());
    expect(harness.queryByRole("status")).toBeNull();
    act(() => harness.socket.last().serverClose(1006));
    expect(harness.getByRole("status")).toHaveTextContent("Reconnecting");
  });
});
```

- [ ] **Step 8: Run, then build**

```bash
cd frontend && pnpm test src/app && pnpm check && pnpm build
```
Expected: tests pass, `steiger` clean, `tsc --noEmit` clean, and `vite build` succeeds — which is the first proof the router's generated `routeTree.gen.ts` and the entry point actually line up.

- [ ] **Step 9: Commit**

```bash
git add frontend/src frontend/testing
git commit -m "feat(frontend): the shell — one socket, one guard, one boundary"
```

---

## Task 9: Getting in — sign in, and redeem an invite

There is no open sign-up (§10.1): an invite code is the only way in, and it is spent once. `redeemRequest`'s `extra="forbid"` is load-bearing on the server because the field the body must never carry is `role`; the client's half is that it sends exactly the four fields and validates them before it does.

**Files:**
- Create: `frontend/src/features/sign-in/{model/use-sign-in.ts,ui/sign-in-form.tsx,index.ts}`
- Create: `frontend/src/features/redeem-invite/{model/use-redeem.ts,ui/redeem-form.tsx,index.ts}`
- Create: `frontend/src/pages/login/{ui/login-page.tsx,index.ts}`, `frontend/src/pages/redeem/{ui/redeem-page.tsx,index.ts}`
- Create: `frontend/src/app/routes/{login.tsx,redeem.tsx}`
- Create: `frontend/src/features/sign-in/ui/sign-in-form.test.tsx`, `frontend/src/features/redeem-invite/ui/redeem-form.test.tsx`

**Interfaces:**
- Consumes: `loginRequestSchema`, `redeemRequestSchema`, `meSchema`, `apiSend`, `ApiFetchError`; `meKey`; `Button`, `Field`, `Banner`.
- Produces: `useSignIn()` and `useRedeem()` — TanStack mutations returning `{ mutate, isPending, error }`; `<SignInForm onDone />`, `<RedeemForm onDone />`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/features/sign-in/ui/sign-in-form.test.tsx`:

```typescript
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { screen, waitFor } from "@testing-library/react";
import { server } from "../../../../testing/msw";
import { renderWithApp } from "../../../../testing/render";
import { meKey } from "@/entities/game";
import { SignInForm } from "./sign-in-form";

const ME = { user_id: "u1", username: "alexey", display_name: "Alexey", role: "player" };

describe("SignInForm", () => {
  it("posts the two fields and puts the user in the cache", async () => {
    let body: unknown = null;
    server.use(
      http.post("/api/auth/login", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(ME);
      }),
    );
    const onDone = vi.fn();
    const harness = renderWithApp(<SignInForm onDone={onDone} />);
    await userEvent.type(screen.getByLabelText("USERNAME"), "alexey");
    await userEvent.type(screen.getByLabelText("PASSWORD"), "hunter2hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(body).toEqual({ username: "alexey", password: "hunter2hunter2" });
    expect(harness.queryClient.getQueryData(meKey())).toEqual(ME);
  });

  it("shows the server's message and code when the credentials are refused", async () => {
    server.use(
      http.post("/api/auth/login", () =>
        HttpResponse.json(
          { code: "credentials_invalid", message: "invalid username or password", details: null },
          { status: 401 },
        ),
      ),
    );
    renderWithApp(<SignInForm onDone={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("USERNAME"), "alexey");
    await userEvent.type(screen.getByLabelText("PASSWORD"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    expect(await screen.findByRole("status")).toHaveTextContent("invalid username or password");
    expect(screen.getByRole("status")).toHaveTextContent("credentials_invalid");
  });

  it("says the server could not be reached rather than inventing a code", async () => {
    server.use(http.post("/api/auth/login", () => HttpResponse.error()));
    renderWithApp(<SignInForm onDone={vi.fn()} />);
    await userEvent.type(screen.getByLabelText("USERNAME"), "alexey");
    await userEvent.type(screen.getByLabelText("PASSWORD"), "hunter2hunter2");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

    const banner = await screen.findByRole("status");
    expect(banner).toHaveTextContent("could not be reached");
    expect(banner.textContent).not.toMatch(/credentials_invalid|unauthenticated/);
  });

  it("does not submit an empty form", async () => {
    const calls = vi.fn();
    server.use(http.post("/api/auth/login", () => { calls(); return HttpResponse.json(ME); }));
    renderWithApp(<SignInForm onDone={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(calls).not.toHaveBeenCalled();
  });
});
```

`frontend/src/features/redeem-invite/ui/redeem-form.test.tsx`:

```typescript
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";
import { server } from "../../../../testing/msw";
import { renderWithApp } from "../../../../testing/render";
import { RedeemForm } from "./redeem-form";

const ME = { user_id: "u2", username: "petra.k", display_name: "Petra", role: "player" };

async function fill(overrides: Partial<Record<string, string>> = {}) {
  await userEvent.type(screen.getByLabelText("INVITE CODE"), overrides.code ?? "7QK4M2XD9RTP");
  await userEvent.type(screen.getByLabelText("USERNAME"), overrides.username ?? "petra.k");
  await userEvent.type(screen.getByLabelText("DISPLAY NAME"), overrides.display ?? "Petra");
  await userEvent.type(screen.getByLabelText("PASSWORD"), overrides.password ?? "longenough1");
}

describe("RedeemForm", () => {
  it("sends exactly the four fields the contract declares", async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post("/api/auth/redeem", async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(ME, { status: 201 });
      }),
    );
    const onDone = vi.fn();
    renderWithApp(<RedeemForm onDone={onDone} />);
    await fill();
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(Object.keys(body).sort()).toEqual(["code", "display_name", "password", "username"]);
  });

  it("refuses a short password before it reaches the network", async () => {
    const calls = vi.fn();
    server.use(http.post("/api/auth/redeem", () => { calls(); return HttpResponse.json(ME, { status: 201 }); }));
    renderWithApp(<RedeemForm onDone={vi.fn()} />);
    await fill({ password: "short" });
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    expect(await screen.findByText(/at least 8/i)).toBeInTheDocument();
    expect(calls).not.toHaveBeenCalled();
  });

  it("refuses a username the contract's pattern rejects", async () => {
    renderWithApp(<RedeemForm onDone={vi.fn()} />);
    await fill({ username: "petra k!" });
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    expect(await screen.findByText(/letters, digits/i)).toBeInTheDocument();
  });

  it("surfaces invite_invalid from the server", async () => {
    server.use(
      http.post("/api/auth/redeem", () =>
        HttpResponse.json({ code: "invite_invalid", message: "invite code is not usable", details: null }, { status: 401 }),
      ),
    );
    renderWithApp(<RedeemForm onDone={vi.fn()} />);
    await fill();
    await userEvent.click(screen.getByRole("button", { name: /create account/i }));
    expect(await screen.findByRole("status")).toHaveTextContent("invite code is not usable");
  });
});
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd frontend && pnpm test src/features`
Expected: FAIL — neither form exists.

- [ ] **Step 3: Write the mutations**

`frontend/src/features/sign-in/model/use-sign-in.ts`:

```typescript
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { meKey } from "@/entities/game";
import { type LoginRequest, apiSend, meSchema } from "@/shared/api";

export function useSignIn(onDone: () => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: LoginRequest) => apiSend("/api/auth/login", meSchema, body),
    onSuccess: (me) => {
      // Seed rather than invalidate: the response *is* `/api/auth/me`'s body,
      // and a refetch here would race the socket opening on the next render.
      queryClient.setQueryData(meKey(), me);
      onDone();
    },
  });
}
```

`frontend/src/features/redeem-invite/model/use-redeem.ts` is the same shape against `/api/auth/redeem` with `RedeemRequest`.

- [ ] **Step 4: Write the forms**

`frontend/src/features/sign-in/ui/sign-in-form.tsx`:

```typescript
import { useForm } from "@tanstack/react-form";
import { ApiFetchError, loginRequestSchema } from "@/shared/api";
import { Banner, Button, Field } from "@/shared/ui";
import { useSignIn } from "../model/use-sign-in";

export function SignInForm({ onDone }: { onDone: () => void }) {
  const signIn = useSignIn(onDone);
  const form = useForm({
    defaultValues: { username: "", password: "" },
    // The generated schema is the validator. Writing `minLength: 1` here by
    // hand would be a second copy of a rule the server already owns, and the
    // two would drift the first time the contract moved.
    validators: { onSubmit: loginRequestSchema },
    onSubmit: ({ value }) => signIn.mutateAsync(value).catch(() => undefined),
  });

  return (
    <form
      className="flex w-100 flex-col gap-5 border border-line bg-panel p-8"
      onSubmit={(event) => {
        event.preventDefault();
        void form.handleSubmit();
      }}
    >
      <h1 className="font-display text-3xl tracking-wider">SIGN IN</h1>

      {signIn.error instanceof ApiFetchError && (
        <Banner tone="bad" code={signIn.error.code ?? undefined}>
          {signIn.error.message}
        </Banner>
      )}

      <form.Field name="username">
        {(field) => (
          <Field
            label="Username"
            value={field.state.value}
            onChange={(e) => field.handleChange(e.target.value)}
            error={field.state.meta.errors[0]?.message}
            autoComplete="username"
          />
        )}
      </form.Field>

      <form.Field name="password">
        {(field) => (
          <Field
            label="Password"
            type="password"
            value={field.state.value}
            onChange={(e) => field.handleChange(e.target.value)}
            error={field.state.meta.errors[0]?.message}
            autoComplete="current-password"
          />
        )}
      </form.Field>

      <Button type="submit" disabled={signIn.isPending}>
        {signIn.isPending ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}
```

`frontend/src/features/redeem-invite/ui/redeem-form.tsx` is the same shape with four fields, `validators: { onSubmit: redeemRequestSchema }`, and hints under two of them: `3–32 characters. Letters, digits, dot, dash, underscore.` on username and `At least 8 characters.` on password. Those two strings must match what the contract enforces (`^[A-Za-z0-9._-]+$`, `min_length=8`) because the tests assert on them — but the *enforcement* is the schema's, not the hint's.

- [ ] **Step 5: Write the pages and routes**

`frontend/src/pages/login/ui/login-page.tsx` renders the wordmark panel from the design canvas beside `<SignInForm onDone={...} />` and a link to `/redeem`. The `onDone` navigates to `search.next ?? "/"` using `useNavigate()`.

`frontend/src/app/routes/login.tsx`:

```typescript
import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";
import { LoginPage } from "@/pages/login";

export const Route = createFileRoute("/login")({
  // `next` is a path this app will navigate to, so it is validated as one:
  // an unvalidated `next` is an open redirect, and `redirect({to})` will
  // happily take an absolute URL.
  validateSearch: z.object({ next: z.string().startsWith("/").optional() }),
  component: LoginPage,
});
```

`frontend/src/app/routes/redeem.tsx` is the same shape with no search params.

- [ ] **Step 6: Run until green, then check**

Run: `cd frontend && pnpm test src/features && pnpm check`
Expected: all pass, `steiger` clean (a `feature` importing `@/shared` and `@/entities` is legal; a `feature` importing another `feature` is not, and none does).

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): sign in and redeem, validated by the contract that enforces them"
```

---

## Task 10: The lobby — REST for the first paint, the socket for everything after

§9.3's pattern in its simplest form: `GET /api/games` fills the list, `subscribe: lobby` keeps it filled. The dispatcher already writes `["lobby"]`; this task is the screen and the two commands.

**Files:**
- Create: `frontend/src/entities/game/api/lobby.ts`, `frontend/src/entities/game/api/maps.ts`
- Create: `frontend/src/features/create-game/{model/use-create-game.ts,ui/create-game-panel.tsx,index.ts}`
- Create: `frontend/src/features/join-game/{model/use-join-game.ts,index.ts}`
- Create: `frontend/src/pages/lobby/{ui/lobby-page.tsx,ui/game-row.tsx,index.ts}`
- Create: `frontend/src/app/routes/_authed.index.tsx`
- Create: `frontend/src/pages/lobby/ui/lobby-page.test.tsx`

**Interfaces:**
- Consumes: `lobbyGameSummarySchema`, `createGameRequestSchema`, `gameSnapshotSchema`, `mapSummarySchema`; `lobbyKey`, `gameKey`; `writeGame` (via `app/`, which is why the create/join mutations live in a feature but hand their snapshot to the router rather than to the cache — see below).
- Produces: `lobbyQueryOptions()`, `mapsQueryOptions()`, `useCreateGame()`, `useJoinGame()`, `<LobbyPage>`.

**One thing worth stating before the code:** `POST /api/games` and `POST /api/games/{id}/join` both answer with a `GameSnapshot`. It is tempting to write it into `["game", id]` from the mutation — and that would be a second component writing the game cache, which Task 7's lint gate forbids. The mutation navigates to `/games/{id}` instead, and that route's loader fetches the same snapshot through the one merge rule. The extra round trip costs a few milliseconds on a LAN and removes a writer.

- [ ] **Step 1: Write the query options**

`frontend/src/entities/game/api/lobby.ts`:

```typescript
import { queryOptions } from "@tanstack/react-query";
import { z } from "zod";
import { apiFetch, lobbyGameSummarySchema } from "@/shared/api";
import { lobbyKey } from "../model/keys";

export function lobbyQueryOptions() {
  return queryOptions({
    queryKey: lobbyKey(),
    queryFn: () => apiFetch("/api/games", z.array(lobbyGameSummarySchema)),
  });
}
```

`frontend/src/entities/game/api/maps.ts` is the same against `/api/maps` with `z.array(mapSummarySchema)`, keyed `["maps"]`, plus `mapDetailQueryOptions(mapId)` against `/api/maps/{id}` with `mapDetailSchema`, keyed by `mapKey(mapId)`.

- [ ] **Step 2: Write the failing test**

`frontend/src/pages/lobby/ui/lobby-page.test.tsx`:

```typescript
import { act, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { server } from "../../../../testing/msw";
import { renderWithApp } from "../../../../testing/render";
import { LobbyPage } from "./lobby-page";

const GAME = { game_id: "g1", host_id: "u2", map_id: "czechia", player_count: 2, max_players: 3, status: "lobby" };

function withLobby(games: unknown[] = [GAME]) {
  server.use(
    http.get("/api/games", () => HttpResponse.json(games)),
    http.get("/api/maps", () => HttpResponse.json([{ map_id: "czechia", region_count: 14 }])),
  );
}

describe("LobbyPage", () => {
  it("paints from REST before the socket has said anything", async () => {
    withLobby();
    renderWithApp(<LobbyPage />);
    expect(await screen.findByText("2 / 3")).toBeInTheDocument();
  });

  it("subscribes to the lobby topic on mount", async () => {
    withLobby();
    const harness = renderWithApp(<LobbyPage />);
    act(() => harness.socket.last().open());
    await waitFor(() =>
      expect(harness.socket.last().frames()).toContainEqual({ type: "subscribe", topic: "lobby" }),
    );
  });

  it("updates in place when a lobby.update arrives", async () => {
    withLobby();
    const harness = renderWithApp(<LobbyPage />);
    await screen.findByText("2 / 3");
    act(() => harness.socket.last().open());
    act(() =>
      harness.socket.last().deliver({
        type: "lobby.update",
        games: [{ ...GAME, player_count: 3 }],
      }),
    );
    expect(await screen.findByText("3 / 3")).toBeInTheDocument();
  });

  it("offers Join for a game with room and refuses one that is full", async () => {
    withLobby([GAME, { ...GAME, game_id: "g2", player_count: 3 }]);
    renderWithApp(<LobbyPage />);
    expect(await screen.findByRole("button", { name: "Join" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Full" })).toBeDisabled();
  });

  it("shows an empty state rather than a blank panel", async () => {
    withLobby([]);
    renderWithApp(<LobbyPage />);
    expect(await screen.findByText(/no open games/i)).toBeInTheDocument();
  });

  it("surfaces no_default_preset from create rather than a blank screen", async () => {
    withLobby([]);
    server.use(
      http.post("/api/games", () =>
        HttpResponse.json(
          { code: "no_default_preset", message: "no default preset is configured", details: null },
          { status: 409 },
        ),
      ),
    );
    const { getByRole, findByRole } = renderWithApp(<LobbyPage />);
    await screen.findByText(/no open games/i);
    (await findByRole("button", { name: /create game/i })).click();
    expect(await findByRole("status")).toHaveTextContent("no default preset is configured");
  });
});
```

- [ ] **Step 3: Write the page**

`frontend/src/pages/lobby/ui/lobby-page.tsx` subscribes to the lobby topic, reads `useQuery(lobbyQueryOptions())`, renders a `<GameRow>` per game and the create panel. The subscription is a plain effect — the lobby topic needs no refcount, because exactly one screen ever holds it:

```typescript
useEffect(() => {
  send({ type: "subscribe", topic: "lobby" });
  return () => send({ type: "unsubscribe", topic: "lobby" });
}, [send]);
```

`<GameRow>` renders map, host, a seat pip per `max_players` (filled up to `player_count`), the count, the status chip, and one button: `Join` when `player_count < max_players && status === "lobby"`, `Full` (disabled) when it is not, `Rejoin` when the status is anything else. `<CreateGamePanel>` renders the preset and map selects and the rules readout, all from `mapsQueryOptions()` and the fixed `DEFAULT` label — **preset listing is Plan 7's** (`PresetRepository` is read-only in Plan 5 and there is no `GET /api/presets`), so Plan 6 sends `preset_id: null` and the server picks the default. The panel says so in one line: `Default rules — presets are configurable from the admin screens.`

- [ ] **Step 4: Write the mutations**

```typescript
export function useCreateGame() {
  const navigate = useNavigate();
  return useMutation({
    mutationFn: (body: CreateGameRequest) => apiSend("/api/games", gameSnapshotSchema, body),
    onSuccess: (snapshot) => navigate({ to: "/games/$gameId", params: { gameId: snapshot.state.game_id } }),
  });
}
```

`useJoinGame()` is the same against `/api/games/${gameId}/join`. Neither writes `["game", id]` — see the note above.

- [ ] **Step 5: Run, check, commit**

```bash
cd frontend && pnpm test src/pages/lobby && pnpm check
git add frontend/src && git commit -m "feat(frontend): the lobby, painted by REST and kept fresh by the socket"
```

---

## Task 11: The map — parsed at runtime, re-validated in the browser, coloured by nothing it stores

§8.1 is the most opinionated paragraph in the spec and every clause has a reason. Runtime-parsed inline SVG, so a map stays a two-file drop with no code change and no rebuild. Real React `<path>` elements rather than `dangerouslySetInnerHTML`, so React keeps ownership of fills, strokes and handlers. The same contract enforced here as in Task 1, because the asset is fetched rather than bundled — and a fetched asset is an input.

**Files:**
- Create: `frontend/src/entities/map/model/{parse.ts,parse.test.ts}`, `frontend/src/entities/map/api/map-query.ts`, `frontend/src/entities/map/index.ts`
- Create: `frontend/src/widgets/game-stage/ui/{map-board.tsx,map-board.test.tsx}`, `frontend/src/widgets/game-stage/index.ts`
- Create: `frontend/src/shared/lib/store.ts` (the Zustand store — the five keys and no sixth)
- Create: `frontend/src/shared/lib/store.test.ts`

**Interfaces:**
- Consumes: `mapDetailSchema`, `apiFetch`; `ownershipOf`, `TerritoryView`; `seatVar`.
- Produces:
  - `parseMapSvg(source: string, regionIds: readonly string[]): ParsedMap` — throws `MapContractError` with every problem in its message.
  - `type ParsedMap = { viewBox: string; regions: readonly { id: string; d: string; fillRule?: string; clipRule?: string }[] }`.
  - `mapQueryOptions(mapId)` → the parsed map, cached forever.
  - `<MapBoard state={ClientGameState} onSelect={(regionId) => void} />`.
  - `useBoardStore` — `{ selectedRegionId, mapZoom, mapPan, openPanel, soundEnabled }` and their setters.

- [ ] **Step 1: Write the store, and the test that keeps it honest**

`frontend/src/shared/lib/store.ts`:

```typescript
import { create } from "zustand";

/**
 * §9.2's table, exactly. Five keys, and the "explicitly not" column is the
 * important half: territory owner, score, round, the current question and the
 * timer are *never* here. Every one of them is server state, and a copy of
 * server state in a client store is a copy that will be stale at the worst
 * possible moment.
 */
export interface BoardState {
  selectedRegionId: string | null;
  mapZoom: number;
  mapPan: { x: number; y: number };
  openPanel: "none" | "log" | "rules";
  soundEnabled: boolean;
  select(regionId: string | null): void;
  setZoom(zoom: number): void;
  setPan(pan: { x: number; y: number }): void;
  setPanel(panel: BoardState["openPanel"]): void;
  toggleSound(): void;
}

export const useBoardStore = create<BoardState>((set) => ({
  selectedRegionId: null,
  mapZoom: 1,
  mapPan: { x: 0, y: 0 },
  openPanel: "none",
  soundEnabled: true,
  select: (selectedRegionId) => set({ selectedRegionId }),
  setZoom: (mapZoom) => set({ mapZoom }),
  setPan: (mapPan) => set({ mapPan }),
  setPanel: (openPanel) => set({ openPanel }),
  toggleSound: () => set((state) => ({ soundEnabled: !state.soundEnabled })),
}));
```

Extend `frontend/src/shared/lib/index.ts` — without this, Task 11's `import { useBoardStore } from "@/shared/lib"` does not resolve, and `fsd/no-public-api-sidestep` forbids reaching past the segment's index to `./store` directly:

```typescript
export { cn } from "./cn";
export { invariant } from "./invariant";
export { type BoardState, useBoardStore } from "./store";
```

`frontend/src/shared/lib/store.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { useBoardStore } from "./store";

describe("the board store", () => {
  it("holds exactly the five keys §9.2 allows", () => {
    const state = useBoardStore.getState();
    const data = Object.entries(state)
      .filter(([, value]) => typeof value !== "function")
      .map(([key]) => key)
      .sort();
    // If this fails because you added a key, read §9.2 before you change it:
    // territory owner, score, round, current question and timer are server
    // state and belong in the query cache.
    expect(data).toEqual(["mapPan", "mapZoom", "openPanel", "selectedRegionId", "soundEnabled"]);
  });

  it("clears a selection", () => {
    useBoardStore.getState().select("praha");
    expect(useBoardStore.getState().selectedRegionId).toBe("praha");
    useBoardStore.getState().select(null);
    expect(useBoardStore.getState().selectedRegionId).toBeNull();
  });
});
```

- [ ] **Step 2: Write the parser's failing test**

`frontend/src/entities/map/model/parse.test.ts` — deliberately the same case list as `backend/tests/maps/test_svg_validator.py`, because the claim §8.1 makes is that the two agree:

```typescript
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { MapContractError, parseMapSvg } from "./parse";

const IDS = ["a", "b"];
const GOOD = '<path id="a" d="M0 0h1v1z"/><path id="b" d="M2 2h1v1z"/>';
const svg = (body = GOOD, attrs = 'viewBox="0 0 100 100"') =>
  `<svg xmlns="http://www.w3.org/2000/svg" ${attrs}>${body}</svg>`;

describe("parseMapSvg", () => {
  it("returns the viewBox and one region per path", () => {
    const parsed = parseMapSvg(svg(), IDS);
    expect(parsed.viewBox).toBe("0 0 100 100");
    expect(parsed.regions.map((r) => r.id)).toEqual(["a", "b"]);
    expect(parsed.regions[0]?.d).toBe("M0 0h1v1z");
  });

  it("keeps fill-rule and clip-rule, the only two style attributes allowed", () => {
    const parsed = parseMapSvg(
      svg('<path id="a" d="M0 0z" fill-rule="evenodd" clip-rule="evenodd"/><path id="b" d="M1 1z"/>'),
      IDS,
    );
    expect(parsed.regions[0]?.fillRule).toBe("evenodd");
  });

  it.each([
    ["a script", `${GOOD}<script>alert(1)</script>`],
    ["a foreignObject", `${GOOD}<foreignObject><div/></foreignObject>`],
    ["a use", `${GOOD}<use href="#a"/>`],
    ["an image", `${GOOD}<image href="x.png"/>`],
    ["a style element", `${GOOD}<style>path{fill:red}</style>`],
    ["a group wrapper", `<g>${GOOD}</g>`],
  ])("rejects %s", (_name, body) => {
    expect(() => parseMapSvg(svg(body), IDS)).toThrow(MapContractError);
  });

  it.each(["transform=\"translate(5,5)\"", 'href="#x"', 'style="fill:red"', 'onclick="x()"'])(
    "rejects the disallowed attribute %s",
    (attr) => {
      expect(() => parseMapSvg(svg(`<path id="a" d="M0 0z" ${attr}/><path id="b" d="M1 1z"/>`), IDS)).toThrow(
        /disallowed attribute/,
      );
    },
  );

  it("rejects a missing viewBox", () => {
    expect(() => parseMapSvg(svg(GOOD, ""), IDS)).toThrow(/viewBox/);
  });

  it("rejects an id that map.json does not know", () => {
    expect(() => parseMapSvg(svg(`${GOOD}<path id="c" d="M4 4z"/>`), IDS)).toThrow(/no region/);
  });

  it("rejects a region with no path — a hole in the board is not a board", () => {
    expect(() => parseMapSvg(svg('<path id="a" d="M0 0z"/>'), IDS)).toThrow(/no path/);
  });

  it("rejects duplicate ids", () => {
    expect(() => parseMapSvg(svg('<path id="a" d="M0 0z"/><path id="a" d="M1 1z"/><path id="b" d="M2 2z"/>'), IDS)).toThrow(
      /duplicate/,
    );
  });

  it("rejects a DOCTYPE", () => {
    expect(() => parseMapSvg(`<!DOCTYPE svg>${svg()}`, IDS)).toThrow(MapContractError);
  });

  it("rejects something that is not XML at all", () => {
    expect(() => parseMapSvg("<html><body>404</body></html>", IDS)).toThrow(MapContractError);
  });

  it("reports every problem at once, not the first", () => {
    const bad = svg('<path id="a" d="M0 0z" transform="translate(1,1)"/>', "");
    const error = (() => {
      try {
        parseMapSvg(bad, IDS);
      } catch (e) {
        return e as Error;
      }
      throw new Error("expected a throw");
    })();
    expect(error.message).toMatch(/viewBox/);
    expect(error.message).toMatch(/disallowed attribute/);
    expect(error.message).toMatch(/no path/);
  });

  it("agrees with the Python validator about the shipped map", () => {
    // The claim §8.1 makes is that build time and run time enforce *the same*
    // contract. `backend/tests/maps/test_svg_validator.py` asserts this file
    // passes there; this asserts it passes here. If one of them ever fails
    // alone, the two implementations have drifted and one is wrong.
    const root = resolve(__dirname, "../../../../../data/maps/czechia");
    const map = JSON.parse(readFileSync(resolve(root, "map.json"), "utf8")) as {
      regions: { id: string }[];
    };
    const parsed = parseMapSvg(
      readFileSync(resolve(root, "map.svg"), "utf8"),
      map.regions.map((r) => r.id),
    );
    expect(parsed.regions).toHaveLength(map.regions.length);
    expect(parsed.viewBox).toBeTruthy();
  });
});
```

- [ ] **Step 3: Write the parser**

`frontend/src/entities/map/model/parse.ts`:

```typescript
const SVG_NS = "http://www.w3.org/2000/svg";
const PATH_ATTRS = new Set(["id", "d", "fill-rule", "clip-rule"]);
const ROOT_ATTRS = new Set(["xmlns", "viewBox", "width", "height"]);

export interface ParsedRegion {
  id: string;
  d: string;
  fillRule?: string;
  clipRule?: string;
}

export interface ParsedMap {
  viewBox: string;
  regions: readonly ParsedRegion[];
}

export class MapContractError extends Error {
  constructor(readonly problems: readonly string[]) {
    super(`map.svg does not satisfy the contract:\n- ${problems.join("\n- ")}`);
    this.name = "MapContractError";
  }
}

/**
 * §8.1's contract, enforced in the browser — defence in depth, because the
 * asset is *fetched*: it never passed through a build, and the file the
 * server has today is not necessarily the file the repository gated.
 *
 * `DOMParser` with `image/svg+xml` builds a detached document: nothing is
 * inserted, no script runs, no network fetch is triggered. What comes out is
 * a list of `d` strings that React renders as its own `<path>` elements
 * (§8.1: no `dangerouslySetInnerHTML`, so React keeps ownership of fills,
 * strokes and handlers).
 *
 * It fails closed and reports every problem, because the caller's only
 * sensible reaction is to show a named error rather than a partial board
 * (decision 12), and whoever fixes the map wants the whole list.
 */
export function parseMapSvg(source: string, regionIds: readonly string[]): ParsedMap {
  const problems: string[] = [];

  if (/<!DOCTYPE/i.test(source)) {
    // No DTD, no entities (§8.1). `DOMParser` will not expand external
    // entities, but a DOCTYPE has no legitimate reason to be in a normalized
    // map and refusing it is one line.
    throw new MapContractError(["the file carries a DOCTYPE"]);
  }

  const document = new DOMParser().parseFromString(source, "image/svg+xml");
  if (document.getElementsByTagName("parsererror").length > 0) {
    throw new MapContractError(["the file is not parseable as XML"]);
  }

  const root = document.documentElement;
  if (root.namespaceURI !== SVG_NS || root.localName !== "svg") {
    throw new MapContractError([`the root element is <${root.localName}>, not an SVG <svg>`]);
  }

  const viewBox = root.getAttribute("viewBox");
  if (viewBox === null || viewBox.trim() === "") problems.push("the root <svg> has no viewBox");
  for (const attribute of Array.from(root.attributes)) {
    if (!ROOT_ATTRS.has(attribute.name)) {
      problems.push(`the root <svg> carries a disallowed attribute: ${attribute.name}`);
    }
  }

  const regions: ParsedRegion[] = [];
  const seen: string[] = [];
  for (const child of Array.from(root.children)) {
    if (child.namespaceURI !== SVG_NS || child.localName !== "path") {
      problems.push(`<${child.localName}> is not allowed: every region is a top-level <path>`);
      continue;
    }
    if (child.children.length > 0) {
      problems.push(`<path> has children; the file must be flat`);
    }
    for (const attribute of Array.from(child.attributes)) {
      if (!PATH_ATTRS.has(attribute.name)) {
        problems.push(`<path> carries a disallowed attribute: ${attribute.name}`);
      }
    }
    const id = child.getAttribute("id");
    const d = child.getAttribute("d");
    if (id === null) {
      problems.push("a <path> has no id");
      continue;
    }
    seen.push(id);
    if (d === null || d === "") {
      problems.push(`path "${id}" has no d`);
      continue;
    }
    const fillRule = child.getAttribute("fill-rule");
    const clipRule = child.getAttribute("clip-rule");
    regions.push({
      id,
      d,
      ...(fillRule === null ? {} : { fillRule }),
      ...(clipRule === null ? {} : { clipRule }),
    });
  }

  const duplicates = [...new Set(seen.filter((id, index) => seen.indexOf(id) !== index))].sort();
  if (duplicates.length > 0) problems.push(`duplicate path ids: ${duplicates.join(", ")}`);

  const wanted = new Set(regionIds);
  const got = new Set(seen);
  const missing = [...wanted].filter((id) => !got.has(id)).sort();
  const extra = [...got].filter((id) => !wanted.has(id)).sort();
  if (missing.length > 0) problems.push(`regions with no path: ${missing.join(", ")}`);
  if (extra.length > 0) problems.push(`paths with no region in map.json: ${extra.join(", ")}`);

  if (problems.length > 0) throw new MapContractError(problems);
  return { viewBox: viewBox as string, regions };
}
```

`frontend/src/entities/map/api/map-query.ts`:

```typescript
import { queryOptions } from "@tanstack/react-query";
import { mapKey } from "@/entities/game";
import { apiFetch, mapDetailSchema } from "@/shared/api";
import { type ParsedMap, parseMapSvg } from "../model/parse";

/**
 * Two fetches behind one key: the detail (which names the SVG's URL and the
 * region ids the SVG must match) and the SVG itself. Cached forever — a map
 * is immutable for the life of a game, and `games.map_sha256` is what
 * guarantees that.
 *
 * The SVG is fetched with `fetch`, not `apiFetch`: it is served by Caddy from
 * `data/maps` (Spec 1B §10.2) and is not an API endpoint, so an error from it
 * is not an envelope and must not be parsed as one.
 */
export function mapQueryOptions(mapId: string) {
  return queryOptions({
    queryKey: mapKey(mapId),
    queryFn: async (): Promise<ParsedMap> => {
      const detail = await apiFetch(`/api/maps/${mapId}`, mapDetailSchema);
      const response = await fetch(detail.svg_url);
      if (!response.ok) {
        throw new Error(`map ${mapId}: ${detail.svg_url} answered ${response.status}`);
      }
      return parseMapSvg(await response.text(), detail.regions.map((r) => r.region_id));
    },
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
  });
}
```

- [ ] **Step 4: Write the board**

`frontend/src/widgets/game-stage/ui/map-board.tsx`:

```typescript
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { yourOptions } from "@/entities/game";
import { mapQueryOptions } from "@/entities/map";
import { ownershipOf } from "@/entities/territory";
import type { ClientGameState } from "@/shared/api";
import { seatVar } from "@/shared/config";
import { useBoardStore } from "@/shared/lib";
import { Banner } from "@/shared/ui";

/**
 * §8.1: region appearance is derived, never stored.
 *
 * Fill is `var(--seat-N)` where N is the owner's seat; a free region is a
 * token; an offered region is stroked in gold; everything else during a
 * choosing turn is dimmed. Nothing here holds a colour and nothing here
 * consults a rule — `your_options` is the whole of the client's knowledge
 * about what may be clicked (§8.8).
 */
export function MapBoard({
  state,
  onSelect,
}: {
  state: ClientGameState;
  onSelect: (regionId: string) => void;
}) {
  const map = useQuery(mapQueryOptions(state.map_id));
  const selected = useBoardStore((s) => s.selectedRegionId);
  const ownership = useMemo(() => ownershipOf(state), [state]);
  const options = yourOptions(state);
  const offered = useMemo(
    () => new Set([...options.pick, ...options.attack]),
    [options.pick, options.attack],
  );

  if (map.isPending) {
    return <div className="flex h-full items-center justify-center text-ink-faint">Loading the map…</div>;
  }
  if (map.isError) {
    // Decision 12: fail closed and say which map. A partial board is worse
    // than no board, because a player would act on it.
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Banner tone="bad" code={state.map_id}>
          This map could not be drawn. {(map.error as Error).message}
        </Banner>
      </div>
    );
  }

  return (
    <svg viewBox={map.data.viewBox} className="h-full w-full" role="img" aria-label="Game map">
      <title>Game map</title>
      {map.data.regions.map((region) => {
        const held = ownership.get(region.id);
        const isOffered = offered.has(region.id);
        const isSelected = selected === region.id;
        const fill =
          held?.ownerSeat != null
            ? seatVar(held.ownerSeat)
            : isOffered
              ? "var(--color-line-strong)"
              : "var(--color-region-free)";
        return (
          <path
            key={region.id}
            d={region.d}
            fillRule={region.fillRule as "evenodd" | "nonzero" | undefined}
            clipRule={region.clipRule}
            fill={fill}
            fillOpacity={offered.size > 0 && !isOffered && !isSelected ? 0.35 : 1}
            stroke={isSelected ? "var(--color-ink)" : isOffered ? "var(--color-gold)" : "var(--color-base)"}
            strokeWidth={isSelected || isOffered ? 4 : held?.isBase ? 4 : 2}
            className={isOffered ? "cursor-pointer" : undefined}
            role={isOffered ? "button" : undefined}
            aria-label={region.id}
            aria-disabled={!isOffered}
            onClick={isOffered ? () => onSelect(region.id) : undefined}
          />
        );
      })}
    </svg>
  );
}
```

- [ ] **Step 5: Write the board's test**

`frontend/src/widgets/game-stage/ui/map-board.test.tsx` covers the four claims that matter: a region owned by seat 1 is filled with `var(--seat-1)`; a free region is not; only regions in `your_options` are clickable (`aria-disabled="false"`) and clicking one calls `onSelect`; clicking a region that is *not* offered calls nothing; and a map whose SVG violates the contract renders the named error instead of any `<path>`. Serve the SVG through MSW (`http.get("/maps/czechia/map.svg", ...)`) and the detail through `http.get("/api/maps/czechia", ...)`.

- [ ] **Step 6: Run, check, commit**

```bash
cd frontend && pnpm test src/entities/map src/widgets src/shared/lib && pnpm check
git add frontend/src && git commit -m "feat(frontend): the map, re-validated in the browser and coloured by seat alone"
```

---

## Task 12: The game screen — fixed geometry, a clock that is allowed to be wrong, and the room before the start

§9.5's layout exists to make one promise: the dock's geometry does not depend on whether the question has an image, so nothing shifts at the moment the timer starts. §9.6 exists to make another: the whole match's media is prefetched on entering the game, because an image that begins loading when the timer starts costs a player on a slow connection real seconds and the server will not wait.

**Files:**
- Create: `frontend/src/app/use-media-prefetch.ts`
- Create: `frontend/src/widgets/player-strip/{ui/player-strip.tsx,index.ts}`
- Create: `frontend/src/widgets/game-stage/ui/game-stage.tsx`
- Create: `frontend/src/widgets/turn-dock/{ui/{timer-bar.tsx,turn-dock.tsx},model/use-deadline.ts,index.ts}`
- Create: `frontend/src/pages/game/{ui/{game-page.tsx,room-view.tsx,board-view.tsx},index.ts}`
- Create: `frontend/src/features/start-game/{model/use-start-game.ts,index.ts}`
- Create: `frontend/src/app/routes/_authed.games.$gameId.tsx`
- Create: `frontend/src/widgets/turn-dock/model/use-deadline.test.ts`, `frontend/src/pages/game/ui/game-page.test.tsx`

**Interfaces:**
- Consumes: `gameKey`, `deadlineOf`, `orderedPlayers`, `ownershipOf`, `questionOf`; `useGameSubscription`, `useSocket`; `MapBoard`.
- Produces:
  - `gameQueryOptions(gameId, queryClient)` — §9.3's first paint, merged through `writeGame`.
  - `useDeadline(deadlineAt: string | null)` → `{ remainingMs: number; expired: boolean }`.
  - `useMediaPrefetch(urls: readonly string[])`.
  - `<PlayerStrip>`, `<GameStage>`, `<TimerBar>`, `<GamePage>`.

- [ ] **Step 1: The game query — §9.3's first paint, through the one merge rule**

Add to `frontend/src/entities/game/api/game.ts`:

```typescript
import { type QueryClient, queryOptions } from "@tanstack/react-query";
import { writeGame } from "@/app/dispatcher";
import { type GameSnapshot, apiFetch, gameSnapshotSchema } from "@/shared/api";
import { gameKey } from "../model/keys";
```

**Wait — that import is exactly what Task 7's lint gate forbids, and correctly.** `entities` may not import `app`. The query options therefore live in `app/`, next to the merge rule they use:

`frontend/src/app/game-query.ts`:

```typescript
import { type QueryClient, queryOptions } from "@tanstack/react-query";
import { gameKey } from "@/entities/game";
import { type GameSnapshot, apiFetch, gameSnapshotSchema } from "@/shared/api";
import { writeGame } from "./dispatcher";

/**
 * §9.3: "`["game", id]` has a real `queryFn` — `GET /games/{id}` — returning
 * the same `GameSnapshot` through the same `project_snapshot`. One
 * projection, two transports: the page survives a refresh and renders while
 * the socket is still connecting."
 *
 * The REST response can land *after* a newer socket update. Rather than
 * letting TanStack write whatever it fetched, the fetch is merged through
 * `writeGame` and the query returns what the cache now holds — so there is
 * still exactly one rule deciding which of two versions is newer, and
 * TanStack's own write is a no-op re-set of the same object reference.
 */
export function gameQueryOptions(gameId: string, queryClient: QueryClient) {
  return queryOptions({
    queryKey: gameKey(gameId),
    queryFn: async (): Promise<GameSnapshot> => {
      const fetched = await apiFetch(`/api/games/${gameId}`, gameSnapshotSchema);
      writeGame(queryClient, gameId, fetched);
      return queryClient.getQueryData<GameSnapshot>(gameKey(gameId)) ?? fetched;
    },
  });
}
```

- [ ] **Step 2: The clock, and its failing test**

`frontend/src/widgets/turn-dock/model/use-deadline.test.ts`:

```typescript
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useDeadline } from "./use-deadline";

// The offset the socket would have measured; the hook takes it as an
// argument precisely so this is testable without a socket.
const NO_OFFSET = () => 0;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-08-20T12:00:00.000Z"));
});
afterEach(() => vi.useRealTimers());

describe("useDeadline", () => {
  it("counts down from the server's instant", () => {
    const { result } = renderHook(() => useDeadline("2026-08-20T12:00:20.000Z", NO_OFFSET));
    expect(result.current.remainingMs).toBe(20_000);
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(result.current.remainingMs).toBeLessThanOrEqual(15_000);
  });

  it("applies the measured clock offset, so a skewed laptop still agrees", () => {
    // The client's clock is 3 s behind the server's.
    const { result } = renderHook(() => useDeadline("2026-08-20T12:00:20.000Z", () => 3_000));
    expect(result.current.remainingMs).toBe(17_000);
  });

  it("reports expired at zero and never goes negative", () => {
    const { result } = renderHook(() => useDeadline("2026-08-20T12:00:01.000Z", NO_OFFSET));
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(result.current.remainingMs).toBe(0);
    expect(result.current.expired).toBe(true);
  });

  it("has no deadline and is never expired when the turn has none", () => {
    const { result } = renderHook(() => useDeadline(null, NO_OFFSET));
    expect(result.current.remainingMs).toBe(0);
    expect(result.current.expired).toBe(false);
  });

  it("restarts cleanly when the deadline moves to the next window", () => {
    const { result, rerender } = renderHook(({ at }: { at: string }) => useDeadline(at, NO_OFFSET), {
      initialProps: { at: "2026-08-20T12:00:05.000Z" },
    });
    act(() => vi.advanceTimersByTime(6_000));
    expect(result.current.expired).toBe(true);
    rerender({ at: "2026-08-20T12:00:30.000Z" });
    expect(result.current.expired).toBe(false);
  });
});
```

`frontend/src/widgets/turn-dock/model/use-deadline.ts`:

```typescript
import { useEffect, useRef, useState } from "react";

/**
 * §8.3 of Spec 1B: rendered from `deadline_at` plus the ping/pong offset,
 * driven by `requestAnimationFrame`, disabling input at the locally computed
 * deadline. **Presentation only** — the server's `ctx.now >= deadline_at`
 * stays authoritative, and this hook must never send anything, mark an
 * answer late, or tell the server that time is up.
 *
 * `expired` is therefore a UI affordance: it greys the dock. If the two
 * clocks disagree by 200 ms, the worst case is a player who could have
 * answered being stopped 200 ms early — which is the safe direction, and why
 * the offset is measured rather than assumed.
 */
export function useDeadline(
  deadlineAt: string | null,
  offsetMs: () => number,
): { remainingMs: number; expired: boolean } {
  const [remainingMs, setRemaining] = useState(0);
  const frame = useRef<number | null>(null);

  useEffect(() => {
    if (deadlineAt === null) {
      setRemaining(0);
      return;
    }
    const deadline = Date.parse(deadlineAt);
    const tick = () => {
      const serverNow = Date.now() + offsetMs();
      setRemaining(Math.max(0, deadline - serverNow));
      frame.current = requestAnimationFrame(tick);
    };
    tick();
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = null;
    };
  }, [deadlineAt, offsetMs]);

  return { remainingMs, expired: deadlineAt !== null && remainingMs <= 0 };
}
```

Note for the implementer: `jsdom` does not drive `requestAnimationFrame` from `vi.advanceTimersByTime` unless the fake timers include it. Vitest's `vi.useFakeTimers()` fakes `requestAnimationFrame` by default in recent versions; if the test above hangs or never advances, add `vi.useFakeTimers({ toFake: ["Date", "requestAnimationFrame", "cancelAnimationFrame", "setTimeout", "clearTimeout"] })` rather than replacing rAF with `setInterval` in the hook — the hook's frame pacing is the point.

- [ ] **Step 3: Media prefetch**

`frontend/src/app/use-media-prefetch.ts`:

```typescript
import { useEffect } from "react";

/**
 * §9.6. The whole match's pool is drawn at `GameStarted`, so every image the
 * game will ever show is known on entry and can be in cache before any timer
 * starts. The URLs are content-addressed and opaque (`/api/media/a3f9c1…`),
 * so prefetching them leaks neither question text nor answers.
 *
 * Four at a time: twenty-nine parallel requests on a phone on shared Wi-Fi is
 * how you turn a fairness fix into a fairness problem.
 */
export function useMediaPrefetch(urls: readonly string[]): void {
  useEffect(() => {
    if (urls.length === 0) return;
    let cancelled = false;
    const queue = [...urls];

    const pump = (): void => {
      const url = queue.shift();
      if (url === undefined || cancelled) return;
      const image = new Image();
      image.onload = pump;
      image.onerror = pump; // a missing image must not stall the queue
      image.src = url;
    };
    for (let lane = 0; lane < Math.min(4, queue.length); lane++) pump();

    return () => {
      cancelled = true;
    };
  }, [urls]);
}
```

- [ ] **Step 4: The screen**

`frontend/src/pages/game/ui/game-page.tsx` is the whole of §9.5's geometry, and the geometry is the point:

```typescript
// A stable empty array: `?? []` would allocate a new one every render and
// re-fire `useMediaPrefetch`'s effect on every frame of the timer.
const NO_MEDIA: readonly string[] = [];

/** §9.5: "The question card skeleton reserves the stage height in advance,
 *  so nothing shifts at the moment the timer starts." The skeleton is the
 *  same three boxes at the same three heights as the real screen. */
function GameSkeleton() {
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-base" aria-busy="true">
      <div className="h-24 shrink-0 bg-panel" />
      <div className="h-100 shrink-0 bg-stage" />
      <div className="grow bg-base" />
    </div>
  );
}

function GameError({ error }: { error: unknown }) {
  const failure = error instanceof ApiFetchError ? error : null;
  return (
    <div className="flex h-screen items-center justify-center p-8">
      <Banner tone="bad" code={failure?.code ?? undefined}>
        {failure?.message ?? "This game could not be opened."}
      </Banner>
    </div>
  );
}

export function GamePage({ gameId }: { gameId: string }) {
  const queryClient = useQueryClient();
  useGameSubscription(gameId);
  const game = useQuery(gameQueryOptions(gameId, queryClient));
  useMediaPrefetch(game.data?.state.media_prefetch ?? NO_MEDIA);

  if (game.isPending) return <GameSkeleton />;
  if (game.isError) return <GameError error={game.error} />;

  const state = game.data.state;
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-base text-ink">
      <PlayerStrip state={state} />
      {/* Fixed height, both branches. §9.5: "The dock's geometry does not
          depend on whether the question has an image — only the stage's
          content changes. One render mode, no layout shift." */}
      <GameStage state={state} />
      <TurnDock state={state} />
    </div>
  );
}
```

`<GameStage>` renders the question's `media_url` when a question with an image is open and `<MapBoard>` otherwise — inside a container with a fixed `h-[400px]`, so the two branches cannot differ in height. `<PlayerStrip>` renders `orderedPlayers(state)` with the seat colour as a left border, the base's remaining hit points as pips from `ownershipOf`, the score, and a chip for `you` / answered / eliminated. `<TurnDock>` is Task 13's; for now it renders the phase and the timer.

`frontend/src/pages/game/ui/room-view.tsx` handles `phase === "lobby"`: the seats (including empty ones, up to `rules.player_count`), the rules readout, and one button. Start is offered to **any seated player** — Plan 5's `start_game` authorizes participation, not host-ness — and is disabled with the reason when there are not enough players. The reason is the server's: attempt it and show the `not_enough_players` envelope, rather than reimplementing the count rule on the client.

`frontend/src/features/start-game/model/use-start-game.ts` posts to `/api/games/${gameId}/start`, parses `gameSnapshotSchema`, and — like create and join — does not write the cache: the socket delivers the started state as a `game.update` a moment later, which is the path that must work anyway.

`frontend/src/app/routes/_authed.games.$gameId.tsx`:

```typescript
import { createFileRoute } from "@tanstack/react-router";
import { GamePage } from "@/pages/game";
import { gameQueryOptions } from "../game-query";

export const Route = createFileRoute("/_authed/games/$gameId")({
  loader: ({ context, params }) =>
    context.queryClient.ensureQueryData(gameQueryOptions(params.gameId, context.queryClient)),
  component: () => <GamePage gameId={Route.useParams().gameId} />,
});
```

- [ ] **Step 5: The screen's test**

`frontend/src/pages/game/ui/game-page.test.tsx` asserts:
- the page renders from the REST snapshot before the socket opens (§9.3's whole point);
- a `game.update` arriving over the socket re-renders the score without a refetch (assert `GET /api/games/g1` was called exactly once);
- a REST response that lands *after* a newer socket update does not roll the board back (serve the REST call with a delay, deliver `seq: 9` first, resolve REST with `seq: 5`, assert the rendered round is 9's);
- the stage keeps its height when a media question opens (assert the stage container's class list is identical in both states — the honest DOM-level version of "no layout shift");
- `media_prefetch` produces one `Image()` per URL (spy on `globalThis.Image`);
- `phase: "lobby"` renders the room with an enabled Start for a seated player.

- [ ] **Step 6: Run, check, commit**

```bash
cd frontend && pnpm test src/pages/game src/widgets && pnpm check
git add frontend/src && git commit -m "feat(frontend): the game screen, its fixed geometry, and a clock that only ever disables"
```

---

## Task 13: The four things a player can do

Every command shares one shape: build a frame, send it, mark it pending by `command_id`, and wait for the server to say what happened (decision 10). Nothing is optimistic; nothing is retried.

**Files:**
- Create: `frontend/src/features/pick-region/`, `select-target/`, `submit-answer/`, each with `model/` and `ui/`
- Create: `frontend/src/app/use-command.ts`
- Create: `frontend/src/widgets/question-dock/{ui/{question-dock.tsx,choice-list.tsx,numeric-entry.tsx},index.ts}`
- Create: `frontend/src/app/use-command.test.tsx`, `frontend/src/widgets/question-dock/ui/question-dock.test.tsx`

**Interfaces:**
- Produces: `useCommand()` → `{ send(frame): string; pending: ReadonlySet<string>; failure: { commandId: string; code: ErrorCode; message: string } | null; clear(): void }`; `<QuestionDock state>`, `<ChoiceList>`, `<NumericEntry>`.

- [ ] **Step 1: The command hook**

`frontend/src/app/use-command.ts`:

```typescript
import { useCallback, useEffect, useState } from "react";
import type { ClientFrame, ErrorCode } from "@/shared/api";
import { useSocket } from "./socket-provider";

export interface CommandFailure {
  commandId: string;
  code: ErrorCode;
  message: string;
}

let counter = 0;
const newCommandId = () => `c${++counter}-${Math.random().toString(36).slice(2, 8)}`;

/**
 * Decision 10, in one place.
 *
 * `command_id` is transport correlation and nothing else (§8.3): with several
 * actions in flight the client cannot otherwise tell which one a
 * `REGION_NOT_FREE` belongs to. It is never used to retry — a rejected
 * command is a decision, not a hiccup.
 *
 * A command clears from `pending` when the server answers it: an `error`
 * carrying its id, or any `game.update`, which by definition carries the
 * state the command either did or did not produce. That second clause is
 * deliberately broad — the alternative is inspecting the new state to guess
 * whether *this* command caused it, which is the client re-deriving the
 * server's decision, i.e. exactly what this design refuses to do.
 */
export function useCommand() {
  const { send: rawSend, client } = useSocket();
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const [failure, setFailure] = useState<CommandFailure | null>(null);

  useEffect(() => {
    if (client === null) return;
    return client.onMessage((message) => {
      if (message.type === "error") {
        if (message.command_id !== null) {
          setPending((held) => {
            const next = new Set(held);
            next.delete(message.command_id as string);
            return next;
          });
          setFailure({
            commandId: message.command_id,
            code: message.code,
            message: message.message,
          });
        }
        return;
      }
      if (message.type === "game.update" || message.type === "game.snapshot") {
        setPending(new Set());
      }
    });
  }, [client]);

  const send = useCallback(
    (build: (commandId: string) => ClientFrame): string => {
      const commandId = newCommandId();
      setFailure(null);
      setPending((held) => new Set(held).add(commandId));
      rawSend(build(commandId));
      return commandId;
    },
    [rawSend],
  );

  return { send, pending, failure, clear: () => setFailure(null) };
}
```

- [ ] **Step 2: The three command features**

Each is a hook plus the UI that calls it. `pick-region`:

```typescript
export function usePickRegion(gameId: string, deadlineId: number | null) {
  const { send, pending, failure } = useCommand();
  return {
    pick: (regionId: string) => {
      if (deadlineId === null) return;
      send((command_id) => ({
        type: "pick_region",
        command_id,
        game_id: gameId,
        deadline_id: deadlineId,
        payload: { region_id: regionId },
      }));
    },
    isSending: pending.size > 0,
    failure,
  };
}
```

`select-target` is identical with `type: "select_attack_target"`. `submit-answer` builds the payload from the question's kind:

```typescript
export function useSubmitAnswer(gameId: string, deadlineId: number | null) {
  const { send, pending, failure } = useCommand();
  return {
    answerChoice: (idx: number) =>
      deadlineId !== null &&
      send((command_id) => ({
        type: "submit_answer",
        command_id,
        game_id: gameId,
        deadline_id: deadlineId,
        payload: { kind: "choice", idx },
      })),
    // The value stays a string from the input to the wire. Parsing it to a
    // number here — even to "validate" it — is how `Decimal("0.1")` stops
    // round-tripping, which is why `SubmittedValue.value` is a string in the
    // first place.
    answerNumeric: (value: string) =>
      deadlineId !== null &&
      send((command_id) => ({
        type: "submit_answer",
        command_id,
        game_id: gameId,
        deadline_id: deadlineId,
        payload: { kind: "numeric", value },
      })),
    isSending: pending.size > 0,
    failure,
  };
}
```

`deadline_id` comes from `deadlineIdOf(state)` and is never invented: it is what makes an answer belong to *this* window rather than the one that just closed.

- [ ] **Step 3: The dock**

`<QuestionDock>` renders `questionOf(state)`: category and difficulty, the prompt in Barlow 600, then `<ChoiceList>` for `multiple_choice` and `<NumericEntry>` for `numeric`, then `<TimerBar>`. Its five states come straight from the design canvas's system artboard: untouched, pointed at, yours-and-sent, revealed-correct, and shut. **Revealed-correct is driven by a `question_resolved` narration event, not by the state** — `correct_choice_index` exists only on that event (§8.7), and the pre-resolution DTO has nowhere to put it, which is the guarantee.

Input is disabled when any of: the local clock says `expired`, `yourAnswer(state) !== null`, or a command is in flight. Those three are separate reasons and the dock says which:

```typescript
const reason = expired
  ? "Time is up."
  : yourAnswer(state) !== null
    ? "Answer sent."
    : isSending
      ? "Sending…"
      : null;
```

- [ ] **Step 4: The tests**

`use-command.test.tsx`:
- a sent frame carries a unique `command_id` and the given `deadline_id`;
- an `error` with a matching `command_id` clears that command and surfaces its code;
- an `error` for a *different* `command_id` does not clear this one;
- a `game.update` clears everything pending;
- nothing is ever re-sent (deliver an error, advance timers, assert exactly one frame of that type was sent).

`question-dock.test.tsx`:
- clicking a choice sends `submit_answer` with `kind: "choice"` and the right `idx`;
- typing `0.1` and submitting sends the string `"0.1"`, not a number (`expect(frame.payload.value).toBe("0.1")` and `typeof … === "string"`);
- after the local deadline passes, choices are disabled and no frame is sent;
- with `your_answer` set, choices are disabled and the dock says the answer was sent;
- a `not_your_turn` error renders the server's message;
- before `question_resolved`, no choice carries a correct/incorrect marker — asserted structurally, by querying for the marker's test id and expecting none.

- [ ] **Step 5: Run, check, commit**

```bash
cd frontend && pnpm test src && pnpm check
git add frontend/src && git commit -m "feat(frontend): pick, target, answer — correlated, never optimistic, never retried"
```

---

## Task 14: Full time, surrender, presence, and one whole game

**Files:**
- Create: `frontend/src/widgets/results/{ui/results.tsx,index.ts}`
- Create: `frontend/src/features/surrender/{model/use-surrender.ts,ui/surrender-button.tsx,index.ts}`
- Create: `frontend/src/app/use-presence.ts`
- Create: `frontend/src/pages/game/ui/full-game.test.tsx`

- [ ] **Step 1: Results and surrender**

`<Results>` renders when `phase === "finished"`: the winner from `winner_id`, then every player ranked by `score`, with `bonus_score` shown separately — the client never adds anything up. A "Back to the lobby" link. When `phase === "aborted"`, the same widget says so and names the reason from the `game_aborted` narration event if one arrived, and simply "This game was ended" if the screen was opened afterwards.

`useSurrender(gameId)` sends `{ type: "surrender", command_id, game_id }` — no `deadline_id` and no payload, because surrender is not windowed. The button asks for confirmation once; it is the only irreversible thing a player can do.

- [ ] **Step 2: Presence**

`frontend/src/app/use-presence.ts` holds the last `game.presence` per game in a small `useSyncExternalStore`-backed map fed from the socket. It is deliberately not in the query cache: §8.3 says presence is not a domain event — no `seq`, not persisted, absent from replay — and putting it in the cache next to `GameSnapshot` would be the one place a reader could mistake it for state. The player strip renders a dimmed name for a player who is not connected.

- [ ] **Step 3: One whole game, in one test**

`frontend/src/pages/game/ui/full-game.test.tsx` is the closest this plan gets to Spec 1 §12.4's E2E — no browser, no backend, but the whole client path from a lobby to `FINISHED`:

1. Render the game page for a game in `phase: "lobby"` with two of three seats filled. Assert Start is present and the room is shown.
2. Deliver a `game.update` seating the third player. Assert the strip shows three.
3. Deliver a `game.update` with `phase: "expansion"` and a `expansion_question` turn. Assert the dock shows the prompt and four choices, and the map is on the stage.
4. Click a choice. Assert one `submit_answer` frame with the right `deadline_id`.
5. Deliver an update whose turn carries `your_answer`. Assert the choices are disabled.
6. Deliver an update with an `expansion_picking` turn offering two regions. Assert exactly those two `<path>`s are clickable, click one, assert one `pick_region` frame.
7. Deliver a gapped update (`base_seq` that does not match) carrying `phase: "battle"`. Assert the board updated and **no narration fired** — the one assertion that proves the gap rule is wired into the real screen and not just the dispatcher's unit test.
8. Deliver `phase: "finished"` with a `winner_id`. Assert the results widget names the winner and the dock is gone.

- [ ] **Step 4: The full gate, and a real game against a real backend**

```bash
cd frontend && pnpm test && pnpm check && pnpm build && pnpm codegen:check
```

Then play one, because a test suite is not a game:

```bash
# terminal 1
cd backend
export TRIVIADOR_DATABASE_URL=postgresql+asyncpg://triviador:triviador@127.0.0.1:5433/triviador
export TRIVIADOR_ALLOWED_ORIGINS=http://localhost:5173
export TRIVIADOR_MAPS_ROOT=$(cd .. && pwd)/data/maps
uv run alembic upgrade head
uv run triviador seed-questions --csv ../data/seeds/questions.csv
uv run triviador admin-create --username admin --password 'change-me-now'
uv run uvicorn triviador.api.app:create_app --factory --port 8000

# terminal 2
cd frontend && pnpm dev
```

Open `http://localhost:5173` in three profiles or three browsers, redeem two invites from the admin account, create a game, join it twice, start it, and play it to the end. What you are checking is the list of things no unit test can see: that the map looks like Czechia, that the timer does not stutter, that the dock does not jump when a question opens, that the four seat colours are distinguishable on your actual screen, and that a player who closes their laptop lid and reopens it lands back on the right board.

- [ ] **Step 5: Commit**

```bash
git add frontend/src && git commit -m "feat(frontend): full time, surrender, presence, and a whole game in one test"
```

---

## What this plan deliberately does not do

- **No `/admin/*` anything.** Plan 7 owns it, lazily loaded and role-guarded (Spec 1B §9). A stub route now would be a promise in the router.
- **No Playwright, no compose, no Caddy.** Spec 1 §12.4's single E2E and §12.6's `playwright smoke` CI gate are Plan 8's, where the compose file that runs the stack exists. Task 14's `full-game.test.tsx` is the client-side stand-in and does not replace it.
- **No preset picker.** `GET /api/presets` does not exist — `PresetRepository` is read-only and unexposed until Plan 7. Task 10 sends `preset_id: null` and says so on screen.
- **No sound.** §9.2 reserves `soundEnabled` in the store and Task 11 implements the flag; nothing plays anything. The event bus is where sound will attach when someone wants it.
- **No spectating.** `GET /api/games/{id}` refuses a non-participant once a game has started (Plan 5's `get_game`), and Spec 1 §13 puts spectating in Spec 2.
- **No match history, no replay.** Spec 2.

## Self-review

**Spec coverage.** Spec 1 §9.1 → Task 7 (two sinks, asserted). §9.2 → Task 11's store test, which fails on a sixth key. §9.3 → Task 7's `writeGame` tests plus Task 12's late-REST test. §9.4 → Task 3's `steiger` config and Task 8's `useGameSubscription`. §9.5 → Task 12's fixed-height stage and its no-layout-shift assertion. §9.6 → Task 12's `useMediaPrefetch`. §9.7 → Tasks 9, 10, 12 (the four player-facing screens; the three admin ones are Plan 7). §11.7 → Task 8's boundary and banner, Task 8's `useResyncGame`. §14.1 → Task 1. §14.2 → Task 3. §14.3 → Task 2. §12.4 → deliberately deferred, stated above. Spec 1B §8.1 → Tasks 1 and 11, with a test in each asserting the same file. §8.2 → Task 7. §8.3 → Task 12's `useDeadline`. §8.4 → Task 8's routes and guard. §8.6's client half → Task 5's `createClockOffset`.

**Two things a reviewer should push on before execution starts.**
1. **Decision 4 (shadcn deferred).** It is a stated deviation from Spec 1 §4's stack list. Cheap to reverse at Task 3, expensive after Task 13.
2. **Task 1's stop rule.** If no licence-clean SVG turns up, the plan stops rather than improvising. Everything from Task 11 on is blocked behind it, so it is worth deciding early what the fallback is — a hand-authored schematic map is the obvious one, and it is a decision for a person, not for an implementer at 2 a.m.

**One thing this plan changed about itself while being written**, so it does not read as an inconsistency: Task 12 initially put `gameQueryOptions` in `entities/game/api/`, which would have imported `app/dispatcher` from an entity — forbidden by both `steiger` and Task 7's lint gate. It lives in `app/game-query.ts`. The File Structure block above still lists the entity's `api/` directory for `me.ts`, `lobby.ts` and `maps.ts`, which is correct: those three call `apiFetch` and nothing else.
