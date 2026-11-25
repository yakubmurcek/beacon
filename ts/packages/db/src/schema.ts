// The Postgres mirror of the old hub's SQLite file (data/usage.db), one table per SQLite table, same names and
// columns (docs/design/2026-11-ts-migration.md, "Data"). The value shapes stay SQLite's until the cut-over is done:
// epoch-second times are double precision (time.time() floats, returned raw by the API), 0/1 flags and small counts
// are integer, ids, references and token counts bigint, JSON stays text. NOT NULL, defaults, UNIQUE and the CHECK copy
// the SQLite declarations; foreign keys are added where nothing ever deletes the parent row (tasks, questions,
// rules), so a later snapshot cannot hold an orphan. notices.push_id has none: notices and pushes are pruned apart.
// The SQLite file has no foreign keys at all; the 2026-11-24 probe found no orphans for any of these.
import { bigint, check, doublePrecision, index, integer, pgTable, primaryKey, text } from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";

export const tasks = pgTable(
  "tasks",
  {
    id: bigint("id", { mode: "number" }).primaryKey().generatedByDefaultAsIdentity(),
    project: text("project").notNull(),
    title: text("title").notNull(),
    body: text("body").notNull().default(""),
    status: text("status").notNull(),
    priority: integer("priority"),
    due: text("due"),
    tags: text("tags").notNull().default("[]"),
    source: text("source").unique(),
    createdAt: doublePrecision("created_at").notNull(),
    updatedAt: doublePrecision("updated_at").notNull(),
    closedAt: doublePrecision("closed_at"),
    step: integer("step"),
  },
  (t) => [index("tasks_status").on(t.status, t.updatedAt)],
);

export const questions = pgTable(
  "questions",
  {
    id: bigint("id", { mode: "number" }).primaryKey().generatedByDefaultAsIdentity(),
    project: text("project").notNull(),
    question: text("question").notNull(),
    context: text("context").notNull().default(""),
    options: text("options").notNull().default("[]"),
    defaultAnswer: text("default_answer"),
    kind: text("kind").notNull(),
    blocking: integer("blocking").notNull().default(0),
    status: text("status").notNull(),
    answer: text("answer"),
    answerNote: text("answer_note"),
    standing: integer("standing").notNull().default(0),
    answeredVia: text("answered_via"),
    answeredAt: doublePrecision("answered_at"),
    appliedAt: doublePrecision("applied_at"),
    askedBy: text("asked_by"),
    sessionId: text("session_id"),
    taskId: bigint("task_id", { mode: "number" }).references(() => tasks.id),
    source: text("source").unique(),
    createdAt: doublePrecision("created_at").notNull(),
    updatedAt: doublePrecision("updated_at").notNull(),
    blockingReason: text("blocking_reason"),
    askedBlocking: integer("asked_blocking").notNull().default(0),
    gateBlocking: integer("gate_blocking"),
    takenBy: text("taken_by"),
    waitedAt: doublePrecision("waited_at"),
    see: text("see").notNull().default("[]"),
    seeNone: text("see_none"),
    harvestedAt: doublePrecision("harvested_at"),
    handoffAt: doublePrecision("handoff_at"),
    handoffReason: text("handoff_reason"),
    projectAsked: integer("project_asked").notNull().default(0),
    steps: text("steps").notNull().default("[]"),
    lint: text("lint").notNull().default("[]"),
    flag: text("flag"),
    flaggedAt: doublePrecision("flagged_at"),
    laterUntil: doublePrecision("later_until"),
  },
  (t) => [index("questions_status").on(t.status, t.updatedAt), index("questions_task").on(t.taskId, t.status)],
);

export const rules = pgTable("rules", {
  id: bigint("id", { mode: "number" }).primaryKey().generatedByDefaultAsIdentity(),
  project: text("project"),
  kind: text("kind").notNull(),
  summary: text("summary").notNull(),
  answer: text("answer").notNull(),
  source: text("source").notNull(),
  evidence: text("evidence").notNull().default("[]"),
  status: text("status").notNull(),
  proposalQid: bigint("proposal_qid", { mode: "number" }).references(() => questions.id),
  why: text("why"),
  takesDefault: integer("takes_default").notNull().default(0),
  fired: integer("fired").notNull().default(0),
  lastFiredAt: doublePrecision("last_fired_at"),
  createdAt: doublePrecision("created_at").notNull(),
  decidedAt: doublePrecision("decided_at"),
  updatedAt: doublePrecision("updated_at").notNull(),
});

