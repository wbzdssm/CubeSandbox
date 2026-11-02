// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

/**
 * CubeSandbox persistent-volume management.
 *
 * e2b-compatible: the {@link Volume} static methods mirror the e2b SDK (and
 * the `cubesandbox` Python SDK's `Volume` class). Volumes survive sandbox
 * lifecycles; mount them at creation via `Sandbox.create({ volumeMounts })`.
 */

import { Config, type ConfigOptions, resolveConfig } from "./config.js";
import {
  ApiError,
  AuthenticationError,
  VolumeInUseError,
  VolumeNotFoundError,
  VolumeError,
} from "./exceptions.js";
import { fetch, Headers, Response, Agent, ProxyAgent } from "undici";
import { VolumeContent, type VolumeContentOptions, type VolumeMetadataOptions, type VolumeReadOptions, type VolumeWriteData } from "./volume-content.js";

export interface VolumeConnectionOpts extends ConfigOptions {
  config?: Config | ConfigOptions;
  domain?: string;
  debug?: boolean;
  signal?: AbortSignal;
  headers?: Record<string, string>;
  apiHeaders?: Record<string, string>;
  proxy?: string;
  httpVersion?: "1.1" | "2";
  logger?: { debug?: (message: string, ...args: any[]) => void };
}

export interface VolumeAndToken {
  volumeId: string;
  name: string;
  token: string;
  domain?: string;
}

function managementConfig(options: VolumeConnectionOpts): Config {
  if (options.config) return new Config({ ...resolveConfig(options.config), ...options });
  const domain = options.domain || process.env.E2B_DOMAIN || "e2b.app";
  const debug = options.debug ?? (process.env.E2B_DEBUG?.toLowerCase() === "true");
  return new Config({
    requestTimeoutMs: 60000,
    ...options,
    apiUrl: options.apiUrl || process.env.CUBE_API_URL || process.env.E2B_API_URL || (debug ? "http://localhost:3000" : `https://api.${domain}`),
  });
}

async function volumeFetch(config: Config, url: string, options: VolumeConnectionOpts, init: Parameters<typeof fetch>[1] = {}) {
  const headers = new Headers(init.headers);
  if (config.apiKey) headers.set("X-API-Key", config.apiKey);
  for (const [key, value] of Object.entries({ ...options.headers, ...options.apiHeaders })) headers.set(key, value);
  if (!Number.isFinite(config.requestTimeoutMs) || config.requestTimeoutMs < 0) throw new Error("requestTimeoutMs must be non-negative and finite");
  const version = options.httpVersion ?? process.env.E2B_HTTP_VERSION ?? "2";
  if (!["1.1", "2"].includes(version)) throw new Error("httpVersion must be '1.1' or '2'");
  const dispatcher = options.proxy ? new ProxyAgent({ uri: options.proxy, allowH2: version === "2" }) : new Agent({ allowH2: version === "2" });
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (options.signal?.aborted) abort();
  else options.signal?.addEventListener("abort", abort, { once: true });
  const timer = config.requestTimeoutMs > 0 ? setTimeout(abort, config.requestTimeoutMs) : undefined;
  try {
    const response = await fetch(url, { ...init, headers, redirect: "manual", signal: controller.signal, dispatcher });
    const body = await response.arrayBuffer();
    options.logger?.debug?.("volume management response", response.status);
    return new Response([204, 205, 304].includes(response.status) ? null : body, { status: response.status, headers: response.headers });
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", abort);
    await dispatcher.close();
  }
}

/**
 * Mirrors CubeAPI's `MAX_VOLUME_NAME_LEN` and the `^[a-zA-Z0-9_-]+$` rule
 * enforced in `CubeAPI/src/handlers/volumes.rs`. Validating client-side turns
 * an opaque HTTP 400 into a clean error at the call site.
 */
export const MAX_VOLUME_NAME_LEN = 128;
const VOLUME_NAME_RE = /^[a-zA-Z0-9_-]+$/;

const INSPECT = Symbol.for("nodejs.util.inspect.custom");

