import { join } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterAll, beforeEach, describe, expect, it } from "vitest";
import { main } from "../src/cli.js";
import { copyToPostgres, verify } from "../src/copy.js";
import { reverseToSqlite } from "../src/reverse.js";
import { dump, openSnapshot } from "../src/sqlite.js";
import { FakePg, fixtureDb, scratch } from "./helpers.js";

const s = scratch();
afterAll(s.done);
let n = 0;
const file = (name: string) => join(s.dir, `${String((n += 1))}-${name}`);

describe("copy, verify and reverse (against the in-memory stand-in)", () => {
  let pg: FakePg;
  beforeEach(() => {
    pg = new FakePg();
  });

  it("load every table in one transaction, set the sequences, and read back the same", async () => {
    const db = openSnapshot(fixtureDb(file("fixture.db")));
    const loaded = await copyToPostgres(db, pg);
    expect(pg.log[0]).toBe("BEGIN");
    expect(pg.log.filter((l) => l === "COMMIT")).toHaveLength(1);
    expect(loaded.tables.get("tasks")?.rows).toBe(38);
    expect(loaded.sequences.get("notices")).toBe("41"); // the AUTOINCREMENT mark, above the highest id (5)
    expect(loaded.sequences.get("pushes")).toBe("11"); // no rows left, the mark stays
    expect(loaded.sequences.get("endings")).toBe("7");
    expect(loaded.sequences.get("tasks")).toBe("38");
    expect(loaded.sequences.get("rule_sweeps")).toBe("1");
    expect(await verify(pg, loaded)).toEqual([]);
    db.close();
  });

  it("refuse to load over rows unless asked to replace them, and rerun to the same result", async () => {
    const db = openSnapshot(fixtureDb(file("fixture.db")));
    const first = await copyToPostgres(db, pg);
    await expect(copyToPostgres(db, pg)).rejects.toThrow("tasks already holds rows: pass --replace");
    expect(pg.log.at(-1)).toBe("ROLLBACK");
    const again = await copyToPostgres(db, pg, true);
    expect(again).toEqual(first);
    expect(await verify(pg, again)).toEqual([]);
    db.close();
  });

  it("report a changed row, a lost row, a wrong sequence and a table never copied", async () => {
    const db = openSnapshot(fixtureDb(file("fixture.db")));
    const loaded = await copyToPostgres(db, pg);
    const tasks = pg.tables.get("tasks") ?? [];
    tasks[0] = (tasks[0] ?? "").replace("snow-game", "snow-gamf");
    pg.tables.get("labs_calls")?.pop();
    pg.sequences.set('"endings"', "6");
    pg.sequences.set('"rule_sweeps"', null);
    loaded.tables.delete("palette");
    expect(await verify(pg, loaded)).toEqual([
      "tasks: 38 rows on both sides, different contents",
      "palette: not copied",
      "labs_calls: 3 rows in SQLite, 2 in Postgres",
      "rule_sweeps: sequence at unset, expected 1",
      "endings: sequence at 6, expected 7",
    ]);
    db.close();
  });

  it("roll back a failed load and a failed read", async () => {
    const db = openSnapshot(fixtureDb(file("fixture.db")));
    pg.failOn = /^SELECT setval/;
    await expect(copyToPostgres(db, pg)).rejects.toThrow("refused");
    expect(pg.log.at(-1)).toBe("ROLLBACK");
    pg.failOn = undefined;
    const loaded = await copyToPostgres(db, pg, true);
    pg.tables.set("palette", ["only-one-field"]);
    await expect(verify(pg, loaded)).rejects.toThrow("palette: a COPY line with 1 fields");
    expect(pg.log.at(-1)).toBe("ROLLBACK");
    db.close();
  });

  it("write Postgres back into a fresh file holding exactly the snapshot's data, sqlite_sequence included", async () => {
    const source = openSnapshot(fixtureDb(file("fixture.db")));
    await copyToPostgres(source, pg);
    const out = file("reversed.db");
    const read = await reverseToSqlite(pg, out);
    expect(read.get("events")?.rows).toBe(3);
    const back = openSnapshot(out);
    expect(dump(back)).toBe(dump(source));
    source.close();
    back.close();
  });

  it("never write over an existing file, and leave no half-filled one behind a failed read", async () => {
    const existing = fixtureDb(file("existing.db"));
    await expect(reverseToSqlite(pg, existing)).rejects.toThrow("exists: the reverse copy writes a fresh file only");
    pg.failOn = /^SELECT pg_sequence_last_value/;
    const out = file("failed.db");
    await expect(reverseToSqlite(pg, out)).rejects.toThrow("refused");
    expect(pg.log.at(-1)).toBe("ROLLBACK");
    const db = new DatabaseSync(out);
    expect(db.prepare("SELECT count(*) AS n FROM tasks").get()?.["n"]).toBe(0);
    db.close();
  });
});

