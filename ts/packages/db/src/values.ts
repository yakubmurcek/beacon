import { createHash } from "node:crypto";
import type { Kind } from "./tables.js";

/** What node:sqlite hands back with big-int reads on: the storage class is the JS type. */
export type SqliteValue = null | bigint | number | string | Uint8Array;

/** A value the mirror cannot carry exactly: the copy stops rather than change it. */
export class DataError extends Error {}

/** JS's shortest round-trip form, which Postgres float8 input reads back to the same double. */
export function formatReal(n: number): string {
  return Object.is(n, -0) ? "-0" : String(n);
}

/**
 * The one text form both sides are compared in (null stays null): integers in decimal, floats in their shortest
 * exact form, text unchanged. A SQLite value whose storage class does not fit its column is refused.
 */
export function fromSqlite(value: SqliteValue, kind: Kind, where: string): string | null {
  if (value === null) {
    return null;
  }
  if (kind === "int" && typeof value === "bigint") {
    return value.toString();
  }
  if (kind === "real" && typeof value === "number") {
    return formatReal(value);
  }
  if (kind === "text" && typeof value === "string") {
    return value;
  }
  const found = value instanceof Uint8Array ? "a blob" : `a ${typeof value === "string" ? "text" : typeof value}`;
  throw new DataError(`${where}: ${found} value in a ${kind} column`);
}

/** The same form from a field of Postgres's COPY text output. */
export function fromCopy(field: string | null, kind: Kind): string | null {
  if (field === null) {
    return null;
  }
  switch (kind) {
    case "int":
      return BigInt(field).toString();
    case "real":
      return formatReal(Number(field));
    case "text":
      return field;
  }
}

/** A normalized value as SQLite stores it again: integers as integers, floats as reals. */
export function toSqlite(value: string | null, kind: Kind): bigint | number | string | null {
  if (value === null) {
    return null;
  }
  switch (kind) {
    case "int":
      return BigInt(value);
    case "real":
      return Number(value);
    case "text":
      return value;
  }
}

/** SQLite's storage class of a value, for comparisons that must also prove the classes survived. */
export function storageClass(value: SqliteValue): string {
  if (value === null) {
    return "null";
  }
  if (value instanceof Uint8Array) {
    return "blob";
  }
  return typeof value === "bigint" ? "integer" : typeof value === "number" ? "real" : "text";
}

/** One row as one line; the column order is the spec's, so both sides agree whatever order a file stores. */
export function rowLine(values: readonly (string | null)[], classes?: readonly string[]): string {
  return JSON.stringify(classes === undefined ? values : values.map((v, i) => [classes[i], v]));
}

const MASK = (1n << 256n) - 1n;

/**
 * An order-independent checksum of a multiset of rows: the sum of their SHA-256 values, modulo 2^256. Equal row sets
 * give equal sums whatever order each side reads them in; a duplicate row counts twice, as it should.
 */
export class Digest {
  rows = 0;
  #sum = 0n;

  add(line: string): void {
    this.rows += 1;
    this.#sum = (this.#sum + BigInt(`0x${createHash("sha256").update(line).digest("hex")}`)) & MASK;
  }

  get hex(): string {
    return this.#sum.toString(16).padStart(64, "0");
  }
}