function maskToken(token: string): string {
  return token ? "***" : "";
}

/** A CubeSandbox persistent volume descriptor. */
export interface VolumeInfo {
  volumeId: string;
  name: string;
}

export const VolumeInfo = class {
  /** Stable identifier (equals ``name`` or an auto-generated UUID). */
  readonly volumeId: string;
  /** Human-readable display name. */
  readonly name: string;
  /**
   * Optional auth token issued by the volume plugin. Empty when the plugin
   * does not issue one, and always empty for entries returned by
   * {@link Volume.list} (tokens are only surfaced on create / get-single).
   * It is a data-plane credential: `toString`/`console.log` mask it, so read
   * the field directly when the actual value is needed.
   */
  readonly token: string;

  readonly domain?: string;

  constructor(volumeId: string, name: string, token = "", domain?: string) {
    this.volumeId = volumeId;
    this.name = name;
    this.token = token;
    this.domain = domain;
  }

  static fromDict(data: Record<string, any>): VolumeAndToken {
    if (!data || typeof data !== "object") throw new ApiError("invalid volume response");
    const volumeId = data.volumeID ?? data.volume_id;
    validateVolumeId(volumeId);
    if (typeof data.name !== "string" || (data.token != null && typeof data.token !== "string")) throw new ApiError("invalid volume response");
    return { volumeId, name: data.name, token: data.token ?? "", domain: typeof data.domain === "string" && data.domain ? data.domain : undefined };
  }

  /** Masks the token so the credential never leaks into logs or error text. */
  toString(): string {
    return `VolumeInfo(volumeId=${JSON.stringify(this.volumeId)}, name=${JSON.stringify(
      this.name,
    )}, token=${JSON.stringify(maskToken(this.token))})`;
  }

  /** `console.log` / `util.inspect` render the masked form as well. */
  [INSPECT](): string {
    return this.toString();
  }
};

/**
 * CubeSandbox mount options for one Volume attachment.
 *
 * Plain {@link Volume}, {@link VolumeInfo} and volume-ID string values remain
 * the e2b-compatible shorthand in ``volumeMounts``. Wrap a value only when
 * Cube-specific attachment options such as ``readOnly`` are needed.
 */
export class VolumeMount {
  readonly volume: Volume | VolumeInfo | string;
  readonly readOnly: boolean;

  constructor(volume: Volume | VolumeInfo | string, options: { readOnly?: boolean } = {}) {
    this.volume = volume;
    this.readOnly = options.readOnly ?? false;
  }
}

/**
 * ``Sandbox.create({ volumeMounts })`` argument — an e2b-compatible mapping of
 * mount path → volume, with an optional {@link VolumeMount} wrapper for
 * attachment-specific options.
 */
export type VolumeMountsArg = Record<string, Volume | VolumeInfo | string | VolumeMount>;

function validateName(name: string): void {
  if (!name) {
    return;
  }
  if (name.length > MAX_VOLUME_NAME_LEN) {
    throw new Error(
      `volume name must be at most ${MAX_VOLUME_NAME_LEN} characters, got ${name.length}`,
    );
  }
  if (!VOLUME_NAME_RE.test(name)) {
    throw new Error(
      `volume name must match ^[a-zA-Z0-9_-]+$ (letters, digits, '_' and '-'), got ${JSON.stringify(name)}`,
    );
  }
}

/**
 * Reject volume IDs that are unsafe to embed in a URL path.
 *
 * Defense-in-depth against path traversal: a malicious ``volumeId`` such as
 * ``../other`` must not be interpolated into the request URL. The accepted
 * character class covers both named volumes and auto-generated UUIDs.
 */
function validateVolumeId(volumeId: string): string {
  if (typeof volumeId !== "string" || !volumeId) {
    throw new Error(`volumeId must be a non-empty string, got ${JSON.stringify(volumeId)}`);
  }
  if (!VOLUME_NAME_RE.test(volumeId)) {
    throw new Error(
      `volumeId must match ^[a-zA-Z0-9_-]+$ (letters, digits, '_' and '-'), got ${JSON.stringify(volumeId)}`,
    );
  }
  return volumeId;
}