describe("the command line", () => {
  const run = async (
    argv: string[],
    pg = new FakePg(),
    env: NodeJS.ProcessEnv = { BEACON_DATABASE_URL: "postgresql:///x" },
  ) => {
    const out: string[] = [];
    const err: string[] = [];
    const urls: string[] = [];
    let clock = 0;
    const code = await main(argv, {
      connect: (url) => {
        urls.push(url);
        return Promise.resolve(pg);
      },
      migrate: (url) => {
        urls.push(`migrate ${url}`);
        return Promise.resolve();
      },
      env,
      out: (l) => out.push(l),
      err: (l) => err.push(l),
      now: () => (clock += 1500),
    });
    return { code, out, err, urls };
  };

  it("copies, reverses, compares and dumps, timing each step", async () => {
    const pg = new FakePg();
    const fixture = fixtureDb(file("cli.db"));
    const copied = await run(["copy", "--sqlite", fixture], pg);
    expect(copied).toMatchObject({ code: 0, err: [], urls: ["postgresql:///x"] });
    expect(copied.out[0]).toMatch(/^copy: 34 tables, \d+ rows in 1\.50 s$/);
    expect(copied.out[1]).toBe("verify: row counts, checksums and sequences match (1.50 s)");
    const back = file("cli-back.db");
    const reversed = await run(["reverse", "--sqlite", back, "--database", "postgresql:///y"], pg);
    expect(reversed).toMatchObject({ code: 0, err: [], urls: ["postgresql:///y"] });
    expect(reversed.out[1]).toBe("verify: the new file matches Postgres (1.50 s)");
    expect(await run(["compare", fixture, back])).toMatchObject({ code: 0, out: ["compare: identical (1.50 s)"] });
    const dumped = await run(["dump", back]);
    expect(dumped.out[0]).toMatch(/^-- alarms \(/);
    expect(await run(["migrate"])).toMatchObject({ code: 0, urls: ["migrate postgresql:///x"] });
  });

  it("exits 1 on a difference", async () => {
    const a = fixtureDb(file("a.db"));
    const b = fixtureDb(file("b.db"));
    const db = new DatabaseSync(b);
    db.exec("DELETE FROM palette");
    db.close();
    const res = await run(["compare", a, b]);
    expect(res).toMatchObject({ code: 1, out: ["1 difference(s)"] });
    expect(res.err[0]).toMatch(/^palette: 2 rows in /);
  });

  it("exits 2 with the usage on a wrong call, and 2 on a data error", async () => {
    for (const argv of [[], ["nope"], ["copy"], ["compare", "a"], ["dump"], ["copy", "--bogus"]]) {
      const res = await run(argv);
      expect(res.code).toBe(2);
      expect(res.err.at(-1)).toContain("beacon-db: the SQLite <-> Postgres copy tools");
    }
    expect((await run(["migrate"], new FakePg(), {})).err[0]).toBe(
      "no database: pass --database or set BEACON_DATABASE_URL",
    );
    const missing = await run(["copy", "--sqlite", file("missing.db")]);
    expect(missing).toMatchObject({ code: 2 });
    expect(missing.err).toHaveLength(1);
  });
});
