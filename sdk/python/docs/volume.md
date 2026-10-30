# Persistent Volumes

`Volume` is the class-level helper for managing CubeSandbox **persistent
volumes** — e2b-compatible stores backed by a volume plugin (COS, NFS, …).
Create a volume, mount it into a sandbox via
`Sandbox.create(volume_mounts={...})` (e2b-compatible mapping), and its data
survives sandbox restarts and can be shared across sandboxes.

```python
from cubesandbox import Sandbox, Volume, VolumeMount
```

Management calls remain class methods: `create` and `connect` return a live
`Volume` instance; `Volume.list()` and `Volume.get_info(id)` return `VolumeInfo`
data; `destroy` returns a `bool`. Instance file operations now follow the E2B
Volume API: `vol.read_file`, `vol.write_file`, `vol.list(path)`,
`vol.get_info(path)`, `vol.make_dir`, `vol.exists`, `vol.update_metadata`,
and `vol.remove`.

**Implementation status:** the Python direct-content client is implemented, but
this checkout's CubeAPI/CubeMaster only provide volume CRUD and sandbox mounts.
They do **not** implement `/volumecontent` or issue content-access capabilities.
Direct methods require a separately deployed compatible content gateway and
its valid volume-scoped token. Configuring a URL alone cannot add that server
capability. There is no hidden helper sandbox or shell fallback.

---

## Configuration

Volume management calls only hit the management plane (`CUBE_API_URL`).
Reading/writing files *inside* a mounted volume goes through the data plane, so
`CUBE_PROXY_NODE_IP` is also required when running outside the CubeProxy node.

| Environment Variable | Required | Default | Used by |
|---|:---:|---|---|
| `CUBE_API_URL` | management | `http://127.0.0.1:3000` | volume CRUD |
| `CUBE_VOLUME_API_URL` | direct content | none | deployed volume-content gateway base URL |
| `CUBE_PROXY_NODE_IP` | remote mount access | none | `sb.files.*` on the mounted volume |
| `CUBE_TEMPLATE_ID` | for mount | none | `Sandbox.create(...)` |

Bind `Config(api_url=..., volume_api_url=...)` via `Volume.create/connect(config=...)`.
Direct operations inherit that configuration and accept `request_timeout=` per
call. They send only `Authorization: Bearer <volume token>` to the gateway,
never the management `X-API-Key` or sandbox access token. Empty plugin tokens
are not authorization: a compatible gateway must issue and verify a scoped
content token. HTTPS is required outside loopback, TLS verification is enabled,
and authenticated redirects are not followed.

---

## API reference

| Method | HTTP | Parameters | Returns |
|---|---|---|---|
| `Volume.create(name=None, *, driver=None, config=None)` | `POST /volumes` | See parameter table below | `Volume` |
| `Volume.connect(volume_id, *, config=None)` | `GET /volumes/{id}` | `volume_id`: identifier | `Volume` |
| `Volume.list(*, config=None)` | `GET /volumes` | — | `list[VolumeInfo]` (**`token` always empty**) |
| `Volume.get_info(volume_id, *, config=None)` | `GET /volumes/{id}` | `volume_id`: identifier | `VolumeInfo` (with `token`) |
| `Volume.destroy(volume_id, *, config=None)` | `DELETE /volumes/{id}` | `volume_id`: identifier | `bool` (`True` on success, `False` on 404) |

**`Volume.create` parameters**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `name` | `str \| None` | No | Volume name. Must match `^[a-zA-Z0-9_-]+$`, ≤128 chars. Omit for a server-assigned UUID. |
| `driver` | `str \| None` | No | Volume plugin name (e.g. `"cos"`). Pass `None` or `""` to omit the field → backend uses its default plugin. |
| `config` | `Config \| None` | No | SDK config, overrides environment variables. |

### `create`

Modeled on e2b, where `Volume.create(name)` takes no driver:

- **`create(name)`** — e2b-compatible. The request body is just
  `{"name": ...}`; the backend uses its **first configured** volume plugin.
