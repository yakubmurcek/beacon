import path from "node:path";
import type { NextConfig } from "next";

const repo = path.resolve(import.meta.dirname, "../../..");

const config: NextConfig = {
  // Files only: no page server on a box that already swaps (design note, section 2).
  output: "export",
  // Moved pages live under /next until each one's router line switches it to its old address.
  basePath: "/next",
  trailingSlash: true,
  images: { unoptimized: true },
  // The pages read web/tokens.css and web/i18n-cs.js from the old app's folder: one source until it retires.
  turbopack: { root: repo },
  outputFileTracingRoot: repo,
  typedRoutes: true,
};

export default config;
