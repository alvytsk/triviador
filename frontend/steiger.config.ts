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
  {
    files: ["./src/features/**"],
    rules: {
      // Same reasoning as entities/ above, one layer up: `sign-in` and
      // `redeem-invite` (§10.1) are each one user intention with its own
      // mutation and its own form, and each is currently mounted from one
      // page. That is FSD's boundary for what a feature is, not a sign that
      // two features should be merged into their one caller.
      "fsd/insignificant-slice": "off",
    },
  },
  {
    files: ["./src/widgets/**"],
    rules: {
      // `game-stage` (Task 11 of Plan 6) draws the board; the game screen
      // that mounts it is Task 12. Between the two tasks this slice has
      // *zero* references rather than one, which is the same "not built yet,
      // not a merge candidate" situation entities/ and features/ are already
      // exempted for above — just caught one task earlier in the sequence.
      "fsd/insignificant-slice": "off",
    },
  },
]);