export const events = pgTable(
  "events",
  {
    id: text("id").primaryKey(),
    machine: text("machine").notNull(),
    agent: text("agent").notNull(),
    session: text("session").notNull(),
    project: text("project"),
    model: text("model"),
    ts: doublePrecision("ts").notNull(),
    kind: text("kind").notNull(),
    text: text("text"),
    subagent: integer("subagent").default(0),
    speed: text("speed"),
    calls: integer("calls").default(1),
    input: bigint("input", { mode: "number" }).default(0),
    output: bigint("output", { mode: "number" }).default(0),
    cacheRead: bigint("cache_read", { mode: "number" }).default(0),
    cacheWrite5m: bigint("cache_write_5m", { mode: "number" }).default(0),
    cacheWrite1h: bigint("cache_write_1h", { mode: "number" }).default(0),
    reasoning: bigint("reasoning", { mode: "number" }).default(0),
    tools: integer("tools").default(0),
    costReported: doublePrecision("cost_reported"),
    effort: text("effort"),
  },
  (t) => [index("events_session").on(t.machine, t.session), index("events_ts").on(t.ts)],
);

export const palette = pgTable("palette", {
  project: text("project").primaryKey(),
  slot: integer("slot").notNull(),
});

export const machines = pgTable("machines", {
  name: text("name").primaryKey(),
  lastSeen: doublePrecision("last_seen"),
  version: integer("version"),
  counts: text("counts"),
});

export const live = pgTable("live", {
  machine: text("machine").primaryKey(),
  seenAt: doublePrecision("seen_at").notNull(),
  sessions: text("sessions").notNull(),
});

export const taskSessions = pgTable(
  "task_sessions",
  {
    taskId: bigint("task_id", { mode: "number" })
      .notNull()
      .references(() => tasks.id),
    sessionId: text("session_id").notNull(),
    linkedAt: doublePrecision("linked_at").notNull(),
  },
  (t) => [primaryKey({ columns: [t.taskId, t.sessionId] })],
);

export const taskComments = pgTable(
  "task_comments",
  {
    id: bigint("id", { mode: "number" }).primaryKey().generatedByDefaultAsIdentity(),
    taskId: bigint("task_id", { mode: "number" })
      .notNull()
      .references(() => tasks.id),
    at: doublePrecision("at").notNull(),
    author: text("author").notNull(),
    text: text("text").notNull(),
  },
  (t) => [index("task_comments_task").on(t.taskId, t.at)],
);

export const gateLog = pgTable(
  "gate_log",
  {
    id: bigint("id", { mode: "number" }).primaryKey().generatedByDefaultAsIdentity(),
    ts: doublePrecision("ts").notNull(),
    machine: text("machine"),
    session: text("session"),
    tool: text("tool"),
    category: text("category"),
    what: text("what"),
    input: text("input"),
    cwd: text("cwd"),
    mode: text("mode"),
    approved: text("approved"),
    error: text("error"),
    receivedAt: doublePrecision("received_at").notNull(),
  },
  (t) => [index("gate_log_ts").on(t.ts)],
);

export const ruleUses = pgTable(
  "rule_uses",
  {
    id: bigint("id", { mode: "number" }).primaryKey().generatedByDefaultAsIdentity(),
    ruleId: bigint("rule_id", { mode: "number" })
      .notNull()
      .references(() => rules.id),
    questionId: bigint("question_id", { mode: "number" })
      .notNull()
      .references(() => questions.id),
    why: text("why").notNull(),
    at: doublePrecision("at").notNull(),
    undoneAt: doublePrecision("undone_at"),
  },
  (t) => [index("rule_uses_rule").on(t.ruleId, t.at)],
);

export const ruleSweeps = pgTable("rule_sweeps", {
  id: bigint("id", { mode: "number" }).primaryKey().generatedByDefaultAsIdentity(),
  at: doublePrecision("at").notNull(),
  days: integer("days").notNull(),
  answers: integer("answers").notNull(),
  repeated: integer("repeated").notNull(),
  risky: integer("risky").notNull(),
  covered: integer("covered").notNull(),
  modelCalls: integer("model_calls").notNull(),
  proposed: text("proposed").notNull().default("[]"),
});

export const sessionTitles = pgTable("session_titles", {
  key: text("key").primaryKey(),
  title: text("title").notNull(),
  at: doublePrecision("at").notNull(),
});

