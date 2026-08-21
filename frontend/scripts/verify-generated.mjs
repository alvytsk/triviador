// Proves the generated modules parse *and evaluate*, against the real
// `zod` this repo installs — not just that `codegen:check` produced the
// same bytes as last time.
//
// `codegen:check` only diffs bytes; it cannot tell a byte-stable-but-
// broken regeneration from a healthy one. That gap is not hypothetical:
// the `zodVersion: 3` fix in `codegen.mjs` exists because
// `json-schema-to-zod` silently emitted Zod-v4-shaped output against the
// v3 this repo installs, and nothing but a hand-run `tsc --noEmit` caught
// it. Dynamic `import()` (rather than a parse-only check) is the
// stronger test on purpose: the topological ordering in `codegen.mjs`
// exists so every `$ref` becomes a reference to an already-declared
// sibling const, and a use-before-declaration in that ordering only
// throws at evaluation time — a syntax-only check would not see it.
//
// Runs under Node's native TypeScript stripping
// (`--experimental-strip-types`, wired into `codegen:check`) so this
// stays dependency-free: no `typescript`, no build step. That toolchain
// choice belongs to Plan 6, not to this contracts-only package.

import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { DOCUMENTS } from "./codegen.mjs";

const dir = resolve(import.meta.dirname, "../src/shared/api/generated");
const modules = [...DOCUMENTS.map(([, module]) => module), "errors.ts"];

for (const name of modules) {
  const url = pathToFileURL(resolve(dir, name)).href;
  const loaded = await import(url);
  const exported = Object.keys(loaded).length;
  if (exported === 0) {
    throw new Error(`${name}: loaded but exported nothing`);
  }
  console.log(`${name}: loaded, ${exported} exports`);
}
