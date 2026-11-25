import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { drizzle } from "drizzle-orm/node-postgres";
import { migrate } from "drizzle-orm/node-postgres/migrator";
import pg from "pg";
import { from as copyFrom, to as copyTo } from "pg-copy-streams";
import { batch, splitLines } from "./copytext.js";

/** The little of Postgres the copy tools use; tests hand them an in-memory stand-in. */
export interface Pg {
  query(text: string, values?: readonly unknown[]): Promise<{ rows: Record<string, unknown>[] }>;
  /** Streams lines (no trailing newline) into a `COPY … FROM STDIN`. */
  copyIn(sql: string, lines: AsyncIterable<string> | Iterable<string>): Promise<void>;
  /** The lines a `COPY … TO STDOUT` writes. */
  copyOut(sql: string): AsyncIterable<string>;
  end(): Promise<void>;
}

/** One connection (a copy runs in one transaction), text in UTF-8, floats in their shortest exact form. */
export async function connectPg(url: string): Promise<Pg> {
  const client = new pg.Client({ connectionString: url, application_name: "beacon-db-copy" });
  await client.connect();
  await client.query("SET client_encoding TO 'UTF8'; SET extra_float_digits TO 1");
  return {
    query: (text, values) => client.query(text, values === undefined ? undefined : [...values]),
    copyIn: (sql, lines) => pipeline(Readable.from(batch(lines)), client.query(copyFrom(sql))),
    copyOut: (sql) => splitLines(client.query(copyTo(sql))),
    end: () => client.end(),
  };
}

/** Applies the committed migrations (drizzle/) that are not applied yet. */
export async function migrateDb(url: string, migrationsFolder: string): Promise<void> {
  const client = new pg.Client({ connectionString: url, application_name: "beacon-db-migrate" });
  await client.connect();
  try {
    await migrate(drizzle(client), { migrationsFolder });
  } finally {
    await client.end();
  }
}
