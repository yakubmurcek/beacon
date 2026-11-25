import { z } from "zod";

/** What `GET /healthz` answers on the api and the worker: the systemd watchdog and the deploy timer read it. */
export const Health = z.object({
  ok: z.boolean(),
  /** `api` or `worker`. */
  service: z.enum(["api", "worker"]),
  /** The release's git commit; the deploy timer checks a fresh release reports its own. */
  commit: z.string(),
  /** Seconds since the process started. */
  uptime: z.number().nonnegative(),
  /** `ok`, `down` (Postgres refused), or `off` (no database configured). */
  db: z.enum(["ok", "down", "off"]),
});
export type Health = z.infer<typeof Health>;
