import { describe, expect, it } from "vitest";
import { DataError, Digest, formatReal, fromCopy, fromSqlite, rowLine, storageClass, toSqlite } from "../src/values.js";

describe("normalized values", () => {
  it("formats floats in their shortest exact form, -0 as 0 (SQLite cannot keep it)", () => {
    expect(formatReal(1764012345.1234567)).toBe("1764012345.1234567");
    expect(formatReal(0.1 + 0.2)).toBe("0.30000000000000004");
    expect(formatReal(-0)).toBe("0");
    expect(formatReal(1.5e300)).toBe("1.5e+300");
    expect(Number(formatReal(1e-7))).toBe(1e-7);
  });
  it("reads SQLite values whose storage class fits the column", () => {
    expect(fromSqlite(null, "int", "t.c")).toBeNull();
    expect(fromSqlite(9007199254740993n, "int", "t.c")).toBe("9007199254740993");
    expect(fromSqlite(1.5, "real", "t.c")).toBe("1.5");
    expect(fromSqlite("x", "text", "t.c")).toBe("x");
  });
  it("refuses a value of another storage class rather than change it", () => {
    expect(() => fromSqlite("1", "int", "t.c")).toThrow(new DataError("t.c: a text value in a int column"));
    expect(() => fromSqlite(1.5, "int", "t.c")).toThrow("a number value");
    expect(() => fromSqlite(1n, "real", "t.c")).toThrow("a bigint value");
    expect(() => fromSqlite(new Uint8Array([1]), "text", "t.c")).toThrow("a blob value");
  });
  it("reads COPY fields into the same form", () => {
    expect(fromCopy(null, "real")).toBeNull();
    expect(fromCopy("9007199254740993", "int")).toBe("9007199254740993");
    expect(fromCopy("1.764012345123457e+09", "real")).toBe("1764012345.123457");
    expect(fromCopy("-0", "real")).toBe("0");
    expect(fromSqlite(-0, "real", "t.c")).toBe("0");
    expect(fromCopy("a\tb", "text")).toBe("a\tb");
  });
  it("turns normalized values back into SQLite's classes", () => {
    expect(toSqlite(null, "int")).toBeNull();
    expect(toSqlite("12", "int")).toBe(12n);
    expect(toSqlite("1.5", "real")).toBe(1.5);
    expect(toSqlite("1", "text")).toBe("1");
    expect(toSqlite("Infinity", "real")).toBe(Infinity);
    expect(() => toSqlite("NaN", "real")).toThrow("a NaN, which SQLite stores as NULL");
  });
  it("names storage classes", () => {
    expect([null, 1n, 1.5, "a", new Uint8Array()].map(storageClass)).toEqual([
      "null",
      "integer",
      "real",
      "text",
      "blob",
    ]);
  });
  it("writes a row as one line, with classes when asked", () => {
    expect(rowLine(["1", null])).toBe('["1",null]');
    expect(rowLine(["1", null], ["integer", "null"])).toBe('[["integer","1"],["null",null]]');
  });
});

describe("Digest", () => {
  const of = (lines: string[]) => {
    const d = new Digest();
    for (const l of lines) {
      d.add(l);
    }
    return d;
  };
  it("does not depend on the order rows come in", () => {
    expect(of(["a", "b", "c"]).hex).toBe(of(["c", "a", "b"]).hex);
  });
  it("counts a duplicate row twice and tells sets apart", () => {
    expect(of(["a", "a"]).hex).not.toBe(of(["a"]).hex);
    expect(of(["a", "a"]).rows).toBe(2);
    expect(of(["a", "b"]).hex).not.toBe(of(["a", "c"]).hex);
    expect(of([]).hex).toBe("0".repeat(64));
  });
});