- **`create(name, driver="cos")`** — CubeSandbox extension. Pass a non-empty
  `driver` to pin a specific plugin. Use it only when you must select among
  multiple plugins.

> When `name` is omitted the server generates a UUID and uses it as both the
> name and the volume ID.

### `VolumeInfo`

| Attribute | Type | Notes |
|---|---|---|
| `.volume_id` | `str` | Stable identifier (name or auto UUID). Maps from `volumeID`/`volume_id`. |
| `.name` | `str` | Display name. |
| `.token` | `str` | Plugin-issued token. Populated on `create` / `get_info`; **empty on `list`**. |

### Mounting and per-sandbox access modes

Pass a dict mapping mount paths to volumes (e2b-compatible):

```python
Sandbox.create(volume_mounts={"/workspace": vol})
Sandbox.create(volume_mounts={"/workspace": "vol-xxx"})
```

Each value can be a `Volume`, `VolumeInfo`, or bare `volume_id` string and must resolve to an existing volume. Existing e2b-style values remain read-write.

To make one sandbox attachment read-only, wrap its value in `VolumeMount`:

```python
from cubesandbox import Sandbox, VolumeMount

Sandbox.create(
    volume_mounts={
        "/dataset": VolumeMount(vol, read_only=True),
        "/workspace": workspace_vol,
    }
)
```

`read_only` only mounts that attachment as read-only. It prevents the current sandbox from writing or modifying files through that mount, but does not create an immutable snapshot. The same Volume can be attached with different access modes in different sandboxes, for example read-write in sandbox A and read-only in sandbox B.

---

## Examples

### 1. Create

```python
from cubesandbox import Volume

vol = Volume.create("my-data")     # with a name
print(vol.volume_id, vol.name, vol.token)

vol = Volume.create()              # omit name → server-assigned UUID
print(vol.volume_id)               # auto-generated UUID
```

### 2. Mount into a sandbox

```python
from cubesandbox import Sandbox, Volume

vol = Volume.create("my-data", driver="cos")

with Sandbox.create(
    volume_mounts={"/workspace": vol},
) as sb:
    sb.files.write("/workspace/note.txt", "persisted!")
    print(sb.files.read("/workspace/note.txt"))   # "persisted!"
```

### 3. Query and destroy

```python
for v in Volume.list():            # note: v.token is "" here
    print(v.volume_id, v.name)

one = Volume.get_info(vol.volume_id)  # one.token is populated
vol = Volume.connect(vol.volume_id)   # -> live Volume instance
Volume.destroy(vol.volume_id)         # kill all mounting sandboxes first (see Notes)
```

### 4. Cross-sandbox data sharing

Multiple sandboxes can mount the same volume concurrently. Data is visible
to all mounters in real time.

```python
from cubesandbox import Sandbox, Volume

vol = Volume.create("shared", driver="cos")
mount = {"/workspace": vol}

# Sandbox A writes data.
a = Sandbox.create(volume_mounts=mount)
a.files.write("/workspace/probe.txt", "hello from A")

# Sandbox B mounts the same volume and reads it immediately.
with Sandbox.create(volume_mounts=mount) as b:
    print(b.files.read("/workspace/probe.txt"))   # "hello from A"

# ⚠️ All mounting sandboxes must be killed before deleting the volume.
a.kill()
b.kill()  # context manager already killed it on exit; explicit for clarity
Volume.destroy(vol.volume_id)
```

---

## Direct content API and E2B comparison