function resolveVolumeId(volume: Volume | VolumeInfo | string): string {
  if (volume && typeof volume === "object" && typeof volume.volumeId === "string" && typeof volume.name === "string") {
    return volume.volumeId;
  }
  if (typeof volume === "string") {
    return volume;
  }
  throw new Error(
    "volumeMounts value must be a Volume, VolumeInfo, VolumeMount or volume-id string",
  );
}

/**
 * Validate a mount path before forwarding it to the backend: a clean absolute
 * POSIX path with no ``.``/``..`` segments. The backend validates as well;
 * this turns a malicious or typo'd path into a clean error at the call site.
 */
function validateMountPath(path: string): string {
  if (typeof path !== "string" || !path) {
    throw new Error(`volume mount path must be a non-empty string, got ${JSON.stringify(path)}`);
  }
  if (!path.startsWith("/")) {
    throw new Error(
      `volume mount path must be absolute (start with '/'), got ${JSON.stringify(path)}`,
    );
  }
  for (const segment of path.split("/")) {
    if (segment === "." || segment === "..") {
      throw new Error(
        `volume mount path must not contain '.' or '..' segments, got ${JSON.stringify(path)}`,
      );
    }
  }
  return path;
}

/**
 * Serialize the e2b-style ``{path: volume}`` mapping into the wire format
 * shared with the Python and Go SDKs: ``[{name, path, readOnly?}]`` with
 * ``readOnly`` omitted when false.
 */
export function serializeVolumeMounts(mounts: VolumeMountsArg): Record<string, unknown>[] {
  const serialized: Record<string, unknown>[] = [];
  for (const [path, value] of Object.entries(mounts)) {
    const volume = value instanceof VolumeMount ? value.volume : value;
    const readOnly = value instanceof VolumeMount ? value.readOnly : false;
    const item: Record<string, unknown> = {
      name: validateVolumeId(resolveVolumeId(volume)),
      path: validateMountPath(path),
    };
    if (readOnly) {
      item.readOnly = true;
    }
    serialized.push(item);
  }
  return serialized;
}

async function checkVolumeResponse(resp: {
  ok: boolean;
  status: number;
  text: () => Promise<string>;
}, volumeNotFound = false): Promise<void> {
  if (resp.ok) {
    return;
  }
  let msg = "";
  try {
    const raw = (await resp.text()).trim();
    try {
      const body = JSON.parse(raw) as Record<string, unknown>;
      msg =
        (typeof body.message === "string" && body.message) ||
        (typeof body.detail === "string" && body.detail) ||
        raw;
    } catch {
      msg = raw;
    }
  } catch {
    msg = "";
  }
  if (!msg) {
    msg = `HTTP ${resp.status}`;
  }
  const code = resp.status;
  if (code === 401 || code === 403) {
    throw new AuthenticationError(msg, code);
  }
  if (code === 404 && volumeNotFound) {
    throw new VolumeNotFoundError(msg, code);
  }
  // CubeMaster refuses to delete a mounted volume with "volume <id> is in use
  // by <n> node(s); ..." relayed as HTTP 409. The duplicate-name 409 ("volume
  // already exists") intentionally stays a generic ApiError.
  if (code === 409 && msg.toLowerCase().includes("in use")) {
    throw new VolumeInUseError(msg, code);
  }
  throw new VolumeError(msg, code);
}

/**
 * Class-level helper for CubeSandbox persistent-volume management.
 *
 * e2b-compatible: methods mirror the e2b SDK. ``create`` and ``connect``
 * return a live {@link Volume} instance; ``list`` and ``getInfo`` return
 * plain {@link VolumeInfo} descriptors.
 */
export class Volume extends VolumeInfo {
  private readonly config: Config;
  private readonly connection: VolumeConnectionOpts;
  override readonly domain?: string;
  readonly debug?: boolean;

