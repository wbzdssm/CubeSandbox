// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

import { Blob } from "node:buffer";
import { ReadableStream } from "node:stream/web";
import { fetch, type RequestInit, type Response } from "undici";
import type { Config } from "./config.js";
import { ApiError, AuthenticationError, VolumeContentUnavailableError, VolumePathNotFoundError } from "./exceptions.js";

export enum VolumeFileType {
  FILE = "file", DIRECTORY = "directory", SYMLINK = "symlink", UNKNOWN = "unknown",
}

export interface VolumeEntryStat {
  name: string;
  type: VolumeFileType;
  path: string;
  size: number;
  mode: number;
  uid: number;
  gid: number;
  atime: Date;
  mtime: Date;
  ctime: Date;
  target?: string | null;
}

export interface VolumeContentOptions {
  requestTimeoutMs?: number;
  signal?: AbortSignal;
}
export interface VolumeMetadataOptions extends VolumeContentOptions {
  uid?: number;
  gid?: number;
  mode?: number;
  force?: boolean;
}
export interface VolumeReadOptions extends VolumeContentOptions {
  format?: "text" | "bytes" | "blob" | "stream";
}
export type VolumeWriteData = string | Uint8Array | ArrayBuffer | Blob | ReadableStream<Uint8Array>;

function pathWithinVolume(path: string): string {
  if (typeof path !== "string" || !path.startsWith("/") || /[\u0000\\]/.test(path) || path.split("/").some(s => s === "." || s === "..")) {
    throw new Error("volume path must be absolute without NUL, backslash or dot segments");
  }
  return path;
}

function metadata(options: VolumeMetadataOptions): Record<string, number | boolean> {
  const result: Record<string, number | boolean> = {};
  for (const key of ["uid", "gid", "mode"] as const) {
    const value = options[key];
    if (value !== undefined) {
      if (!Number.isSafeInteger(value) || value < 0) throw new Error(`${key} must be a non-negative integer`);
      result[key] = value;
    }
  }
  if (options.force !== undefined) {
    if (typeof options.force !== "boolean") throw new Error("force must be boolean");
    result.force = options.force;
  }
  return result;
}

function entry(data: unknown): VolumeEntryStat {
  if (!data || typeof data !== "object") throw new ApiError("invalid volume entry response");
  const value = data as Record<string, unknown>;
  if (typeof value.name !== "string" || typeof value.path !== "string" || !value.path.startsWith("/") || !Object.values(VolumeFileType).includes(value.type as VolumeFileType)) {
    throw new ApiError("invalid volume entry response");
  }
  for (const key of ["size", "mode", "uid", "gid"]) {
    if (typeof value[key] !== "number" || !Number.isSafeInteger(value[key]) || (value[key] as number) < 0) throw new ApiError("invalid volume entry metadata");
  }
  const times: Record<string, Date> = {};
  for (const key of ["atime", "mtime", "ctime"]) {
    if (typeof value[key] !== "string") throw new ApiError("invalid volume entry timestamp");
    const date = new Date(value[key] as string);
    if (!Number.isFinite(date.getTime())) throw new ApiError("invalid volume entry timestamp");
    times[key] = date;
  }
  if (value.target != null && typeof value.target !== "string") throw new ApiError("invalid symlink target");
  return { ...value, ...times } as unknown as VolumeEntryStat;
}

/** E2B-shaped direct client. A configured URL alone cannot add gateway support. */
export class VolumeContent {
  private readonly baseUrl: string;

  constructor(volumeId: string, private readonly token: string, private readonly config: Config) {
    if (!/^[A-Za-z0-9_-]+$/.test(volumeId)) throw new Error("invalid volume ID");
    const raw = config.volumeApiUrl;
    if (!raw) throw new VolumeContentUnavailableError("configure CUBE_VOLUME_API_URL for a deployed content gateway");
    let url: URL;
    try { url = new URL(raw); } catch { throw new Error("invalid volume API URL"); }
    if (!["http:", "https:"].includes(url.protocol) || !url.hostname || url.username || url.password || /[?#]/.test(raw)) {
      throw new Error("volume API URL must be HTTP(S) without credentials, query or fragment");
    }
    if (url.protocol !== "https:" && !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)) {
      throw new Error("volume credentials require HTTPS outside loopback");
    }
    if (!token) throw new AuthenticationError("a gateway-issued volume token is required");
    if (!/^[A-Za-z0-9._~+/-]+=*$/.test(token)) throw new Error("token must use the Bearer character set");
    this.baseUrl = `${url.toString().replace(/\/+$/, "")}/volumecontent/${volumeId}`;
  }

  private async failure(response: Response): Promise<never> {
    let text = "";
    if (response.body) {
      const reader = response.body.getReader();
      const parts: Uint8Array[] = [];
      let size = 0;
      try {
        while (true) {
          const next = await reader.read();
          if (next.done) break;
          size += next.value.byteLength;
          if (size > 65536) { parts.length = 0; break; }
          parts.push(next.value);
        }
        text = Buffer.concat(parts).toString("utf8");
      } finally { await reader.cancel(); reader.releaseLock(); }
    }
    let code = "";
    let message = `volume content request failed (HTTP ${response.status})`;
    try {
      const body = JSON.parse(text) as Record<string, unknown>;
      if (typeof body?.code === "string") code = body.code;
      if (typeof body?.message === "string") message = body.message.split(this.token).join("***");
    } catch { /* Non-JSON errors do not disclose raw headers or credentials. */ }
    if ([401, 403].includes(response.status)) throw new AuthenticationError(message, response.status);
    if (response.status === 404 && ["path_not_found", "volume_path_not_found", "not_found"].includes(code)) throw new VolumePathNotFoundError(message, 404);
    if ([404, 405, 501, 503].includes(response.status)) throw new VolumeContentUnavailableError(message, response.status);
    throw new ApiError(message, response.status);
  }