export const sessionNames = pgTable(
  "session_names",
  {
    machine: text("machine").notNull(),
    session: text("session").notNull(),
    title: text("title").notNull(),
    source: text("source").notNull(),
    at: doublePrecision("at").notNull(),
  },
  (t) => [primaryKey({ columns: [t.machine, t.session] })],
);

export const taskEffort = pgTable("task_effort", {
  taskId: bigint("task_id", { mode: "number" })
    .primaryKey()
    .references(() => tasks.id),
  project: text("project"),
  agentSteps: integer("agent_steps").notNull(),
  activeMin: doublePrecision("active_min").notNull(),
  closedAt: doublePrecision("closed_at").notNull(),
});

export const taskGuesses = pgTable("task_guesses", {
  taskId: bigint("task_id", { mode: "number" })
    .primaryKey()
    .references(() => tasks.id),
  guessMin: doublePrecision("guess_min").notNull(),
  at: doublePrecision("at").notNull(),
  actualMin: doublePrecision("actual_min"),
});

export const hosts = pgTable("hosts", {
  machine: text("machine").primaryKey(),
  seenAt: doublePrecision("seen_at").notNull(),
  data: text("data").notNull(),
});

export const jobsSeen = pgTable(
  "jobs_seen",
  {
    machine: text("machine").notNull(),
    jobId: text("job_id").notNull(),
    name: text("name"),
    kind: text("kind"),
    firstSeen: doublePrecision("first_seen"),
    lastSeen: doublePrecision("last_seen"),
    badSince: doublePrecision("bad_since"),
    own: integer("own"),
  },
  (t) => [primaryKey({ columns: [t.machine, t.jobId] })],
);

export const alarms = pgTable("alarms", {
  key: text("key").primaryKey(),
  text: text("text"),
  firstBad: doublePrecision("first_bad"),
  sentAt: doublePrecision("sent_at"),
});

export const namesSnapshot = pgTable(
  "names_snapshot",
  {
    id: bigint("id", { mode: "number" }).primaryKey(),
    at: doublePrecision("at"),
    data: text("data"),
  },
  (t) => [check("names_snapshot_id_check", sql`${t.id} = 1`)],
);

export const rankDays = pgTable(
  "rank_days",
  {
    day: text("day").notNull(),
    key: text("key").notNull(),
    value: doublePrecision("value").notNull(),
  },
  (t) => [primaryKey({ columns: [t.day, t.key] })],
);

export const rankReleases = pgTable("rank_releases", {
  key: text("key").primaryKey(),
  day: text("day").notNull(),
  kind: text("kind"),
  what: text("what"),
});

export const rankMeta = pgTable("rank_meta", {
  k: text("k").primaryKey(),
  v: text("v").notNull(),
});

export const jobRuns = pgTable(
  "job_runs",
  {
    machine: text("machine").notNull(),
    run: text("run").notNull(),
    job: text("job").notNull(),
    agent: text("agent"),
    session: text("session"),
    firstSeen: doublePrecision("first_seen").notNull(),
    lastSeen: doublePrecision("last_seen").notNull(),
    model: text("model"),
    effort: text("effort"),
  },
  (t) => [primaryKey({ columns: [t.machine, t.run] }), index("job_runs_seen").on(t.lastSeen)],
);

export const labsViews = pgTable("labs_views", {
  key: text("key").primaryKey(),
  title: text("title").notNull(),
  cards: text("cards").notNull(),
  questions: text("questions").notNull(),
  asks: integer("asks").notNull().default(0),
  created: doublePrecision("created").notNull(),
  updated: doublePrecision("updated").notNull(),
  changes: text("changes").notNull().default("[]"),
});

export const labsCalls = pgTable("labs_calls", {
  at: doublePrecision("at").notNull(),
});

export const cardRules = pgTable("card_rules", {
  id: bigint("id", { mode: "number" }).primaryKey().generatedByDefaultAsIdentity(),
  text: text("text").notNull(),
  source: text("source").notNull(),
  qid: bigint("qid", { mode: "number" }).references(() => questions.id),
  active: integer("active").notNull().default(1),
  createdAt: doublePrecision("created_at").notNull(),
});

