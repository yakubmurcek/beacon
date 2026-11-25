import type { IncomingMessage, ServerResponse } from "node:http";
import type pg from "pg";
import { health } from "./health.js";

/** The worker's only HTTP surface: its health, for the watchdog and the deploy timer. */
export function workerHandler(pool: pg.Pool | undefined) {
  return (req: IncomingMessage, res: ServerResponse): void => {
    if (req.method !== "GET" || req.url !== "/healthz") {
      res.writeHead(404).end();
      return;
    }
    void health("worker", pool).then((body) => {
      res.writeHead(body.ok ? 200 : 503, { "content-type": "application/json" }).end(JSON.stringify(body));
    });
  };
}