Reference: [E2B volume read/write](https://e2b.dev/docs/volumes/read-write) and
[Python Volume implementation](https://github.com/e2b-dev/E2B/blob/main/packages/python-sdk/e2b/volume/volume_sync.py),
checked 2026-10-30. This aligns the core sync API and HTTP layout, not every
E2B option or its service availability guarantees. AsyncVolume, watches,
recursive removal and arbitrary request headers are not implemented here.

| Instance operation | Direct HTTP protocol | Return |
|---|---|---|
| `vol.read_file(path, format="text")` | `GET /volumecontent/{id}/file?path=...` | `str` |
| `vol.read_file(path, format="bytes")` | same | `bytes`, without text decoding |
| `vol.read_file(path, format="stream")` | same, streaming response | lazy `Iterator[bytes]` |
| `vol.write_file(path, data, uid=None, gid=None, mode=None, force=None)` | `PUT .../file`, raw octet-stream; options in query | `VolumeEntryStat` |
| `vol.list(path, depth=None)` | `GET .../dir`, depth in query | `list[VolumeEntryStat]` |
| `vol.make_dir(path, uid=None, gid=None, mode=None, force=None)` | `POST .../dir`, options in query | `VolumeEntryStat` |
| `vol.get_info(path)` | `GET .../path` | `VolumeEntryStat` |
| `vol.exists(path)` | stat; only typed missing-path errors become false | `bool` |
| `vol.update_metadata(path, uid=None, gid=None, mode=None)` | `PATCH .../path`, JSON options | `VolumeEntryStat` |
| `vol.remove(path)` | `DELETE .../path` | `None` |

`Volume.list()` / `Volume.get_info(volume_id)` keep their management meaning.
For compatibility, `vol.list()` without a path still lists volumes, and
`vol.get_info(non_absolute_volume_id)` and `vol.get_info(volume_id=...)` retain
the previous management call. Supplying both `path` and `volume_id` is rejected.
File paths must be absolute **within the volume**, not host filesystem paths;
NUL, backslashes and dot traversal segments are rejected. Root removal is
rejected. The gateway must additionally enforce symlink-safe confinement.

File writes accept text, bytes and readable file-like objects. File-like uploads
are read in 64 KiB chunks without materializing the entire file; the SDK does not
close caller-owned files. Read streams are lazy and bound memory usage; explicitly
close the iterator on early exit. Each operation owns and closes its HTTP client,
so this first implementation does not promise cross-call connection pooling.
Default request timeout inherits `Config.request_timeout`; stream reads use a
60-second idle timeout unless a per-call timeout is supplied. These are transport
timeouts, not an overall wall-clock transfer deadline. Unlike E2B, timeout `0`
is rejected instead of disabling timeouts. Writes are never automatically retried
or re-uploaded as multipart on an authentication or server error.

**Example for a deployed compatible gateway only:** the current server cannot
issue `CUBE_VOLUME_CONTENT_TOKEN`. The operator must provide a gateway-issued
scoped capability; do not substitute the management API key or an arbitrary
non-empty plugin token.

```python
import os
from contextlib import closing
from cubesandbox import Config, Volume

config = Config(volume_api_url=os.environ["CUBE_VOLUME_API_URL"])
vol = Volume(
    os.environ["CUBE_VOLUME_ID"], "example",
    token=os.environ["CUBE_VOLUME_CONTENT_TOKEN"], config=config,
)
vol.make_dir("/data", force=True)
vol.write_file("/data/hello.txt", "hello", force=True)
assert vol.read_file("/data/hello.txt") == "hello"
with closing(vol.read_file("/data/hello.txt", format="stream")) as chunks:
    assert b"".join(chunks) == b"hello"
print(vol.list("/data"))
vol.remove("/data/hello.txt")
```

### Existing chain and server-side implementation plan

Current chain:

```text
Volume CRUD -> CubeAPI /volumes -> CubeMaster /cube/volume
            -> plugin Create/Destroy -> t_cube_volume
Sandbox volumeMounts -> Master resolves driver/private_data
                     -> Cubelet plugin Attach/Detach -> hostPath/bind/virtiofs
sb.files -> CubeProxy -> sandbox envd -> mounted files
```

There is no volume content address in the management response and no file RPC in
the plugin interface: controllers only Create/Destroy, nodes only Attach/Detach.
A Volume token is not currently a general file-access authorization mechanism.
Making file calls to `/volumes/{id}` or replacing sandbox ID with volume ID
cannot provide direct access.

Recommended next server phase (not implemented by this SDK change):

1. Add a volume-content gateway with the routes above; keep byte transfers away
   from Master/CubeAPI management handlers. Support mounted filesystem drivers
   through Cubelet's plugin attachment layer instead of exposing private_data or
   backend AK/SK to the SDK. Do not launch a helper sandbox or shell commands.
2. Add session/attachment leases to the existing volume reference accounting.
   Destroy must not race an active transfer; acquire/release and crash cleanup
   must be idempotent. Shared writes use normal filesystem semantics, not an
   implicit atomic/multi-writer transaction guarantee.
3. Issue and validate tenant/owner/volume-scoped read/write capabilities with
   expiry; enforce read-only access **server-side**. Current volume records lack
   owner/tenant scope, so this needs a real AuthZ design, not simply accepting
   any plugin token. Refresh/rotation and logout/revocation remain server work.
4. Enforce root-dir/dirfd confinement, safe symlinks, rename boundaries, limits
   and observable transfer failures; authenticate before filesystem access.
   Never let SDK paths become arbitrary host paths.
5. Publish/configure the gateway endpoint and standardized entry/error types.
   Entry JSON includes name, type (`file/directory/symlink/unknown`), absolute
   volume path, integer size/mode/uid/gid, ISO timestamps atime/mtime/ctime,
   and optional symlink target. `ctime` retains filesystem metadata semantics,
   not a promise of original creation time.
6. Add integration tests: pre-upload -> create sandbox with the same volume ->
   read/modify -> kill sandbox -> SDK direct read. Add token expiry, tenant
   isolation, read-only, traversal/symlink, partial upload and destroy/transfer
   races before enabling the feature in production.

For future HTTP interoperability, a missing **path** must be a structured 404
with `code` in `path_not_found`, `volume_path_not_found`, `not_found` and a
`message`. A bare router 404, missing gateway or missing volume is not interpreted
as `exists=False`; it raises `VolumeContentUnavailableError`. This explicit
error-code requirement is a Cube extension; do not assume any E2B deployment's
arbitrary 404 response is sufficient. Other malformed success responses raise
`ApiError` rather than silently returning an empty directory.

## Errors & status codes

Volume management uses the existing mapping below. Direct methods additionally
raise `VolumePathNotFoundError` for typed missing paths and
`VolumeContentUnavailableError` for missing configuration, unrecognized 404 or
HTTP 405/501/503. Authentication 401/403 stays `AuthenticationError`; redirects
are errors, not credential-forwarding fallbacks. `exists()` does not suppress
authorization, gateway, transport or server errors.


| HTTP status | Exception raised | Meaning |
|---|---|---|
| 2xx | — (returns normally) | success |
| 401 / 403 | `AuthenticationError` | unauthenticated / forbidden |
| 404 | `VolumeNotFoundError` | volume does not exist (`get_info` / `connect` / `destroy`) |
| any other non-2xx (400 / 405 / 409 / 500 …) | `ApiError` | bad params, name conflict, backend error |

Client-side validation raises **before** any network call:

| Condition | Exception |
|---|---|
| `name` violates `^[a-zA-Z0-9_-]+$` or > 128 chars | `ValueError` |
| mount dict missing `name` / `path` | `ValueError` |

All API exceptions derive from `CubeSandboxError` and expose `.status_code`:

```python
from cubesandbox import Volume, VolumeNotFoundError, ApiError

try:
    Volume.get_info("does-not-exist")
except VolumeNotFoundError:
    ...                       # 404
except ApiError as e:
    print(e.status_code)      # 500
```

---

## Notes

- **Kill all mounting sandboxes before deleting a volume.** A volume can be
  mounted by multiple sandboxes concurrently. `Volume.destroy()` does not
  auto-detach — if any running sandbox still holds the volume, the delete may
  fail or leak backend mounts. Always `sb.kill()` every sandbox that mounts the
  volume before calling `Volume.destroy(volume_id)`.
- **`list` never returns tokens.** Tokens are only surfaced by `create` and
  `get_info`; call `Volume.get_info(volume_id)` when you need the token.
- **`name` is optional.** Omit it and the server assigns a UUID that serves as
  both `volume_id` and `name`.
- **Read-only is per attachment.** Use `VolumeMount(volume, read_only=True)` at sandbox creation time. Creating the `Volume` itself remains unchanged.
