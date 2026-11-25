import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { Health } from "@beacon/contracts";
import { afterEach, describe, expect, it } from "vitest";
import { workerHandler } from "../src/worker-http.js";

let server: Server | undefined;
afterEach(() => {
  server?.close();
});

async function start(): Promise<string> {
  server = createServer(workerHandler(undefined));
  await new Promise<void>((resolve) => server?.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return `http://127.0.0.1:${String(port)}`;
}

describe("worker health", () => {
  it("answers its health", async () => {
    const res = await fetch(`${await start()}/healthz`);
    expect(res.status).toBe(200);
    expect(Health.parse(await res.json())).toMatchObject({ service: "worker", db: "off", commit: "dev" });
  });

  it("serves nothing else", async () => {
    const base = await start();
    expect((await fetch(`${base}/`)).status).toBe(404);
    expect((await fetch(`${base}/healthz`, { method: "POST" })).status).toBe(404);
  });
});
