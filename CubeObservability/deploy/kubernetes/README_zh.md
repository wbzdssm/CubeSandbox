# Kubernetes 沙箱 OTel Collector 方案

[English](./README.md)

`install.sh` 用于不通过 CubeSandbox Helm Chart、但需要在现有 Kubernetes / TKE
集群独立部署 Collector 的场景。Helm 用户直接设置
`observability.enabled=true`。

## 1. 采集边界

DaemonSet `cube-node-otelcol` 只运行在
`cube.tencent.com/cube-node=true` 的沙箱计算节点，采集：

- Cubelet `127.0.0.1:9998/v1/metrics/resource` 的 `cubesandbox_*` 指标。
- 节点 `/data/cubelet/log/<sandbox-id>/{stdout,stderr}` 中的沙箱标准输出/标准错误日志。

它不挂载 `/proc`、`/sys` 或 `/data/log`，不采集节点资源、模板日志、平台组件日志
和 systemd 日志。OCI Exec 独立日志本期不支持。

## 2. 前提

- 目标 namespace 已存在，默认 `cube-system`。
- Cubelet DaemonSet 使用 `hostNetwork: true`，使同节点 Collector 可以抓取
  `127.0.0.1:9998`。
- 计算节点带标签 `cube.tencent.com/cube-node=true`。
- 节点允许 Collector 以 root 只读挂载 `/data/cubelet/log`（启用日志时）。
- 计算节点能拉取 `otel/opentelemetry-collector-contrib:0.162.0`，或通过
  `CUBE_OTELCOL_IMAGE` 指定内网镜像。

脚本会检查已有 `cube-node` DaemonSet 的 `hostNetwork`；若不是 `true`，安装会停止。

## 3. 数据流

```text
Cubelet /v1/metrics/resource
  -> prometheus receiver
  -> OTLP/HTTP 或 Prometheus Remote Write

/data/cubelet/log/*/{stdout,stderr}
  -> filelog/sandbox receiver
  -> sandbox_id / stdout|stderr 字段解析
  -> Elasticsearch 或 OTLP/HTTP
```

Collector 通过 Downward API 将 `spec.nodeName` 写入 `node` / `host_name`。
ConfigMap 和 Secret checksum 变化时，DaemonSet 自动滚动更新。

## 4. 安装

### 4.1 Prometheus OTLP + Loki（推荐）

```bash
env \
  CUBE_METRICS_NAMESPACE=cube-system \
  CUBE_OBSERVABILITY_CLUSTER_ID=prod-k8s \
  CUBE_METRICS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:9092/api/v1/otlp \
  CUBE_LOGS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:3100/otlp \
  ./CubeObservability/deploy/kubernetes/install.sh install
```

### 4.2 Prometheus Remote Write + Loki

```bash
env \
  CUBE_METRICS_NAMESPACE=cube-system \
  CUBE_METRICS_REMOTE_WRITE_URL=https://mimir.example/api/v1/push \
  CUBE_METRICS_AUTHORIZATION='Bearer <token>' \
  CUBE_METRICS_TENANT_ID=tenant-a \
  CUBE_LOGS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:3100/otlp \
  ./CubeObservability/deploy/kubernetes/install.sh install
```

### 4.3 只渲染 Collector 配置

```bash
env \
  CUBE_METRICS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:9092/api/v1/otlp \
  CUBE_LOGS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:3100/otlp \
  CUBE_OTEL_CONFIG_OUTPUT=/tmp/cubesandbox-k8s-otelcol.yaml \
  ./CubeObservability/deploy/kubernetes/install.sh render
```

### 4.4 改用 Elasticsearch

将 `CUBE_LOGS_OTLP_HTTP_ENDPOINT` 替换为
`CUBE_LOGS_ES_ENDPOINT=http://10.0.0.10:9200`，并可通过
`CUBE_LOGS_ES_INDEX` 设置索引。两个日志 endpoint 不能同时配置。

