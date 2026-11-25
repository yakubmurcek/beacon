import type pg from "pg";
import { describe, expect, it } from "vitest";
import { createPool, databaseUrl, ping } from "../src/index.js";

const pool = (query: () => Promise<unknown>) => ({ query }) as unknown as pg.Pool;

describe("databaseUrl", () => {
  it("is unset when the variable is missing or empty", () => {
    expect(databaseUrl({})).toBeUndefined();
    expect(databaseUrl({ BEACON_DATABASE_URL: "" })).toBeUndefined();
  });
  it("passes a set URL through", () => {
    expect(databaseUrl({ BEACON_DATABASE_URL: "postgresql:///beacon" })).toBe("postgresql:///beacon");
  });
});

describe("ping", () => {
  it("is ok when the query answers", async () => {
    expect(await ping(pool(() => Promise.resolve({ rows: [] })))).toBe("ok");
  });
  it("is down when the query fails", async () => {
    expect(await ping(pool(() => Promise.reject(new Error("refused"))))).toBe("down");
  });
  it("is down when the query hangs past the timeout", async () => {
    expect(
      await ping(
        pool(() => new Promise(() => undefined)),
        20,
      ),
    ).toBe("down");
  });
});

describe("createPool", () => {
  it("builds a pool without connecting", async () => {
    const p = createPool("postgresql://127.0.0.1:1/none", 1);
    expect(p.options.max).toBe(1);
    await p.end();
  });
});
