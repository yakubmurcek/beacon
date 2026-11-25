import { createServer } from "node:http";
import { createPool, databaseUrl } from "@beacon/db";
import { workerHandler } from "./worker-http.js";

/**
 * The background worker. Stage 0 runs no jobs: it only proves the unit, its memory cap and its health endpoint.
 * The old hub's loops move here in stage 2 and run only after the cut-over (design note, section 7).
 */
const port = Number(process.env["BEACON_WORKER_PORT"] ?? 4833);
const url = databaseUrl();
const pool = url === undefined ? undefined : createPool(url, 2);
const server = createServer(workerHandler(pool));

for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.once(signal, () => {
    server.close();
    void (pool?.end() ?? Promise.resolve()).then(() => process.exit(0));
  });
}

server.listen(port, "127.0.0.1");