export const cardChecks = pgTable(
  "card_checks",
  {
    id: bigint("id", { mode: "number" }).primaryKey().generatedByDefaultAsIdentity(),
    ts: doublePrecision("ts").notNull(),
    qid: bigint("qid", { mode: "number" }).references(() => questions.id),
    sessionId: text("session_id"),
    askedBy: text("asked_by"),
    kind: text("kind").notNull(),
    verdict: text("verdict").notNull(),
    called: integer("called").notNull().default(0),
    rules: text("rules").notNull().default("[]"),
    why: text("why"),
    question: text("question"),
  },
  (t) => [index("card_checks_ts").on(t.ts)],
);

export const endings = pgTable(
  "endings",
  {
    id: bigint("id", { mode: "number" }).primaryKey().generatedByDefaultAsIdentity(),
    sessionId: text("session_id").notNull().unique(),
    agent: text("agent"),
    machine: text("machine"),
    project: text("project"),
    title: text("title"),
    state: text("state").notNull(),
    line: text("line").notNull(),
    next: text("next"),
    actions: text("actions").notNull().default("[]"),
    reply: text("reply"),
    cwd: text("cwd"),
    source: text("source").notNull().default("hook"),
    createdAt: doublePrecision("created_at").notNull(),
    updatedAt: doublePrecision("updated_at").notNull(),
    clearedAt: doublePrecision("cleared_at"),
    acted: text("acted"),
    questionId: bigint("question_id", { mode: "number" }).references(() => questions.id),
  },
  (t) => [index("endings_open").on(t.clearedAt, t.updatedAt)],
);

export const endingSeen = pgTable("ending_seen", {
  sessionId: text("session_id").primaryKey(),
  agent: text("agent"),
  machine: text("machine"),
  project: text("project"),
  title: text("title"),
  line: text("line"),
  last: doublePrecision("last").notNull(),
});

export const talkUsage = pgTable(
  "talk_usage",
  {
    at: doublePrecision("at").notNull(),
    kind: text("kind").notNull(),
    seconds: doublePrecision("seconds").notNull().default(0),
    cost: doublePrecision("cost").notNull().default(0),
    device: text("device"),
  },
  (t) => [index("talk_usage_at").on(t.at)],
);

export const pushSubs = pgTable("push_subs", {
  endpoint: text("endpoint").primaryKey(),
  p256dh: text("p256dh").notNull(),
  auth: text("auth").notNull(),
  device: text("device").notNull().default(""),
  createdAt: doublePrecision("created_at").notNull(),
  lastOk: doublePrecision("last_ok"),
  lastError: text("last_error"),
});

export const notices = pgTable(
  "notices",
  {
    id: bigint("id", { mode: "number" }).primaryKey().generatedByDefaultAsIdentity(),
    createdAt: doublePrecision("created_at").notNull(),
    level: text("level").notNull(),
    title: text("title").notNull(),
    body: text("body").notNull().default(""),
    link: text("link").notNull().default(""),
    source: text("source").notNull().default(""),
    key: text("key").notNull().default(""),
    topic: text("topic").notNull().default(""),
    repeats: integer("repeats").notNull().default(0),
    alone: integer("alone").notNull().default(0),
    state: text("state").notNull(),
    pushId: bigint("push_id", { mode: "number" }),
    readAt: doublePrecision("read_at"),
  },
  (t) => [
    index("notices_key").on(t.key, t.createdAt),
    index("notices_state").on(t.state, t.createdAt),
    index("notices_topic").on(t.topic, t.createdAt),
  ],
);

export const pushes = pgTable(
  "pushes",
  {
    id: bigint("id", { mode: "number" }).primaryKey().generatedByDefaultAsIdentity(),
    at: doublePrecision("at").notNull(),
    level: text("level").notNull(),
    title: text("title").notNull(),
    body: text("body").notNull().default(""),
    link: text("link").notNull().default(""),
    count: integer("count").notNull(),
    channel: text("channel").notNull(),
    ok: integer("ok").notNull(),
    error: text("error"),
  },
  (t) => [index("pushes_at").on(t.at)],
);

export const tables = [
  tasks,
  questions,
  rules,
  events,
  palette,
  machines,
  live,
  taskSessions,
  taskComments,
  gateLog,
  ruleUses,
  ruleSweeps,
  sessionTitles,
  sessionNames,
  taskEffort,
  taskGuesses,
  hosts,
  jobsSeen,
  alarms,
  namesSnapshot,
  rankDays,
  rankReleases,
  rankMeta,
  jobRuns,
  labsViews,
  labsCalls,
  cardRules,
  cardChecks,
  endings,
  endingSeen,
  talkUsage,
  pushSubs,
  notices,
  pushes,
] as const;
