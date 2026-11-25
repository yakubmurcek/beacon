// Bundles the copy tools into one file with their migrations next to it, so a box runs `node bundle/beacon-db.js`
// with Node 24 and no node_modules (nothing is installed from npm on the hub server).
import { cpSync } from "node:fs";
import { build } from "esbuild";

await build({
  entryPoints: { "beacon-db": "src/bin.ts" },
  bundle: true,
  platform: "node",
  target: "node24",
  format: "esm",
  outdir: "bundle",
  sourcemap: true,
  external: ["pg-native"],
  banner: { js: "import { createRequire } from 'node:module'; const require = createRequire(import.meta.url);" },
});
cpSync("drizzle", "bundle/drizzle", { recursive: true });
