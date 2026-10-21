# CubeSandbox OTel Collector (systemd)

## Scope

The systemd Collector runs on each CubeSandbox compute node and collects only:

- sandbox CPU and memory metrics from the local Cubelet resource endpoint;
- the node sandbox-count metric when the installed Cubelet exposes it;
- sandbox `stdout` and `stderr` logs from `/data/cubelet/log/<sandbox-id>/`.

It does not collect host metrics, control-plane logs, template logs, or separate
OCI Exec logs. Cubelet currently does not expose sandbox network or disk
metrics.

## Data flow

```text
Cubelet /v1/metrics/resource -> Prometheus receiver -> resource/batch
                            -> OTLP/HTTP or Prometheus Remote Write

/data/cubelet/log/*/{stdout,stderr} -> filelog -> resource/transform/batch
                                    -> Loki OTLP/HTTP or Elasticsearch
```

Metrics and logs are independent pipelines. Either pipeline may be omitted when
its destination is not configured. The default one-click topology sends both
to the backend node and selects Loki for logs; Elasticsearch remains optional.

## Generated configuration

`install.sh` validates environment variables and generates
`/etc/cube-sandbox/otelcol.yaml`. To inspect the result without installing:

```bash
CUBE_OBSERVABILITY_CLUSTER_ID=cube-prod \
CUBE_METRICS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:9092/api/v1/otlp \
CUBE_LOGS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:3100/otlp \
CUBE_OTEL_CONFIG_OUTPUT=/tmp/otelcol.yaml \
./install.sh render

otelcol-contrib validate --config=file:/tmp/otelcol.yaml
```

The generator derives enabled pipelines directly from the configured
destinations, so the generated YAML should not be edited in place.

## State and restart behavior

- Binary: `/usr/local/bin/otelcol-contrib`
- Configuration: `/etc/cube-sandbox/otelcol.yaml`
- Unit: `/etc/systemd/system/cube-sandbox-otelcol.service`
- File offsets and OTLP queues:
  `/var/lib/cube-sandbox/otelcol/storage`
- Prometheus Remote Write WAL:
  `/var/lib/cube-sandbox/otelcol/prom_rw`

`filelog.start_at` controls only the first discovery of a file. Later reads
resume from the persisted `file_storage` offset. Cubelet deletes the sandbox
log directory when the sandbox is destroyed, so the Collector must tail files
while the sandbox exists.

The generated systemd unit runs as root because Cubelet log directories may be
root-only. It enables `NoNewPrivileges`, a read-only system view, a private
temporary directory, and a restricted writable-path allowlist.

## Backends and authentication

- Metrics: OTLP/HTTP or Prometheus Remote Write
- Logs: Loki OTLP/HTTP (recommended) or Elasticsearch
- Metrics auth: `CUBE_METRICS_AUTHORIZATION` and
  `CUBE_METRICS_TENANT_ID`
- Loki auth: `CUBE_LOGS_AUTHORIZATION` and `CUBE_LOGS_TENANT_ID`
- Elasticsearch auth: Basic Auth or API key; HTTPS verification is enabled by
  default

See the component [README](../../../README.md) for all variables, defaults,
retention, and deployment commands.
