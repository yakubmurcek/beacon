import type { Health } from "@beacon/contracts";
import { ping } from "@beacon/db";
import type pg from "pg";

const started = Date.now();

/** The release's commit: the deploy step writes it into the unit's environment. */
function commit(env: NodeJS.ProcessEnv = process.env): string {
  return env["BEACON_COMMIT"] ?? "dev";
}

export async function health(service: Health["service"], pool: pg.Pool | undefined): Promise<Health> {
  const db = pool === undefined ? "off" : await ping(pool);
  return { ok: db !== "down", service, commit: commit(), uptime: (Date.now() - started) / 1000, db };
}
