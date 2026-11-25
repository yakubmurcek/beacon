import { existsSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { postgresLines, postgresSequences } from "./copy.js";
import type { Pg } from "./pg.js";
import { checkSchema, type Summary } from "./sqlite.js";
import { SQLITE_DDL } from "./sqlite-ddl.js";
import { columnList, quote, TABLES } from "./tables.js";
import { DataError, Digest, rowLine, toSqlite } from "./values.js";

/**
 * Writes Postgres back into a NEW SQLite file the old hub opens as its own: the schema is the hub's captured
 * CREATE statements (scripts/sqlite_ddl.py keeps them equal to what the hub's code creates), the rows come from
 * one repeatable-read snapshot, and sqlite_sequence gets each AUTOINCREMENT table's sequence value back, so an id
 * handed out in Postgres is never handed out again after a switch back. Returns what was read, per table.
 */
export async function reverseToSqlite(pg: Pg, path: string): Promise<Map<string, Summary>> {
  if (existsSync(path)) {
    throw new DataError(`${path} exists: the reverse copy writes a fresh file only`);
  }
  const db = new DatabaseSync(path);
  const read = new Map<string, Summary>();
  try {
    for (const statement of SQLITE_DDL) {
      db.exec(statement);
    }
    checkSchema(db);
    await pg.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY");
    try {
      db.exec("BEGIN");
      const tops = new Map<string, bigint>();
      for (const spec of TABLES) {
        const insert = db.prepare(
          `INSERT INTO ${quote(spec.name)} (${columnList(spec)}) VALUES (${spec.columns.map(() => "?").join(", ")})`,
        );
        const id = spec.columns.findIndex((c) => c.name === spec.identity);
        let top = 0n;
        const digest = new Digest();
        for await (const values of postgresLines(pg, spec)) {
          const row = values.map((v, i) => toSqlite(v, spec.columns[i]?.kind ?? "text"));
          insert.run(...row);
          const own = row[id];
          if (typeof own === "bigint" && own > top) {
            top = own;
          }
          digest.add(rowLine(values));
        }
        tops.set(spec.name, top);
        read.set(spec.name, { rows: digest.rows, digest: digest.hex });
      }
      const sequences = await postgresSequences(pg);
      db.exec("DELETE FROM sqlite_sequence");
      const mark = db.prepare("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)");
      for (const spec of TABLES.filter((s) => s.autoincrement)) {
        const v = sequences.get(spec.name);
        const top = tops.get(spec.name) ?? 0n;
        const seq = v === undefined || v === null ? top : BigInt(v) > top ? BigInt(v) : top;
        if (seq > 0n) {
          mark.run(spec.name, seq);
        }
      }
      db.exec("COMMIT");
      await pg.query("COMMIT");
    } catch (error) {
      if (db.isTransaction) {
        db.exec("ROLLBACK");
      }
      await pg.query("ROLLBACK");
      throw error;
    }
  } finally {
    db.close();
  }
  return read;
}
