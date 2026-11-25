import { getTableConfig } from "drizzle-orm/pg-core";
import { describe, expect, it } from "vitest";
import { tables } from "../src/schema.js";
import { SQLITE_DDL } from "../src/sqlite-ddl.js";
import { columnList, kindOf, quote, TABLES } from "../src/tables.js";

describe("the mirrored tables", () => {
  it("are the hub's 34, parents first", () => {
    const names = TABLES.map((t) => t.name);
    expect(names).toHaveLength(34);
    expect(names.indexOf("tasks")).toBeLessThan(names.indexOf("task_comments"));
    expect(names.indexOf("questions")).toBeLessThan(names.indexOf("endings"));
    expect(names.indexOf("rules")).toBeLessThan(names.indexOf("rule_uses"));
  });
  it("know their identities and the AUTOINCREMENT ones", () => {
    const by = new Map(TABLES.map((t) => [t.name, t]));
    expect(by.get("endings")).toMatchObject({ identity: "id", autoincrement: true });
    expect(by.get("tasks")).toMatchObject({ identity: "id", autoincrement: false });
    expect(by.get("task_effort")?.identity).toBeUndefined();
    expect(by.get("names_snapshot")?.identity).toBeUndefined();
    expect(
      TABLES.filter((t) => t.autoincrement)
        .map((t) => t.name)
        .sort(),
    ).toEqual(["endings", "notices", "pushes"]);
  });
  it("mark AUTOINCREMENT exactly where the hub's own CREATE statements say it", () => {
    const declared = SQLITE_DDL.flatMap((sql) => {
      const name = /^CREATE TABLE (\w+)/.exec(sql)?.[1];
      return name !== undefined && /\bAUTOINCREMENT\b/i.test(sql) ? [name] : [];
    });
    expect(declared).toHaveLength(3);
    expect(
      TABLES.filter((t) => t.autoincrement)
        .map((t) => t.name)
        .sort(),
    ).toEqual(declared.sort());
  });
  it("read back in the old insertion order: the integer key, else the row's time then its key", () => {
    const by = new Map(TABLES.map((t) => [t.name, t.order.join()]));
    expect(by.get("tasks")).toBe("id");
    expect(by.get("events")).toBe("ts,id");
    expect(by.get("labs_calls")).toBe("at");
    expect(by.get("rank_meta")).toBe("k");
    expect(by.get("job_runs")).toBe("first_seen,machine,run");
  });
  it("map Postgres types to SQLite kinds", () => {
    expect(["bigint", "integer", "double precision", "text"].map(kindOf)).toEqual(["int", "int", "real", "text"]);
    expect(() => kindOf("jsonb")).toThrow("jsonb");
  });
  it("quote identifiers", () => {
    expect(quote('a"b')).toBe('"a""b"');
    expect(
      columnList({
        name: "x",
        columns: [
          { name: "a", kind: "int" },
          { name: "b", kind: "text" },
        ],
        identity: undefined,
        autoincrement: false,
        order: [],
      }),
    ).toBe('"a", "b"');
  });
});

describe("the foreign keys", () => {
  it("point at tasks, questions and rules only, never at pushes (pruned apart from notices)", () => {
    const refs = tables.flatMap((t) =>
      getTableConfig(t).foreignKeys.map((fk) => {
        const r = fk.reference();
        return `${getTableConfig(t).name}.${r.columns.map((c) => c.name).join()} -> ${getTableConfig(r.foreignTable).name}.${r.foreignColumns.map((c) => c.name).join()}`;
      }),
    );
    expect(refs.sort()).toEqual([
      "card_checks.qid -> questions.id",
      "card_rules.qid -> questions.id",
      "endings.question_id -> questions.id",
      "questions.task_id -> tasks.id",
      "rule_uses.question_id -> questions.id",
      "rule_uses.rule_id -> rules.id",
      "rules.proposal_qid -> questions.id",
      "task_comments.task_id -> tasks.id",
      "task_effort.task_id -> tasks.id",
      "task_guesses.task_id -> tasks.id",
      "task_sessions.task_id -> tasks.id",
    ]);
  });
});
