import pg from "pg";

/**
 * Where Postgres is. Unset means "no database here" (a fresh clone, CI without a service): the apps still start and
 * report `db: "off"`. On the hub server it is a unix-socket URL with peer auth, so no password exists anywhere.
 */
export function databaseUrl(env: NodeJS.ProcessEnv = process.env): string | undefined {
  const url = env["BEACON_DATABASE_URL"];
  return url === undefined || url === "" ? undefined : url;
}

/** One pool per process (the api and the worker each hold one); small, because the box has 2 cores. */
export function createPool(url: string, max = 5): pg.Pool {
  return new pg.Pool({ connectionString: url, max, idleTimeoutMillis: 30_000, application_name: "beacon" });
}

/** `ok` when a trivial query answers within the timeout, `down` otherwise. */
export async function ping(pool: pg.Pool, timeoutMs = 2_000): Promise<"ok" | "down"> {
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<"down">((resolve) => {
    timer = setTimeout(() => {
      resolve("down");
    }, timeoutMs);
  });
  const query = pool.query("select 1").then(
    () => "ok" as const,
    () => "down" as const,
  );
  try {
    return await Promise.race([query, timeout]);
  } finally {
    clearTimeout(timer);
  }
}
