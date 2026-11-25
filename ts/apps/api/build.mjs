// Bundles the two entry points into one file each, so a release is `node dist/server.js` with no node_modules.
import { build } from "esbuild";

await build({
  entryPoints: { server: "src/server.ts", worker: "src/worker.ts" },
  bundle: true,
  platform: "node",
  target: "node24",
  format: "esm",
  outdir: "bundle",
  sourcemap: true,
  // pg's optional native binding is never used.
  external: ["pg-native"],
  banner: { js: "import { createRequire } from 'node:module'; const require = createRequire(import.meta.url);" },
});
