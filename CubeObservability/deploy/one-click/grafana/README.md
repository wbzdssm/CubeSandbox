# CubeSandbox 沙箱 Grafana 看板

本目录提供 sandbox-only 看板，指标使用 Prometheus，日志可选择 Loki 或
Elasticsearch：

1. `dashboards/cubesandbox-host-sandbox.json`
   - UID：`cubesandbox-host-sandbox`
   - 数据源：Prometheus
   - 展示 Cubelet 暴露的沙箱 CPU、内存、限额、节流和实例数量。
2. `dashboards/cubesandbox-logs.json`
   - UID：`cubesandbox-logs`
   - 数据源：Loki（默认和推荐）
   - 使用 LogQL 检索沙箱标准输出/标准错误日志。
3. `dashboards/cubesandbox-logs-elasticsearch.json`
   - UID：`cubesandbox-logs`
   - 数据源：Elasticsearch（可选）
   - 使用 Lucene 检索同一批沙箱标准输出/标准错误日志。

安装器根据 `CUBE_LOGS_BACKEND=loki|elasticsearch` 只加载一份日志看板，因此两个
日志 JSON 共用 UID 不会冲突。看板不展示节点资源、模板日志、平台组件日志或
systemd 日志，也不包含 OCI Exec 独立日志。

## 数据链路

```text
Cubelet 127.0.0.1:9998/v1/metrics/resource
  -> OTel Collector prometheus receiver
  -> Prometheus
  -> CubeSandbox 沙箱资源监控

/data/cubelet/log/<sandbox-id>/{stdout,stderr}
  -> OTel Collector filelog/sandbox receiver
  -> Loki native OTLP (default) or Elasticsearch Bulk API
  -> CubeSandbox 沙箱日志
```

## 指标与标签

看板使用以下 Cubelet 指标族：

```text
cubesandbox_node_sandbox_count
cubesandbox_node_sandbox_available_count
cubesandbox_host_sandbox_cpu_*
cubesandbox_host_sandbox_memory_*
```

节点计数指标属于本次开发改动。正式发布的 `v0.7.0 ~ v0.7.2` 没有该指标，
看板会在有活跃沙箱资源序列时自动回退到 `count(...)`；空节点可能显示 No data。

Collector / scraper 需要保留下列标签：

```text
cluster="<cluster ID>"
node="<node IP or Kubernetes node name>"
job="cubesandbox-resource"
sandbox_id="<sandbox ID>"
```

## 日志字段与 Loki 标签策略

两个后端都保留以下字段语义：

- `service_name=cubesandbox-sandbox`
- `cubesandbox_log_source=sandbox-init`
- `cubesandbox_cluster_id`
- `host_name`
- `cubesandbox_sandbox_id`
- `log_iostream=stdout|stderr`
- `log_file_path`
- 日志正文和时间戳

Loki 将 OTel 属性名中的 `.` 转成 `_`。为避免高基数，只将 `service_name`、
`cubesandbox_cluster_id`、`host_name` 建为索引 label；Sandbox ID、流类型等作为
structured metadata 保存。Loki 看板中的 Sandbox ID 使用文本正则输入，而不是
高基数 label 变量。

Elasticsearch 使用 raw mapping，正文和时间字段分别为 `Body`、`@timestamp`。

## 手工导入

1. 配置 Prometheus 数据源，URL 指向 `http://<prometheus>:9092`。
2. 选择日志后端：
   - Loki：配置 `loki` 类型数据源，URL 指向 `http://<loki>:3100`，导入
     `cubesandbox-logs.json`。
   - Elasticsearch：URL 指向 `http://<elasticsearch>:9200`，Index 使用
     `cubesandbox-logs*`，Time field 使用 `@timestamp`，Message field 使用
     `Body`，导入 `cubesandbox-logs-elasticsearch.json`。
3. 导入 `cubesandbox-host-sandbox.json`。

一键安装会按所选后端自动创建数据源并加载正确的日志看板。

## No data 排查

```bash
# Cubelet 是否暴露沙箱指标
curl -fsS http://127.0.0.1:9998/v1/metrics/resource | grep '^cubesandbox_'

# Collector 是否健康
curl -fsS http://127.0.0.1:13133/

# 默认 Loki 是否健康
LOKI_HOST=10.0.0.10
curl -fsS "http://${LOKI_HOST}:3100/ready"

# Elasticsearch 是否已有沙箱日志（选择 ES 时）
ELASTICSEARCH_HOST=10.0.0.10
curl -fsS "http://${ELASTICSEARCH_HOST}:9200/cubesandbox-logs*/_count"
```

还应确认指标时间序列包含 `cluster`、`node` 和 `sandbox_id` 标签；日志数据源类型与
导入的日志看板匹配。Loki 可先在 Explore 中查询
`{service_name="cubesandbox-sandbox"}`；Elasticsearch 可检查
`service_name.keyword=cubesandbox-sandbox`。
