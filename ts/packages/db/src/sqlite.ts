import { existsSync, statSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { columnList, quote, type TableSpec, TABLES } from "./tables.js";
import { DataError, Digest, fromSqlite, rowLine, type SqliteValue, storageClass } from "./values.js";

/**
 * A snapshot opened read-only: the tools never write the file they copy from. A non-empty -wal beside it means a
 * live file, not a `.backup` snapshot (writes may still land), so it is refused unless `allowWal` says otherwise.
 */
export function openSnapshot(path: string, allowWal = false): DatabaseSync {
  if (!existsSync(path)) {
    throw new DataError(`${path}: no such SQLite file`);
  }
  const wal = `${path}-wal`;
  if (!allowWal && existsSync(wal) && statSync(wal).size > 0) {
    throw new DataError(`${wal} holds writes: copy from a .backup snapshot (or pass --allow-wal)`);
  }
  return new DatabaseSync(path, { readOnly: true });
}

/**
 * The file holds exactly the mirrored tables with exactly their columns (in any order: the live file has columns
 * that later ALTERs added at the end). A table or column the mirror lacks would be lost, so it stops the run.
 */
export function checkSchema(db: DatabaseSync, specs: readonly TableSpec[] = TABLES): void {
  const names = db
    .prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
    .all()
    .map((r) => String(r["name"]));
  const expected = specs.map((s) => s.name).sort();
  const problems: string[] = [];
  for (const n of names.filter((x) => !expected.includes(x))) {
    problems.push(`table ${n} is not in the Postgres schema`);
  }
  for (const n of expected.filter((x) => !names.includes(x))) {
    problems.push(`table ${n} is missing from the SQLite file`);
  }
  for (const spec of specs.filter((s) => names.includes(s.name))) {
    const have = db
      .prepare(`SELECT name FROM pragma_table_info(?)`)
      .all(spec.name)
      .map((r) => String(r["name"]))
      .sort();
    const want = spec.columns.map((c) => c.name).sort();
    if (have.join() !== want.join()) {
      problems.push(`${spec.name} has columns (${have.join(", ")}), the schema (${want.join(", ")})`);
    }
  }
  if (problems.length > 0) {
    throw new DataError(`the SQLite file does not match the mirror schema: ${problems.join("; ")}`);
  }
}

/** Every row of a table as raw values in the spec's column order. */
export function* sqliteRows(db: DatabaseSync, spec: TableSpec): Generator<SqliteValue[]> {
  const statement = db.prepare(`SELECT ${columnList(spec)} FROM ${quote(spec.name)}`);
  statement.setReadBigInts(true);
  for (const row of statement.iterate()) {
    yield spec.columns.map((c) => row[c.name] as SqliteValue);
  }
}

/** sqlite_sequence's high-water marks, by table. */
export function sqliteSequence(db: DatabaseSync): Map<string, bigint> {
  const statement = db.prepare("SELECT name, seq FROM sqlite_sequence");
  statement.setReadBigInts(true);
  return new Map(statement.all().map((r) => [String(r["name"]), r["seq"] as bigint]));
}

/** One table's rows as normalized lines; `classes` adds each value's storage class (a SQLite-to-SQLite compare). */
function* sqliteLines(db: DatabaseSync, spec: TableSpec, classes = false): Generator<string> {
  for (const values of sqliteRows(db, spec)) {
    const norm = values.map((v, i) => {
      const column = spec.columns[i];
      return column === undefined ? null : fromSqlite(v, column.kind, `${spec.name}.${column.name}`);
    });
    yield rowLine(norm, classes ? values.map(storageClass) : undefined);
  }
}

export interface Summary {
  readonly rows: number;
  readonly digest: string;
}

const SEQUENCE = "sqlite_sequence";

/**
 * Per table the row count and order-independent checksum. With `classes` (two SQLite files compared) each value's
 * storage class counts too, and sqlite_sequence is one more table; without, the lines match Postgres's side.
 */
export function summarize(db: DatabaseSync, classes = true): Map<string, Summary> {
  checkSchema(db);
  const out = new Map<string, Summary>();
  for (const spec of TABLES) {
    const digest = new Digest();
    for (const line of sqliteLines(db, spec, classes)) {
      digest.add(line);
    }
    out.set(spec.name, { rows: digest.rows, digest: digest.hex });
  }
  if (!classes) {
    return out;
  }
  const digest = new Digest();
  for (const [name, seq] of sqliteSequence(db)) {
    digest.add(rowLine([name, seq.toString()]));
  }
  out.set(SEQUENCE, { rows: digest.rows, digest: digest.hex });
  return out;
}

/** The whole file as sorted normalized lines, table by table: two files hold the same data when these are equal. */
export function* dumpLines(db: DatabaseSync): Generator<string> {
  checkSchema(db);
  for (const spec of [...TABLES].sort((a, b) => (a.name < b.name ? -1 : 1))) {
    yield `-- ${spec.name} (${spec.columns.map((c) => c.name).join(", ")})`;
    yield* [...sqliteLines(db, spec, true)].sort();
  }
  yield `-- ${SEQUENCE}`;
  yield* [...sqliteSequence(db)].map(([name, v]) => rowLine([name, v.toString()])).sort();
}

export function dump(db: DatabaseSync): string {
  return `${[...dumpLines(db)].join("\n")}\n`;
}

/** The differences between two summaries, one line each; empty means equal. */
export function differences(a: Map<string, Summary>, b: Map<string, Summary>, left = "a", right = "b"): string[] {
  const out: string[] = [];
  for (const name of new Set([...a.keys(), ...b.keys()])) {
    const x = a.get(name);
    const y = b.get(name);
    if (x === undefined || y === undefined) {
      out.push(`${name}: only in ${x === undefined ? right : left}`);
    } else if (x.rows !== y.rows) {
      out.push(`${name}: ${String(x.rows)} rows in ${left}, ${String(y.rows)} in ${right}`);
    } else if (x.digest !== y.digest) {
      out.push(`${name}: same ${String(x.rows)} rows counted, different contents`);
    }
  }
  return out;
}
