# Beacon

A one-person hub for a studio run by AI agents: tasks, decisions, usage, machines and results in one web app
(Python standard library server in `hub/`, plain JS pages in `web/`, collectors in `collector/`, agent CLIs in `client/`).
README.md has the architecture, the APIs and how to run it.

Before any work here, read `~/.config/beacon/notes.md` when it exists: the deployment owner's own notes (who uses
the hub, how it must look and read, past decisions). They win over defaults; they are kept outside the repo.

## Rules

- Nothing personal or machine-specific in code, tests or docs: names, emails, hosts, machines, accounts, clients
  and paths come from the settings (`hub/settings.py`, `config.example.json`; pages get them from `/config.js`).
  Tests run on `config.example.json` only.
- The nav lives only in `scripts/nav.py`: edit it there and run it; it rewrites every page's header (and the
  header copies in the sibling budget and Lighthouse checkouts, when present).
- Several sessions edit this repo at once: work in a worktree (`git worktree add ../beacon-<topic>`), rebase on
  `origin/main` right before pushing, never touch another session's uncommitted files, pick a free port for a local server.
- Deploy: the server's checkout is the live app. `git pull --rebase` there; `web/` shows on reload, `hub/` changes
  need a restart of the hub's service.
- Test: `python3 -m unittest discover -s tests`; lint: `ruff check`. Screenshot web changes at 390 px and desktop
  and look at them before calling them done.
- Look: colours, spacing, radii and timings only from `web/tokens.css` (both themes); shared parts in `web/app.css`,
  no second skin or `:not(#x)` override (`tests/test_design.py` fails on both).
- Czech is a switch (README "Czech"): every new English label or message on a page gets its Czech line in `web/i18n-cs.js`.
- Page scripts never declare a global `n`: the header's inline badge script owns `var n` (a `const n` blanks the page).
- Anything that runs on its own (loop, timer, agent) must show on `/machines` and alarm when broken; one that calls
  a model also shows on `/ai`.
- Bulk model runs (evals, labeling): the cheapest model that works and a hard cap.
- New views start as a Labs question (`hub/labs.py`: add a data source, not a page).

## Map

| What | Where |
|---|---|
| Architecture, APIs, collectors | `README.md` |
| Every session ending as a card in the Decisions feed (hook, tap back into the session, nightly backstop) | `hub/endings.py`, README "Endings" |
| Deployment settings and what reads them | `hub/settings.py`, `config.example.json` |
| Outdated copies on any machine (checkouts, files, tool versions, dead hooks) | `hub/freshness.py`, README "Outdated copies" |
| Jobs that run a model by themselves, their model and cost | `hub/aijobs.py`, README "AI jobs" |
| Bad decision cards: the lint every ask passes, his 👎 flags, the one log | `hub/questions.py` (`lint`, `flag`, `bad`), README "Bad questions" |
