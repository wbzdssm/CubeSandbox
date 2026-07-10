# Persistent Volumes

`Volume` is the class-level helper for managing CubeSandbox **persistent
volumes** — e2b-compatible stores backed by a volume plugin (COS, NFS, …).
Create a volume, mount it into a sandbox via
`Sandbox.create(volume_mounts=[...])`, and its data survives sandbox restarts
and can be shared across sandboxes.

```python
from cubesandbox import Sandbox, Volume, VolumeMount
```

All `Volume` methods are **class methods** — no instance needed.

---

## Configuration

Volume management calls only hit the management plane (`CUBE_API_URL`).
Reading/writing files *inside* a mounted volume goes through the data plane, so
`CUBE_PROXY_NODE_IP` is also required when running outside the CubeProxy node.

| Environment Variable | Required | Default | Used by |
|---|:---:|---|---|
| `CUBE_API_URL` | ✅ | `http://127.0.0.1:3000` | all `Volume.*` calls |
| `CUBE_PROXY_NODE_IP` | remote | — | `sb.files.*` on the mounted volume |
| `CUBE_TEMPLATE_ID` | for mount | — | `Sandbox.create(...)` |

You can also pass an explicit `Config` to every method via `config=`.

---

## API reference

| Method | HTTP | Parameters | Returns |
|---|---|---|---|
| `Volume.create(name=None, *, driver=None, config=None)` | `POST /volumes` | `name`: optional, `^[a-zA-Z0-9_-]+$`, ≤128 chars. `driver`: **optional** plugin name (e.g. `"cos"`, `"nfs"`); when falsy (`None`/`""`) no driver is sent → backend uses the first configured plugin. | `VolumeInfo` |
| `Volume.list(*, config=None)` | `GET /volumes` | — | `list[VolumeInfo]` (**`token` always empty**) |
| `Volume.get(volume_id, *, config=None)` | `GET /volumes/{id}` | `volume_id`: identifier | `VolumeInfo` (with `token`) |
| `Volume.delete(volume_id, *, config=None)` | `DELETE /volumes/{id}` | `volume_id`: identifier | `None` |

### `create`: default plugin vs. pinned driver

Modeled on e2b, where `Volume.create(name)` takes **no** driver:

- **`create(name)`** — e2b-compatible. The request body is just
  `{"name": ...}`; CubeMaster falls back to the **first configured** volume
  plugin. Use this by default, including when several plugins are registered
  and "the first one" is acceptable.
- **`create(name, driver="cos")`** — CubeSandbox extension. Pass a non-empty
  `driver` to pin a specific plugin. Use it only when you must select among
  multiple plugins. A `None`/empty `driver` means "unspecified" and the field
  is simply not sent.

> Wire body: without a driver it sends `{"name": "<name-or-empty>"}`; with a
> non-empty `driver` it adds `{"driver": "<driver>"}`. When `name` is omitted the
> server generates a UUID and uses it as both the name and the volume ID.

### `VolumeInfo`

| Attribute | Type | Notes |
|---|---|---|
| `.volume_id` | `str` | Stable identifier (name or auto UUID). Maps from `volumeID`/`volume_id`. |
| `.name` | `str` | Display name. |
| `.token` | `str` | Plugin-issued token. Populated on `create` / `get`; **empty on `list`**. |

### `VolumeMount`

Passed to `Sandbox.create(volume_mounts=[...])`. Either the typed dataclass or a
plain dict is accepted:

```python
VolumeMount(name=<volume_id>, path="/workspace")   # typed
{"name": <volume_id>, "path": "/workspace"}         # dict — equivalent
```

`name` must be an existing `volume_id`; a dict missing `name`/`path` raises
`ValueError`.

---

## Examples

### 1. Create (default plugin, e2b-compatible)

```python
from cubesandbox import Volume

vol = Volume.create("my-data")     # name optional; omit to get a UUID
print(vol.volume_id, vol.name, vol.token)
```

### 2. Create pinned to a specific driver

```python
vol = Volume.create("my-data", driver="cos")

# An empty driver means "unspecified": no driver is sent, so the backend uses
# its first configured plugin.
Volume.create("x", driver="")   # equivalent to Volume.create("x")
```

### 3. List / get / delete

```python
for v in Volume.list():            # note: v.token is "" here
    print(v.volume_id, v.name)

one = Volume.get(vol.volume_id)    # one.token is populated
Volume.delete(vol.volume_id)       # kill any mounting sandbox first (see note)
```

### 4. Mount into a sandbox and use it

```python
from cubesandbox import Sandbox, Volume, VolumeMount

vol = Volume.create("my-data", driver="cos")

with Sandbox.create(
    volume_mounts=[VolumeMount(name=vol.volume_id, path="/workspace")],
) as sb:
    sb.files.write("/workspace/note.txt", "persisted!")
    print(sb.files.read("/workspace/note.txt"))   # "persisted!"
```

### 5. Cross-sandbox persistence (the real test)

Data written in one sandbox is readable from another mounting the same volume:

```python
from cubesandbox import Sandbox, Volume, VolumeMount

vol = Volume.create("shared", driver="cos")
mount = [VolumeMount(name=vol.volume_id, path="/workspace")]

# Sandbox A writes, then is destroyed.
a = Sandbox.create(volume_mounts=mount)
a.files.write("/workspace/probe.txt", "hello from A")
a.kill()

# Sandbox B mounts the SAME volume and reads it back.
with Sandbox.create(volume_mounts=mount) as b:
    print(b.files.read("/workspace/probe.txt"))   # "hello from A"

Volume.delete(vol.volume_id)
```

> An end-to-end script covering exactly this flow lives at
> `tests/integration_test_volume.py`. Run it **on the CubeProxy host** so the
> data-plane write stays on loopback:
>
> ```bash
> CUBE_API_URL=http://127.0.0.1:3000 CUBE_TEMPLATE_ID=<tpl> \
> CUBE_PROXY_NODE_IP=127.0.0.1 CUBE_VOLUME_DRIVER=cos \
> python3 tests/integration_test_volume.py
> ```

---

## Errors & status codes

Every `Volume` method routes non-2xx responses through the same mapping:

| HTTP status | Exception raised | Meaning |
|---|---|---|
| 2xx | — (returns normally) | success |
| 401 / 403 | `AuthenticationError` | unauthenticated / forbidden |
| 404 | `VolumeNotFoundError` | volume does not exist (`get` / `delete`) |
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
    Volume.get("does-not-exist")
except VolumeNotFoundError:
    ...                       # ideally: 404
except ApiError as e:
    print(e.status_code)      # currently: 500 — see the known issue below
```

### Known backend issue

The backend currently collapses **all** volume business errors into
**HTTP 500** (`ret_code` is hardcoded to `-1`), so `VolumeNotFoundError` (404),
name-conflict (409) and bad-request (400) are **not** distinguishable at the SDK
level today — they all surface as `ApiError(500)`. The mapping above reflects
the intended contract; it becomes accurate once the backend returns proper
status codes. Details: [`volume-error-code-bug.md`](./volume-error-code-bug.md).

---

## Notes

- **`delete` does not auto-detach.** Deleting a volume still mounted by a
  running sandbox may leak the backend mount. Always `sb.kill()` the mounting
  sandbox(es) first, then `Volume.delete(...)`.
- **`list` never returns tokens.** Tokens are only surfaced by `create` and
  `get`; call `Volume.get(id)` when you need the token.
- **Name is optional everywhere.** Omit it and the server assigns a UUID used as
  both name and `volume_id`.
