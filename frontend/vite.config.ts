import { readFile } from "node:fs/promises";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

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
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/media": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true, changeOrigin: false },
    },
  },
});
