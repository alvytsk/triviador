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
