import { DataError } from "./values.js";

// Postgres COPY's text format: one row per line, tab between fields, \N for null, backslash escapes inside a field.

const ESCAPE: Readonly<Record<string, string>> = { "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t" };
const UNESCAPE: Readonly<Record<string, string>> = { b: "\b", f: "\f", n: "\n", r: "\r", t: "\t", v: "\v" };

export function encodeField(value: string | null, where: string): string {
  if (value === null) {
    return "\\N";
  }
  if (value.includes("\0")) {
    throw new DataError(`${where}: a NUL character, which Postgres text cannot hold`);
  }
  return value.replace(/[\\\n\r\t]/g, (c) => ESCAPE[c] ?? c);
}

export function encodeLine(values: readonly (string | null)[], where: string): string {
  return values.map((v) => encodeField(v, where)).join("\t");
}

function unescape(field: string): string {
  return field.replace(
    /\\(?:([0-7]{1,3})|x([0-9a-fA-F]{1,2})|([\s\S]))/g,
    (_, octal?: string, hex?: string, c?: string) => {
      if (octal !== undefined) {
        return String.fromCharCode(Number.parseInt(octal, 8));
      }
      if (hex !== undefined) {
        return String.fromCharCode(Number.parseInt(hex, 16));
      }
      return c === undefined ? "" : (UNESCAPE[c] ?? c);
    },
  );
}

export function decodeLine(line: string): (string | null)[] {
  return line.split("\t").map((f) => (f === "\\N" ? null : unescape(f)));
}

/** Lines out of COPY's byte stream; a line is cut only at a raw newline, so UTF-8 split across chunks stays whole. */
export async function* splitLines(chunks: AsyncIterable<Buffer>): AsyncGenerator<string> {
  let rest: Buffer = Buffer.alloc(0);
  for await (const chunk of chunks) {
    let buf: Buffer = rest.length === 0 ? chunk : Buffer.concat([rest, chunk]);
    let nl = buf.indexOf(10);
    while (nl !== -1) {
      yield buf.toString("utf8", 0, nl);
      buf = buf.subarray(nl + 1);
      nl = buf.indexOf(10);
    }
    rest = buf;
  }
  if (rest.length > 0) {
    yield rest.toString("utf8");
  }
}

/** Lines joined into chunks of about `size` characters, each line ending in a newline, for COPY FROM's stream. */
export async function* batch(lines: AsyncIterable<string> | Iterable<string>, size = 1 << 20): AsyncGenerator<string> {
  let parts: string[] = [];
  let length = 0;
  for await (const line of lines) {
    parts.push(line, "\n");
    length += line.length + 1;
    if (length >= size) {
      yield parts.join("");
      parts = [];
      length = 0;
    }
  }
  if (parts.length > 0) {
    yield parts.join("");
  }
}
