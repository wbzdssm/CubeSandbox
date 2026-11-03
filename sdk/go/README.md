# cubesandbox Go SDK

Go SDK for [CubeSandbox](https://github.com/TencentCloud/CubeSandbox). It matches the current Python SDK surface: sandbox lifecycle, code execution, commands, PTY (interactive terminal), filesystem operations (read, write, list, stat, exists, remove, rename, mkdir, watch), snapshots, clone, rollback, and L7 egress policy.

## Install

```bash
go get github.com/tencentcloud/CubeSandbox/sdk/go
```

## Configuration

```bash
export CUBE_API_URL=http://127.0.0.1:3000
export CUBE_TEMPLATE_ID=<your-template-id>

# Optional remote data-plane access.
export CUBE_PROXY_NODE_IP=<cubeproxy-node-ip>
export CUBE_PROXY_PORT_HTTP=80
export CUBE_PROXY_SCHEME=http
export CUBE_SANDBOX_DOMAIN=cube.app

# Template tried first when CreateOptions.MCP is set and TemplateID is empty;
# CUBE_TEMPLATE_ID is used if it does not exist.
export CUBE_MCP_TEMPLATE_ID=mcp-gateway
```

`NewConfigFromEnv` also accepts `E2B_API_URL` and `E2B_API_KEY`; `CUBE_API_URL` and `CUBE_API_KEY` take precedence.
`CUBE_PROXY_SCHEME` supports `http` and `https`; when omitted, port `443` defaults to `https` and other ports default to `http`.

## Create And Run Code

```go
package main

import (
	"context"
	"fmt"

	cubesandbox "github.com/tencentcloud/CubeSandbox/sdk/go"
)

func main() {
	ctx := context.Background()
	client := cubesandbox.NewClient(cubesandbox.NewConfigFromEnv())
	defer client.Close()

	sb, err := client.Create(ctx, cubesandbox.CreateOptions{})
	if err != nil {
		panic(err)
	}
	defer sb.Kill(ctx)

	exec, err := sb.RunCode(ctx, "x = 41\nx + 1", cubesandbox.RunCodeOptions{})
	if err != nil {
		panic(err)
	}
	fmt.Println(exec.Text)
}
```

`Client.Close` releases local idle HTTP connections held by the SDK client. It does not pause or destroy remote sandboxes.
Use `Sandbox.Kill` to destroy a sandbox, or `Sandbox.Pause` when you want to keep the sandbox for a later `Client.Connect`.
When `WithHTTPClient` is used with a shared `*http.Client`, `Client.Close` also closes that shared client's idle connections.

## Commands

```go
result, err := sb.Commands().Run(ctx, "echo hello", cubesandbox.CommandOptions{})
if err != nil {
	panic(err)
}
fmt.Println(result.Stdout, result.Stderr, result.ExitCode)
```

`Commands.Run` starts `/bin/bash -l -c <command>` through envd's `process.Process/Start` API and returns stdout, stderr, and the `EndEvent` exit code. Callers are still responsible for treating untrusted shell input carefully.

## PTY (interactive terminal)

`sb.Pty()` opens a real pseudo-terminal for interactive programs that need a TTY — shells/REPLs, full-screen tools (`vim`, `top`), or agent-driven terminals. It mirrors the Python/Node `sandbox.pty` surface.

```go
pty := sb.Pty()

// Start an interactive login shell (80x24). Optionally set opts.User / opts.Cwd.
handle, err := pty.Create(ctx, cubesandbox.PtySize{Rows: 24, Cols: 80}, cubesandbox.PtyCreateOptions{})
if err != nil {
	panic(err)
}

// Drive it: send input, resize the window.
_ = handle.SendStdin(ctx, []byte("echo hi && stty size\n"))
_ = handle.Resize(ctx, cubesandbox.PtySize{Rows: 40, Cols: 120})

// Consume output with Wait's callback OR by ranging handle.Output() — not both,
// they share one stream. Wait blocks until the shell exits (or you call
// handle.Kill / handle.Disconnect) and returns the exit code.
code, err := handle.Wait(func(chunk []byte) {
	os.Stdout.Write(chunk)
})
fmt.Println("pty exited with", code, err)
```

Reattach to a still-running PTY from elsewhere with `pty.Connect(ctx, pid, ...)`, or control one by PID without a handle:

```go
handle, _ := pty.Create(ctx, cubesandbox.PtySize{Rows: 24, Cols: 80}, cubesandbox.PtyCreateOptions{})
handle.Disconnect() // detach without killing; the shell keeps running

again, _ := pty.Connect(ctx, handle.PID(), cubesandbox.PtyConnectOptions{})
_ = pty.SendStdin(ctx, again.PID(), []byte("ls\n"))
killed, _ := pty.Kill(ctx, again.PID()) // false if the PID already exited
_ = killed
```

| Method | Description |
|---|---|
| `Pty.Create(ctx, size, opts)` | Start `/bin/bash -i -l` with a PTY; seeds `TERM`/`LANG`/`LC_ALL` (overridable via `opts.Envs`). Streaming `process.Process/Start`. |
| `Pty.Connect(ctx, pid, opts)` | Reattach to a running PTY. Streaming `process.Process/Connect`. |
| `Pty.Kill(ctx, pid)` | `SIGKILL` a PTY; returns `false` (not an error) if the PID was not found. |
| `Pty.SendStdin(ctx, pid, data)` | Write bytes to the PTY master (`SendInput`). |
| `Pty.Resize(ctx, pid, size)` | Resize the window (`Update`). |
| `handle.Output()` | Channel of raw output chunks; closed when the stream ends. |
| `handle.Wait(onData)` | Block until exit, return the exit code; surfaces envd errors (e.g. `signal: killed`). |
| `handle.Disconnect()` | Stop receiving output without killing the PTY. |
| `handle.PID()` / `ExitCode()` / `ErrorMessage()` | PTY process ID; exit code (returns `0, false` until known); and envd end error. |
| `handle.Kill` / `SendStdin` / `Resize` | Per-handle shortcuts that target this PTY's PID. |

Consume output via **either** `Output()` **or** `Wait(onData)`, not both — they share one stream.

`PtyCreateOptions.Timeout` / `PtyConnectOptions.Timeout` (default 60s, `<= 0` uses the default) is both sent to envd as `Connect-Timeout-Ms` and enforced client-side as an idle abort that resets on every received frame; on expiry `Wait` returns an "idle" timeout error.

## Files

```go
// Read & write
content, err := sb.Files().Read(ctx, "/etc/hosts")
png, err := sb.Files().ReadBytes(ctx, "/tmp/chart.png")
err = sb.Files().Write(ctx, "/tmp/hello.txt", []byte("hi"))

// Execute all filesystem operations as a specific sandbox user.
rootFiles := sb.Files().ForUser("root")
content, err = rootFiles.Read(ctx, "/root/hello.txt")
err = rootFiles.Write(ctx, "/root/hello.txt", []byte("hi"))

// Batch write
n, err := sb.Files().WriteFiles(ctx, []cubesandbox.WriteEntry{
	{Path: "/tmp/a.txt", Data: []byte("aaa")},
	{Path: "/tmp/b.txt", Data: []byte("bbb")},
})

// Directory operations
entries, err := sb.Files().List(ctx, "/tmp")
entry, err := sb.Files().Stat(ctx, "/tmp/hello.txt")
exists, err := sb.Files().Exists(ctx, "/tmp/hello.txt")
entry, err = sb.Files().MakeDir(ctx, "/tmp/mydir")
entry, err = sb.Files().Rename(ctx, "/tmp/old.txt", "/tmp/new.txt")
err = sb.Files().Remove(ctx, "/tmp/hello.txt")

// Watch for changes
watcher, err := sb.Files().WatchDir(ctx, "/tmp")
if err != nil {
	panic(err)
}
defer watcher.Close()
for ev := range watcher.Events {
	fmt.Println(ev.Name, ev.Type) // e.g. "hello.txt" "EVENT_TYPE_CREATE"
}
```

| Method | Description |
|---|---|
| `ForUser(user)` | Return an immutable view that runs all filesystem operations as `user` |
| `Read(ctx, path)` | Download file content as text via `GET /files` (e2b `format=text`) |
| `ReadBytes(ctx, path)` | Download raw bytes (e2b `format=bytes`) |
| `ReadStream(ctx, path)` | Stream download; caller must `Close` (e2b `format=stream`) |
| `Write(ctx, path, data)` | Upload via `POST /files` (octet-stream, multipart fallback) |
| `WriteFiles(ctx, entries)` | Batch write, stops on first error, returns count |
| `List(ctx, path)` | List directory entries via `ListDir` RPC |
| `Stat(ctx, path)` | File/directory metadata via `Stat` RPC |
| `Exists(ctx, path)` | `true` if path exists (Stat + 404 check) |
| `MakeDir(ctx, path)` | Create directory via `MakeDir` RPC |
| `Rename(ctx, old, new)` | Move/rename via `Move` RPC |
| `Remove(ctx, path)` | Delete file or directory via `Remove` RPC |
| `WatchDir(ctx, path)` | Stream filesystem events (Connect streaming) |

## Snapshots, Clone, Rollback

```go
snap, err := sb.CreateSnapshot(ctx, "") // POST /sandboxes/:id/snapshots

snaps, nextToken, err := client.ListSnapshots(ctx, cubesandbox.ListSnapshotsOptions{
	SandboxID: sb.SandboxID,
	Limit:     100,
})

deleted, err := client.DeleteSnapshot(ctx, snap.SnapshotID) // DELETE /templates/:id?snapshot_only=true
// `deleted` is false when the snapshot no longer exists; a name may be passed
// instead of an ID. Only snapshots are matched, never templates; several
// same-named snapshots return an error (HTTP 409), so use the `snap-` ID.

_, err = sb.Rollback(ctx, snap.SnapshotID) // POST /sandboxes/:id/rollback

clones, err := sb.Clone(ctx, cubesandbox.CloneOptions{N: 3, Concurrency: 3})

// One server-side call: the backend snapshots sb once and derives N copies.
forks, err := sb.Fork(ctx, cubesandbox.ForkOptions{Count: cubesandbox.IntPtr(3)})
```

`DeleteSnapshot` accepts a snapshot ID or name and returns `(deleted bool, err error)`; `deleted` is `false` when the snapshot does not exist. `Clone` snapshots the sandbox, creates `N` sandboxes from it (capped by `Concurrency`), then deletes the ephemeral snapshot. If any create fails, all successful siblings are killed and the first error is returned. `Rollback` restarts the sandbox process and drops pooled data-plane connections so the next call reconnects.

`Fork` returns exactly `Count` entries — each a `ForkResult` with the derived `Sandbox` or that fork's error — so partial success keeps copies without failing the call (unlike `Clone`). `Count` is 1..100, default 1 (`IntPtr` for a value, nil = default); `Timeout` is a `time.Duration` idle TTL per fork. The temporary snapshot is server-managed.

## Volumes

Persistent volumes survive sandbox lifecycles and are mounted at creation via `VolumeMounts`.

```go
// Driver is optional: when empty the backend uses its first configured
// volume plugin (e2b compatible). Name is optional too — when empty the
// server generates a UUID used as both name and volume ID.
volume, err := client.CreateVolume(ctx, cubesandbox.CreateVolumeOptions{
	Name:   "my-data",
	Driver: "cos",
}) // POST /volumes

volumes, err := client.ListVolumes(ctx)         // GET /volumes (no tokens)
volume, err = client.GetVolume(ctx, "my-data")  // GET /volumes/:id (includes token)

sb, err := client.Create(ctx, cubesandbox.CreateOptions{
	TemplateID: "base",
	VolumeMounts: []cubesandbox.VolumeMount{
		{Name: "my-data", Path: "/workspace"},
		{Name: "shared-cache", Path: "/cache", ReadOnly: true},
	},
})

err = client.DeleteVolume(ctx, "my-data") // DELETE /volumes/:id
```

Deleting a volume does not auto-detach it: destroy the sandboxes using it first, otherwise the server refuses with an error matching `ErrVolumeInUse`. A delete of a missing volume matches `ErrVolumeNotFound`, so idempotent cleanup can ignore that case:

```go
if err := client.DeleteVolume(ctx, "my-data"); err != nil {
	switch {
	case errors.Is(err, cubesandbox.ErrVolumeInUse):
		// still mounted — destroy the sandboxes using it first
	case errors.Is(err, cubesandbox.ErrVolumeNotFound):
		// already gone
	default:
		return err
	}
}
```

## L7 Egress Policy

```go
sb, err := client.Create(ctx, cubesandbox.CreateOptions{
	Network: cubesandbox.NetworkOptions{
		Rules: []cubesandbox.Rule{{
			Name:  "github-api",
			Match: cubesandbox.Match{Host: "api.github.com", Scheme: "https"},
			Action: cubesandbox.Action{
				Allow:  true,
				Audit:  "metadata",
				Inject: []cubesandbox.Inject{{Header: "Authorization", Secret: "token", Format: "Bearer ${SECRET}"}},
			},
		}},
	},
})
```

## Pause And Connect

```go
wait := true
if err := sb.Pause(ctx, cubesandbox.PauseOptions{Wait: &wait}); err != nil {
	panic(err)
}

resumed, err := client.Connect(ctx, sb.SandboxID)
if err != nil {
	panic(err)
}
_ = resumed
```

`Sandbox.Resume` is available for compatibility but deprecated; prefer `Client.Connect`.

## MCP Gateway (Preview)

Set `MCP` to start an E2B-compatible MCP gateway inside the sandbox. It needs a template that ships `mcp-gateway`; when `TemplateID` is empty, the alias `mcp-gateway` is tried first (override with `CUBE_MCP_TEMPLATE_ID` or `Config.MCPTemplateID`), then `CUBE_TEMPLATE_ID` if that template does not exist. An error matching `ErrTemplateNotFound` is returned when neither exists. A failed start returns an error wrapping `ErrMCPGateway` and kills the sandbox. See [MCP Gateway](../../docs/guide/mcp-gateway.md).

```go
sb, err := client.Create(ctx, cubesandbox.CreateOptions{
	MCP: cubesandbox.MCPServers{"duckduckgo": map[string]any{}},
})
if err != nil {
	return err
}
url := sb.GetMCPURL()               // streamable HTTP endpoint
token, err := sb.GetMCPToken(ctx)   // send as "Authorization: Bearer <token>"
```

## Network Policy

```go
denyInternet := false
sb, err := client.Create(ctx, cubesandbox.CreateOptions{
	AllowInternetAccess: &denyInternet,
	Network: cubesandbox.NetworkOptions{
		AllowOut: []string{"151.101.0.0/16"},
		DenyOut:  []string{"0.0.0.0/0"},
	},
})
```

Customize the Host forwarded to user services with a per-sandbox template:

```go
maskRequestHost := "localhost:${PORT}"
sb, err := client.Create(ctx, cubesandbox.CreateOptions{
	Network: cubesandbox.NetworkOptions{
		MaskRequestHost: &maskRequestHost,
	},
})
```

## Host Directory Mount

```go
sb, err := client.Create(ctx, cubesandbox.CreateOptions{
	Metadata: map[string]string{
		"hostdir-mount": `[{"hostPath":"/data/shared","mountPath":"/mnt/data"}]`,
	},
})
```

## Remote Proxy

When `CUBE_PROXY_NODE_IP` is set, data-plane requests connect directly to that IP and port while preserving the virtual sandbox host:

```text
URL:  <CUBE_PROXY_SCHEME>://49983-<sandboxID>.<CUBE_SANDBOX_DOMAIN>/<envd-endpoint>
TCP:  <CUBE_PROXY_NODE_IP>:<CUBE_PROXY_PORT_HTTP>
Host: 49983-<sandboxID>.<CUBE_SANDBOX_DOMAIN>
```

The host prefix is the sandbox's internal service port: `49983` (envd) for
commands/filesystem/files/PTY, and `49999` (Jupyter) for the code interpreter
(`RunCode` / `/execute`).

You can also set it directly:

```go
cfg := cubesandbox.Config{
	APIURL:         "http://10.0.0.1:3000",
	TemplateID:     "tpl-xxxxxxxx",
	ProxyNodeIP:    "10.0.0.1",
	ProxyPortHTTP:  80,
	ProxyScheme:    "http",
	SandboxDomain:  "cube.app",
}
client := cubesandbox.NewClient(cfg)
```

## Integration Tests

Unit tests do not require a live service:

```bash
go test ./...
```

Live integration tests are behind the `integration` build tag. They require `CUBE_API_URL`, auto-discover a READY template from `/templates` when `CUBE_TEMPLATE_ID` is unset, and use `CUBE_PROXY_NODE_IP` for remote data-plane proxying when needed.

```bash
export CUBE_API_URL=http://<your-cubeapi-host>:3000
export CUBE_TEMPLATE_ID=<your-template-id>
export CUBE_PROXY_NODE_IP=<your-cubeproxy-node-ip>
export CUBE_PROXY_PORT_HTTP=80
export CUBE_PROXY_SCHEME=http
export CUBE_SANDBOX_DOMAIN=cube.app
go test -tags=integration -run Integration -count=1 ./...
```

## Direct Volume Verification (no sandbox)

The separate `sdk/go/e2e` module tests the local SDK through `replace => ..`.
It pins official COS/MinIO test dependencies without adding them to the SDK's
`go.mod`; Go 1.22 or newer is required. Run from the repository checkout:

```bash
cd sdk/go
go vet ./...
go test -race -count=1 ./...
cd e2e
go mod download
go vet ./...
go test -race -count=1 -v ./...
```

Default E2E execution uses only loopback fixtures: it verifies backend adapters,
SDK routing, stream checksums and cleanup. Five live cases skip unless explicitly
enabled. Do not use the existing `integration` build tag for these tests: those
older tests create sandboxes and volumes. These new cases require neither a
template nor management credentials and restrict SDK HTTP requests to read-only
content routes for the selected volume. They do not prove server-side behavior.

### Real dataflow: Go SDK -> deployed gateway -> COS/MinIO (no shim)

**Use these cases for real dataflow acceptance on machine 34.** They create no
test server, token, object or metadata. Two independent reads are compared:

1. Official COS/MinIO SDK streams the existing object directly to SHA-256.
2. CubeSandbox Go SDK requests the deployed content gateway and checks both
   buffered and streaming reads against that real source digest and size.

The source object is streamed again after validation to detect concurrent changes.
Real gateway metadata, parent listing, exists/404, invalid token rejection and
stream early-close behavior are also checked. All SDK requests are read-only
volume routes; storage calls are Get only. These cases need only read credentials,
and perform no cleanup because they do not create or modify anything.

The backend environment variables are listed in the table below. Add the following
**per-backend** variables (replace `MINIO` with `COS` for COS):

| Variable | Value |
|---|---|
| `CUBE_VOLUME_MINIO_API_URL` | Actual deployed content gateway origin/base URL, NOT MinIO port 9000 |
| `CUBE_VOLUME_MINIO_CONTENT_TOKEN` | Valid gateway-issued capability for the selected volume, NOT AK/SK or a management key |
| `CUBE_VOLUME_MINIO_TEST_PATH` | Absolute volume path of an existing stable, non-empty file, at most 64 MiB |
| `CUBE_VOLUME_MINIO_TEST_SHA256` | Optional expected 64-character SHA-256; if omitted, the direct backend read is the baseline |

Use secure environment injection or the Bash helper below for credentials, then
run from `sdk/go/e2e`:

```bash
CUBE_VOLUME_DATAFLOW_E2E=1 go test -count=1 -v -run '^TestVolumeDataflowMinIO$' .
CUBE_VOLUME_DATAFLOW_E2E=1 go test -count=1 -v -run '^TestVolumeDataflowCOS$' .
```

For interactive configuration after defining `read_volume_env` below:

```bash
for name in VOLUME_ID ENDPOINT BUCKET REGION ACCESS_KEY_ID SECRET_ACCESS_KEY API_URL CONTENT_TOKEN TEST_PATH; do
    read_volume_env "CUBE_VOLUME_MINIO_$name"
done
CUBE_VOLUME_DATAFLOW_E2E=1 go test -count=1 -v -run '^TestVolumeDataflowMinIO$' .

for name in VOLUME_ID BUCKET REGION SECRET_ID SECRET_KEY API_URL CONTENT_TOKEN TEST_PATH; do
    read_volume_env "CUBE_VOLUME_COS_$name"
done
CUBE_VOLUME_DATAFLOW_E2E=1 go test -count=1 -v -run '^TestVolumeDataflowCOS$' .
```

Use `-count=1` and select one backend when only that backend is configured. No
`CUBE_VOLUME_BACKEND_E2E` or `CUBE_VOLUME_GATEWAY_E2E` switch is needed. Missing
settings fail an enabled case; there is **no fallback to a shim or sandbox**.
The actual object key must be `volumes/<volumeID><TEST_PATH>`, as used by the
COS/S3 FUSE plugins. This mapping does not apply to JuiceFS chunk storage or to a
gateway configured with another prefix. Make sure the file is flushed to storage
and stable before running the tests. Non-loopback gateway credentials require
HTTPS; do not disable TLS verification. A genuine local service may use loopback.

**Current repository limitation:** CubeAPI/CubeMaster provide volume CRUD and
mounting, but no `/volumecontent` service; the COS/S3 plugins return empty tokens.
These tests require an independently deployed real gateway and its scoped token.
If machine 34 only runs the current management API and MinIO, the full dataflow
cannot pass yet. A 404 or empty token is a missing prerequisite, not permission
to fake that layer. Digest equality proves read content agreement; server-side
logs are still needed to prove the gateway used the intended backend, issued no
sandbox/VM/container, and handled authorization/leases correctly.

### Supplemental storage probe via a loopback shim (not real dataflow acceptance)

Run the following in `sdk/go/e2e` on the storage test host (for example, machine
34). Supply an **existing** volume ID; the tests do not create or destroy volumes.
No endpoint, bucket or credential is hardcoded.

| Backend | Required environment variables |
|---|---|
| MinIO | `CUBE_VOLUME_MINIO_VOLUME_ID`, `CUBE_VOLUME_MINIO_ENDPOINT`, `CUBE_VOLUME_MINIO_BUCKET`, `CUBE_VOLUME_MINIO_REGION`, `CUBE_VOLUME_MINIO_ACCESS_KEY_ID`, `CUBE_VOLUME_MINIO_SECRET_ACCESS_KEY` |
| COS | `CUBE_VOLUME_COS_VOLUME_ID`, `CUBE_VOLUME_COS_BUCKET` (`BucketName-APPID`), `CUBE_VOLUME_COS_REGION`, `CUBE_VOLUME_COS_SECRET_ID`, `CUBE_VOLUME_COS_SECRET_KEY` |

Optional temporary-credential tokens are `CUBE_VOLUME_MINIO_SESSION_TOKEN` and
`CUBE_VOLUME_COS_SESSION_TOKEN`. Use a secure environment injection mechanism or
this interactive **Bash** helper; secrets are not placed in shell history:

```bash
read_volume_env() {
    case "$1" in
        *SECRET*|*TOKEN*|*ACCESS_KEY_ID)
            read -r -s -p "$1: " "$1"; printf '\n' ;;
        *) read -r -p "$1: " "$1" ;;
    esac
    export "$1"
}

for name in VOLUME_ID ENDPOINT BUCKET REGION ACCESS_KEY_ID SECRET_ACCESS_KEY; do
    read_volume_env "CUBE_VOLUME_MINIO_$name"
done
CUBE_VOLUME_BACKEND_E2E=1 go test -count=1 -v -run '^TestVolumeStorageMinIO$' .

for name in VOLUME_ID BUCKET REGION SECRET_ID SECRET_KEY; do
    read_volume_env "CUBE_VOLUME_COS_$name"
done
CUBE_VOLUME_BACKEND_E2E=1 go test -count=1 -v -run '^TestVolumeStorageCOS$' .
```

The MinIO endpoint must be an HTTP(S) origin without credentials, path, query or
fragment. If MinIO runs locally on machine 34, enter `http://127.0.0.1:9000`.
For a non-loopback endpoint use HTTPS. Only for an explicitly trusted test
network, set `CUBE_VOLUME_MINIO_ALLOW_HTTP=1` to allow remote HTTP; it does not
provide transport confidentiality. COS always uses HTTPS with certificate
verification. The tests do not auto-load proxy or management credentials.

Both cases write one random object under
`volumes/<volumeID>/sdk-direct-probe/go-<random>.bin`, verify bytes/stream
SHA-256, metadata/list/exists, missing paths, invalid tokens and early stream
closure, then delete and HEAD-check only that object. Upload failures also
trigger a cleanup attempt with a fresh timeout. Existing directory markers,
`.keep`, volumes and buckets are never deleted. Credentials need object Put,
Get/Head and Delete permissions. Versioned buckets may retain object versions;
process termination may leave the probe key, which must be cleaned up manually.
Logs report SHA-256 and cleanup keys, never credentials.

**Boundary:** only file bytes come from the real storage backend. Metadata,
directory listings and tokens are generated by the local shim. Passing these
cases is not production gateway, metadata, capability, isolation or lease
validation. Missing configuration fails an enabled case rather than skipping it.
Use `-count=1` to prevent cached Go test results.

### Deployed content gateway: strictly read-only

This independent test requires an already deployed compatible content gateway
and its valid volume-scoped token. MinIO port 9000 is **not** that gateway. A
management CubeAPI without `/volumecontent` cannot pass it; do not substitute an
AK/SK, management API key or empty plugin token.

```bash
for name in API_URL ID CONTENT_TOKEN TEST_PATH; do
    read_volume_env "CUBE_VOLUME_$name"
done
# Optional: expected 64-character SHA-256 in CUBE_VOLUME_TEST_SHA256.
CUBE_VOLUME_GATEWAY_E2E=1 go test -count=1 -v -run '^TestVolumeGatewayReadOnly$' .
```

`CUBE_VOLUME_TEST_PATH` must be an absolute path to a stable existing small file
inside the volume. The baseline read is buffered, so choose a file that fits the
runner's memory. Non-loopback gateways require HTTPS. This test checks reads,
metadata, parent listing, exists/404, early closure and invalid-token rejection;
it writes no objects and creates/deletes no volumes or sandboxes. Server-side
no-sandbox evidence, cross-tenant/volume isolation, expired capabilities and
lease/delete coordination still require separate server observations and tests.
