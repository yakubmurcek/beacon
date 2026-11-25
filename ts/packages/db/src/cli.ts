import { parseArgs } from "node:util";
import { copyToPostgres, verify } from "./copy.js";
import type { Pg } from "./pg.js";
import { reverseToSqlite } from "./reverse.js";
import { differences, dumpLines, openSnapshot, summarize } from "./sqlite.js";

const USAGE = `beacon-db: the SQLite <-> Postgres copy tools (docs/design/2026-11-ts-migration.md, "Data")

  copy    --sqlite <snapshot.db> [--database <url>] [--replace]   load Postgres from a snapshot, then verify it
  reverse --sqlite <new.db> [--database <url>]                    write Postgres back into a fresh SQLite file
  compare <a.db> <b.db>                                           same rows, values and storage classes?
  dump    <file.db>                                               the normalized text the compare is about
  migrate [--database <url>]                                      apply the committed migrations

--database defaults to BEACON_DATABASE_URL. Any difference exits 1, a usage or data error 2.`;

export interface Deps {
  readonly connect: (url: string) => Promise<Pg>;
  readonly migrate: (url: string) => Promise<void>;
  readonly env: NodeJS.ProcessEnv;
  readonly out: (line: string) => void;
  readonly err: (line: string) => void;
  readonly now: () => number;
}

class UsageError extends Error {}

function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(2)} s`;
}

function total(tables: Map<string, { rows: number }>): number {
  return [...tables.values()].reduce((n, t) => n + t.rows, 0);
}

async function run(argv: readonly string[], deps: Deps): Promise<number> {
  const { values, positionals } = parseArgs({
    args: [...argv],
    allowPositionals: true,
    options: {
      sqlite: { type: "string" },
      database: { type: "string" },
      replace: { type: "boolean", default: false },
    },
  });
  const [command, ...rest] = positionals;
  const url = (): string => {
    const u = values.database ?? deps.env["BEACON_DATABASE_URL"];
    if (u === undefined || u === "") {
      throw new UsageError("no database: pass --database or set BEACON_DATABASE_URL");
    }
    return u;
  };
  const file = (): string => {
    if (values.sqlite === undefined) {
      throw new UsageError(`${command ?? ""} needs --sqlite <file>`);
    }
    return values.sqlite;
  };
  const report = (problems: readonly string[], ok: string): number => {
    for (const p of problems) {
      deps.err(p);
    }
    deps.out(problems.length === 0 ? ok : `${String(problems.length)} difference(s)`);
    return problems.length === 0 ? 0 : 1;
  };
  switch (command) {
    case "copy": {
      const db = openSnapshot(file());
      const pg = await deps.connect(url());
      try {
        let t = deps.now();
        const loaded = await copyToPostgres(db, pg, values.replace);
        deps.out(
          `copy: ${String(loaded.tables.size)} tables, ${String(total(loaded.tables))} rows in ${seconds(deps.now() - t)}`,
        );
        t = deps.now();
        const problems = await verify(pg, loaded);
        return report(problems, `verify: row counts, checksums and sequences match (${seconds(deps.now() - t)})`);
      } finally {
        db.close();
        await pg.end();
      }
    }
    case "reverse": {
      const path = file();
      const pg = await deps.connect(url());
      let read;
      try {
        const t = deps.now();
        read = await reverseToSqlite(pg, path);
        deps.out(`reverse: ${String(read.size)} tables, ${String(total(read))} rows in ${seconds(deps.now() - t)}`);
      } finally {
        await pg.end();
      }
      const t = deps.now();
      const db = openSnapshot(path);
      try {
        const problems = differences(read, summarize(db, false), "Postgres", path);
        return report(problems, `verify: the new file matches Postgres (${seconds(deps.now() - t)})`);
      } finally {
        db.close();
      }
    }
    case "compare": {
      const [a, b] = rest;
      if (a === undefined || b === undefined) {
        throw new UsageError("compare needs two files");
      }
      const t = deps.now();
      const [x, y] = [openSnapshot(a), openSnapshot(b)];
      try {
        const problems = differences(summarize(x), summarize(y), a, b);
        return report(problems, `compare: identical (${seconds(deps.now() - t)})`);
      } finally {
        x.close();
        y.close();
      }
    }
    case "dump": {
      const [a] = rest;
      if (a === undefined) {
        throw new UsageError("dump needs a file");
      }
      const db = openSnapshot(a);
      try {
        for (const line of dumpLines(db)) {
          deps.out(line);
        }
      } finally {
        db.close();
      }
      return 0;
    }
    case "migrate": {
      await deps.migrate(url());
      deps.out("migrate: up to date");
      return 0;
    }
    case undefined:
    default:
      throw new UsageError(command === undefined ? "no command" : `unknown command ${command}`);
  }
}

/** Runs one command; the exit code is 0 when everything matched, 1 on a difference, 2 on a usage or data error. */
export async function main(argv: readonly string[], deps: Deps): Promise<number> {
  try {
    return await run(argv, deps);
  } catch (error) {
    deps.err(error instanceof Error ? error.message : String(error));
    if (error instanceof UsageError || (error instanceof TypeError && "code" in error)) {
      deps.err(USAGE);
    }
    return 2;
  }
}
