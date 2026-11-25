import { existsSync, renameSync, rmSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { checkPostgres, postgresLines, postgresSequences, undoing } from "./copy.js";
import type { Pg } from "./pg.js";
import { checkSchema, differences, type Summary, sqliteSequence, summarize } from "./sqlite.js";
import { SQLITE_DDL } from "./sqlite-ddl.js";
import { columnList, quote, TABLES } from "./tables.js";
import { DataError, Digest, rowLine, toSqlite } from "./values.js";

export interface ReverseResult {
  /** What was read from Postgres, per table. */
  readonly read: Map<string, Summary>;
  /** Where the written file differs from Postgres (rows, contents, sqlite_sequence); empty means it was renamed into place. */
  readonly problems: string[];
}

const SIBLINGS = ["", "-wal", "-shm", "-journal"];

function removePartial(partial: string): void {
  for (const end of SIBLINGS) {
    rmSync(`${partial}${end}`, { force: true });
  }
}

/** Fills `db` from one repeatable-read snapshot; returns what was read and the sqlite_sequence marks it wrote. */
async function fill(pg: Pg, db: DatabaseSync): Promise<{ read: Map<string, Summary>; marks: Map<string, bigint> }> {
  const read = new Map<string, Summary>();
  const marks = new Map<string, bigint>();
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
        const row = values.map((v, i) => {
          const column = spec.columns[i];
          try {
            return toSqlite(v, column?.kind ?? "text");
          } catch (error) {
            throw new DataError(`${spec.name}.${column?.name ?? "?"}: ${error instanceof Error ? error.message : ""}`);
          }
        });
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
        marks.set(spec.name, seq);
      }
    }
    db.exec("COMMIT");
    await pg.query("COMMIT");
  } catch (error) {
    return undoing(error, async () => {
      try {
        if (db.isTransaction) {
          db.exec("ROLLBACK");
        }
      } finally {
        await pg.query("ROLLBACK");
      }
    });
  }
  return { read, marks };
}

/**
 * Writes Postgres back into a NEW SQLite file the old hub opens as its own. The schema is the hub's captured CREATE
 * statements (scripts/sqlite_ddl.py keeps them equal to what the hub's code creates); rows come from one
 * repeatable-read snapshot in each table's order, so rowids follow the old order; sqlite_sequence gets each
 * AUTOINCREMENT table's sequence back, so an id handed out in Postgres is never handed out again after a switch
 * back. All of it goes to `<path>.partial`, is read back and compared, and only an identical file is renamed to
 * `path`; anything else removes the partial file, so a failed run never leaves a valid-looking empty hub file.
 */
export async function reverseToSqlite(pg: Pg, path: string): Promise<ReverseResult> {
  for (const end of SIBLINGS) {
    if (existsSync(`${path}${end}`)) {
      throw new DataError(`${path}${end} exists: the reverse copy writes a fresh file only`);
    }
  }
  await checkPostgres(pg);
  const partial = `${path}.partial`;
  removePartial(partial);
  let result: ReverseResult;
  try {
    const db = new DatabaseSync(partial);
    let filled: { read: Map<string, Summary>; marks: Map<string, bigint> };
    try {
      for (const statement of SQLITE_DDL) {
        db.exec(statement);
      }
      checkSchema(db);
      filled = await fill(pg, db);
    } finally {
      db.close();
    }
    const back = new DatabaseSync(partial, { readOnly: true });
    try {
      const problems = differences(filled.read, summarize(back, false), "Postgres", path);
      const written = sqliteSequence(back);
      for (const name of new Set([...written.keys(), ...filled.marks.keys()])) {
        if (written.get(name) !== filled.marks.get(name)) {
          problems.push(
            `sqlite_sequence.${name}: ${String(written.get(name) ?? "none")} written, Postgres says ${String(filled.marks.get(name) ?? "none")}`,
          );
        }
      }
      result = { read: filled.read, problems };
    } finally {
      back.close();
    }
  } catch (error) {
    return undoing(error, () => {
      removePartial(partial);
    });
  }
  if (result.problems.length > 0) {
    removePartial(partial);
  } else {
    renameSync(partial, path);
  }
  return result;
}
