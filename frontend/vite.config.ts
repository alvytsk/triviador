import { readFile } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

const here = fileURLToPath(new URL(".", import.meta.url));
const MAPS_ROOT = resolve(here, "../data/maps");

// Defaults are the host-side ports (`pnpm dev` outside compose); the compose
// dev overlay overrides all three with service names.
const API_TARGET = process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000";
const MEDIA_TARGET = process.env.VITE_MEDIA_TARGET ?? "http://127.0.0.1:3902";
const MEDIA_HOST = process.env.VITE_MEDIA_HOST ?? "triviador-media.web.garage.internal";

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

/**
 * `scripts/assert-admin-split.mjs`'s two checks (a source-text grep for a
 * couple of admin-only schema string literals) proved, on the whole-branch
 * review, to have a real blind spot: they prove no eager chunk *constructs
 * an admin schema*, which is not the same claim as "no eager chunk ships
 * admin code". Reviewer proof: importing `AdminShell` (which imports only
 * `@tanstack/react-router` and `@/shared/lib` — no `entities/admin`, no
 * `generated/admin`) into the eager `_authed.admin.tsx` landed its own
 * `"Back to lobby"` literal in the entry chunk, and `pnpm check:bundle`
 * still reported OK, because neither marker string is anywhere in
 * `AdminShell`.
 *
 * `dist/.vite/manifest.json` (the file `assert-admin-split.mjs` already
 * reads) records only each chunk's `file`/`imports`/`dynamicImports` — not
 * which *source modules* were bundled into it, so it cannot answer the
 * question this plugin exists to answer. Rollup's own bundle object (the
 * argument to `generateBundle`) can: every `OutputChunk` carries a
 * `moduleIds` array, the actual list of every source module Rollup folded
 * into that chunk. This plugin writes that list out, one JSON file mapping
 * each emitted chunk's `fileName` to its `moduleIds`, so
 * `assert-admin-split.mjs` can test the claim Spec 1B §9 actually makes —
 * no chunk in the entry graph contains a module under `src/pages/admin/**`
 * or an admin feature slice — instead of a proxy for it.
 */
function emitModuleIds(): Plugin {
  return {
    name: "triviador-emit-module-ids",
    generateBundle(_options, bundle) {
      const moduleIdsByFile: Record<string, string[]> = {};
      for (const output of Object.values(bundle)) {
        if (output.type !== "chunk") continue;
        moduleIdsByFile[output.fileName] = Object.keys(output.modules);
      }
      this.emitFile({
        type: "asset",
        fileName: ".vite/module-ids.json",
        source: JSON.stringify(moduleIdsByFile),
      });
    },
  };
}

export default defineConfig({
  plugins: [
    emitModuleIds(),
    tanstackRouter({
      target: "react",
      autoCodeSplitting: true,
      routesDirectory: "./src/app/routes",
      // Generated next to the routes it describes, not at `src/routeTree.gen.ts`
      // (the plugin's default) — `main.tsx` imports it from here, and both
      // `biome.json` and `steiger.config.ts` already exempt this exact path.
      generatedRouteTree: "./src/app/routes/routeTree.gen.ts",
      // Otherwise the generator scans its own output as a candidate route
      // file every run and warns that it exports no `Route`.
      routeFileIgnorePattern: "\\.gen\\.ts$",
    }),
    react(),
    tailwindcss(),
    serveMaps(),
  ],
  resolve: { alias: { "@": resolve(here, "src") } },
  build: {
    // `scripts/assert-admin-split.mjs` (`pnpm check:bundle`) reads
    // `dist/.vite/manifest.json` to build the real static-import graph
    // rather than regex-scanning minified chunk source — a manifest's
    // `imports`/`dynamicImports` distinction survives minification
    // unconditionally; grepping mangled output for a bare identifier does
    // not.
    manifest: true,
  },
  server: {
    port: 5173,
    proxy: {
      // `changeOrigin` stays false on purpose: the browser's Origin header
      // must arrive at the backend as `http://localhost:5173`, which is what
      // `TRIVIADOR_ALLOWED_ORIGINS` has to contain and what the socket
      // handshake checks (§6.4). Rewriting it would make development pass a
      // check that production performs differently.
      "/api": { target: API_TARGET, changeOrigin: false },
      "/ws": { target: API_TARGET.replace(/^http/, "ws"), ws: true, changeOrigin: false },
      // NOT the backend. Media never passes through the API — that is §9.1's
      // whole point, and pointing this at :8000 (as it once did) 404s every
      // question image in development.
      //
      // Garage resolves a bucket from the Host header against
      // `root_domain = ".web.garage.internal"`, so the browser's own Host
      // (`localhost:5173`) matches no bucket. §10.2's Caddy config sets the
      // identical header for the same reason; this is the dev half of that
      // rule.
      "/media": {
        target: MEDIA_TARGET,
        changeOrigin: false,
        headers: { Host: MEDIA_HOST },
      },
    },
  },
});
