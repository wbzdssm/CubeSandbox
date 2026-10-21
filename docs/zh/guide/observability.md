# CubeObservability：CubeSandbox 沙箱可观测性部署

[English](../../guide/observability.md) | 简体中文

CubeSandbox 的标准部署入口默认不安装可观测性组件。需要启用时，使用
one-click / Terraform 的环境变量或 Helm Value；不提供额外的安装命令参数。

采集范围限定为沙箱：

- 指标：仅抓取 Cubelet `127.0.0.1:9998/v1/metrics/resource` 暴露的
  `cubesandbox_*` 沙箱资源指标。
- 日志：仅采集沙箱标准输出（stdout）与标准错误（stderr）日志，
  落盘路径为 `/data/cubelet/log/<sandbox-id>/{stdout,stderr}`。

Prometheus、Grafana 和日志后端一键部署，用于接收和展示上述沙箱可观测数据。
日志后端同时支持 Loki 与 Elasticsearch，默认选择 Loki。

目录按组件资产和部署职责划分：

- Grafana 镜像构建与 Dashboard 源文件：`CubeObservability/`；
- one-click、systemd 与腾讯云 Terraform：
  `deploy/one-click/observability/`；
- 独立 Kubernetes Collector：`deploy/kubernetes/observability/`；
- Helm 内置 Collector 与后端：`deploy/kubernetes/chart/`。

## 1. 能力、版本与安装前自检

### 1.1 Cubelet 能力基线

本方案使用当前 Cubelet 提供的以下数据：

- `/v1/metrics/resource` 暴露的沙箱 CPU/Memory 指标。逐沙箱序列带
  `sandbox_id`，`guest_workload` 序列还带 `container_id`。
- `cubesandbox_node_sandbox_count` 与
  `cubesandbox_node_sandbox_available_count` 节点级 Gauge；无活跃沙箱时返回 `0`。
- `/data/cubelet/log/<sandbox-id>/{stdout,stderr}` 中的沙箱标准输出/标准错误日志。

