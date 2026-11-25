import { randomBytes } from "node:crypto";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import pg from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { copyToPostgres, verify } from "../src/copy.js";
import { connectPg, migrateDb, type Pg } from "../src/pg.js";
import { reverseToSqlite } from "../src/reverse.js";
import { differences, dump, openSnapshot, summarize } from "../src/sqlite.js";
import { fixtureDb, scratch } from "./helpers.js";

// A real Postgres: CI starts a postgres:18 service; locally point BEACON_TEST_DATABASE_URL at any server where the
// role may create databases. Each run makes its own throwaway database and drops it, so runs never collide.
const admin = process.env["BEACON_TEST_DATABASE_URL"];
if (admin === undefined || admin === "") {
  process.stderr.write(
    "roundtrip.pg.test.ts skipped: set BEACON_TEST_DATABASE_URL to run the round trip on a real Postgres\n",
  );
}

describe.skipIf(admin === undefined || admin === "")(
  "round trip through a real Postgres (BEACON_TEST_DATABASE_URL)",
  () => {
    const s = scratch();
    const name = `beacon_test_${randomBytes(6).toString("hex")}`;
    let url = "";
    let db: Pg;

    beforeAll(async () => {
      const root = new pg.Client({ connectionString: admin });
      await root.connect();
      await root.query(`CREATE DATABASE ${name}`);
      await root.end();
      const u = new URL(admin ?? "");
      u.pathname = `/${name}`;
      url = u.toString();
      await migrateDb(url, fileURLToPath(new URL("../drizzle", import.meta.url)));
      db = await connectPg(url);
    });

    afterAll(async () => {
      await db.end();
      const root = new pg.Client({ connectionString: admin });
      await root.connect();
      await root.query(`DROP DATABASE IF EXISTS ${name} WITH (FORCE)`);
      await root.end();
      s.done();
    });

    it("SQLite -> Postgres -> SQLite gives the identical normalized dump", async () => {
      const source = openSnapshot(fixtureDb(join(s.dir, "fixture.db")));
      const loaded = await copyToPostgres(source, db);
      expect(await verify(db, loaded)).toEqual([]);
      const out = join(s.dir, "reversed.db");
      const { read, problems } = await reverseToSqlite(db, out);
      expect(problems).toEqual([]);
      const back = openSnapshot(out);
      expect(differences(read, summarize(back, false))).toEqual([]);
      expect(dump(back)).toBe(dump(source));
      expect(differences(summarize(source), summarize(back))).toEqual([]);
      source.close();
      back.close();
    });

    it("keeps exact values: 17-digit floats, integers past 2^53, '' apart from NULL, escapes", async () => {
      const { rows } = await db.query(
        "SELECT ts::text AS ts, cache_read::text AS big, project, text FROM events WHERE id IN ('e1', 'e2', 'e3 ''quoted''') ORDER BY id",
      );
      expect(rows[0]).toMatchObject({ ts: "1764012345.1234567", big: "9007199254740993", project: "snow-game" });
      expect(rows[1]?.["text"]).toContain("a tab\there, a backslash \\ and \\N, a newline\nand a CR\r and emoji 🚀");
      expect(rows[2]).toMatchObject({ project: "", text: "" });
      expect(rows[1]?.["project"]).toBeNull();
    });

    it("hands out ids after the copied ones and the AUTOINCREMENT marks", async () => {
      const add = async (sql: string) => String((await db.query(sql)).rows[0]?.["id"]);
      expect(
        await add(
          "INSERT INTO notices (created_at, level, title, state) VALUES (1, 'inbox', 't', 'kept') RETURNING id",
        ),
      ).toBe("42");
      expect(
        await add(
          "INSERT INTO pushes (at, level, channel, count, ok, title) VALUES (1, 'digest', 'webpush', 1, 1, 't') RETURNING id",
        ),
      ).toBe("12");
      expect(
        await add(
          "INSERT INTO tasks (project, title, status, created_at, updated_at) VALUES ('p', 't', 'todo', 1, 1) RETURNING id",
        ),
      ).toBe("39");
      expect(
        await add(
          "INSERT INTO rule_sweeps (at, days, answers, repeated, risky, covered, model_calls) VALUES (1, 1, 1, 0, 0, 0, 0) RETURNING id",
        ),
      ).toBe("2");
      const out = join(s.dir, "after-writes.db");
      await reverseToSqlite(db, out);
      const back = openSnapshot(out);
      const seq = back.prepare("SELECT name, seq FROM sqlite_sequence ORDER BY name").all();
      expect(seq).toEqual([
        { name: "endings", seq: 7 },
        { name: "notices", seq: 42 },
        { name: "pushes", seq: 12 },
      ]);
      back.close();
    });

    it("refuses a database with rows unless told to replace them, and reloads the same on a rerun", async () => {
      const source = openSnapshot(fixtureDb(join(s.dir, "again.db")));
      await expect(copyToPostgres(source, db)).rejects.toThrow("already holds rows");
      const loaded = await copyToPostgres(source, db, true);
      expect(await verify(db, loaded)).toEqual([]);
      source.close();
    });

    it("stops on a foreign-key orphan instead of loading it", async () => {
      const path = fixtureDb(join(s.dir, "orphan.db"));
      const { DatabaseSync } = await import("node:sqlite");
      const w = new DatabaseSync(path);
      w.exec("INSERT INTO task_comments (task_id, at, author, text) VALUES (999999, 1, 'a', 'orphan')");
      w.close();
      const source = openSnapshot(path);
      await expect(copyToPostgres(source, db, true)).rejects.toThrow(/foreign key/);
      source.close();
      const { rows } = await db.query("SELECT count(*)::int AS n FROM tasks");
      expect(rows[0]?.["n"]).toBe(38); // the failed run changed nothing: the rerun above is still there
    });

    it("survives a copy the client aborts mid-COPY: the earlier load stays and the connection still answers", async () => {
      const path = fixtureDb(join(s.dir, "blob.db"));
      const { DatabaseSync } = await import("node:sqlite");
      const w = new DatabaseSync(path);
      w.exec(
        "INSERT INTO events (id, machine, agent, session, ts, kind, text) VALUES ('blob', 'm', 'a', 's', 1, 'prompt', X'00ff')",
      );
      w.close();
      const source = openSnapshot(path);
      await expect(copyToPostgres(source, db, true)).rejects.toThrow("events.text: a blob value in a text column");
      source.close();
      expect((await db.query("SELECT 1 AS one")).rows).toEqual([{ one: 1 }]);
      expect((await db.query("SELECT count(*)::int AS n FROM tasks")).rows[0]?.["n"]).toBe(38);
      expect((await db.query("SELECT count(*)::int AS n FROM events WHERE id = 'blob'")).rows[0]?.["n"]).toBe(0);
    });

    it("compares a Postgres -0 equal to SQLite's 0.0 (SQLite cannot store -0)", async () => {
      const source = openSnapshot(fixtureDb(join(s.dir, "zero.db")));
      const loaded = await copyToPostgres(source, db, true);
      await db.query("UPDATE talk_usage SET cost = '-0'::float8 WHERE kind = 'say'");
      expect((await db.query("SELECT cost::text AS c FROM talk_usage WHERE kind = 'say'")).rows[0]?.["c"]).toBe("-0");
      expect(await verify(db, loaded)).toEqual([]);
      const out = join(s.dir, "zero-back.db");
      expect((await reverseToSqlite(db, out)).problems).toEqual([]);
      const back = openSnapshot(out);
      expect(dump(back)).toBe(dump(source));
      back.close();
      source.close();
    });
  },
);
