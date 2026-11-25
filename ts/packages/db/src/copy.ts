import type { DatabaseSync } from "node:sqlite";
import { decodeLine, encodeLine } from "./copytext.js";
import type { Pg } from "./pg.js";
import { checkSchema, type Summary, sqliteRows, sqliteSequence } from "./sqlite.js";
import { columnList, quote, type TableSpec, TABLES } from "./tables.js";
import { DataError, Digest, fromCopy, fromSqlite, rowLine } from "./values.js";

export interface CopyResult {
  /** What was read from SQLite, per table: the counts and checksums Postgres must match. */
  readonly tables: Map<string, Summary>;
  /** Per identity table the sequence's last value as set (null: never called, the next id is 1). */
  readonly sequences: Map<string, string | null>;
}

async function rollback(pg: Pg, error: unknown): Promise<never> {
  await pg.query("ROLLBACK");
  throw error;
}

/**
 * Loads every table from the snapshot into Postgres inside ONE transaction: truncate, COPY each table parents
 * first, then set each identity's sequence past both the highest id and SQLite's AUTOINCREMENT mark. Rerunnable;
 * on a database that already holds rows it refuses unless `replace` is set, so a stray run cannot wipe live data.
 */
export async function copyToPostgres(db: DatabaseSync, pg: Pg, replace = false): Promise<CopyResult> {
  checkSchema(db);
  const marks = sqliteSequence(db);
  const tables = new Map<string, Summary>();
  const sequences = new Map<string, string | null>();
  await pg.query("BEGIN");
  try {
    if (!replace) {
      for (const spec of TABLES) {
        if ((await pg.query(`SELECT 1 FROM ${quote(spec.name)} LIMIT 1`)).rows.length > 0) {
          throw new DataError(`${spec.name} already holds rows: pass --replace to truncate the whole database first`);
        }
      }
    }
    await pg.query(`TRUNCATE ${TABLES.map((s) => quote(s.name)).join(", ")} RESTART IDENTITY`);
    for (const spec of TABLES) {
      const digest = new Digest();
      const id = spec.columns.findIndex((c) => c.name === spec.identity);
      let top = marks.get(spec.name) ?? 0n;
      const lines = function* (): Generator<string> {
        for (const values of sqliteRows(db, spec)) {
          const norm = values.map((v, i) => {
            const column = spec.columns[i];
            return column === undefined ? null : fromSqlite(v, column.kind, `${spec.name}.${column.name}`);
          });
          const own = values[id];
          if (typeof own === "bigint" && own > top) {
            top = own;
          }
          digest.add(rowLine(norm));
          yield encodeLine(norm, spec.name);
        }
      };
      await pg.copyIn(`COPY ${quote(spec.name)} (${columnList(spec)}) FROM STDIN`, lines());
      tables.set(spec.name, { rows: digest.rows, digest: digest.hex });
      if (spec.identity !== undefined) {
        const called = top > 0n;
        await pg.query("SELECT setval(pg_get_serial_sequence($1, $2), $3, $4)", [
          quote(spec.name),
          spec.identity,
          called ? top.toString() : "1",
          called,
        ]);
        sequences.set(spec.name, called ? top.toString() : null);
      }
    }
    await pg.query("COMMIT");
  } catch (error) {
    return rollback(pg, error);
  }
  return { tables, sequences };
}

/** One table read back from Postgres as the same normalized lines the SQLite side made. */
export async function* postgresLines(pg: Pg, spec: TableSpec): AsyncGenerator<(string | null)[]> {
  for await (const line of pg.copyOut(`COPY ${quote(spec.name)} (${columnList(spec)}) TO STDOUT`)) {
    const fields = decodeLine(line);
    if (fields.length !== spec.columns.length) {
      throw new DataError(`${spec.name}: a COPY line with ${String(fields.length)} fields`);
    }
    yield fields.map((f, i) => {
      const column = spec.columns[i];
      return column === undefined ? null : fromCopy(f, column.kind);
    });
  }
}

/** The last value of each identity's sequence (null: never called). */
export async function postgresSequences(pg: Pg): Promise<Map<string, string | null>> {
  const out = new Map<string, string | null>();
  for (const spec of TABLES) {
    if (spec.identity !== undefined) {
      const { rows } = await pg.query(
        "SELECT pg_sequence_last_value(pg_get_serial_sequence($1, $2)::regclass)::text AS v",
        [quote(spec.name), spec.identity],
      );
      const v = rows[0]?.["v"];
      out.set(spec.name, typeof v === "string" ? v : null);
    }
  }
  return out;
}

/**
 * Reads every table back from Postgres (one repeatable-read snapshot) and compares row counts, checksums and
 * sequences with what the copy loaded. Returns the differences, one line each; empty means identical.
 */
export async function verify(pg: Pg, expected: CopyResult): Promise<string[]> {
  const out: string[] = [];
  await pg.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
  try {
    for (const spec of TABLES) {
      const digest = new Digest();
      for await (const values of postgresLines(pg, spec)) {
        digest.add(rowLine(values));
      }
      const want = expected.tables.get(spec.name);
      if (want === undefined) {
        out.push(`${spec.name}: not copied`);
      } else if (want.rows !== digest.rows) {
        out.push(`${spec.name}: ${String(want.rows)} rows in SQLite, ${String(digest.rows)} in Postgres`);
      } else if (want.digest !== digest.hex) {
        out.push(`${spec.name}: ${String(want.rows)} rows on both sides, different contents`);
      }
    }
    for (const [name, v] of await postgresSequences(pg)) {
      const want = expected.sequences.get(name) ?? null;
      if (v !== want) {
        out.push(`${name}: sequence at ${v ?? "unset"}, expected ${want ?? "unset"}`);
      }
    }
    await pg.query("COMMIT");
  } catch (error) {
    return rollback(pg, error);
  }
  return out;
}