  constructor(volumeId: string, name: string, token: string, domain?: string, debug?: boolean);
  constructor(info: VolumeAndToken, config?: Config | VolumeConnectionOpts);
  constructor(info: string | VolumeAndToken, nameOrConfig?: string | Config | VolumeConnectionOpts, token = "", domain?: string, debug?: boolean) {
    super(typeof info === "string" ? info : info.volumeId, typeof info === "string" ? nameOrConfig as string : info.name, typeof info === "string" ? token : info.token);
    this.domain = typeof info === "string" ? domain : info.domain;
    this.connection = typeof nameOrConfig === "object" ? nameOrConfig : {};
    this.debug = debug ?? this.connection.debug;
    this.config = typeof info === "string" ? new Config({ requestTimeoutMs: 60000 }) : resolveConfig(nameOrConfig as Config | ConfigOptions | undefined);
  }

  private content(options: VolumeContentOptions = {}): VolumeContent {
    validateVolumeId(this.volumeId);
    const domain = options.domain || this.domain || process.env.E2B_DOMAIN || "e2b.app";
    const debug = options.debug ?? this.debug ?? (process.env.E2B_DEBUG?.toLowerCase() === "true");
    const config = new Config({ ...this.config, volumeApiUrl: options.apiUrl || this.config.volumeApiUrl || (debug ? "http://localhost:8080" : `https://api.${domain}`) });
    return new VolumeContent(this.volumeId, options.token || this.token, config, { proxy: this.connection.proxy, httpVersion: this.connection.httpVersion });
  }

  readFile(path: string, options: VolumeReadOptions & { format: "stream" }): Promise<import("node:stream/web").ReadableStream<Uint8Array>>;
  readFile(path: string, options: VolumeReadOptions & { format: "bytes" }): Promise<Uint8Array>;
  readFile(path: string, options: VolumeReadOptions & { format: "blob" }): Promise<import("node:buffer").Blob>;
  readFile(path: string, options?: VolumeReadOptions & { format?: "text" }): Promise<string>;
  readFile(path: string, options: VolumeReadOptions = {}) {
    const content = this.content(options);
    switch (options.format) {
      case "bytes": return content.readFile(path, { ...options, format: "bytes" });
      case "blob": return content.readFile(path, { ...options, format: "blob" });
      case "stream": return content.readFile(path, { ...options, format: "stream" });
      default: return content.readFile(path, options as VolumeReadOptions & { format?: "text" });
    }
  }

  writeFile(path: string, data: VolumeWriteData, options: VolumeMetadataOptions = {}) {
    return this.content(options).writeFile(path, data, options);
  }

  list(path: string, options: VolumeContentOptions & { depth?: number } = {}) {
    return this.content(options).list(path, options);
  }

  getInfo(path: string, options: VolumeContentOptions = {}) {
    return this.content(options).getInfo(path, options);
  }

  makeDir(path: string, options: VolumeMetadataOptions = {}) {
    return this.content(options).makeDir(path, options);
  }

  updateMetadata(path: string, values: Pick<VolumeMetadataOptions, "uid" | "gid" | "mode">, options: VolumeContentOptions = {}) {
    return this.content(options).updateMetadata(path, values, options);
  }

  exists(path: string, options: VolumeContentOptions = {}) {
    return this.content(options).exists(path, options);
  }

  remove(path: string, options: VolumeContentOptions = {}) {
    return this.content(options).remove(path, options);
  }

  /** Masks the token, same as {@link VolumeInfo.toString}. */
  override toString(): string {
    return super.toString().replace("VolumeInfo(", "Volume(");
  }

  override [INSPECT](): string {
    return this.toString();
  }

