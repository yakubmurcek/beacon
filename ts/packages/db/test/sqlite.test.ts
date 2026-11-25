import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, describe, expect, it } from "vitest";
import { checkSchema, differences, dump, openSnapshot, summarize } from "../src/sqlite.js";
import { SQLITE_DDL } from "../src/sqlite-ddl.js";
import { fixtureDb, scratch } from "./helpers.js";

const s = scratch();
afterEach(() => undefined);
process.on("exit", s.done);

function hubFile(name: string): DatabaseSync {
  const db = new DatabaseSync(join(s.dir, name));
  for (const statement of SQLITE_DDL) {
    db.exec(statement);
  }
  return db;
}

describe("reading a snapshot", () => {
  it("opens only an existing file, read-only", () => {
    expect(() => openSnapshot(join(s.dir, "missing.db"))).toThrow("no such SQLite file");
    const db = openSnapshot(fixtureDb(join(s.dir, "ro.db")));
    expect(() => {
      db.exec("DELETE FROM tasks");
    }).toThrow();
    db.close();
  });
  it("accepts the hub's schema with its columns in any order", () => {
    const db = new DatabaseSync(":memory:");
    for (const statement of SQLITE_DDL) {
      db.exec(statement.replace("CREATE TABLE palette (\n", "CREATE TABLE palette (\n  -- moved\n"));
    }
    db.exec("DROP TABLE palette; CREATE TABLE palette (slot INTEGER NOT NULL, project TEXT PRIMARY KEY)");
    expect(() => {
      checkSchema(db);
    }).not.toThrow();
  });
  it("stops on a table or column the mirror does not have", () => {
    const db = hubFile("drift.db");
    db.exec("CREATE TABLE extra (a TEXT); ALTER TABLE tasks ADD COLUMN later TEXT; DROP TABLE palette");
    expect(() => {
      checkSchema(db);
    }).toThrow(/table extra is not in the Postgres schema; table palette is missing.*tasks has columns/);
  });
});

describe("summaries and dumps", () => {
  it("see the same data in two files with the same rows", () => {
    const a = openSnapshot(fixtureDb(join(s.dir, "a.db")));
    const b = openSnapshot(fixtureDb(join(s.dir, "b.db")));
    expect(differences(summarize(a), summarize(b))).toEqual([]);
    expect(dump(a)).toBe(dump(b));
    expect(dump(a)).toContain("-- sqlite_sequence\n");
    a.close();
    b.close();
  });
  it("tell a changed value, a missing row and a changed storage class apart from equal", () => {
    const base = openSnapshot(fixtureDb(join(s.dir, "base.db")));
    const changed = new DatabaseSync(fixtureDb(join(s.dir, "changed.db")));
    changed.exec("UPDATE tasks SET title = title || '!' WHERE id = 1; DELETE FROM labs_calls WHERE rowid = 1");
    changed.exec("UPDATE rank_meta SET v = 3 WHERE k = 'version'"); // TEXT affinity keeps '3': same class, same value
    changed.exec("UPDATE sqlite_sequence SET seq = 42 WHERE name = 'notices'");
    const problems = differences(summarize(base), summarize(changed), "base", "changed");
    expect(problems).toEqual([
      "tasks: same 38 rows counted, different contents",
      "labs_calls: 3 rows in base, 2 in changed",
      "sqlite_sequence: same 3 rows counted, different contents",
    ]);
    base.close();
    changed.close();
  });
  it("name a table only one side has", () => {
    const one = new Map([["a", { rows: 1, digest: "x" }]]);
    expect(differences(one, new Map(), "left", "right")).toEqual(["a: only in left"]);
    expect(differences(new Map(), one, "left", "right")).toEqual(["a: only in right"]);
  });
});
