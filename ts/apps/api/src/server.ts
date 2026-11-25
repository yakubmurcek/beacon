import { buildApp } from "./app.js";

const port = Number(process.env["BEACON_API_PORT"] ?? 4832);
const app = buildApp({ logger: true });

for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.once(signal, () => {
    void app.close().then(() => process.exit(0));
  });
}

// Loopback only: Caddy is the one door in front of it (design note, section 5).
await app.listen({ host: "127.0.0.1", port });
