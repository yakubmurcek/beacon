// The command line: node bundle/beacon-db.js <command> (see cli.ts for the commands). Only wiring lives here.
import { fileURLToPath } from "node:url";
import { main } from "./cli.js";
import { connectPg, migrateDb } from "./pg.js";

// The bundle carries the migrations next to it (build.mjs); from a checkout they sit one folder up from dist/.
const bundled = fileURLToPath(new URL("drizzle", import.meta.url));
const folder = process.env["BEACON_MIGRATIONS"] ?? bundled;

process.exitCode = await main(process.argv.slice(2), {
  connect: connectPg,
  migrate: (url) => migrateDb(url, folder),
  env: process.env,
  out: (line) => {
    process.stdout.write(`${line}\n`);
  },
  err: (line) => {
    process.stderr.write(`${line}\n`);
  },
  now: () => performance.now(),
});
