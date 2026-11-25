import { getTableConfig } from "drizzle-orm/pg-core";
import { tables } from "./schema.js";

/** How a column's values are compared and carried: SQLite INTEGER, REAL and TEXT. */
export type Kind = "int" | "real" | "text";

interface Column {
  readonly name: string;
  readonly kind: Kind;
}

export interface TableSpec {
  readonly name: string;
  readonly columns: readonly Column[];
  /** The column Postgres numbers itself (an identity), whose sequence is set after a copy. */
  readonly identity: string | undefined;
  /** SQLite AUTOINCREMENT: its high-water mark lives in sqlite_sequence and must survive a round trip. */
  readonly autoincrement: boolean;
  /**
   * The order the reverse copy inserts rows in, so the new file's rowids follow the old order: the integer key
   * itself, else the row's time and then its key (SQLite kept text-key and keyless rows in insertion order).
   */
  readonly order: readonly string[];
}

/** The time a row was written, by the names the hub uses, first match wins. */
const TIMES = [
  "ts",
  "at",
  "created_at",
  "created",
  "linked_at",
  "first_seen",
  "first_bad",
  "seen_at",
  "last_seen",
  "last",
  "day",
];

/** The three tables the hub declares AUTOINCREMENT, so an id is never reused after rows are pruned. */
const AUTOINCREMENT = new Set(["endings", "notices", "pushes"]);

export function kindOf(sqlType: string): Kind {
  switch (sqlType) {
    case "bigint":
    case "integer":
      return "int";
    case "double precision":
      return "real";
    case "text":
      return "text";
    default:
      throw new Error(`no SQLite kind for the Postgres type ${sqlType}`);
  }
}

/** Every mirrored table, parents before the tables that reference them (the load order). */
export const TABLES: readonly TableSpec[] = tables.map((table) => {
  const config = getTableConfig(table);
  const columns = config.columns.map((c) => ({ name: c.name, kind: kindOf(c.getSQLType()) }));
  const key = [
    ...config.columns.filter((c) => c.primary).map((c) => c.name),
    ...(config.primaryKeys[0]?.columns.map((c) => c.name) ?? []),
  ];
  const intKey = key.length === 1 && columns.find((c) => c.name === key[0])?.kind === "int";
  const time = TIMES.find((t) => columns.some((c) => c.name === t));
  return {
    name: config.name,
    columns,
    identity: config.columns.find((c) => c.generatedIdentity !== undefined)?.name,
    autoincrement: AUTOINCREMENT.has(config.name),
    order: intKey ? key : [...(time === undefined ? [] : [time]), ...key.filter((k) => k !== time)],
  };
});

/** A double-quoted SQL identifier (both dialects). */
export function quote(name: string): string {
  return `"${name.replaceAll('"', '""')}"`;
}

export function columnList(spec: TableSpec): string {
  return spec.columns.map((c) => quote(c.name)).join(", ");
}
