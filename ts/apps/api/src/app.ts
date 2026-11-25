import { createPool, databaseUrl } from "@beacon/db";
import Fastify, { type FastifyInstance } from "fastify";
import type pg from "pg";
import { health } from "./health.js";

export interface AppOptions {
  /** A pool to use instead of the one from `BEACON_DATABASE_URL` (tests pass their own, or none). */
  pool?: pg.Pool | undefined;
  logger?: boolean;
}

/**
 * The API as a Fastify instance, not yet listening, so tests can `inject` requests. Stage 0 serves only its health;
 * routes move here domain by domain in stage 2 (docs/design/2026-11-ts-migration.md).
 */
export function buildApp(opts: AppOptions = {}): FastifyInstance {
  const url = databaseUrl();
  const pool = "pool" in opts ? opts.pool : url === undefined ? undefined : createPool(url);
  const app = Fastify({ logger: opts.logger ?? false, bodyLimit: 64 * 1024 });
  app.get("/healthz", async (_req, reply) => {
    const body = await health("api", pool);
    return reply.code(body.ok ? 200 : 503).send(body);
  });
  app.addHook("onClose", async () => {
    await pool?.end();
  });
  return app;
}
