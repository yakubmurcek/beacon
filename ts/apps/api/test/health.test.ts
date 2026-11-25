import { Health } from "@beacon/contracts";
import type pg from "pg";
import { describe, expect, it } from "vitest";
import { buildApp } from "../src/app.js";

describe("GET /healthz", () => {
  it("answers ok with no database configured", async () => {
    const app = buildApp({ pool: undefined });
    const res = await app.inject({ method: "GET", url: "/healthz" });
    expect(res.statusCode).toBe(200);
    const body = Health.parse(res.json());
    expect(body).toMatchObject({ ok: true, service: "api", db: "off" });
    await app.close();
  });

  it("answers 503 when Postgres refuses", async () => {
    const refusing = { query: () => Promise.reject(new Error("refused")), end: () => Promise.resolve() };
    const app = buildApp({ pool: refusing as unknown as pg.Pool });
    const res = await app.inject({ method: "GET", url: "/healthz" });
    expect(res.statusCode).toBe(503);
    expect(Health.parse(res.json())).toMatchObject({ ok: false, db: "down" });
    await app.close();
  });

  it("knows no other route yet", async () => {
    const app = buildApp({ pool: undefined });
    const res = await app.inject({ method: "GET", url: "/api/tasks" });
    expect(res.statusCode).toBe(404);
    await app.close();
  });
});
