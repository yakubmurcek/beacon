# Design note: Beacon moves to TypeScript and Postgres, in stages

Status: proposed, 2026-11-24; revised the same day after a cold outside review (its findings and what changed: section 14).
Waiting for the owner's go before any app code.
Inventory of what exists today: [2026-11-ts-migration-inventory.md](2026-11-ts-migration-inventory.md).

## 1. Problem

Beacon grew from a small usage dashboard into a 40k-line hub (26k of it in `hub/` and `web/`, the part rewritten) (Python standard-library server, SQLite, plain JS
pages) under a "standard library only, no build step" rule that was set for the dashboard and never re-decided.
It is now used dozens of times a day, is written to by every agent session on four machines, and is meant to
show senior work. The stack is the wrong one for that: no shared types between pages and API, a single process
that serves requests and runs over a dozen background jobs, twelve modules that each own a piece of the schema, no
migrations.

**Goal.** The same product on TypeScript end to end (Next.js + React pages, a Node API and worker, Postgres with
typed queries), moved without a day where the hub is broken, with a rollback at every step, and the full quality
kit on the new code from its first commit.

**Not goals.** The move itself adds no features (other sessions keep adding them, under the rules in section 10), no redesign of any page (pixel parity is the bar), no change to
how agents and machines talk to the hub (same paths, same JSON, same tokens). The collectors and the agent CLIs
stay Python: they are HTTP clients of a stable API and run on machines where Python is already there; porting
them is a separate decision after the move.

**Prior attempts.** None at a stack move. The lessons that apply come from earlier side apps (a tablet deck, a
three-person board, a second TV app): each new app or URL beside the hub went unused. So this is one app at the
same address, never a second thing to open.

## 2. Decisions

