import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import type { Pg } from "../src/pg.js";
import { SQLITE_DDL } from "../src/sqlite-ddl.js";
import { TABLES } from "../src/tables.js";

/** A scratch folder, removed by the returned function. */
export function scratch(): { dir: string; done: () => void } {
  const dir = mkdtempSync(join(tmpdir(), "beacon-db-"));
  return {
    dir,
    done: () => {
      rmSync(dir, { recursive: true, force: true });
    },
  };
}

/** The fixture as the hub would hold it: the hub's own schema, then test/fixture.sql. */
export function fixtureDb(path: string): string {
  const db = new DatabaseSync(path);
  for (const statement of SQLITE_DDL) {
    db.exec(statement);
  }
  db.exec(readFileSync(new URL("fixture.sql", import.meta.url), "utf8"));
  db.close();
  return path;
}

/**
 * An in-memory stand-in for the few Postgres statements the tools send: tables hold COPY lines as given, sequences
 * hold what setval set. Enough to test the tools' logic; the real round trip runs in roundtrip.pg.test.ts.
 */
export class FakePg implements Pg {
  readonly tables = new Map<string, string[]>();
  readonly sequences = new Map<string, string | null>();
  readonly log: string[] = [];
  failOn: RegExp | undefined;
  /** The columns information_schema reports: the mirror's, unless a test changes them. */
  columns = TABLES.flatMap((t) =>
    t.columns.map((c) => ({
      table_name: t.name,
      column_name: c.name,
      data_type: { int: "bigint", real: "double precision", text: "text" }[c.kind],
    })),
  );

  query(text: string, values: readonly unknown[] = []): Promise<{ rows: Record<string, unknown>[] }> {
    this.log.push(text);
    if (this.failOn?.test(text) === true) {
      return Promise.reject(new Error(`refused: ${text}`));
    }
    if (text.includes("information_schema.columns")) {
      return Promise.resolve({ rows: [...this.columns] });
    }
    const peek = /^SELECT 1 FROM "(\w+)" LIMIT 1$/.exec(text);
    if (peek?.[1] !== undefined) {
      return Promise.resolve({ rows: (this.tables.get(peek[1]) ?? []).length > 0 ? [{ "?column?": 1 }] : [] });
    }
    if (text.startsWith("TRUNCATE")) {
      for (const name of text.matchAll(/"(\w+)"/g)) {
        this.tables.set(name[1] ?? "", []);
        this.sequences.delete(`"${name[1] ?? ""}"`);
      }
    } else if (text.startsWith("SELECT setval")) {
      this.sequences.set(String(values[0]), values[3] === true ? String(values[2]) : null);
    } else if (text.startsWith("SELECT pg_sequence_last_value")) {
      return Promise.resolve({ rows: [{ v: this.sequences.get(String(values[0])) ?? null }] });
    }
    return Promise.resolve({ rows: [] });
  }

  async copyIn(sql: string, lines: AsyncIterable<string> | Iterable<string>): Promise<void> {
    this.log.push(sql);
    const name = /^COPY "(\w+)"/.exec(sql)?.[1] ?? "";
    const rows = this.tables.get(name) ?? [];
    for await (const line of lines) {
      rows.push(line);
    }
    this.tables.set(name, rows);
  }

  async *copyOut(sql: string): AsyncGenerator<string> {
    this.log.push(sql);
    await Promise.resolve();
    yield* this.tables.get(/ FROM "(\w+)"/.exec(sql)?.[1] ?? "") ?? [];
  }

  end(): Promise<void> {
    return Promise.resolve();
  }
}
