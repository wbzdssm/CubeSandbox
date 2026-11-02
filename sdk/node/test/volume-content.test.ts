// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

import { Blob } from "node:buffer";
import { createServer, type IncomingHttpHeaders, type RequestListener, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { ReadableStream } from "node:stream/web";
import { afterAll, beforeAll, beforeEach, expect, it } from "vitest";
import { Config } from "../src/config.js";
import {
  ApiError,
  AuthenticationError,
  Volume,
  VolumeFileType,
  VolumeInfo,
  VolumePathNotFoundError,
} from "../src/index.js";

const TOKEN = "volume-content-secret";
const ENTRY = {
  name: "data.txt", type: "file", path: "/data.txt", size: 3, mode: 420, uid: 0, gid: 0,
  atime: "2026-10-30T01:02:03Z", mtime: "2026-10-30T01:02:03Z", ctime: "2026-10-30T01:02:03Z",
};
interface RecordedRequest {
  method: string;
  url: URL;
  headers: IncomingHttpHeaders;
  body: Buffer;
}
let server: Server;
let baseUrl: string;
let volume: Volume;
let requests: RecordedRequest[];
let status: number;
let responseBody: string | Buffer;
let responseHeaders: Record<string, string>;

beforeAll(async () => {
  server = createServer(async (req, res) => {
    const parts: Buffer[] = [];
    for await (const part of req) parts.push(Buffer.from(part));
    requests.push({ method: req.method!, url: new URL(req.url!, baseUrl), headers: req.headers, body: Buffer.concat(parts) });
    res.writeHead(status, responseHeaders);
    res.end(responseBody);
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve, reject) => {
    server.close(error => error ? reject(error) : resolve());
    server.closeAllConnections();
  });
});

beforeEach(() => {
  requests = [];
  status = 200;
  responseBody = JSON.stringify(ENTRY);
  responseHeaders = { "Content-Type": "application/json" };
  volume = new Volume(new VolumeInfo("vol-test", "test", TOKEN), new Config({
    apiUrl: "https://management.invalid", apiKey: "management-secret", volumeApiUrl: `${baseUrl}/gateway`,
  }));
});

it("reads raw data through the gateway using only the volume bearer", async () => {
  responseBody = Buffer.from([0, 255, 42]);
  const path = "/a space/#中文.txt";
  expect(await volume.readFile(path, { format: "bytes" })).toEqual(new Uint8Array(responseBody));
  const request = requests[0];
  expect(request.method).toBe("GET");
  expect(request.url.pathname).toBe("/gateway/volumecontent/vol-test/file");
  expect(request.url.searchParams.get("path")).toBe(path);
  expect(request.headers.authorization).toBe(`Bearer ${TOKEN}`);
  expect(request.headers["x-api-key"]).toBeUndefined();
  expect(request.headers["x-access-token"]).toBeUndefined();
});

it.each(["Authorization", "aUtHoRiZaTiOn", "X-API-Key", "x-access-token"])("rejects reserved content header %s", async header => {
  await expect(volume.readFile("/data.txt", { headers: { [header]: "management-secret" } })).rejects.toThrow("headers cannot override");
  await expect(volume.readFile("/data.txt", { apiHeaders: { [header]: "management-secret" } })).rejects.toThrow("headers cannot override");
  expect(requests).toHaveLength(0);
});

it("supports text, blob and stream reads", async () => {
  responseBody = "hello";
  expect(await volume.readFile("/data.txt")).toBe("hello");
  expect(await (await volume.readFile("/data.txt", { format: "blob" })).text()).toBe("hello");
  const stream = await volume.readFile("/data.txt", { format: "stream" });
  const reader = stream.getReader();
  const parts: Uint8Array[] = [];
  while (true) {
    const next = await reader.read();
    if (next.done) break;
    parts.push(next.value);
  }
  reader.releaseLock();
  expect(Buffer.concat(parts).toString()).toBe("hello");
});

it.each(["text", "bytes", "buffer", "blob", "stream"])("uploads %s without dropping zero or false options", async (format) => {
  const bytes = new TextEncoder().encode("payload");
  const data = format === "text" ? "payload" : format === "bytes" ? bytes : format === "buffer" ? bytes.buffer : format === "blob" ? new Blob([bytes]) : new ReadableStream<Uint8Array>({
    start(controller) { controller.enqueue(bytes); controller.close(); },
  });
  const result = await volume.writeFile("/data.txt", data, { uid: 0, gid: 0, mode: 0, force: false });
  expect(result.type).toBe(VolumeFileType.FILE);
  expect(result.mtime).toEqual(new Date(ENTRY.mtime));
  const request = requests[0];
  expect(request.method).toBe("PUT");
  expect(request.headers["content-type"]).toBe("application/octet-stream");
  expect(request.body.toString()).toBe("payload");
  expect(Object.fromEntries(request.url.searchParams)).toEqual({ path: "/data.txt", uid: "0", gid: "0", mode: "0", force: "false" });
});

it("routes directory and metadata operations", async () => {
  await volume.makeDir("/nested", { mode: 0, force: false });
  expect(requests[0].method).toBe("POST");
  expect(requests[0].url.pathname).toBe("/gateway/volumecontent/vol-test/dir");
  await volume.updateMetadata("/data.txt", { uid: 0, mode: 0 });
  expect(requests[1].method).toBe("PATCH");
  expect(JSON.parse(requests[1].body.toString())).toEqual({ uid: 0, mode: 0 });
  expect(await volume.exists("/data.txt")).toBe(true);
  responseBody = JSON.stringify([ENTRY]);
  expect(await volume.list("/", { depth: 0 })).toHaveLength(1);
  expect(requests[3].url.searchParams.get("depth")).toBe("0");
  status = 204;
  await volume.remove("/data.txt");
  expect(requests[4].method).toBe("DELETE");
});

it("maps all content 404 responses to path-not-found like E2B", async () => {
  status = 404;
  responseBody = JSON.stringify({ code: "path_not_found", message: "missing" });
  expect(await volume.exists("/missing")).toBe(false);
  await expect(volume.readFile("/missing")).rejects.toBeInstanceOf(VolumePathNotFoundError);
  responseBody = "404 page not found";
  expect(await volume.exists("/missing")).toBe(false);
});

it.each([401, 403, 405, 500, 501, 503])("handles HTTP %d without retrying or disclosing tokens", async (code) => {
  status = code;
  responseBody = JSON.stringify({ message: `error ${TOKEN}` });
  const errorClass = [401, 403].includes(code) ? AuthenticationError : ApiError;
  const result = volume.writeFile("/data.txt", "payload");
  await expect(result).rejects.toBeInstanceOf(errorClass);
  await expect(result).rejects.toThrow("error ***");
  expect(requests).toHaveLength(1);
});

it("does not follow redirects", async () => {
  status = 307;
  responseHeaders.Location = `${baseUrl}/must-not-follow`;
  await expect(volume.readFile("/data.txt")).rejects.toBeInstanceOf(ApiError);
  expect(requests).toHaveLength(1);
});

it.each(["relative", "/a/../b", "/a/./b", "/nul\0", "/back\\slash"])("rejects unsafe path %j before sending", async path => {
  await expect(volume.readFile(path)).rejects.toThrow();
  expect(requests).toHaveLength(0);
});

it("rejects root deletion, invalid options and malformed success responses", async () => {
  await expect(volume.remove("///")).rejects.toThrow();
  await expect(volume.makeDir("/x", { mode: -1 })).rejects.toThrow();
  await expect(volume.list("/", { depth: -1 })).rejects.toThrow();
  expect(requests).toHaveLength(0);
  responseBody = JSON.stringify({ entries: [] });
  await expect(volume.list("/")).rejects.toBeInstanceOf(ApiError);
  responseBody = JSON.stringify({ ...ENTRY, size: "bad" });
  await expect(volume.getInfo("/data.txt")).rejects.toBeInstanceOf(ApiError);
});

it("honors an already-aborted caller signal without sending", async () => {
  const controller = new AbortController();
  controller.abort();
  await expect(volume.readFile("/data.txt", { signal: controller.signal })).rejects.toThrow();
  expect(requests).toHaveLength(0);
});

it.each(["getInfo", "list", "makeDir", "writeFile", "updateMetadata"] as const)("maps malformed JSON from %s to ApiError", async operation => {
  responseBody = "{invalid";
  const result = operation === "writeFile" ? volume.writeFile("/data.txt", "payload")
    : operation === "updateMetadata" ? volume.updateMetadata("/data.txt", { uid: 0 })
    : volume[operation]("/data.txt");
  await expect(result).rejects.toBeInstanceOf(ApiError);
  await expect(result).rejects.toMatchObject({ statusCode: 200 });
});

async function withStreamingGateway(handler: RequestListener, action: (volume: Volume) => Promise<void>) {
  const gateway = createServer(handler);
  await new Promise<void>(resolve => gateway.listen(0, "127.0.0.1", resolve));
  const address = gateway.address() as AddressInfo;
  const volume = new Volume(new VolumeInfo("vol-test", "test", TOKEN), new Config({
    volumeApiUrl: `http://127.0.0.1:${address.port}`,
  }));
  try {
    await action(volume);
  } finally {
    await new Promise<void>((resolve, reject) => {
      gateway.close(error => error ? reject(error) : resolve());
      gateway.closeAllConnections();
    });
  }
}

it("does not apply the handshake timeout to an active read stream", async () => {
  await withStreamingGateway((_req, res) => {
    res.writeHead(200);
    res.write("first");
    const timer = setTimeout(() => res.end("last"), 300);
    res.on("close", () => clearTimeout(timer));
  }, async volume => {
    const stream = await volume.readFile("/data.txt", { format: "stream", requestTimeoutMs: 100, streamIdleTimeoutMs: 1000 });
    const reader = stream.getReader();
    const parts: Uint8Array[] = [];
    try {
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        parts.push(next.value);
      }
    } finally { reader.releaseLock(); }
    expect(Buffer.concat(parts).toString()).toBe("firstlast");
  });
});

