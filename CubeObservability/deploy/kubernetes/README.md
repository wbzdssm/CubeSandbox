# CubeSandbox OTel Collector (Kubernetes)

[简体中文](./README_zh.md)

## Scope and topology

The standalone Kubernetes installer deploys one OTel Collector Pod per
CubeSandbox node as a DaemonSet. It collects only:

- sandbox CPU and memory metrics from that node's Cubelet;
- the node sandbox-count metric when supported by the Cubelet version;
- sandbox `stdout` and `stderr` logs.

It does not collect host metrics, control-plane logs, template logs, or separate
OCI Exec logs. Cubelet currently does not expose sandbox network or disk
metrics.

The DaemonSet uses `hostNetwork: true` and scrapes
`127.0.0.1:9998/v1/metrics/resource`. The installer checks that Cubelet Pods in
the target namespace also use host networking, because a loopback target cannot
reach a Cubelet running only on the Pod network.

## Data flow

```text
same-node Cubelet -> Prometheus receiver -> OTLP/HTTP or Remote Write

same-node /data/cubelet/log/*/{stdout,stderr}
  -> filelog receiver -> Loki OTLP/HTTP or Elasticsearch
```

The Kubernetes Node name becomes the `node` label. Metrics and logs are
independent pipelines, and either may be omitted when no destination is
configured.

## Configuration and Secrets

The installer renders the Collector configuration locally, validates all
inputs, stores the YAML in a ConfigMap, and stores configured authorization
values in a Kubernetes Secret. The generated Pod references secret values
through environment variables; credentials are not written into the ConfigMap.

Inspect the generated configuration without applying resources:

```bash
CUBE_OBSERVABILITY_CLUSTER_ID=cube-prod \
CUBE_METRICS_OTLP_HTTP_ENDPOINT=http://prometheus.example/api/v1/otlp \
CUBE_LOGS_OTLP_HTTP_ENDPOINT=http://loki.example/otlp \
CUBE_OTEL_CONFIG_OUTPUT=/tmp/otelcol-k8s.yaml \
./install.sh render

otelcol-contrib validate --config=file:/tmp/otelcol-k8s.yaml
```

Use `CUBE_METRICS_AUTHORIZATION`, `CUBE_METRICS_TENANT_ID`,
`CUBE_LOGS_AUTHORIZATION`, and `CUBE_LOGS_TENANT_ID` for authenticated OTLP or
Remote Write destinations. Elasticsearch also supports Basic Auth or API key.

## State and security

`/var/lib/otelcol` in the container maps to the per-node host path
`/var/lib/cube-sandbox/otelcol-k8s` by default. Override the host path with
`CUBE_OTEL_K8S_STATE_HOST_PATH`. It preserves file offsets, disk-backed OTLP
queues, and the Remote Write WAL when the Pod is replaced on the same node.

The Collector runs as root to access this node state and root-owned Cubelet log
files. The container has a read-only root filesystem, drops every Linux
capability, and disables privilege escalation. The sandbox log path is mounted
read-only.

## Operations

```bash
# Apply or update the DaemonSet
./install.sh

# Remove Kubernetes resources (node state is retained)
./install.sh uninstall
```

Configuration and Secret hashes are Pod-template annotations, so a meaningful
configuration or credential change triggers a rolling DaemonSet update.

See the component [observability guide](../../README.md) for backend choices
and the Helm-integrated deployment path.
