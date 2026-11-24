# Design note: Beacon moves to TypeScript and Postgres, in stages

Status: proposed, 2026-11-24. Waiting for the owner's go before any app code.
Inventory of what exists today: [2026-11-ts-migration-inventory.md](2026-11-ts-migration-inventory.md).

## 1. Problem

Beacon grew from a small usage dashboard into a 45k-line hub (Python standard-library server, SQLite, plain JS
pages) under a "standard library only, no build step" rule that was set for the dashboard and never re-decided.
It is now used dozens of times a day, is written to by every agent session on four machines, and is meant to
show senior work. The stack is the wrong one for that: no shared types between pages and API, a single process
that serves requests and runs nine background loops, twelve modules that each own a piece of the schema, no
migrations.

**Goal.** The same product on TypeScript end to end (Next.js + React pages, a Node API and worker, Postgres with
typed queries), moved without a day where the hub is broken, with a rollback at every step, and the full quality
kit on the new code from its first commit.

**Not goals.** No new features during the move, no redesign of any page (pixel parity is the bar), no change to
how agents and machines talk to the hub (same paths, same JSON, same tokens). The collectors and the agent CLIs
stay Python: they are HTTP clients of a stable API and run on machines where Python is already there; porting
them is a separate decision after the move.

**Prior attempts.** None at a stack move. The lessons that apply come from earlier side apps (a tablet deck, a
three-person board, a second TV app): each new app or URL beside the hub went unused. So this is one app at the
same address, never a second thing to open.

## 2. Decisions