it("cancels an in-flight read stream and releases it without lock errors", async () => {
  await withStreamingGateway((_req, res) => {
    res.writeHead(200);
    res.write("first");
  }, async volume => {
    const stream = await volume.readFile("/data.txt", { format: "stream" });
    const reader = stream.getReader();
    expect((await reader.read()).done).toBe(false);
    const pending = reader.read();
    await expect(reader.cancel()).resolves.toBeUndefined();
    await expect(pending).resolves.toMatchObject({ done: true });
    reader.releaseLock();
  });
});

it("aborts a stalled stream using streamIdleTimeoutMs", async () => {
  await withStreamingGateway((_req, res) => {
    res.writeHead(200);
    res.write("first");
  }, async volume => {
    const stream = await volume.readFile("/data.txt", { format: "stream", streamIdleTimeoutMs: 50 });
    const reader = stream.getReader();
    try {
      expect((await reader.read()).done).toBe(false);
      await expect(reader.read()).rejects.toThrow();
    } finally { reader.releaseLock(); }
  });
});

it("keeps the caller abort signal active after the stream handshake", async () => {
  await withStreamingGateway((_req, res) => {
    res.writeHead(200);
    res.write("first");
  }, async volume => {
    const controller = new AbortController();
    const stream = await volume.readFile("/data.txt", { format: "stream", signal: controller.signal });
    const reader = stream.getReader();
    try {
      expect((await reader.read()).done).toBe(false);
      const pending = reader.read();
      controller.abort();
      await expect(pending).rejects.toThrow();
    } finally { reader.releaseLock(); }
  });
});