脚本创建：

- Secret `cube-node-otelcol`：后端地址、Authorization、tenant。
- ConfigMap `cube-node-otelcol`：Collector YAML。
- DaemonSet `cube-node-otelcol`：只调度到沙箱计算节点。

卸载：

```bash
./CubeObservability/deploy/kubernetes/install.sh uninstall
```

## 5. 主要配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CUBE_METRICS_NAMESPACE` | `cube-system` | Kubernetes namespace |
| `CUBE_OTELCOL_IMAGE` | `otel/opentelemetry-collector-contrib:0.162.0` | Collector 镜像 |
| `CUBE_OBSERVABILITY_CLUSTER_ID` | `cubesandbox` | 集群标签 |
| `CUBE_METRICS_OTLP_HTTP_ENDPOINT` | 空 | 指标 OTLP/HTTP 基础地址 |
| `CUBE_METRICS_REMOTE_WRITE_URL` | 空 | Remote Write 地址，与指标 OTLP 二选一 |
| `CUBE_METRICS_AUTHORIZATION` | 空 | 完整 Authorization 值 |
| `CUBE_METRICS_REMOTE_WRITE_TOKEN` | 空 | Bearer Token 简写，与上一项二选一 |
| `CUBE_METRICS_TENANT_ID` | 空 | `X-Scope-OrgID` |
| `CUBE_LOGS_ES_ENDPOINT` | 空 | Elasticsearch 地址 |
| `CUBE_LOGS_ES_INDEX` | `cubesandbox-logs` | 日志索引 |
| `CUBE_LOGS_OTLP_HTTP_ENDPOINT` | 空 | 日志 OTLP/HTTP 地址，与 ES 二选一 |

## 6. Pod 与挂载

- `hostNetwork: true`，抓取同节点 Cubelet 回环地址。
- nodeSelector：`cube.tencent.com/cube-node=true`。
- toleration：`cube.tencent.com/compute=true:NoSchedule`。
- Collector 以 root 运行，但保持只读 rootfs、丢弃全部 capabilities，并禁止提权；
  日志启用时只读挂载 `/data/cubelet/log`。
- `/var/lib/otelcol` 默认映射到节点
  `/var/lib/cube-sandbox/otelcol-k8s`（可通过
  `CUBE_OTEL_K8S_STATE_HOST_PATH` 修改），保存 filelog 游标、OTLP 队列或 Remote
  Write WAL。同一节点上的 Pod 被替换后仍可续读。

## 7. Pipeline

指标：

```yaml
service:
  pipelines:
    metrics:
      receivers: [prometheus]
      processors: [memory_limiter, resource/metrics, batch]
      exporters: [otlp_http/metrics]
```

日志：

```yaml
service:
  pipelines:
    logs/sandbox:
      receivers: [filelog/sandbox]
      processors:
        [memory_limiter, resource/logs_common, resource/sandbox,
         attributes/logs_common, attributes/sandbox, batch]
      exporters: [otlp_http/logs]
```

完整字段说明和配置示例见
[`../../README_zh.md`](../../README_zh.md#4-otel-collector-配置)。

## 8. 检查

```bash
kubectl -n cube-system get daemonset cube-node-otelcol
kubectl -n cube-system get pods -l app.kubernetes.io/name=cube-node-otelcol -o wide
kubectl -n cube-system logs daemonset/cube-node-otelcol --tail=100
```

Collector Ready 只表示健康端口可访问；还应在 Prometheus 中检查
`cubesandbox_node_sandbox_count`（包含本次节点计数改动的后续发布版）或
`cubesandbox_host_sandbox_memory_current_bytes`（`v0.7.0 ~ v0.7.2` 有活跃沙箱时），
并在默认 Loki 中检查 `{service_name="cubesandbox-sandbox"}`；选择 Elasticsearch
时检查 `service_name.keyword:cubesandbox-sandbox`。