| Question | Options looked at | Pick and why |
|---|---|---|
| Rewrite at all | fix in place (JSDoc types are already checked by `tsc`, the loops could move to a second Python process, a migrations table is a day) · move the stack | **Move**, the owner's decision of 2026-11-24, made knowing the cheaper path: the hub is also the proof of working on the stack a TypeScript/React/Node employer runs, which fixing Python cannot show. The cost is real (estimates in section 6), so each stage ends in a coherent state and there is a stop rule. |
| Where the code lives | new repo · same repo | **Same repo**, a pnpm workspace beside `hub/`. One history, the inventory and the old tests sit next to the new code, the parity harness can start both apps from one checkout, and the end state is "the Python hub folder deleted", not two repos to reconcile. The kit's gates apply to the workspace from its first commit (own CI job). |
| Backend shape | Next.js route handlers for the API and loops started from `instrumentation.ts` · a separate Node API + worker beside the pages · Hono or Express | **Fastify API + a worker process.** Request serving and background loops are separate processes (today one stuck loop slows every page). Fastify with a Zod type provider gives typed routes and an OpenAPI file for free, which the request fuzzing needs. |
| How pages are served | Next.js server (`next start`) · Next.js static export served by Caddy · Vite + React static build | **Next.js with `output: 'export'`, served by Caddy as files.** Pages fetch from the browser anyway (row below), so a Node page server would be a ~400 MB process serving a shell on a box that already swaps. The static export keeps Next.js (routing, layouts, the stack the job runs) at zero runtime memory and no third unit; if a page ever needs server rendering, `next start` can be added then. |
| Typed queries | Drizzle · Kysely · Prisma · pgtyped | **Drizzle.** The schema is TypeScript, so row types come from one place; `drizzle-kit` generates plain SQL migration files that can be reviewed and linted (Squawk). Kysely is as good at queries, but its schema lives elsewhere; Prisma adds a query engine and hides SQL. Comparisons put their runtime cost within a few ms of each other ([Neon](https://neon.com/blog/orms-vs-query-builders-for-your-typescript-application), [Marmelab](https://marmelab.com/blog/2025/06/26/kysely-vs-drizzle)). |
| Database | Postgres in the existing cluster · a second Postgres · keep SQLite | **A new database and role in the Postgres 18 cluster already running on the hub server** (it holds one other 1.7 GB database). One more install would cost RAM the box does not have. |
| Styling | port the CSS as is · Tailwind | **Port as is.** `web/tokens.css` and `web/app.css` become global CSS, each page's CSS a CSS module. The owner's look was tuned over weeks; a rewrite in another system is a redesign. `tests/test_design.py`'s rules (tokens only, no second skin) are ported. |
| Pages fetch data | server components with server-side fetch · client fetch of the same JSON API | **Client fetch through one typed client.** Identity is decided per request from the caller's tailnet address (section 5); a server-side fetch would arrive from loopback and lose it. |
| Where it runs | Vercel · the hub server | **The hub server.** The API needs the tailscaled LocalAPI for identity, the local files (knowledge folders, project checkouts, question screenshots), the `claude` CLI for model jobs, and reads are tailnet-only with no login. None of that fits a public host. |
| Backend language | Rust was asked about | **TypeScript.** One language for pages, API and types; Rust only if a measured hot path ever needs it. |

## 3. Target architecture

```
 tailnet doors ─────────────▶ Caddy (one routing table, every door) ──┬─▶ files   Next.js static export, pages (current release)
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
| `apps/web` | Next.js (App Router, static export), one route per page, the shared header; reads `web/tokens.css` and the Czech dictionary from their current files until the old pages are gone (one source, no double edits) |
| `apps/api` | Fastify server (`server.ts`) and the worker (`worker.ts`): two entry points, one package, one set of services |
| `packages/contracts` | Zod schemas of every request and response; the pages' typed client and the API's route types both come from here |
| `packages/db` | Drizzle schema, generated migrations, the SQLite copy and reverse-copy tools |
| `tests/parity` | the harness that runs the old and the new app on the same data and compares answers |

**Memory on the hub server** (2 cores, 3.8 GB, about 2.35 GB available today, swap already in use; the old hub
alone is 500 MB). Two new Node processes (api, worker), each with `--max-old-space-size` and a systemd `MemoryHigh`
(throttles and reclaims) at 300 MB, `MemoryMax` only as the last line at 450 MB (it is a kill mid-write, not a
restart). Model calls spawn the `claude` CLI, which is itself a few hundred MB: they never run inside the api or
worker cgroup but each in its own transient scope (`systemd-run --scope`, its own memory cap, one at a time per job
kind), so a model call can neither push the API over its cap nor be killed with it. Postgres keeps its current
settings (`shared_buffers` 128 MB). Stage 0 measures all of this under the old hub's real load; if the overlap does
not fit, the move waits for the next server size up (a few euros a month), the owner's call, not a silent swap storm.

## 4. Data

**Schema.** The first migration mirrors the 34 SQLite tables one to one (same names and columns), and the same
value shapes: times are epoch seconds as `double precision` (the old code stores `time.time()` floats and the API
returns them raw; `timestamptz` would round them to microseconds and break both the JSON parity and the round
trip), 0/1 flags stay `integer` (JSON `1`, not `true`), identities are `bigint` with their sequences set after the
copy, and `NOT NULL` and foreign keys go wherever the data allows. The probe on 2026-11-24 found the SQLite file intact, no
foreign-key orphans and no column holding mixed types, so that is most places. JSON text columns stay `text`
in the mirror; they become `jsonb`, times `timestamptz` and flags `boolean` in later expand/contract migrations, after the cut-over, when only Node writes.
Keeping the shape 1:1 keeps the copy simple and the parity comparison honest.

**Differences that bite, each with a parity test.** SQLite `LIKE` is case-insensitive for ASCII, Postgres `LIKE`
is not (search uses `ILIKE`); text ordering follows the locale in Postgres (ordering-sensitive columns use
`COLLATE "C"`); integer division and float sums differ (costs are summed as `numeric` and rounded where the old
code rounds); "today" and day boundaries come from the deployment's time zone setting in both apps; `INSERT OR
REPLACE` (used only on natural-key tables) becomes `ON CONFLICT … DO UPDATE`; any order the
old code took from `rowid` gets an explicit `ORDER BY`.

**The copy tool** (`packages/db`, run by hand and in CI on a fixture). Reads a `.backup` snapshot of `usage.db`
read-only, loads Postgres with `COPY` inside one transaction (truncate and load, so a rerun gives the same result),
then compares per table the row count and a checksum of the normalized rows on both sides; any difference fails
the run. **The reverse tool** writes Postgres back into a fresh SQLite file: the old hub's own code creates
the schema (its modules create and alter their tables on start), the tool only fills it, and it restores
`sqlite_sequence` for the AUTOINCREMENT tables (endings, notices, pushes) so ids are never reused after a rollback. Their round trip
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

**Disk.** The hub server's disk is 84% full (5.8 GB free). New costs: the Postgres database (~300 MB), 30 nightly
dumps (~1 GB compressed, measured in stage 0), a scratch restore dropped right after its check, three releases
(~150 MB each), while the SQLite snapshots keep running. Stage 0 measures the real sum and frees old backups
(four 27 MB copies from September, more in the backup folder) before it adds anything; a disk watch already alarms.

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

Estimates are agent-session days, the owner's time is a few taps per stage. **Stop rule:** each stage ends in a
coherent state. If stage 1 runs past twice its estimate, or a page needs more than two switch-backs, the move stops
with the pages moved so far on the new app and the API on Python, which is a sound end state, and the owner decides
whether to continue.

**Stage 0: skeleton (about 2 days; the owner's go needed).**
- The workspace with the kit (section 8), the Postgres database and role, the api and worker units, the deploy
  pipeline, the copy and reverse tools rehearsed on a copy of the live database (timed), the fallback counter.
- Two small changes to the old hub, made now so the cut-over can rely on them: a `HUB_LOOPS=0` switch that starts the
  hub with every background job off (the job classes already take `background=`; today only alarms have a switch),
  and the `tasks` CLI honouring `Retry-After` on a 503 for up to 5 minutes (today it gives up after 12 s; boxes pick
  the new CLI up through `/cli`).
- Doors move behind Caddy **one at a time**: the `https` name first (already Caddy), then the short `http` name, then
  the server's `:4829` door (collectors, watchdog, Lighthouse), the public MCP door last. Each move: `caddy validate`,
  reload, the identity tests from section 5 from a real device, the watchdog probing that door; switch-back is the
  previous `tailscale serve` or Caddy line, one command, written down before the move.
- Done when the hub works exactly as before through the router for 24 hours, every door probed, and the round-trip
  copy is identical on a live copy.

**Stage 1: pages, one at a time, on the old API (about half a day per page, 6-8 days in all).**
Order: Inbox first as the pilot (small, proves the switch end to end), then by use: Tasks, Decisions, Team, Usage,
Machines, Outreach, Knowledge, AI jobs, Labs (microphone needs the https name), Build log, Rank, Power. Per page:

1. Re-read the inventory diff: `git log` on the page's files since the last port, so a feature another session
   added last night is not lost.
2. Port it; the page reads the same JSON through the typed client, whose Zod schemas are checked against
   recorded live answers (a schema that rejects a real answer fails CI).
3. Gate: screenshots of old and new at 390 px and 1440 px, English and Czech, on the same fixture data with a frozen
   browser clock (Playwright's clock), compared on the same machine in the same run with a small per-pixel tolerance
   for text anti-aliasing; a larger difference is a fail unless the porting session shows the owner both pictures
   and he accepts it. The layout invariants and axe pass; the owner's main taps on the page run as a Playwright
   script on both.
4. Switch: the page's line in the router points to the new files. The old page stays reachable at `/old/<page>`
   for the quiet week.
5. Switch back: the line returns to the old hub; one command, under a minute, no data involved.

**Stage 2: the API, built and proven with no traffic (about 8-10 days).**
Domain by domain (usage and ingest, machines and names, work and decisions, notices, rank, labs and talk, outreach
and knowledge, build log and power), the new API implements every route in the inventory. Two proofs:
- **The behaviour suite.** The API-level tests in `tests/` (about 9.6k lines that already describe the write rules:
  question lint, claims, endings, the gate, notices) are turned into black-box HTTP tests that take a base URL and
  run against both apps on the same fixture; a rule either app breaks fails. This is the real parity suite.
- **The data harness.** The old hub started with `HUB_LOOPS=0` on a SQLite snapshot, the new API on the Postgres
  copy of the same snapshot, both under `libfaketime` at the same instant (the old code reads the clock in 140
  places; no seam is added to retiring code); every GET in `tests/test_routes.py` and every path recorded in a week
  of the router's access log (paths and query strings only) are sent to both and the JSON compared. Differences must
  be zero or listed with a reason. It runs nightly on a fresh snapshot, so a change another session makes to the old
  hub shows up the next morning as a diff.
The worker's jobs are ported here too; in both suites model calls go to a stub (no spend, no loops on either side).

**Stage 3: the cut-over (one night, rehearsed three times on copies first, each timed).**
1. Pause the home server's hub timers (task agent, dispatcher, sweep, backstop) for the window.
2. Restart the old hub with `HUB_LOOPS=0`: from here nothing in it writes except HTTP.
3. Freeze: the router answers writes with `503` and `Retry-After: 120`; reads are still served. Collectors resend
   their batch on the next tick (their offsets move only after a good push); the CLI waits; hooks with short
   timeouts may drop a gate-log line or an ending, which the nightly endings backstop re-files. Target: under 3
   minutes.
4. Snapshot, copy, verify counts and checksums. Abort rule: any failure → lift the freeze, restart the old hub with
   loops on; nothing has switched.
5. Start the worker (the only alarm and loop runner from now; the watchdog must see its alarm heartbeat on
   `/api/live` within 2 minutes), flip every `/api`, `/mcp`, `/cli`, `/config.js` route and the ingest to the new
   API, lift the freeze, resume the timers. The old hub stays up with loops off, serving only `/old/*`.
6. Check: the smoke set against live, a write from an agent and one from the owner's phone, one collector batch from
   each machine, one notice through the alarm script.

Switch back during the quiet week: freeze, worker stopped, reverse copy into a fresh SQLite file, old hub restarted
with loops on, routes flipped back, freeze lifted. It only works while Postgres still has the mirror schema, so the
quiet week is a **schema freeze**: the deploy timer refuses any release with a new migration while the old hub's unit
exists, and the reverse copy runs every night against the live database and must give a file the old hub opens and
whose answers match. Rehearsed with the cut-over, so the writes made since are kept.

**Stage 4: quiet week, then retire (one week, then half a day).** Seven days of the owner's normal use with the
fallback counter at zero, then: the old hub's unit is removed, `hub/` and `web/` are deleted in one commit (history
keeps them), the SQLite backup unit is replaced, the README rewritten, the removal recorded. Collectors and the CLI
keep their paths. Only then do the schema improvements (times as `timestamptz`, `jsonb`, `boolean`) start, each as
expand/contract.

**The fallback counter** ("a week with no fallback used" in the done-test). Every switch-back is a commit to the
router table, and every request to `/old/*` or answered by the old hub for a moved path is a line in Caddy's
access log. A small worker job counts both per day and shows them on Machines; the week starts again at any
non-zero day.

## 7. Background jobs and alarms

Each loop in the inventory becomes a job in the worker with the same schedule, the same daily model-call caps and
the same `claude -p` / Haiku calls, run through one job runner that records every run (start, end, outcome,
model cost) in a `job_runs` table, so Machines and AI jobs show them as today. A job that throws is logged with its
cause and retried on its next tick; a job that misses two ticks raises a Beacon notice. Units: `beacon-api` and
`beacon-worker`, each with a one-line description, `Restart=always`, `MemoryMax`, and a health
endpoint the watchdog polls; both go on Machines and on the freshness list (they run a release directory, see
below). Only the worker sends alarms and runs jobs, and only where `HUB_ALARMS=1`, as today. Model calls run in their own
scope (section 3).

## 8. Quality kit from the first commit

Coordinated with the checks task (#1973) and the portable kit task (#1981). This is heavier than a one-user tool
needs on its own; it is here because the kit is the product: the new workspace is its first TypeScript install,
and whatever the kit becomes is proven here. Most of it is config that comes with the template, not weeks of work. Gates on every push, in the one `scripts/check`:

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

The server's git checkout stays the old hub's deploy until it retires. The new app deploys as releases. CI on a
green `main` builds once (`next build` static export, the API and worker bundled with esbuild) and stores one
tarball per commit as a GitHub Actions artifact kept 14 days. A `beacon-deploy` timer on the hub server (shown on
Machines) asks GitHub for the newest green build with a read-only token from the vault, downloads it, checks its
hash, unpacks it to `releases/<commit>`, runs pending migrations (expand-only, so the running release keeps
working), points the `current` link at it, restarts the two units and checks each health endpoint for the new
commit. A failed check before any migration ran points `current` back and restarts again. A failed check after a
migration does not flap: the release stays as it is, the timer stops deploying and sends an urgent notice, because
the old code may not match the new schema; the fix is a forward commit. Three releases are kept (nothing is built
on the server). Rolling back code = repointing the link; rolling back a schema change = the next migration, never
an edit. During the quiet week no migration deploys at all (section 6).

## 10. Coordination with other sessions

Other agents keep working on Beacon during the move; the move adds no features of its own.
- Before a page moves, its changes still land in the old hub; the porting session re-checks `git log` on the
  page's files right before its switch (stage 1, step 1).
- Shared files have one source: the new pages read `web/tokens.css` and `web/i18n-cs.js` directly, so a colour or
  a Czech line is changed once. Only page markup and page scripts exist twice, and only until that page switches.
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
| RAM on the hub server during the overlap | no page server (static export); caps per unit and per model call, measured in stage 0; bigger box as the fallback, his call |
| Identity through the new hop | stage 0 identity tests from real devices before any page moves |
| A Postgres behaviour difference changes a number on a page | parity harness on live-shaped data nightly; listed differences only |
| Cut-over takes longer than the freeze | rehearsed three times and timed; abort rule in stage 3, step 4 |
| Old jobs write after the snapshot, or run twice beside the worker | `HUB_LOOPS=0` on the old hub before the snapshot and for good after the flip |
| A model call or a memory cap kills a process mid-write | model calls in their own scope; `MemoryHigh` first, `MemoryMax` as the last line |
| The rollback stops working during the quiet week | schema freeze, the reverse copy proven every night against live |
| The router becomes the one point of failure | doors moved one at a time with a written switch-back; the watchdog probes every door |
| Lost writes during the freeze | night window, under 3 minutes; the CLI waits out `Retry-After`, the timers are paused, collectors resend, the endings backstop re-files; gate decisions read during the freeze fall back as the hook does today when the hub is down |
| Other sessions change the old hub mid-move | inventory diff before each switch, nightly parity run, the "Moved" table |
| Two alarm senders or none | one flag, flipped in the runbook; the watchdog checks the heartbeat |

## 13. Done per stage

| Stage | Done when |
|---|---|
| 0 | kit gates green in CI; hub unchanged through the router for 24 h, every door probed; identity tests pass from a real phone; round-trip copy identical on a live copy; memory and disk measured under real load; both units and the deploy timer on Machines |
| 1 | each page: pixel and invariant gates green, its taps scripted, switched, no switch-back for 2 days before the next of the top three pages |
| 2 | the behaviour suite green on both apps; the data harness at zero unlisted differences across every route and a week of real paths, three nights in a row |
| 3 | cut-over rehearsed three times; done live; smoke set green; one collector batch from each machine seen |
| 4 | seven days of the owner's normal use with the fallback counter at zero; old hub removed. Separately, as the quality bar for the kit (not a user outcome): the same blind review brief on the new code, median of three reviewers 9 or more |

## 14. Outside review (2026-11-24) and what changed

A fresh reviewer read the first version cold and checked it against the code. Every blocker was confirmed in the
code and is fixed above:

| Finding | Change |
|---|---|
| The snapshot was taken while the old hub's own jobs still wrote, and after the flip they would keep running beside the worker (double model spend, double pings) | `HUB_LOOPS=0` added to the old hub in stage 0; set before the snapshot and kept |
| Times are epoch floats, flags 0/1; `timestamptz`/`boolean` would break the parity and the round trip | mirror keeps `double precision` and `integer`; real types come after the old hub retires |
| One migration in the quiet week would silently break the rollback | schema freeze for the week; reverse copy proven nightly against live |
| A 300 MB `MemoryMax` would kill the `claude` CLI the model jobs spawn | model calls in their own scope; `MemoryHigh` first |
| The CLI gives up after 12 s, the freeze is 3 minutes | CLI honours `Retry-After`; timers paused for the window |
| Stage 0 moved every door at once with no way back | one door at a time, each with a written switch-back |
| "Frozen clock" had no mechanism | `libfaketime` on both processes |
| No "fix in place" option, no cost, no stop rule | added to section 2 and section 6 |
| A Next.js server only to serve a shell | static export served by Caddy |
| Only two test files carried over | the API tests become a black-box suite run against both apps |
| Reverse copy, ids, shared files, deploy fetch, disk, sizes | stated in sections 4, 9, 10 |
