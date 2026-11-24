# Inventory of the Python hub (2026-11-24, at `bc7c26f`)

The contract the TypeScript app must keep. Each porting session re-diffs this against `git log` on the files it
moves, right before its switch (see the design note, "Coordination"). Machines are named by role: **hub server**
(runs the hub, Postgres, Caddy), **home server** (runs most timers, Lighthouse, budget), **laptop**, **Windows box**.

## 1. Pages (13 + 3 passed through)

| Path | File | Main APIs it reads |
|---|---|---|
| `/` and `/tasks`, `/new` | `tasks.html` | `/api/tasks*`, `/api/questions`, `/api/live` |
| `/decisions` (`/questions` redirects) | `questions.html` | `/api/questions*`, `/api/endings`, `/api/rules` |
| `/team` (`/outcomes` redirects) | `team.html` | `/api/team`, `/api/outcomes`, `/api/live`, `/api/rank` |
| `/usage` (`/system` redirects) | `index.html` | `/api/day`, `/api/range`, `/api/meta`, `/api/search`, `/api/session` |
| `/machines` | `machines.html` | `/api/machines`, `/api/names`, `/api/freshness` |
| `/ai` | `ai.html` | `/api/ai` |
| `/outreach` | `outreach.html` | `/api/outreach`, `/outreach/doc`, `/outreach/logo`, POST `/api/outreach/move` |
| `/knowledge` | `knowledge.html` | `/api/knowledge/*` |
| `/labs` (`/talk` redirects to `https…/labs?talk`) | `labs.html` | `/api/labs/*`, `/api/talk/*` |
| `/inbox` | `inbox.html` | `/api/notify*` |
| `/buildlog` | `buildlog.html` | `/api/buildlog*`, `/buildlog/img/*` |
| `/rank` | `rank.html` | `/api/rank` |
| `/power` (`/home` redirects) | `power.html` | `/api/power` (proxied to the power meter) |
| `/budget/*`, `/lighthouse/*` | passed through (`hub/apps.py`) to two Next.js apps on the home server, websockets included | |

Shared per page: `/config.js` (deployment names), the header written by `scripts/nav.py`, `web/i18n-cs.js` (Czech
switch), `web/tokens.css` + `app.css`, the feedback pen (`feedback.js` → POST `/api/feedback`), `sw.js` (push only,
caches nothing), three web manifests, icons, `web/vendor/` (d3 parts, force-graph, modern-screenshot).

## 2. API routes

**GET, exact** (`GET_ROUTES` in `hub/server.py`): `/ca.crt`, `/api/outcomes`, `/api/team`, `/api/live`, `/api/rank`,
`/api/ai`, `/api/endings`, `/api/endings/missed`, `/api/meta`, `/api/machines`, `/api/freshness`, `/api/names`,
`/api/notify`, `/api/notify/vapid`, `/api/outreach`, `/outreach/doc`, `/outreach/logo`, `/api/day`, `/api/range`,
`/api/search`, `/api/session`, `/mcp` (405), `/api/labs/views` (owner), `/api/rules`, `/api/gate/approval`,
`/api/gate/log`, `/api/power`.
**GET, prefix**: `/api/buildlog*` and `/buildlog/img/*` (owner or token), `/api/knowledge/*` (owner), `/api/tasks*`
(incl. `/auth`, `/projects`, `/<id>`), `/api/questions/file/*` (owner), `/api/questions*` (incl. `/kinds`, `/rules`, `/bad`).
**Files**: `/cli/tasks`, `/cli/settings.py`, `/cli/config.example.json`, `/cli/config.json` (the Windows box
refreshes the CLI from here), `/config.js`, static `web/`.

**POST**: `/api/ingest` (collector token), `/api/rank/ingest` (collector token), `/api/tasks`,
`/api/tasks/<id>`, `/api/tasks/<id>/comments|sessions`, `/api/questions`,
`/api/questions/<id>[/answer|reopen|withdraw|applied|claim|release|waiting|handoff|see|flag]`, `/api/outreach/move`,
`/api/feedback` (up to 24 MB), `/api/rules/sweep`, `/api/rules/<id>`, `/api/rules/uses/<id>/undo` (owner only),
`/api/endings`, `/api/endings/<id>/clear|unclear|act`, `/api/labs/ask|forget`, `/api/talk/start|hear|say|act|brief|played|warm`,
`/mcp` (tailnet), `/mcp/<key>` (the one public door), `/api/gate/log`, `/api/names`,
`/api/notify`, `/api/notify/read|subscribe|test`. Plus any method on `/budget/*`, `/lighthouse/*`.

`tests/test_routes.py` pins every GET route's status with and without each service and login: the first parity test.

## 3. Doors and identity

| Door | Path to the hub | Identity |
|---|---|---|
| `http://<short name>` | tailscale serve (service) → `127.0.0.1:4829` | whois of the one `X-Forwarded-For` address |
| `https://<short name>` | tailscale TCP 443 → Caddy `:8444` (PROXY protocol, internal CA) → `:4829` | same |
| `http://<hub server>:4829` | tailscale serve → `:4829` (collectors on two machines, the watchdog, Lighthouse) | same |
| public MCP door | Caddy, Anthropic's outbound ranges only, path `/mcp/*` → `:4829` | the key in the path |

Writes: `Authorization: Bearer TASKS_TOKEN` = agent; the owner's tailnet login (via tailscaled LocalAPI whois,
never a header) = owner; plus a cross-site check (JSON content type, `Sec-Fetch-Site`). Ingest uses `USAGE_TOKEN`.
Reads are open to the tailnet except the owner-only ones marked above.