it.each([-1, Infinity, NaN])("rejects invalid stream idle timeout %s without sending", async streamIdleTimeoutMs => {
  await expect(volume.readFile("/data.txt", { format: "stream", streamIdleTimeoutMs })).rejects.toThrow();
  expect(requests).toHaveLength(0);
});

it("supports per-call E2B content URL, token, headers and zero timeout", async () => {
  responseBody = "hello";
  const other = new Volume("v1", "one", "old-token", "unused.test");
  expect(await other.readFile("/data.txt", { apiUrl: `${baseUrl}/override`, token: "replacement", headers: { "X-Correlation-ID": "test" }, requestTimeoutMs: 0, httpVersion: "1.1" })).toBe("hello");
  expect(requests[0].url.pathname).toBe("/override/volumecontent/v1/file");
  expect(requests[0].headers.authorization).toBe("Bearer replacement");
  expect(requests[0].headers["x-correlation-id"]).toBe("test");
});

it("returns only the E2B entry fields", async () => {
  responseBody = JSON.stringify({ ...ENTRY, internalField: "must-not-return" });
  const result = await volume.getInfo("/data.txt");
  expect(result).not.toHaveProperty("internalField");
  expect(Object.keys(result).sort()).toEqual(["name", "type", "path", "size", "mode", "uid", "gid", "atime", "mtime", "ctime", "target"].sort());
});

it("supports streamIdleTimeoutMs=0 without aborting a slow stream", async () => {
  await withStreamingGateway((_req, res) => {
    res.writeHead(200);
    res.write("first");
    const timer = setTimeout(() => res.end("last"), 200);
    res.on("close", () => clearTimeout(timer));
  }, async volume => {
    const stream = await volume.readFile("/data.txt", { format: "stream", requestTimeoutMs: 50, streamIdleTimeoutMs: 0 });
    const reader = stream.getReader();
    const chunks: Uint8Array[] = [];
    try {
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        chunks.push(next.value);
      }
    } finally { reader.releaseLock(); }
    expect(Buffer.concat(chunks).toString()).toBe("firstlast");
  });
});