完整指标名、类型、单位和标签以
[CubeSandbox GitHub 沙箱资源指标文档](https://github.com/tencentcloud/CubeSandbox/blob/master/docs/zh/guide/resource-metrics.md)
为准；其中 `host_sandbox` 序列包含 `sandbox_id`，`guest_workload` 序列包含
`sandbox_id` 与 `container_id`。需要核对实现时可查看
[Cubelet Prometheus 源码](https://github.com/tencentcloud/CubeSandbox/blob/master/Cubelet/plugins/cube/internals/resourcemetrics/prometheus.go)。
节点计数 Gauge 不带动态标签，也不包含 Sandbox ID 列表。

#### 安装前自检

Cubelet 安装完成后执行以下检查。全新 one-click 环境先按
[2.1](#21-one-click) 完成第一台节点安装：

```bash
# Cubelet version and service
/usr/local/services/cubetoolbox/Cubelet/bin/cubecli version
sudo systemctl is-active cube-sandbox-cubelet.service

# Sandbox resource endpoint and per-sandbox ID label
curl -fsS http://127.0.0.1:9998/v1/metrics/resource | \
  grep -E '^cubesandbox_(host_sandbox|guest_workload|node_sandbox_)'
curl -fsS http://127.0.0.1:9998/v1/metrics/resource | \
  grep 'sandbox_id='

# Standard sandbox stdout/stderr log path while a sandbox is active
SANDBOX_ID=019abc-example
ls -l "/data/cubelet/log/${SANDBOX_ID}"/{stdout,stderr}
```

没有活跃沙箱时，`sandbox_id` 检查可以没有输出，两个
`cubesandbox_node_sandbox_*` Gauge 应返回 `0`。插件未启用或首次采样尚未完成时，
指标也可能为空，应结合插件配置和服务状态判断。

### 1.2 安装器版本与兼容基线

Loki 与 Elasticsearch 是二选一的沙箱日志后端：默认安装 Loki；仅在显式选择
`CUBE_LOGS_BACKEND=elasticsearch`（Helm 为
`observability.logs.backend=elasticsearch`）时选择 Elasticsearch。同一部署仅启用
其中一个日志后端，也不会双写。

| 组件 | 安装器固定并已验证版本 | 外部/自备组件兼容基线 | 默认端口 | 角色与兼容说明 |
| --- | --- | --- | --- | --- |
| OTel Collector Contrib | `0.162.0` | 建议保持 `0.162.x` | `127.0.0.1:13133`（健康检查） | 节点采集器。当前 YAML 使用该版本的组件名、OTLP/HTTP 与 Elasticsearch exporter 配置；不能笼统承诺任意更高版本兼容，升级后必须重新执行 `otelcol validate`。 |
| Prometheus | one-click `3.13.4`；Helm `v3.2.1` | 支持 OTLP receiver 与 Remote Write receiver 的 Prometheus `3.x` | `9092` | 沙箱指标后端。必须支持 `--web.enable-otlp-receiver`、`--web.enable-remote-write-receiver` 和本文的 `otlp` 配置。 |
| Loki（日志后端二选一，默认） | `3.7.9` | Loki `3.x`，且启用原生 OTLP 与 structured metadata | `3100` | 默认沙箱日志后端；Collector 写入 `/otlp`，Grafana 使用 LogQL 查询。 |
| Elasticsearch（日志后端二选一，可选） | `8.17.3` | Elasticsearch `8.x` | `9200` | 显式选择后替代 Loki，使用 `raw` mapping 和 Bulk API；其他大版本需要单独验证。 |
| Grafana OSS | `10.4.18` | `>= 10.4`，并兼容 dashboard schema `39` | `3001` | 指标与日志展示。更高版本通常可导入，但升级前仍需验证所选日志数据源和两张看板。 |

固定版本用于一键安装的可重复性；“兼容基线”只适用于自行维护的外部组件，不等于对
所有未来版本的无条件兼容承诺。

上游配置参考：
[Prometheus receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/prometheusreceiver)、
[filelog receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/pkg/stanza/operator/input/file)、
[Elasticsearch exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/exporter/elasticsearchexporter)、
[Prometheus OTLP 接入](https://prometheus.io/docs/guides/opentelemetry/)和
[Loki OTLP 接入](https://grafana.com/docs/loki/latest/send-data/otel/)。

CVM/systemd 内置安装器要求 64 位 Linux、systemd、`curl`、`tar` 和 `python3`；
`python3` 用于安全解包并校验 Loki 发布包内容。Kubernetes / Helm 形态不需要节点
预装这些二进制。

### 1.3 Loki 还是 Elasticsearch

- **Loki（默认）**：资源占用较少，适合快速落地及以标签筛选为主的日志查询。
- **Elasticsearch（可选）**：适合已有 ES 运维体系或需要全文检索的团队。
- 两者二选一；生产环境优先复用团队已有或托管的日志平台。

新装时在 `.env` 中选择：

```dotenv
CUBE_LOGS_BACKEND=loki
# Or:
# CUBE_LOGS_BACKEND=elasticsearch
```

深入阅读：[Loki 官方文档](https://grafana.com/docs/loki/latest/)、
[Loki OTLP ingestion](https://grafana.com/docs/loki/latest/send-data/otel/)、
[Elasticsearch 日志文档](https://www.elastic.co/docs/solutions/observability/logs)和
[Elasticsearch Query DSL](https://www.elastic.co/docs/explore-analyze/query-filter/languages/querydsl)。

## 2. 随标准部署启用

| 部署形态 | 入口 | 启用方式 |
| --- | --- | --- |
| one-click 第一台节点（默认 all-in-one） | `deploy/one-click/install.sh` | 设置 `ONE_CLICK_ENABLE_OBSERVABILITY=1` 后正常执行安装 |
| one-click 计算节点 | `deploy/one-click/install-compute.sh` | 设置 `ONE_CLICK_ENABLE_OBSERVABILITY=1` 和第一台节点地址；仅安装节点 Collector |
| one-click 存量部署 | 原 `install.sh` / `install-compute.sh` | 修改原 `.env` 后以 `--mode=upgrade --yes` 执行，详见 [2.1 C](#c-在-one-click-存量部署中启用) |
| Kubernetes Helm | `deploy/kubernetes/chart` | 安装时传 `--set observability.enabled=true` |
| Terraform 腾讯云 | `deploy/one-click/terraform/tencentcloud/create.sh` | 在 `.env` 中设置 `TENCENTCLOUD_ENABLE_OBSERVABILITY='true'` 后正常执行 `create.sh` |

### 2.1 one-click

one-click 继续使用原有 `.env` 和安装入口，默认不开启可观测性。第一台节点默认是
控制面与计算节点合一的 all-in-one；后续计算节点把指标和日志发送到第一台节点，
不需要另建“监控控制面”。

#### A. 安装第一台 all-in-one 节点

1. 进入源码的 `deploy/one-click` 目录；如果使用 release 压缩包，则进入解压后的
   one-click 根目录。仅在首次安装时复制模板：

```bash
# Source checkout:
cd deploy/one-click
# Extracted release:
# cd /path/to/cube-sandbox-one-click
[[ -f .env ]] || cp env.example .env
chmod 600 .env
```

2. 确认一张后续计算节点可访问的内网 IP，例如 `10.0.0.10`，然后在 `.env` 中设置：

```bash
# .env
CUBE_SANDBOX_NODE_IP=10.0.0.10
ONE_CLICK_ENABLE_OBSERVABILITY=1
CUBE_OBSERVABILITY_CLUSTER_ID=cube-prod
CUBE_LOGS_BACKEND=loki
CUBE_GRAFANA_ADMIN_PASSWORD='<strong-password>'
```

   选择 Elasticsearch 时只需把 `CUBE_LOGS_BACKEND` 改为 `elasticsearch`。如果
   `CUBE_SANDBOX_NODE_IP` 留空，安装器会自动探测，但仍应在安装后确认结果。

3. 执行原有安装命令：

```bash
sudo ./install.sh
```

4. 记录安装器最终使用的第一台节点 IP；后续计算节点填写的必须是这个可达内网 IP：

```bash
grep '^CUBE_SANDBOX_NODE_IP=' \
  /usr/local/services/cubetoolbox/.one-click.env
```

5. 在安全组或防火墙中按最小范围放通：

   - 计算节点到第一台节点：TCP `9092`，以及 Loki 的 `3100` 或 Elasticsearch 的
     `9200`；
   - 管理员到第一台节点：Grafana `3001`；
   - 不要把 `9092/3100/9200` 直接暴露到公网。

6. 在第一台节点验收：

```bash
sudo /usr/local/services/cubetoolbox/observability/install.sh status
sudo systemctl --no-pager --full status cube-sandbox-otelcol.service
curl -fsS http://127.0.0.1:9092/-/ready
curl -fsS http://127.0.0.1:3100/ready
# Elasticsearch users replace the previous line with:
# curl -fsS http://127.0.0.1:9200/
```

   Grafana 地址是 `http://10.0.0.10:3001`。首次进入后确认
   **CubeSandbox sandbox resources** 和 **CubeSandbox sandbox logs** 看板能够查询
   当前节点；没有活跃沙箱时，逐沙箱序列和日志可以为空。

#### B. 安装后续计算节点

1. 在每台计算节点准备同版本 one-click 包和 `.env`：

```bash
[[ -f .env ]] || cp env.example .env
chmod 600 .env
```

2. 在 `.env` 中填写第一台节点 IP，并保持集群 ID 和日志后端与第一台节点一致：

```bash
# .env
ONE_CLICK_CONTROL_PLANE_IP=10.0.0.10
ONE_CLICK_ENABLE_OBSERVABILITY=1
CUBE_OBSERVABILITY_CLUSTER_ID=cube-prod
CUBE_LOGS_BACKEND=loki
```

   `ONE_CLICK_CONTROL_PLANE_IP` 就是第一台 CubeSandbox 节点的内网 IP，不是单独
   新建的监控节点。`install-compute.sh` 会自动设置
   `ONE_CLICK_DEPLOY_ROLE=compute`，无需在 `.env` 中重复配置。没有显式外部 endpoint
   时，安装器会从该 IP 推导 Prometheus 和所选日志后端地址。

3. 安装并检查本机 Collector：

```bash
sudo ./install-compute.sh
sudo systemctl --no-pager --full status cube-sandbox-otelcol.service
sudo journalctl -u cube-sandbox-otelcol.service -n 100 --no-pager
```

4. 创建一个测试沙箱后，在第一台节点的 Grafana 中按 `node_id`、`sandbox_id` 查询；
   如果没有数据，先检查本机 `127.0.0.1:9998`、日志目录和到第一台节点所需端口。

#### C. 在 one-click 存量部署中启用

使用包含可观测性部署文件的 one-click 发布包，并继续维护原安装目录中的 `.env`；
不要用 `env.example` 覆盖现有配置，也不要直接编辑安装目录下生成的
`.one-click.env`。

1. 在第一台节点的 `.env` 中增加或更新：

```dotenv
ONE_CLICK_ENABLE_OBSERVABILITY=1
CUBE_OBSERVABILITY_CLUSTER_ID=cube-prod
CUBE_LOGS_BACKEND=loki
CUBE_GRAFANA_ADMIN_PASSWORD='<strong-password>'
```

2. 使用原 one-click 入口执行升级：

```bash
sudo ./install.sh --mode=upgrade --yes
```

3. 第一台节点验收通过后，在每台计算节点的 `.env` 中增加或更新：

```dotenv
ONE_CLICK_CONTROL_PLANE_IP=10.0.0.10
ONE_CLICK_ENABLE_OBSERVABILITY=1
CUBE_OBSERVABILITY_CLUSTER_ID=cube-prod
CUBE_LOGS_BACKEND=loki
```

```bash
sudo ./install-compute.sh --mode=upgrade --yes
```

将 `10.0.0.10` 替换为第一台节点的可达内网 IP，并确保所有节点使用相同的集群 ID
和日志后端。上述命令会按原 `.env` 协调 one-click 主组件和可观测性组件，执行前应按
现有运维流程备份配置。

#### D. 在线安装

在线安装与内置 MinIO 使用相同的环境变量透传方式：

```bash
# 第一台 all-in-one 节点
curl -fsSL https://raw.githubusercontent.com/tencentcloud/CubeSandbox/master/deploy/one-click/online-install.sh \
  | sudo env ONE_CLICK_ENABLE_OBSERVABILITY=1 \
      CUBE_OBSERVABILITY_CLUSTER_ID=cube-prod \
      CUBE_LOGS_BACKEND=loki \
      bash

# 计算节点
curl -fsSL https://raw.githubusercontent.com/tencentcloud/CubeSandbox/master/deploy/one-click/online-install.sh \
  | sudo env ONE_CLICK_DEPLOY_ROLE=compute \
      ONE_CLICK_CONTROL_PLANE_IP=10.0.0.10 \
      ONE_CLICK_ENABLE_OBSERVABILITY=1 \
      CUBE_OBSERVABILITY_CLUSTER_ID=cube-prod \
      CUBE_LOGS_BACKEND=loki \
      bash
```

`online-install.sh` 只是下载安装包并调用同一份 `install.sh`，因此不是新的部署形态，
也不使用单独的监控参数。需要填写鉴权 Token 等敏感值时，优先下载 release 包后写入
权限为 `0600` 的 `.env`，不要把秘密留在 shell 历史中。

#### E. 外部后端

```dotenv
ONE_CLICK_OBSERVABILITY_ROLE=auto
CUBE_LOGS_BACKEND=loki
CUBE_METRICS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:9092/api/v1/otlp
CUBE_LOGS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:3100/otlp
```

显式配置外部 endpoint 后，`auto` 只在节点安装 Collector，不再安装本地后端。
外部后端的鉴权配置见[4.1](#41-核心环境变量与上报鉴权)。

### 2.2 Kubernetes Helm

部署集群内 Prometheus、Loki（默认）、Grafana，以及计算节点 Collector：

```bash
helm upgrade --install cube ./deploy/kubernetes/chart \
  -n cube-system --create-namespace \
  -f /path/to/runtime-values.yaml \
  --set observability.enabled=true \
  --set observability.clusterId=k8s-prod \
  --set observability.logs.backend=loki
```

只部署计算节点 Collector，并推送到外部后端：

```bash
helm upgrade --install cube ./deploy/kubernetes/chart \
  -n cube-system --create-namespace \
  -f /path/to/runtime-values.yaml \
  --set observability.enabled=true \
  --set observability.backend.enabled=false \
  --set observability.clusterId=k8s-prod \
  --set observability.metrics.otlpEndpoint="http://10.0.0.10:9092/api/v1/otlp" \
  --set observability.logs.backend=loki \
  --set observability.logs.otlpEndpoint="http://10.0.0.10:3100/otlp"
```

`runtime-values.yaml` 与正常 Chart 安装使用同一份配置，必须包含非默认的数据库、
Redis 凭据及节点选择等部署参数；可观测性 Value 只是在原有安装命令上追加。
如需 Elasticsearch，将 `observability.logs.backend` 改为 `elasticsearch`，并在
使用外部后端时设置 `observability.logs.esEndpoint`；不要同时设置两个日志 endpoint。

外部后端需要鉴权时，先用现有 Secret 管理工具在目标 namespace 创建 Secret，并在
`runtime-values.yaml` 中引用；Secret 值应保存完整 `Authorization` Header（例如
`Bearer ...`、ES 的 `ApiKey ...`），不要通过 `--set` 明文传入：

```yaml
# Example only. Create these Secrets through the cluster's secret-management workflow.
apiVersion: v1
kind: Secret
metadata:
  name: cube-observability-auth
  namespace: cube-system
type: Opaque
stringData:
  metrics-authorization: "Bearer <metrics-token>"
  metrics-tenant-id: "cube-prod"
  logs-authorization: "Bearer <logs-token>"
  logs-tenant-id: "cube-prod"
---
apiVersion: v1
kind: Secret
metadata:
  name: cube-grafana-admin
  namespace: cube-system
type: Opaque
stringData:
  admin-user: "admin"
  admin-password: "<strong-password>"
```

使用 Elasticsearch API Key 时，将 `logs-authorization` 改为
`ApiKey <key>`；后端不要求租户 Header 时删除对应 tenant 键。

在 `runtime-values.yaml` 中引用 Secret 的键：

```yaml
observability:
  metrics:
    auth:
      existingSecret: cube-observability-auth
      authorizationKey: metrics-authorization
      tenantIdKey: metrics-tenant-id # optional; empty disables X-Scope-OrgID
  logs:
    auth:
      existingSecret: cube-observability-auth
      authorizationKey: logs-authorization
      tenantIdKey: logs-tenant-id # set "" when the backend needs no tenant header
  backend:
    grafana:
      existingSecret: cube-grafana-admin
      adminUserKey: admin-user
      adminPasswordKey: admin-password
```

HTTPS endpoint 使用容器系统 CA；当前 Chart 未提供自定义 CA Secret。私有 CA 场景需
先扩展 CA 挂载，不能通过关闭证书校验代替。

Helm Collector 固定使用宿主机网络，并通过 `127.0.0.1:9998` 抓取同节点 Cubelet，
因此当前内置拓扑要求保持默认的 `cubeNode.hostNetwork=true`。将 Cubelet 切换为 Pod
网络后，Chart 暂未提供可靠的逐节点指标目标发现机制。

常用 Value：

| Value | 默认值 | 说明 |
| --- | --- | --- |
| `observability.enabled` | `false` | 可观测性总开关 |
| `observability.clusterId` | `cubesandbox` | 指标和日志的集群标签 |
| `observability.otelCollector.scrapeInterval` | `15s` | Cubelet 指标抓取周期；当前抓取超时固定为 `10s`，因此周期不得小于 `10s` |
| `observability.otelCollector.cubeletMetricsTarget` | `127.0.0.1:9998` | 节点本地 Cubelet 指标地址；默认值要求 `cubeNode.hostNetwork=true` |
| `observability.otelCollector.stateHostPath` | `/var/lib/cube-sandbox/otelcol-k8s` | 每节点 filelog 游标、队列和 WAL 的持久目录 |
| `observability.backend.enabled` | `true` | 启用总开关后，是否同时部署 Prometheus、所选日志后端、Grafana |
| `observability.metrics.otlpEndpoint` | `""` | 外部 Prometheus OTLP/HTTP 基础地址 |
| `observability.metrics.remoteWriteUrl` | `""` | 外部 Prometheus Remote Write 地址，可替代 OTLP |
| `observability.metrics.auth.existingSecret` | `""` | 指标 Authorization / tenant Secret |
| `observability.logs.enabled` | `true` | 是否采集沙箱标准输出/标准错误日志 |
| `observability.logs.backend` | `loki` | 日志后端，可选 `loki` 或 `elasticsearch` |
| `observability.logs.otlpEndpoint` | `""` | `backend=loki` 时的外部 OTLP/HTTP 基础地址 |
| `observability.logs.esEndpoint` | `""` | `backend=elasticsearch` 时的外部 Elasticsearch 地址 |
| `observability.logs.auth.existingSecret` | `""` | 日志 Authorization / tenant Secret |
| `observability.backend.<component>.persistence.enabled` | `true` | 内置 Prometheus、日志后端、Grafana 默认使用 PVC |
| `observability.backend.grafana.existingSecret` | `""` | Grafana 管理员用户名/密码 Secret；生产环境建议设置 |

### 2.3 Terraform 腾讯云

修改 `deploy/one-click/terraform/tencentcloud/.env`：

```dotenv
TENCENTCLOUD_ENABLE_OBSERVABILITY='true'
CUBE_LOGS_BACKEND='loki'
```

然后执行原有入口：

```bash
cd deploy/one-click/terraform/tencentcloud
bash ./create.sh
```

也可以只对本次执行启用，不修改 `.env`：

```bash
cd deploy/one-click/terraform/tencentcloud
TENCENTCLOUD_ENABLE_OBSERVABILITY=true bash ./create.sh
```

当前命令传入的环境变量优先于 `.env`，因此 `.env` 保持默认的 `false` 即可。
默认安装 Loki；如需 Elasticsearch，在 `.env` 中将
`CUBE_LOGS_BACKEND='elasticsearch'`，或执行
`CUBE_LOGS_BACKEND=elasticsearch TENCENTCLOUD_ENABLE_OBSERVABILITY=true bash ./create.sh`。

该形态在跳板机部署后端，并在 CVM 沙箱计算节点部署 Collector。

## 3. 独立安装（可选）

不使用 one-click / Helm / Terraform 标准入口时，也可以直接安装可观测性组件。
独立安装器管理
`cube-sandbox-otelcol`、Prometheus、Loki/Elasticsearch 和 Grafana，不修改
CubeMaster、Cubelet 等主服务。
开始前先执行[安装前自检](#安装前自检)，并选定一台其他计算节点可访问的后端节点。

### 3.1 单节点或第一台后端节点

如果后端节点本身也运行沙箱，安装后端和本机 Collector：

```bash
sudo env \
  CUBE_OBSERVABILITY_CLUSTER_ID=cube-prod \
  CUBE_LOGS_BACKEND=loki \
  CUBE_GRAFANA_ADMIN_PASSWORD='REPLACE_WITH_STRONG_PASSWORD' \
  ./deploy/one-click/observability/install.sh all
```

如果该节点只承载后端、不运行沙箱，则不安装本机 Collector：

```bash
sudo env \
  CUBE_OBSERVABILITY_CLUSTER_ID=cube-prod \
  CUBE_LOGS_BACKEND=loki \
  CUBE_GRAFANA_ADMIN_PASSWORD='REPLACE_WITH_STRONG_PASSWORD' \
  ./deploy/one-click/observability/install.sh backend
```

执行前必须替换 Grafana 密码。选择 Elasticsearch 时将
`CUBE_LOGS_BACKEND=loki` 改为 `elasticsearch`。后端节点需按 [2.1 A](#a-安装第一台-all-in-one-节点)
中的端口规则限制访问来源。

### 3.2 其他 CVM / 裸金属计算节点

在每个计算节点安装 Collector，并将 `10.0.0.10` 替换为后端节点内网 IP：

```bash
sudo env \
  CUBE_OBSERVABILITY_CLUSTER_ID=cube-prod \
  CUBE_METRICS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:9092/api/v1/otlp \
  CUBE_LOGS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:3100/otlp \
  ./deploy/one-click/observability/install.sh cvm
```

Elasticsearch 场景删除 `CUBE_LOGS_OTLP_HTTP_ENDPOINT`，改为：

```bash
sudo env \
  CUBE_OBSERVABILITY_CLUSTER_ID=cube-prod \
  CUBE_LOGS_BACKEND=elasticsearch \
  CUBE_METRICS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:9092/api/v1/otlp \
  CUBE_LOGS_ES_ENDPOINT=http://10.0.0.10:9200 \
  ./deploy/one-click/observability/install.sh cvm
```

外部后端需要 Token、租户 Header、ES API Key 或 Basic Auth 时，按
[4.1](#41-核心环境变量与上报鉴权) 增加对应环境变量。

### 3.3 Kubernetes 独立 Collector

命名空间必须已存在，目标计算节点必须带有
`cube.tencent.com/cube-node=true` 标签；Cubelet 必须在宿主机网络命名空间监听
`127.0.0.1:9998`。

```bash
kubectl get namespace cube-system >/dev/null 2>&1 || \
  kubectl create namespace cube-system
kubectl label node <计算节点名称> cube.tencent.com/cube-node=true --overwrite
```

然后安装 Collector DaemonSet，并指向指定后端：

```bash
env \
  CUBE_METRICS_NAMESPACE=cube-system \
  CUBE_OBSERVABILITY_CLUSTER_ID=cube-prod \
  CUBE_METRICS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:9092/api/v1/otlp \
  CUBE_LOGS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:3100/otlp \
  ./deploy/kubernetes/observability/install.sh install
```

### 3.4 验收与卸载

```bash
# systemd all-in-one 完整安装
sudo ./deploy/one-click/observability/install.sh status

# 仅安装 Collector 的 systemd 计算节点
sudo systemctl --no-pager --full status cube-sandbox-otelcol.service

# Remove independently installed systemd components; data directories are retained.
sudo ./deploy/one-click/observability/install.sh uninstall all

# Kubernetes
kubectl -n cube-system rollout status daemonset/cube-node-otelcol
./deploy/kubernetes/observability/install.sh uninstall
```

## 4. OTel Collector 配置

节点采集器使用 `otelcol-contrib 0.162.0`，根据已配置的输出最多生成两个独立数据管道：

| Pipeline | Receiver | 输入 | Exporter |
| --- | --- | --- | --- |
| `metrics` | `prometheus` | Cubelet `/v1/metrics/resource` | `otlp_http/metrics` 或 `prometheus_remote_write` |
| `logs/sandbox` | `filelog/sandbox` | `/data/cubelet/log/*/{stdout,stderr}` | `elasticsearch/logs` 或 `otlp_http/logs` |

表中的名称来自 systemd/独立 Kubernetes 生成器；Helm 使用功能相同的
`prometheus/cubelet_resource_metrics`、`filelog/sandbox_stdio` 和 `logs` 名称。
实现细节见 [OTel 方案设计](../dev/sandbox-otel-recommendation.md)。

### 4.1 核心环境变量与上报鉴权

| 变量 | 默认值 | 示例 | 说明 |
| --- | --- | --- | --- |
| `CUBE_OBSERVABILITY_CLUSTER_ID` | `cubesandbox` | `cube-prod` | 写入指标和日志的集群标签 |
| `CUBE_LOGS_BACKEND` | `loki` | `elasticsearch` | one-click / Terraform 选择的日志后端 |
| `CUBE_SANDBOX_NODE_IP` | 自动探测 | `10.0.0.20` | 仅 systemd：节点标签；无法自动探测或需要固定标签时显式设置 |
| `CUBE_METRICS_OTLP_HTTP_ENDPOINT` | 空 | `http://10.0.0.10:9092/api/v1/otlp` | OTLP/HTTP 基础地址，不要附加 `/v1/metrics` |
| `CUBE_METRICS_REMOTE_WRITE_URL` | 空 | `http://10.0.0.10:9092/api/v1/write` | Prometheus Remote Write 地址；与 OTLP 指标地址二选一 |
| `CUBE_METRICS_AUTHORIZATION` / `CUBE_METRICS_REMOTE_WRITE_TOKEN` | 空 | `Bearer ...` | one-click / systemd / Terraform / 独立 Kubernetes 的指标鉴权，完整 Header 与纯 Token 二选一 |
| `CUBE_METRICS_TENANT_ID` | 空 | `cube-prod` | 指标输出的 `X-Scope-OrgID` |
| `CUBE_LOGS_ES_ENDPOINT` | 空 | `http://10.0.0.10:9200` | Elasticsearch 地址 |
| `CUBE_LOGS_ES_INDEX` | `cubesandbox-logs` | `cube-prod-logs` | 日志索引名 |
| `CUBE_LOGS_ES_USER` / `CUBE_LOGS_ES_PASSWORD` | 空 | `cube` / `...` | one-click / systemd / Terraform / 独立 Kubernetes：ES Basic Auth，必须成对设置 |
| `CUBE_LOGS_ES_API_KEY` | 空 | `...` | one-click / systemd / Terraform / 独立 Kubernetes：ES API Key，与 Basic Auth 二选一 |
| `CUBE_LOGS_ES_TLS_INSECURE_SKIP_VERIFY` | `false` | `true` | 跳过 ES TLS 校验，只建议临时诊断 |
| `CUBE_LOGS_OTLP_HTTP_ENDPOINT` | 空 | `http://10.0.0.10:3100/otlp` | OTLP/HTTP 日志基础地址；与 Elasticsearch 二选一 |
| `CUBE_LOGS_AUTHORIZATION` / `CUBE_LOGS_TOKEN` | 空 | `Bearer ...` | one-click / systemd / Terraform / 独立 Kubernetes 日志鉴权，完整 Header 与纯 Token 二选一 |
| `CUBE_LOGS_TENANT_ID` | 空 | `cube-prod` | 日志输出的 `X-Scope-OrgID` |
| `CUBE_LOGS_SANDBOX_ID` | `*` | `019abc-example` | systemd 形态可限制为一个沙箱 ID |
| `CUBE_LOGS_FILE_START_AT` | `beginning` | `end` | 仅 systemd：首次读取位置，可选 `beginning` 或 `end` |

内置 Grafana provisioning 会复用上述指标/日志凭证配置数据源。执行
`grafana/install.sh dashboards` 时会按 UID 创建或更新数据源。

Terraform 形态未显式设置 `CUBE_OBSERVABILITY_CLUSTER_ID` 时，会优先使用 Terraform
输出的 TKE cluster ID；无法取得时才回退到 `cubesandbox`。

独立 Kubernetes 与 Helm 使用 Kubernetes Node 名称作为节点标签，不读取上述
systemd 专属变量。其 `start_at` 固定为 `beginning`，但只在没有已保存游标时生效：
Collector 状态按节点持久化在 `/var/lib/cube-sandbox/otelcol-k8s`，同一节点上的
Pod 重建会从 `file_storage` 续读，不会从头重读现存文件。

one-click 用户可直接使用 `deploy/one-click/env.example` 中的
`Observability` 配置段。按 [2.1](#21-one-click) 复制为 `.env` 后，按需取消注释并
修改，例如：

```dotenv
ONE_CLICK_ENABLE_OBSERVABILITY=1
CUBE_OBSERVABILITY_CLUSTER_ID=cube-prod
CUBE_LOGS_BACKEND=loki

# Standard all-in-one/compute nodes derive these automatically.
# Set them only for a self-managed or external backend.
CUBE_METRICS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:9092/api/v1/otlp
CUBE_LOGS_OTLP_HTTP_ENDPOINT=http://10.0.0.10:3100/otlp
```

至少配置一个指标或日志输出端。指标的两个输出变量不能同时设置；日志的两个输出变量
也不能同时设置。选择 Elasticsearch 时改为 `CUBE_LOGS_BACKEND=elasticsearch`、
`CUBE_LOGS_ES_ENDPOINT=http://10.0.0.10:9200`，并可用
`CUBE_LOGS_ES_INDEX` 修改索引名。one-click 标准拓扑会按后端选择自动补全内置地址，
不要求手工填写。

#### 鉴权（所有部署形态）

所有形态都支持向带鉴权的**外部**指标和日志后端上报。最简单的 Bearer 示例：

```dotenv
CUBE_METRICS_OTLP_HTTP_ENDPOINT=https://prometheus.example.com/api/v1/otlp
CUBE_METRICS_AUTHORIZATION='Bearer <metrics-token>'
CUBE_LOGS_OTLP_HTTP_ENDPOINT=https://loki.example.com/otlp
CUBE_LOGS_AUTHORIZATION='Bearer <logs-token>'
```

- one-click、独立 systemd 和 Terraform：在各自 `.env` 中设置上述变量；
- 独立 Kubernetes 安装器：接受同名环境变量，并把凭据写入
  `cube-node-otelcol` Secret；
- Helm：创建 Secret 后通过 `observability.metrics.auth.existingSecret` 和
  `observability.logs.auth.existingSecret` 引用，完整示例见 [2.2](#22-kubernetes-helm)；
- Elasticsearch：使用 `CUBE_LOGS_ES_API_KEY`，或成对设置
  `CUBE_LOGS_ES_USER` / `CUBE_LOGS_ES_PASSWORD`。

`*_AUTHORIZATION` 填完整的单行 Header；`*_TOKEN` 只填裸 Token，安装器会补成
`Bearer`。这两种写法不能同时设置。以上变量只配置 Collector/Grafana 客户端 Header，
不会给内置 Prometheus、Loki 或 Elasticsearch 自动开启服务端鉴权；内置后端需要鉴权时，
应在前面部署认证网关/反向代理，或使用托管后端。

官方参考：
[OTLP HTTP exporter](https://github.com/open-telemetry/opentelemetry-collector/tree/main/exporter/otlphttpexporter)、
[Prometheus Remote Write exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/exporter/prometheusremotewriteexporter)、
[Loki authentication](https://grafana.com/docs/loki/latest/operations/authentication/)、
[Elasticsearch API keys](https://www.elastic.co/docs/deploy-manage/api-keys)和
[Kubernetes Secret](https://kubernetes.io/docs/concepts/configuration/secret/)。

不要提交真实凭据；`.env` 应设为 `0600`。HTTPS 默认使用系统 CA，私有 CA 应安装或
挂载信任链，不应长期关闭证书校验。

### 4.2 配置位置与渲染

`render` 与实际安装使用同一套生成逻辑，会将 4.1 中已经设置的环境变量写入最终
配置，无需再编辑生成的 YAML。one-click 的统一入口会自动读取 `.env`；独立执行时，
可先 `export` 这些变量，或在命令前通过 `env` 传入。

| 形态 | 安装后的配置位置 | 使用已配置变量查看渲染结果 |
| --- | --- | --- |
| systemd | `/etc/cube-sandbox/otelcol.yaml` | `./deploy/one-click/observability/install.sh render cvm` |
| 独立 Kubernetes 脚本 | ConfigMap `cube-node-otelcol` | `./deploy/kubernetes/observability/install.sh render` |
| Helm | ConfigMap `<release>-node-otelcol-config` | 使用下方 `helm template` 命令 |

Helm 使用 `values.yaml` / `--set` 中的 `observability.*` Value，不会自动读取
`CUBE_*` 环境变量。systemd Collector 使用
`/var/lib/cube-sandbox/otelcol/storage` 保存 filelog 游标和 OTLP 发送队列；
Kubernetes Collector 容器内使用 `/var/lib/otelcol/storage`，默认映射到节点
`/var/lib/cube-sandbox/otelcol-k8s`。

仅预览 Helm Collector 配置时，仍需为 Chart 的全局凭据校验提供非默认占位值：

```bash
helm template cube ./deploy/kubernetes/chart \
  --set observability.enabled=true \
  --set-string mysql.password=render-only \
  --set-string mysql.rootPassword=render-only \
  --set-string redis.password=render-only \
  -s templates/observability.yaml
```

`render-only` 只能用于本地渲染，实际安装必须通过 Secret values 提供真实凭据。

### 4.3 生成配置示例

下面假设使用 4.1 的示例变量，并将节点地址设置为 `10.0.0.20`，节选 systemd
形态同时向 Prometheus OTLP 接收端和 Loki 发送数据时的核心配置。为便于
阅读，省略了部分 exporter discover/queue/retry 和 `service.telemetry` 字段；完整
结果以 `render` 命令输出为准。代码块中的注释仅用于说明变量映射，实际生成文件不
一定保留这些注释。

这是渲染结果示例，不是需要再次维护的配置模板。`render` 和 `install` 都会自动将
已经配置的环境变量写入 YAML，并补全鉴权、队列和节点标签；无需手工替换下面的
示例值。环境变量变更后重新执行安装即可重新生成配置。

```yaml
receivers:
  # Scrape sandbox resource metrics from the local Cubelet only.
  prometheus:
    trim_metric_suffixes: false
    config:
      scrape_configs:
        - job_name: cubesandbox-resource
          scrape_interval: 5s
          scrape_timeout: 3s
          metrics_path: /v1/metrics/resource
          static_configs:
            - targets: ["127.0.0.1:9998"]
  # CUBE_LOGS_SANDBOX_ID replaces "*"; the default collects every sandbox.
  filelog/sandbox:
    include:
      - /data/cubelet/log/*/stdout
      - /data/cubelet/log/*/stderr
    start_at: beginning # CUBE_LOGS_FILE_START_AT
    storage: file_storage
    include_file_path: true
    include_file_name: false
    max_log_size: 1MiB
    operators:
      - type: regex_parser
        parse_from: attributes["log.file.path"]
        regex: '^/data/cubelet/log/(?P<sandbox_id>[^/]+)/(?P<stream>stdout|stderr)$'
      - type: copy
        from: attributes["log.file.path"]
        to: attributes.log_file_path
      - type: copy
        from: attributes.sandbox_id
        to: attributes.cubesandbox_sandbox_id
      - type: move
        from: attributes.sandbox_id
        to: resource["cubesandbox.sandbox.id"]
      - type: copy
        from: attributes.stream
        to: attributes.log_iostream
      - type: move
        from: attributes.stream
        to: attributes["log.iostream"]

processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 256
  # Values come from CUBE_OBSERVABILITY_CLUSTER_ID and
  # CUBE_SANDBOX_NODE_IP (auto-detected when unset).
  resource/metrics:
    attributes:
      - {key: cluster, value: "cube-prod", action: upsert}
      - {key: node, value: "10.0.0.20", action: upsert}
      - {key: cubesandbox.cluster.id, value: "cube-prod", action: upsert}
      - {key: host.name, value: "10.0.0.20", action: upsert}
  resource/logs_common:
    attributes:
      - {key: host.name, value: "10.0.0.20", action: upsert}
      - {key: cubesandbox.cluster.id, value: "cube-prod", action: upsert}
  attributes/logs_common:
    actions:
      - {key: host_name, value: "10.0.0.20", action: upsert}
      - {key: cubesandbox_cluster_id, value: "cube-prod", action: upsert}
      - {key: cubesandbox_sandbox_id, value: "none", action: insert}
      - {key: log_iostream, value: "none", action: insert}
  resource/sandbox:
    attributes:
      - {key: service.name, value: "cubesandbox-sandbox", action: upsert}
      - {key: cubesandbox.log.source, value: "sandbox-init", action: upsert}
  attributes/sandbox:
    actions:
      - {key: service_name, value: "cubesandbox-sandbox", action: upsert}
      - {key: cubesandbox_log_source, value: "sandbox-init", action: upsert}
  batch:
    timeout: 2s
    send_batch_size: 1024

exporters:
  otlp_http/metrics:
    # CUBE_METRICS_OTLP_HTTP_ENDPOINT; the Collector appends /v1/metrics.
    endpoint: "http://10.0.0.10:9092/api/v1/otlp"
    compression: gzip
    sending_queue:
      enabled: true
      storage: file_storage
    retry_on_failure:
      enabled: true
      max_elapsed_time: 0
  otlp_http/logs:
    # CUBE_LOGS_OTLP_HTTP_ENDPOINT; the Collector appends /v1/logs.
    endpoint: "http://10.0.0.10:3100/otlp"
    compression: gzip
    sending_queue:
      enabled: true
      storage: file_storage
    retry_on_failure:
      enabled: true
      max_elapsed_time: 0

extensions:
  health_check:
    endpoint: 127.0.0.1:13133
  # Persist file offsets and the OTLP sending queue across process restarts.
  file_storage:
    directory: /var/lib/cube-sandbox/otelcol/storage
    create_directory: true

service:
  extensions: [health_check, file_storage]
  pipelines:
    # Metrics and logs are independent pipelines.
    metrics:
      receivers: [prometheus]
      processors: [memory_limiter, resource/metrics, batch]
      exporters: [otlp_http/metrics]
    logs/sandbox:
      receivers: [filelog/sandbox]
      processors: [memory_limiter, resource/logs_common, resource/sandbox, attributes/logs_common, attributes/sandbox, batch]
      exporters: [otlp_http/logs]
```

选择 Elasticsearch 时，日志 exporter 会替换为 `elasticsearch/logs`，地址和索引
分别来自 `CUBE_LOGS_ES_ENDPOINT`、`CUBE_LOGS_ES_INDEX`；其余 receiver、字段提取
和 pipeline 不变。

### 4.4 支持范围、规则与建议参数

#### 指标和日志采集规则

| 类型 | 当前支持规则 | 可配置入口 |
| --- | --- | --- |
| 沙箱指标 | 只抓取本机 Cubelet `/v1/metrics/resource`；包含沙箱 CPU、Memory 和节点沙箱计数 | 集群/节点标签、OTLP 或 Remote Write 目标；Helm 还可配置 `observability.otelCollector.scrapeInterval` |
| 沙箱日志 | 按行读取 `/data/cubelet/log/<sandbox-id>/{stdout,stderr}`，从路径提取 Sandbox ID 和 stdout/stderr | 默认通过 OTLP 写 Loki，也可写 Elasticsearch；systemd 可通过 `CUBE_LOGS_SANDBOX_ID` 和 `CUBE_LOGS_FILE_START_AT` 控制范围与首次读取位置 |
| 状态与重试 | filelog 游标和 OTLP 队列使用 `file_storage`；Remote Write 使用 WAL；Elasticsearch 使用内存队列且最多重试 5 次 | systemd 文件状态保存在宿主机目录；Kubernetes 按节点保存在 `/var/lib/cube-sandbox/otelcol-k8s` |

日志 Receiver 按行保留原始 `Body`；单条日志记录的内置上限在 systemd 和独立
Kubernetes 脚本中设置为 `1MiB`。

两个后端都保留以下语义字段（Loki 会把 OTel 属性名中的 `.` 规范化为 `_`）：

- `service_name=cubesandbox-sandbox`
- `cubesandbox_log_source=sandbox-init`
- `cubesandbox_cluster_id`
- `host_name`
- `cubesandbox_sandbox_id`
- `log_iostream=stdout|stderr`
- `log_file_path`
- 日志正文和时间戳

Loki 只将低基数字段 `service_name`、`cubesandbox_cluster_id`、`host_name` 建为索引
label；`cubesandbox_sandbox_id`、`log_iostream` 等保留为 structured metadata。
这避免每个沙箱生成新的高基数 stream。常用 LogQL：

```logql
# All sandbox stderr
{service_name="cubesandbox-sandbox"} | log_iostream="stderr"

# Error-like messages from one sandbox
{service_name="cubesandbox-sandbox"}
  | cubesandbox_sandbox_id="<sandbox-id>"
  |~ "(?i)error|fatal|panic"
```

选择 Elasticsearch 时，文档另外包含 `Body` 与 `@timestamp`，常用 Lucene
过滤条件：

```text
# All sandbox stderr
service_name:"cubesandbox-sandbox" AND log_iostream:"stderr"

  # Error-like messages from sandbox stdout/stderr
cubesandbox_log_source:"sandbox-init" AND Body:(ERROR OR FATAL OR panic)

# One sandbox
cubesandbox_sandbox_id:"<sandbox-id>"
```

#### Collector 建议参数

| 参数 | 当前内置值 | 建议 |
| --- | --- | --- |
| 指标抓取周期 / 超时 | systemd、独立 K8s：`5s / 3s`；Helm：`15s / 10s` | systemd/独立 K8s 保持内置值；Helm 周期不得小于固定的 `10s` 超时，较大规模建议保留默认 `15s` |
| `CUBE_LOGS_FILE_START_AT`（仅 systemd） | `beginning` | 需要读取当前文件全部内容时保留 `beginning`；只采集 Collector 启动后新增内容时使用 `end` |
| `CUBE_LOGS_SANDBOX_ID`（仅 systemd） | `*` | 正常运行保持 `*`；只在单沙箱排障或临时降低日志量时指定 ID |
| Memory limiter | 固定 `256MiB`；Kubernetes Pod memory request/limit 为 `128Mi / 512Mi` | 先使用默认值；仅提高 Pod limit 不会改变 limiter，目前安装器未提供 limiter 调参入口 |
| Batch | `1024` 条；systemd/独立 K8s 超时 `2s`，Helm 超时 `5s` | 低延迟优先保持当前值；不要直接修改生成文件，后续重新安装会覆盖手工修改 |
| OTLP 发送队列 | `1000`，使用磁盘 `file_storage`，失败持续重试 | 保证 `/var/lib/cube-sandbox/otelcol` 或 Kubernetes 节点状态目录有足够空间 |
| Elasticsearch 发送队列 | 内存队列 `1000`、`10` 个 consumer，最多重试 `5` 次 | 持续故障可能最终丢弃日志，应监控 Collector 日志和后端可用性 |
| Remote Write | 使用 WAL | 保证 WAL 所在目录有足够空间，并监控远端写入失败 |
| Prometheus 保留策略 | one-click：`15d` / `40GB`；Helm：`15d` | one-click 使用 `CUBE_PROMETHEUS_RETENTION_TIME` / `CUBE_PROMETHEUS_RETENTION_SIZE`；Helm 使用 `observability.backend.prometheus.retention`，当前没有 size Value |
| Loki 保留策略 | one-click、Helm：`15d` | one-click 使用 `CUBE_LOKI_RETENTION_PERIOD`；Helm 使用 `observability.backend.loki.retention` |
| Elasticsearch Heap | one-click、Helm：`1g` | one-click 可通过 `CUBE_ES_HEAP_SIZE`、Helm 可通过 `observability.backend.elasticsearch.javaOpts` 调整；通常不超过可用内存的 50% |

除表中明确列出的环境变量或 Helm Value 外，抓取超时、Memory limiter、Batch 和发送
队列目前都是安装器内置值，不应通过手工编辑生成文件长期维护。

Helm 内置 Prometheus、所选日志后端和 Grafana 默认使用 PVC。可在
`observability.backend` 各组件的 `persistence` 下配置 `storageClassName`、
`existingClaim` 和容量；只有一次性验证环境才建议设置 `enabled=false`。需要高可用
时仍建议使用外部托管后端。内置 Loki 默认保留 `15d`；Elasticsearch 索引生命周期
（ILM）需要在后端单独配置，本安装器不会自动创建删除策略。

#### 建议监控表达式

当前安装器提供看板，但不自动安装 Alertmanager、Prometheus 告警规则或 Grafana
告警。以下表达式可作为起点：CPU、节流、内存占比和采样不可用建议使用 `for: 5m`；
内存失败计数应立即触发或只使用很短的等待时间，并根据业务负载调整阈值：

```promql
# CPU usage above 80% of a finite sandbox CPU limit
100 * rate(cubesandbox_host_sandbox_cpu_usage_seconds_total[5m])
  / clamp_min(cubesandbox_host_sandbox_cpu_limit_cores, 0.001) > 80

# CPU throttled periods above 20%
100 * rate(cubesandbox_host_sandbox_cpu_throttled_periods_total[5m])
  / clamp_min(rate(cubesandbox_host_sandbox_cpu_periods_total[5m]), 0.001) > 20

# Memory usage above 85% of a finite sandbox memory limit
100 * cubesandbox_host_sandbox_memory_current_bytes
  / clamp_min(cubesandbox_host_sandbox_memory_limit_bytes, 1) > 85

# Any memory-limit failure during the last five minutes
increase(cubesandbox_host_sandbox_memory_failures_total[5m]) > 0

# A sandbox exists but its resource sample is unavailable
cubesandbox_node_sandbox_count
  - cubesandbox_node_sandbox_available_count > 0
```

CPU/Memory 百分比表达式只适用于导出了有限资源上限的沙箱。

### 4.5 扩展采集其他组件日志

扩展 Cubelet、CubeMaster 等组件日志时，按下面的约定修改对应生成器。不要直接修改
安装后的 `/etc/cube-sandbox/otelcol.yaml` 或 ConfigMap：重新安装会覆盖生成文件，应修改
对应生成器并纳入测试。

三个部署形态的修改入口分别是：

- systemd：`deploy/one-click/observability/otel-systemd/install.sh` 中的
  `write_otel_config()`；确认 Collector 运行用户对新增路径只有必要的只读权限。
- 独立 Kubernetes：
  `deploy/kubernetes/observability/install.sh` 中的 `k8s_config()` 和
  DaemonSet `volumeMounts` / `hostPath`；新增宿主机目录必须只读挂载。
- Helm：`deploy/kubernetes/chart/templates/observability.yaml` 中的 Collector
  ConfigMap、`volumeMounts` 与 `volumes`；同时在 `values.yaml` 暴露明确开关，默认
  保持关闭。

每类组件建议使用独立 receiver 和 pipeline，例如
`filelog/component_cubelet`、`logs/component_cubelet`，避免不同日志格式共享解析器。
新增字段应同时保留 OTel 资源属性和 Elasticsearch 查询需要的扁平属性：

- `service.name` / `service_name`：稳定的低基数服务名，例如
  `cubesandbox-cubelet`。
- `cubesandbox.log.source` / `cubesandbox_log_source`：固定为 `component`。
- `cubesandbox.component.name` / `cubesandbox_component_name`：组件名，例如
  `cubelet`。
- 继续复用 `cubesandbox.cluster.id`、`host.name`、`log.file.path` 和时间戳。

以下是为 systemd/独立 Kubernetes 生成器增加 Cubelet 文本日志时的核心示例；
Loki 使用 `otlp_http/logs`，选择 Elasticsearch 时改为
`elasticsearch/logs`：

```yaml
receivers:
  filelog/component_cubelet:
    # Collect only records written after the Collector starts.
    include:
      - /data/log/Cubelet/*.log
    exclude:
      - /data/log/Cubelet/*.gz
    start_at: end
    storage: file_storage
    include_file_path: true
    include_file_name: false
    max_log_size: 1MiB

processors:
  resource/component_cubelet:
    attributes:
      - {key: service.name, value: "cubesandbox-cubelet", action: upsert}
      - {key: cubesandbox.log.source, value: "component", action: upsert}
      - {key: cubesandbox.component.name, value: "cubelet", action: upsert}
  attributes/component_cubelet:
    actions:
      - {key: service_name, value: "cubesandbox-cubelet", action: upsert}
      - {key: cubesandbox_log_source, value: "component", action: upsert}
      - {key: cubesandbox_component_name, value: "cubelet", action: upsert}

service:
  pipelines:
    logs/component_cubelet:
      receivers: [filelog/component_cubelet]
      processors:
        [memory_limiter, resource/logs_common, resource/component_cubelet,
         attributes/logs_common, attributes/component_cubelet, batch]
      exporters: [otlp_http/logs] # Use elasticsearch/logs for Elasticsearch.
```

扩展时还应遵循以下约束：

- 对 JSON 或多行日志增加专用 parser；解析失败必须保留原始 `Body`，不能阻断其他
  pipeline。
- Loki 只索引稳定的低基数字段。PID、请求 ID、Sandbox ID 等动态值保留为 structured
  metadata，不能新增为 label；组件名可以通过稳定的 `service_name` 区分。
- Kubernetes 只挂载所需目录且保持只读，不得为了方便挂载整个宿主机根目录。
- 排除凭据、Token、请求 Header 等敏感内容；必要时在发送前增加
  `redaction` / `transform` 处理器。
- 使用独立 `file_storage` 游标验证日志轮转、Collector/Pod 重启和后端故障恢复，避免
  重复采集或静默丢失。
- 提交扩展时同步三种生成器（适用的部署形态）、双语文档、Grafana 查询/看板及
  Collector `validate` 测试；未覆盖的形态必须在文档中明确说明。

## 5. 后端与看板

后端固定版本、默认端口与外部组件兼容基线见
[1.2](#12-安装器版本与兼容基线)。

Grafana 自动加载两张 sandbox-only 看板：

- `cubesandbox-host-sandbox.json`：沙箱 CPU、内存、限额、节流及实例数量。
- `cubesandbox-logs.json`：默认 Loki / LogQL 看板，按 Cluster、Node、Sandbox ID、
  stdout/stderr 和正文正则查询沙箱标准输出/标准错误日志。
- 选择 Elasticsearch 时，安装器改为加载
  `cubesandbox-logs-elasticsearch.json`（Grafana 中仍使用统一 UID
  `cubesandbox-logs`），查询条件改为 Lucene。

one-click/systemd 安装后可用
`sudo ./deploy/one-click/observability/install.sh status` 检查 Collector 与后端，并用
`curl -fsS http://127.0.0.1:3100/ready` 检查默认 Loki；选择 Elasticsearch 时使用
`curl -fsS 'http://127.0.0.1:9200/cubesandbox-logs*/_count'`。沙箱销毁会删除本地
日志目录，因此 Collector 必须在沙箱存活期间持续 tail 并写入远端后端。