## 4. Tables (34 plus `sqlite_sequence`, SQLite `usage.db`, 138 MB; integrity and foreign-key checks clean; no column holds mixed types)

| Domain | Tables (rows on 2026-11-24) | Written by |
|---|---|---|
| Usage | `events` 337,544 · `live` 4 · `machines` 4 · `palette` 7 | `store.py` (ingest) |
| Machines | `hosts` 4 · `jobs_seen` 197 · `alarms` 6 · `job_runs` 2,110 | `machines.py`, `aijobs.py` |
| Names | `session_names` 2,055 · `session_titles` 1,101 · `names_snapshot` 1 | `titles.py`, `names.py` |
| Work | `tasks` 1,984 · `task_comments` 6,033 · `task_sessions` 1,463 · `task_effort` 731 · `task_guesses` 45 | `tasks.py`, `progress.py`; tasks are also filed by `questions.py`, `feedback.py`, `outreach.py` |
| Decisions | `questions` 403 · `gate_log` 162 · `endings` 194 · `ending_seen` 204 · `card_rules` 2 · `card_checks` 46 · `rules` 0 · `rule_uses` 0 · `rule_sweeps` 3 | `questions.py`, `endings.py`, `cardrules.py`, `rules.py` |
| Notices | `notices` 2 · `pushes` 1 · `push_subs` 1 | `notify.py`, `webpush.py` |
| Rank | `rank_days` 786 · `rank_meta` 3 · `rank_releases` 30 | `rank.py` |
| Labs, talk | `labs_views` 12 · `labs_calls` 18 · `talk_usage` 26 | `labs.py`, `talk.py` |

Cross-reads are everywhere (studio, labs, aijobs, progress and endings read most domains), so the domains do not
move apart cleanly; see the note's "Data".

Files outside the DB: `data/question-files/`, `data/logos/`, the CA cert, `~/.config/beacon/config.json` and
`card-rules.md`, the knowledge folders and corpus DB (read-only), the project checkouts Outreach reads (pulled every
2 min by a timer), the build-log screenshot service on the home server.

## 5. Background work inside the hub process

| Loop | Module | What | Model calls |
|---|---|---|---|
| machines watch (60 s) | `machines.py` | alarms for silent boxes, failed jobs; only where `HUB_ALARMS=1` | no |
| notify watch (60 s) | `notify.py` | digest, caps, Web Push / Telegram | no |
| titles loop | `titles.py` | names sessions | `claude -p` (Haiku), daily cap |
| narrator loop | `narrate.py` | one-line "now" per working agent | yes, daily cap |
| studio loop | `studio.py` | Team's plain-words view | yes, daily cap |
| progress loop | `progress.py` | effort guesses per task | yes |
| card rules | `cardrules.py` | lint and learn from flagged cards (timers) | Haiku |
| endings reword | `endings.py` | rewords ending cards | Haiku |
| rules sweep | `rules.py` | proposes rules (on POST) | `claude -p` |
| question harvest | `questions.py` | screenshots linked from PR bodies | no |
| labs / talk | `labs.py`, `talk.py` | answers drawn as cards; voice (OpenAI + own TTS on Modal) | yes |
| stale-while-revalidate cache | `swr.py` | rebuilds day / range / meta behind the answer | no |
| warm-up after start | `server.py` | usage, meta, AI jobs, Labs 60 days | no |

## 6. Outside callers

| Caller | Runs | Calls | Auth |
|---|---|---|---|
| `client/tasks` CLI and the Claude Code hooks (`hook-prompt`, `hook-gate`, `hook-ask`, `hook-stop`, `hook-name`) | every session on every machine; 1.5-3 s timeouts, failures swallowed | `/api/tasks*`, `/api/questions*`, POST `/api/endings`, `/api/gate/log`, GET `/api/gate/approval` | token |
| `client/task-agent`, `answer-dispatcher`, `endings-backstop`, `tasks-sweep`, rules review | home server timers (1-10 min, daily) | tasks, questions, live, session, endings, `/api/rules/sweep` | token |
| `client/beacon-mcp` | laptop, Claude Desktop (stdio) | POST `/mcp` | tailnet identity |
| `telegram-alarm` (about 20 scripts on every box) | every machine | POST `/api/notify`, falls back to Telegram | token |
| `collector/collect.py` | 4 machines, every 10 s | POST `/api/ingest`, GET `/api/freshness` | collector token |
| `collector/rank.py` | home server, hourly | POST `/api/rank/ingest`; reads `/lighthouse/api/scoreboard` through the hub | collector token |
| `scripts/tailnet_check.py` | home server, 10 min | `/api/machines`, `/api/names`, questions | token |
| hub watchdog | home server, 5 min | GET `:4829/api/live` | none |
| morning brief, due-today, agent pulse, reflect skill, another repo's preflight (uses an old `http://tasks` alias) | scheduled or by hand | `/api/tasks`, `/api/day`, `/api/live`, `/api/session`, `/api/meta` | none |
| Lighthouse (Next.js) | home server | `/api/tasks`, POST `/api/tasks/<id>`, `/api/questions`, `/api/range`, `/api/live` | token for the POST |
| Windows box CLI wrapper | on each run | `/cli/*` | none |
| backup timer | hub server, daily | `sqlite3 usage.db .backup`, keeps 30 days | file |

Units that are part of the hub: on the hub server the hub itself, its collector, its backup, the checkout-sync timer,
two Caddy site files, a socat forward for Lighthouse; on the home server the eleven timers above plus the build-log
screenshot service the hub calls. All live in the machine-setup repo.