  private async request(method: string, endpoint: string, path: string, options: VolumeContentOptions,
                        params: Record<string, number | boolean> = {}, body?: RequestInit["body"], json = false) {
    pathWithinVolume(path);
    const timeout = options.requestTimeoutMs ?? this.config.requestTimeoutMs;
    if (!Number.isFinite(timeout) || timeout <= 0) throw new Error("requestTimeoutMs must be positive and finite");
    const url = new URL(`${this.baseUrl}/${endpoint}`);
    url.searchParams.set("path", path);
    for (const [name, value] of Object.entries(params)) url.searchParams.set(name, String(value));
    const controller = new AbortController();
    const abort = () => controller.abort();
    if (options.signal?.aborted) abort();
    else options.signal?.addEventListener("abort", abort, { once: true });
    const timer = setTimeout(abort, timeout);
    const release = () => { clearTimeout(timer); options.signal?.removeEventListener("abort", abort); };
    try {
      const response = await fetch(url, {
        method, body, redirect: "manual", signal: controller.signal,
        headers: { Authorization: `Bearer ${this.token}`, ...(body !== undefined ? { "Content-Type": json ? "application/json" : "application/octet-stream" } : {}) },
        ...(body instanceof ReadableStream ? { duplex: "half" as const } : {}),
      });
      if (!response.ok) await this.failure(response);
      return { response, release };
    } catch (error) { release(); throw error; }
  }

  readFile(path: string, options: VolumeReadOptions & { format: "stream" }): Promise<ReadableStream<Uint8Array>>;
  readFile(path: string, options: VolumeReadOptions & { format: "bytes" }): Promise<Uint8Array>;
  readFile(path: string, options: VolumeReadOptions & { format: "blob" }): Promise<Blob>;
  readFile(path: string, options?: VolumeReadOptions & { format?: "text" }): Promise<string>;
  async readFile(path: string, options: VolumeReadOptions = {}): Promise<string | Uint8Array | Blob | ReadableStream<Uint8Array>> {
    if (!["text", "bytes", "blob", "stream"].includes(options.format ?? "text")) throw new Error("invalid read format");
    const { response, release } = await this.request("GET", "file", path, options);
    if (options.format === "stream") {
      if (!response.body) { release(); return new ReadableStream({ start(c) { c.close(); } }); }
      const reader = response.body.getReader();
      return new ReadableStream<Uint8Array>({
        async pull(controller) {
          try {
            const next = await reader.read();
            if (next.done) { controller.close(); reader.releaseLock(); release(); }
            else controller.enqueue(next.value);
          } catch (error) { controller.error(error); reader.releaseLock(); release(); }
        },
        async cancel(reason) { try { await reader.cancel(reason); } finally { reader.releaseLock(); release(); } },
      });
    }
    try {
      if (options.format === "bytes") return new Uint8Array(await response.arrayBuffer());
      if (options.format === "blob") return await response.blob();
      return await response.text();
    } finally { release(); }
  }

  async writeFile(path: string, data: VolumeWriteData, options: VolumeMetadataOptions = {}): Promise<VolumeEntryStat> {
    let body: RequestInit["body"];
    if (typeof data === "string" || data instanceof Uint8Array || data instanceof Blob || data instanceof ReadableStream) body = data;
    else if (data instanceof ArrayBuffer) body = new Uint8Array(data);
    else throw new Error("file data must be text, bytes, Blob, ArrayBuffer or ReadableStream");
    const { response, release } = await this.request("PUT", "file", path, options, metadata(options), body);
    try { return entry(await response.json()); } finally { release(); }
  }

  async list(path: string, options: VolumeContentOptions & { depth?: number } = {}): Promise<VolumeEntryStat[]> {
    const params: Record<string, number> = {};
    if (options.depth !== undefined) {
      if (!Number.isSafeInteger(options.depth) || options.depth < 0) throw new Error("depth must be non-negative integer");
      params.depth = options.depth;
    }
    const { response, release } = await this.request("GET", "dir", path, options, params);
    try {
      const entries = await response.json();
      if (!Array.isArray(entries)) throw new ApiError("invalid volume directory response");
      return entries.map(entry);
    } finally { release(); }
  }

  async getInfo(path: string, options: VolumeContentOptions = {}): Promise<VolumeEntryStat> {
    const { response, release } = await this.request("GET", "path", path, options);
    try { return entry(await response.json()); } finally { release(); }
  }

  async makeDir(path: string, options: VolumeMetadataOptions = {}): Promise<VolumeEntryStat> {
    const { response, release } = await this.request("POST", "dir", path, options, metadata(options));
    try { return entry(await response.json()); } finally { release(); }
  }

  async updateMetadata(path: string, values: Pick<VolumeMetadataOptions, "uid" | "gid" | "mode">, options: VolumeContentOptions = {}): Promise<VolumeEntryStat> {
    const { response, release } = await this.request("PATCH", "path", path, options, {}, JSON.stringify(metadata(values)), true);
    try { return entry(await response.json()); } finally { release(); }
  }

  async exists(path: string, options: VolumeContentOptions = {}): Promise<boolean> {
    try { await this.getInfo(path, options); return true; }
    catch (error) { if (error instanceof VolumePathNotFoundError) return false; throw error; }
  }

  async remove(path: string, options: VolumeContentOptions = {}): Promise<void> {
    if (pathWithinVolume(path).replace(/\//g, "") === "") throw new Error("removing volume root is not allowed");
    const { response, release } = await this.request("DELETE", "path", path, options);
    try { await response.body?.cancel(); } finally { release(); }
  }
}
