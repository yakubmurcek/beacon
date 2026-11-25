import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["ts/apps/*/test/**/*.test.ts", "ts/packages/*/test/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["ts/apps/api/src/**", "ts/packages/*/src/**"],
      // The entry files only wire real listeners, connections and process.argv to what the tests run: the api's two
      // through buildApp/workerHandler, the copy tools' bin.ts through cli.ts's main with a stand-in Postgres.
      exclude: ["ts/apps/api/src/server.ts", "ts/apps/api/src/worker.ts", "ts/packages/db/src/bin.ts"],
      // Floors may only rise (scripts/check's "not loosened" guard covers this file).
      thresholds: { lines: 90, functions: 90, branches: 80, statements: 90 },
    },
  },
});
