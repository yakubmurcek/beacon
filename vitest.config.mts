import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["ts/apps/*/test/**/*.test.ts", "ts/packages/*/test/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["ts/apps/api/src/**", "ts/packages/*/src/**"],
      // The two entry files only wire a listener to signals; what they serve is tested through buildApp/workerHandler.
      exclude: ["ts/apps/api/src/server.ts", "ts/apps/api/src/worker.ts"],
      // Floors may only rise (scripts/check's "not loosened" guard covers this file).
      thresholds: { lines: 90, functions: 90, branches: 80, statements: 90 },
    },
  },
});