  /**
   * POST /volumes — create a new persistent volume.
   *
   * e2b-compatible by default: when ``driver`` is omitted, no driver is sent
   * and the backend falls back to its *first configured* volume plugin. Pass a
   * non-empty ``driver`` to pin a specific plugin (a CubeSandbox extension),
   * e.g. ``"cos"`` or ``"nfs"`` — it must match a ``volume_plugins[].name``
   * entry in the CubeMaster config.
   *
   * ``name`` is optional: it must match ``^[a-zA-Z0-9_-]+$`` and be at most
   * {@link MAX_VOLUME_NAME_LEN} characters; when omitted the server generates
   * a UUID used as both the volume name and volume ID.
   */
  static async create(name: string, options?: VolumeConnectionOpts): Promise<Volume>;
  static async create(options?: VolumeConnectionOpts & { name?: string; driver?: string }): Promise<Volume>;
  static async create(nameOrOptions: string | (VolumeConnectionOpts & { name?: string; driver?: string }) = {}, connection: VolumeConnectionOpts = {}): Promise<Volume> {
    const options = typeof nameOrOptions === "string" ? connection : nameOrOptions;
    const name = typeof nameOrOptions === "string" ? nameOrOptions : nameOrOptions.name ?? "";
    validateName(name);
    const cfg = managementConfig(options);
    const payload: Record<string, unknown> = { name };
    if (typeof nameOrOptions !== "string" && nameOrOptions.driver) {
      payload.driver = nameOrOptions.driver;
    }
    const resp = await volumeFetch(cfg, `${cfg.apiUrl}/volumes`, options, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await checkVolumeResponse(resp);
    const info = VolumeInfo.fromDict((await resp.json()) as Record<string, any>);
    return new Volume({ ...info, domain: info.domain || options.domain || process.env.E2B_DOMAIN || "e2b.app" }, { ...cfg, debug: options.debug, proxy: options.proxy, httpVersion: options.httpVersion });
  }

  /**
   * GET /volumes — list all volumes. Tokens are only surfaced on
   * create / get-single, so ``token`` is always empty in list results.
   */
  static async list(
    options: VolumeConnectionOpts = {},
  ): Promise<VolumeInfo[]> {
    const cfg = managementConfig(options);
    const resp = await volumeFetch(cfg, `${cfg.apiUrl}/volumes`, options);
    await checkVolumeResponse(resp);
    const data = ((await resp.json()) as Record<string, any>[]) || [];
    if (!Array.isArray(data)) return [];
    return data.map((d) => {
      const info = VolumeInfo.fromDict(d);
      return { volumeId: info.volumeId, name: info.name };
    });
  }

  /** GET /volumes/:id — fetch a single volume, including its token. */
  static async getInfo(
    volumeId: string,
    options: VolumeConnectionOpts = {},
  ): Promise<VolumeAndToken> {
    validateVolumeId(volumeId);
    const cfg = managementConfig(options);
    const resp = await volumeFetch(cfg, `${cfg.apiUrl}/volumes/${volumeId}`, options);
    await checkVolumeResponse(resp, true);
    return VolumeInfo.fromDict((await resp.json()) as Record<string, any>);
  }

  /**
   * Connect to an existing volume (e2b compatible; wraps {@link getInfo}) and
   * return a live {@link Volume} instance.
   */
  static async connect(
    volumeId: string,
    options: VolumeConnectionOpts = {},
  ): Promise<Volume> {
    const info = await Volume.getInfo(volumeId, options);
    const volume = new Volume({ ...info, domain: info.domain || options.domain || process.env.E2B_DOMAIN || "e2b.app" }, { ...managementConfig(options), debug: options.debug, proxy: options.proxy, httpVersion: options.httpVersion });
    return volume;
  }

  /**
   * DELETE /volumes/:id — permanently delete a volume.
   *
   * e2b-compatible: resolves ``true`` on success and ``false`` when the volume
   * does not exist (treated as idempotent — "already gone"). Deleting a volume
   * does **not** auto-detach it from running sandboxes: while any sandbox
   * still mounts it, the server refuses with a {@link VolumeInUseError}.
   */
  static async destroy(
    volumeId: string,
    options: VolumeConnectionOpts = {},
  ): Promise<boolean> {
    validateVolumeId(volumeId);
    const cfg = managementConfig(options);
    const resp = await volumeFetch(cfg, `${cfg.apiUrl}/volumes/${volumeId}`, options, {
      method: "DELETE",
    });
    if (resp.status === 404) {
      return false;
    }
    await checkVolumeResponse(resp);
    return true;
  }
}
