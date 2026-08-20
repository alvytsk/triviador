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
    {
      name: "removeAttrs",
      params: { attrs: "(style|class|transform|fill|stroke|stroke-width|opacity)" },
    },
  ],
};
