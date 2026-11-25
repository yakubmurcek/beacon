import { describe, expect, it } from "vitest";
import { batch, decodeLine, encodeField, encodeLine, splitLines } from "../src/copytext.js";

const collect = async <T>(it: AsyncIterable<T>): Promise<T[]> => {
  const out: T[] = [];
  for await (const x of it) {
    out.push(x);
  }
  return out;
};

describe("COPY text format", () => {
  it("round-trips the characters that need escaping, and tells NULL from '' and from a literal \\N", () => {
    const values = ["a\tb\nc\rd\\e", "", null, "\\N", "🚀 Příliš", "\\"];
    const line = encodeLine(values, "t");
    expect(line.split("\t")).toHaveLength(values.length);
    expect(line).not.toContain("\n");
    expect(decodeLine(line)).toEqual(values);
  });
  it("refuses a NUL, which Postgres text cannot hold", () => {
    expect(() => encodeField("a\0b", "t.c")).toThrow("t.c: a NUL character");
  });
  it("reads every escape Postgres may write", () => {
    expect(decodeLine("\\b\\f\\v\\101\\x42\\q")).toEqual(["\b\f\vABq"]);
  });
  it("splits a byte stream into lines, even with a character cut between chunks", async () => {
    const bytes = Buffer.from("one\nčtyři 🚀\nlast");
    const chunks = [bytes.subarray(0, 6), bytes.subarray(6, 13), bytes.subarray(13)];
    const source = (async function* () {
      await Promise.resolve();
      yield* chunks;
    })();
    expect(await collect(splitLines(source))).toEqual(["one", "čtyři 🚀", "last"]);
  });
  it("ends without a partial line when the stream ends in a newline", async () => {
    expect(
      await collect(
        splitLines(
          (async function* () {
            await Promise.resolve();
            yield Buffer.from("a\n");
          })(),
        ),
      ),
    ).toEqual(["a"]);
  });
  it("joins lines into newline-terminated chunks", async () => {
    expect(await collect(batch(["ab", "cd", "e"], 4))).toEqual(["ab\ncd\n", "e\n"]);
    expect(await collect(batch([]))).toEqual([]);
  });
});