| Question | Options looked at | Pick and why |
|---|---|---|
| Where the code lives | new repo · same repo | **Same repo**, a pnpm workspace beside `hub/`. One history, the inventory and the old tests sit next to the new code, the parity harness can start both apps from one checkout, and the end state is "the Python hub folder deleted", not two repos to reconcile. The kit's gates apply to the workspace from its first commit (own CI job). |
| Backend shape | Next.js route handlers for the API and loops started from `instrumentation.ts` · a separate Node API + worker beside Next.js · Hono or Express | **Fastify API + a worker process + Next.js for pages.** Request serving and background loops are separate processes (today one stuck loop slows every page). Fastify with a Zod type provider gives typed routes and an OpenAPI file for free, which the request fuzzing needs. Next.js stays a page renderer, so its memory and cache behaviour never touch the API. |
| Typed queries | Drizzle · Kysely · Prisma · pgtyped | **Drizzle.** The schema is TypeScript, so row types come from one place; `drizzle-kit` generates plain SQL migration files that can be reviewed and linted (Squawk). Kysely is as good at queries, but its schema lives elsewhere; Prisma adds a query engine and hides SQL. Comparisons put their runtime cost within a few ms of each other ([Neon](https://neon.com/blog/orms-vs-query-builders-for-your-typescript-application), [Marmelab](https://marmelab.com/blog/2025/06/26/kysely-vs-drizzle)). |
| Database | Postgres in the existing cluster · a second Postgres · keep SQLite | **A new database and role in the Postgres 18 cluster already running on the hub server** (it holds one other 1.7 GB database). One more install would cost RAM the box does not have. |
| Styling | port the CSS as is · Tailwind | **Port as is.** `web/tokens.css` and `web/app.css` become global CSS, each page's CSS a CSS module. The owner's look was tuned over weeks; a rewrite in another system is a redesign. `tests/test_design.py`'s rules (tokens only, no second skin) are ported. |
| Pages fetch data | server components with server-side fetch · client fetch of the same JSON API | **Client fetch through one typed client**, server rendering only for the shell. Identity is decided per request from the caller's tailnet address (section 5); a server-side fetch from Next.js would arrive from loopback and lose it. |
| Where it runs | Vercel · the hub server | **The hub server.** The API needs the tailscaled LocalAPI for identity, the local files (knowledge folders, project checkouts, question screenshots), the `claude` CLI for model jobs, and reads are tailnet-only with no login. None of that fits a public host. |
| Backend language | Rust was asked about | **TypeScript.** One language for pages, API and types; Rust only if a measured hot path ever needs it. |

## 3. Target architecture

```
 tailnet doors ─────────────▶ Caddy (one routing table, every door) ──┬─▶ web     Next.js, :4831   pages
  http, https, :4829, MCP     per-path switch: old or new             ├─▶ api     Fastify, :4832   /api, /mcp, /cli, /config.js
                                                                      ├─▶ hub.py  old hub,  :4839  everything not moved yet
                                                                      └─▶ budget, Lighthouse on the home server (passed through)
 worker  Node: the loops, model jobs, alarms ──┐
 api ──────────────────────────────────────────┴─▶ Postgres 18, database `beacon`
 collectors (Python, 4 machines, every 10 s) ──▶ POST /api/ingest (unchanged)
```

Workspace layout:

| Folder | What |
|---|---|
| `apps/web` | Next.js (App Router), one route per page, the shared header, the Czech dictionary |
| `apps/api` | Fastify server (`server.ts`) and the worker (`worker.ts`): two entry points, one package, one set of services |
| `packages/contracts` | Zod schemas of every request and response; the pages' typed client and the API's route types both come from here |
| `packages/db` | Drizzle schema, generated migrations, the SQLite copy and reverse-copy tools |
| `tests/parity` | the harness that runs the old and the new app on the same data and compares answers |

**Memory on the hub server** (2 cores, 3.8 GB, about 2.35 GB available today, swap already in use; the old hub
alone is 500 MB): each unit gets a `MemoryMax` (web 400 MB, api 300 MB, worker 300 MB) and restarts on breach;
Postgres keeps its current settings (`shared_buffers` 128 MB). During the overlap both apps run (about 1.5 GB
together at the caps); the old hub's 500 MB comes back when it retires. If the rehearsal measures more than the
caps, the move waits for a bigger box (the next size up costs a few euros a month); that is the owner's call
then, not a silent swap storm. Known risk: a Next.js 16 standalone memory leak reported with `cacheComponents`
([issue 92287](https://github.com/vercel/next.js/issues/92287)); that option stays off.

## 4. Data

**Schema.** The first migration mirrors the 35 SQLite tables one to one (same names and columns), with real types:
`timestamptz` for times, `boolean` for 0/1 flags, `bigint` identities with their sequences set after the copy,
`NOT NULL` and foreign keys wherever the data allows. The probe on 2026-11-24 found the SQLite file intact, no
foreign-key orphans and no column holding mixed types, so that is most places. JSON text columns stay `text`
in the mirror and become `jsonb` in a later expand/contract migration, after the cut-over, when only Node writes.
Keeping the shape 1:1 keeps the copy simple and the parity comparison honest.

**Differences that bite, each with a parity test.** SQLite `LIKE` is case-insensitive for ASCII, Postgres `LIKE`
is not (search uses `ILIKE`); text ordering follows the locale in Postgres (ordering-sensitive columns use
`COLLATE "C"`); integer division and float sums differ (costs are summed as `numeric` and rounded where the old
code rounds); "today" and day boundaries come from the deployment's time zone setting in both apps; `INSERT OR
REPLACE` becomes `ON CONFLICT … DO UPDATE` (it deletes and reinserts in SQLite, which changes ids); any order the
old code took from `rowid` gets an explicit `ORDER BY`.

**The copy tool** (`packages/db`, run by hand and in CI on a fixture). Reads a `.backup` snapshot of `usage.db`
read-only, loads Postgres with `COPY` inside one transaction (truncate and load, so a rerun gives the same result),
then compares per table the row count and a checksum of the normalized rows on both sides; any difference fails
the run. **The reverse tool** writes Postgres back into a fresh SQLite file the old hub can open. Their round trip
(SQLite → Postgres → SQLite) must give identical normalized dumps on the test fixture (every CI run) and on a copy
of the live database (every rehearsal). The reverse tool is what makes a rollback after the cut-over keep the
writes made since.

**One writer per table at any moment.** The domains are not separable: questions, feedback and outreach file
tasks; endings read questions; Team, Labs, AI jobs and progress read nearly every domain; the loops write tables
the pages read. Moving one domain's tables to Postgres while the old hub still writes or reads them would need
the Python modules to talk Postgres or to call the new API across twelve modules: twice the work and a split brain
if one path is missed. So the data moves **once, for all tables, in one rehearsed cut-over**, and everything before
it is risk-free:

1. pages move one by one on top of the old API (no data risk at all);
2. the new API is built domain by domain against Postgres copies and must answer like the old one (no traffic);
3. one short cut-over switches writes, reads and loops together;
4. a quiet week with the reverse path ready.

**Backups.** A nightly `pg_dump` of `beacon` (custom format, 30 days) replaces the SQLite backup unit after the
cut-over, and the same night a restore into a scratch database with row counts compared, so the backup is proven,
not hoped. The SQLite snapshots keep running until the old hub retires.

## 5. Routing, identity and auth

**One router for every door.** Today four doors lead to the old hub (the short `http` name, the short `https` name
through Caddy, the server's own `:4829` used by collectors and the watchdog, and the public MCP door). In stage 0
every door goes through Caddy, which already serves two of them, and Caddy holds one routing table: path → old hub
or new app. A switch is one line in that table plus a reload (seconds, no dropped connections), committed in the
machine-setup repo. The old hub moves to a new loopback port; nothing outside the server changes.

**Identity stays tailscaled's answer.** The new API decides "owner" exactly like the old one: the request must come
from loopback with exactly one `X-Forwarded-For` address in the tailnet ranges, and the tailscaled LocalAPI `whois`
of that address must be the owner's login; a `Tailscale-User-Login` header is never read. The new hop must keep
that true: Caddy trusts only `127.0.0.1` as a proxy and sets `X-Forwarded-For` to the one client address it
resolved (from `tailscale serve` or the PROXY header), replacing anything else. Stage 0 proves it with tests from
a real tailnet device and from the server itself: the owner's phone is owner on both short names; another tailnet
device is not; a spoofed `X-Forwarded-For` or `Tailscale-User-Login` from a device or from a process on the server
changes nothing; the public MCP door never yields owner. Trust boundary, written in the README: anything that can
run code on the hub server can already read the tokens; everything else is checked per request.

**Writes** keep the same rules: bearer `TASKS_TOKEN` = agent, the owner's tailnet login = owner, the collector
token for ingest only, the cross-site check (JSON content type, `Sec-Fetch-Site`, `Origin`) on every write,
constant-time token comparison, the same body limits (24 MB for feedback, 64 KB for notices). The open relay-token
question (card 383, agents can post as the owner on task notes) is not solved by the move; the new API keeps the
old behaviour until that card is decided, so the parity stays exact.

## 6. Stages, switches and switching back

**Stage 0: skeleton (owner's step 4 go needed).**
The workspace with the full kit (section 8), Postgres database and role, the three units, the deploy pipeline,
Caddy in front of every door with all paths still to the old hub, the identity tests above, the copy and reverse
tools rehearsed on a copy of the live database (timed), and the fallback counter. Done when the hub works exactly
as before through the new router for a day and the round-trip copy is identical.

**Stage 1: pages, one at a time, on the old API.**
Order: Inbox first as the pilot (small, proves the switch end to end), then by use: Tasks, Decisions, Team, Usage,
Machines, Outreach, Knowledge, AI jobs, Labs (microphone needs the https name), Build log, Rank, Power. Per page:

1. Re-read the inventory diff: `git log` on the page's files since the last port, so a feature another session
   added last night is not lost.
2. Port it; the page reads the same JSON through the typed client, whose Zod schemas are checked against
   recorded live answers (a schema that rejects a real answer fails CI).
3. Gate: screenshots of old and new at 390 px and 1440 px, English and Czech, on the same fixture data with a
   frozen clock, compared pixel by pixel on the same machine (the one place a pixel diff is a fair gate: same fonts,
   same run); the layout invariants and axe pass; the owner's main taps on the page run as a Playwright script on
   both.
4. Switch: the page's line in the router points to the new app. The old page stays reachable at `/old/<page>` for
   the quiet week.
5. Switch back: the line returns to the old hub; one command, under a minute, no data involved.

**Stage 2: the API, built and proven with no traffic.**
Domain by domain (usage and ingest, machines and names, work and decisions, notices, rank, labs and talk, outreach
and knowledge, build log and power), the new API implements every route in the inventory. The parity harness
starts the old hub on a SQLite snapshot and the new API on the Postgres copy of the same snapshot, both with a
frozen clock, then sends every GET in `tests/test_routes.py`, every path recorded in a week of the router's access
log (paths and query strings only, no bodies), and a scripted sequence of writes followed by reads, and compares
the JSON. Differences must be zero or listed with a reason in the harness. It runs nightly on a fresh snapshot, so
a change another session makes to the old hub shows up the next morning as a diff, not after the cut-over.
The worker's loops are ported here too; their model calls run against a stub in the harness (no spend).

**Stage 3: the cut-over (one night, rehearsed three times on copies first, each timed).**
1. Freeze: the router answers writes with `503` and `Retry-After: 120` (reads still served by the old hub).
   Collectors resend their batch on the next tick; the CLI retries; hooks with short timeouts may drop a gate-log
   line or an ending, which the nightly endings backstop already re-files. Measured target: under 3 minutes.
2. Snapshot, copy, verify counts and checksums (rehearsed time of the copy on a 140 MB file goes here).
3. Restart the old hub with alarms off; start the worker with alarms on (exactly one process sends alarms; the
   watchdog checks the alarm heartbeat that `/api/live` carries and must see it within 2 minutes).
4. Flip every `/api`, `/mcp`, `/cli`, `/config.js` route and the ingest to the new API; lift the freeze.
5. Check: the parity harness's smoke set against live, a write from an agent and one from the owner's phone, one
   collector batch from each machine, one notice through `telegram-alarm`.
Switch back during the quiet week: freeze, reverse copy into a fresh SQLite file, start the old hub with alarms on
and the worker's alarms off, flip the routes back, lift the freeze. Rehearsed with the cut-over, so the writes made
since are kept.

**Stage 4: quiet week, then retire.** Seven days with the fallback counter at zero, then: the old hub's unit is
removed, `hub/` and `web/` are deleted in one commit (history keeps them), the SQLite backup unit is replaced,
README rewritten, the removal recorded. Collectors and the CLI keep their paths.

**The fallback counter** ("a week with no fallback used" in the done-test). Every switch-back is a commit to the
router table, and every request to `/old/*` or answered by the old hub for a moved path is a line in Caddy's
access log. A small worker job counts both per day and shows them on Machines; the week starts again at any
non-zero day.

## 7. Background jobs and alarms

Each loop in the inventory becomes a job in the worker with the same schedule, the same daily model-call caps and
the same `claude -p` / Haiku calls, run through one job runner that records every run (start, end, outcome,
model cost) in a `job_runs` table, so Machines and AI jobs show them as today. A job that throws is logged with its
cause and retried on its next tick; a job that misses two ticks raises a Beacon notice. Units: `beacon-web`,
`beacon-api`, `beacon-worker`, each with a one-line description, `Restart=always`, `MemoryMax`, and a health
endpoint the watchdog polls; all three go on Machines and on the freshness list (they run a release directory, see
below). Only the worker sends alarms, and only where `HUB_ALARMS=1`, as today.

## 8. Quality kit from the first commit

Coordinated with the checks task (#1973) and the portable kit task (#1981): the new workspace is the kit's first
TypeScript install, so whatever the kit becomes is proven here. Gates on every push, in the one `scripts/check`:

| Concern | Check |
|---|---|
| Types | `tsc` strict, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`; no `any` (lint) |
| Lint, format | typescript-eslint `strictTypeChecked`, Biome format; zero findings from day one, so no ratchet needed |
| Dead code, copy-paste | knip, jscpd under 1% |
| Tests | Vitest with coverage floors that may only rise; the parity harness |
| Requests | a table test generated from the route table (no token → 401/403, bad JSON → 400, too big → 413, unknown → 404, never 5xx); Schemathesis on the OpenAPI file from the Zod schemas, short seeded run per push, long nightly |
| Database | Squawk on every migration file ([sbdchd/squawk](https://github.com/sbdchd/squawk)); migrations are immutable once merged (hash check); empty → head and fixture → head give the same schema dump; committed `schema.sql` must match; expand/contract only (every migration works with the running release and the next) |
| Races | one connection pool; writes that read-then-write in a transaction with the right isolation or `SELECT … FOR UPDATE`; a 16-way concurrent write test on the API against a real Postgres (no 5xx, no lost or doubled rows) |
| UI | Playwright over every page at 390 / 1440 px, EN / CS: no horizontal overflow, no clipped text, tap targets ≥ 44 px, no console errors or failed requests; axe |
| Supply chain | pnpm lockfile, `--frozen-lockfile`, install scripts off except an allowlist, a release-age cooldown on new versions (pnpm setting; exact name verified in stage 0), osv-scanner, Dependabot, Actions pinned by SHA, zizmor, CodeQL, gitleaks |
| Anti-gaming | the not-loosened guard and the canaries: one known-bad patch per check must turn it red, nightly |

CI runs the Python checks and the TypeScript checks as two jobs; both must be green.

## 9. Deploy

The server's git checkout stays the old hub's deploy until it retires. The new app deploys as releases: CI on a
green `main` builds once (`next build` standalone output, the API bundled with esbuild) and uploads one tarball per
commit; a `beacon-deploy` timer on the hub server (shown on Machines) takes the newest green build, unpacks it to
`releases/<commit>`, runs pending migrations (expand-only, so the running release keeps working), points the
`current` link at it, restarts the three units and checks each health endpoint for the new commit. A failed check
points `current` back and restarts again, then raises a notice. Three releases are kept (the disk is 84% full;
nothing is built on the server). Rolling back code = repointing the link; rolling back a schema change = the next
migration, never an edit.

## 10. Coordination with other sessions

Other agents keep working on Beacon during the move.
- Before a page moves, its changes still land in the old hub; the porting session re-checks `git log` on the
  page's files right before its switch (stage 1, step 1).
- From the day the first page moves, Beacon's `CLAUDE.md` gets a "Moved" table: a moved page changes only in
  `apps/web`. The same for the API at the cut-over.
- No session touches another session's work in progress; a feature freeze applies only to the page being switched,
  only for the hours of its switch, announced on its task.
- The nightly parity run surfaces any change to the old API as a diff the porting session has to carry over.
- `scripts/nav.py` stays the one source of the header until the old pages are gone; it gains the React header as a
  target, then moves to the workspace.

## 11. What is dropped

- `hub/apps.py`'s pass-through for budget and Lighthouse: Caddy forwards them (websockets included, `X-Hub-Host`
  set the same way).
- The hand-made stale-while-revalidate cache: the API gets one cache helper with the same freshness per route,
  measured against today's answer times.
- The "standard library only, no build step" rule, recorded in the README with why.
- The `/cli/*` file serving stays (the Windows box needs it), served from the release directory.
- Features nobody uses are not deleted silently during the move (the rules feature has had no rule in its tables;
  push subscriptions and Labs history are small). They are listed for the owner after the cut-over; porting them
  is cheap, deleting them is his call.

## 12. Risks

| Risk | Plan |
|---|---|
| RAM on the hub server during the overlap | caps per unit, measured in stage 0; bigger box as the fallback, his call |
| Identity through the new hop | stage 0 identity tests from real devices before any page moves |
| A Postgres behaviour difference changes a number on a page | parity harness on live-shaped data nightly; listed differences only |
| Cut-over takes longer than the freeze | rehearsed three times and timed; abort rule: if the copy or its verification fails, lift the freeze with the old hub still in charge (nothing has switched yet) |
| Lost hook writes during the freeze | night window, under 3 minutes, endings backstop re-files; gate decisions read during the freeze fall back as the hook does today when the hub is down |
| Other sessions change the old hub mid-move | inventory diff before each switch, nightly parity run, the "Moved" table |
| Two alarm senders or none | one flag, flipped in the runbook; the watchdog checks the heartbeat |

## 13. Done per stage

| Stage | Done when |
|---|---|
| 0 | kit gates green in CI; hub unchanged through the new router for 24 h; identity tests pass from a real phone; round-trip copy identical on a live copy; three units and the deploy timer on Machines |
| 1 | each page: pixel and invariant gates green, its taps scripted, switched, no switch-back for 2 days before the next of the top three pages |
| 2 | parity harness: zero unlisted differences across every route and a week of real paths, three nights in a row |
| 3 | cut-over rehearsed three times; done live; smoke set green; one collector batch from each machine seen |
| 4 | seven days with the fallback counter at zero; old hub removed; same blind review brief on the new code, median of three reviewers 9 or more |
