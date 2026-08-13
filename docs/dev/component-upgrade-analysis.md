# Cube 组件升级对比分析

> 文档生成时间：2026-07-30  
> 用途：对比当前版本与目标版本的组件差异，梳理功能变更与新增组件  

---

## 一、目标升级版本总览

| 序号 | 组件名 (Deployment) | 镜像 | 版本号 | 构建日期 |
|------|---------------------|------|--------|----------|
| 1 | cbskeeper | `cube-service-repo.tencentcloudcr.com/eks/cbskeeper` | `1.0.17-beta-3` | — |
| 2 | cube-image-accel | `cube-service-repo.tencentcloudcr.com/image-accel/image-accel-server-control` | `v0.4.0-20260729-1649b6d4` | 2026-07-29 |
| 3 | cube-image-accel | `cube-service-repo.tencentcloudcr.com/cube-meta/cube-controller` | `1.2.1-f1c863f` | — |
| 4 | cube-image-accel-test | `cube-service-repo.tencentcloudcr.com/image-accel/image-accel-server-control` | `v0.4.0-20260729-1649b6d4` | 2026-07-29 |
| 5 | cube-master-agentrun | `cube-service-repo.tencentcloudcr.com/cubemaster/cube-master-v2` | `release-1.7.0-20260727-101410-7df8bd89` | 2026-07-27 |
| 6 | cube-master-agentrun-gray | `cube-service-repo.tencentcloudcr.com/cubemaster/cube-master-v2` | `release-1.7.0-20260727-101410-7df8bd89` | 2026-07-27 |
| 7 | cube-master-agentrun-test | `cube-service-repo.tencentcloudcr.com/cubemaster/cube-master-v2` | `dev-1.7.0-20260702-183115-d1f11db9` | 2026-07-02 |
| 8 | cube-master-dev-deployment | `cube-service-repo.tencentcloudcr.com/cubemaster/cube-master-v2` | `dev-1.6.10-20260721-110851-555149c8` | 2026-07-21 |
| 9 | cube-master-test | `cube-service-repo.tencentcloudcr.com/cubemaster/cube-master-v2` | `dev-1.6.10-20260721-110851-555149c8` | 2026-07-21 |
| 10 | cube-metadata-1 | `cube-service-repo.tencentcloudcr.com/eks/metadata` | `test-20250717-39595dcd` | 2025-07-17 |
| 11 | cube-metadata-2 | `cube-service-repo.tencentcloudcr.com/eks/metadata` | `test-20250717-39595dcd` | 2025-07-17 |
| 12 | cube-migrate | `cube-service-repo.tencentcloudcr.com/eks/cube-migrate` | `test-20250604-d056b69f` | 2025-06-04 |
| 13 | image-convert | `cube-service-repo.tencentcloudcr.com/image-accel/containerd-image-converter` | `cube-2.0.5-0c4fd84ef` | — |
| 14 | kubernetes-proxy | `ccr.ccs.tencentyun.com/tkeimages/apiserver-proxy` | `v1.4.6` | — |
| 15 | networkd | `cube-service-repo.tencentcloudcr.com/eks/networkd` | `v5.1.1-beta-20260728-e57765f8` | 2026-07-28 |
| 16 | networkd-2 | `cube-service-repo.tencentcloudcr.com/eks/networkd` | `v5.1.1-beta-20260728-e57765f8` | 2026-07-28 |
| 17 | networkd-gray | `cube-service-repo.tencentcloudcr.com/eks/networkd` | `test-20260409-e5ed3fc7` | 2026-04-09 |
| 18 | oss-server | `cube-service-repo.tencentcloudcr.com/cube-oss/cuber-oss-server` | `v1.7.7-server-20260728-0b1d9bf9` | 2026-07-28 |
| 19 | oss-server-test | `cube-service-repo.tencentcloudcr.com/cube-oss/cuber-oss-server` | `marvin_test-20260727-ce6d864b` | 2026-07-27 |
| 20 | ritchiechen-perf-test | `cube-service-repo.tencentcloudcr.com/ritchiechen/perf-test` | `latest` | — |

---

## 二、组件版本对比

> ⚠️ 当前版本来源于截图，部分组件可能需要确认。

| 组件名 (Deployment) | 镜像基底 | 当前版本 | 目标版本 | 变更类型 |
|---------------------|----------|----------|----------|----------|
| **cbskeeper** | `eks/cbskeeper` | — | `1.0.17-beta-3` | 🆕 新增 |
| **cube-image-accel** | `image-accel/image-accel-server-control` | — | `v0.4.0` | 🆕 新增 |
| **cube-image-accel** | `cube-meta/cube-controller` | — | `1.2.1` | 🆕 新增 |
| **cube-image-accel-test** | `image-accel/image-accel-server-control` | — | `v0.4.0` | 🆕 新增 |
| **cube-master-agentrun** | `cubemaster/cube-master-v2` | — | `release-1.7.0` | 🆕 新增 |
| **cube-master-agentrun-gray** | `cubemaster/cube-master-v2` | — | `release-1.7.0` | 🆕 新增 |
| **cube-master-agentrun-test** | `cubemaster/cube-master-v2` | — | `dev-1.7.0` | 🆕 新增 |
| **cube-master-dev-deployment** | `cubemaster/cube-master-v2` | — | `dev-1.6.10` | ⬆️ 升级 |
| **cube-master-test** | `cubemaster/cube-master-v2` | — | `dev-1.6.10` | ⬆️ 升级 |
| **cube-metadata-1** | `eks/metadata` | — | `test-20250717` | ⬆️ 升级 |
| **cube-metadata-2** | `eks/metadata` | — | `test-20250717` | ⬆️ 升级 |
| **cube-migrate** | `eks/cube-migrate` | — | `test-20250604` | ➖ 不变 |
| **image-convert** | `image-accel/containerd-image-converter` | — | `cube-2.0.5` | ⬆️ 升级 |
| **kubernetes-proxy** | `tkeimages/apiserver-proxy` | — | `v1.4.6` | ➖ 不变 |
| **networkd** | `eks/networkd` | — | `v5.1.1-beta` | ⬆️ 升级 |
| **networkd-2** | `eks/networkd` | — | `v5.1.1-beta` | ⬆️ 升级 |
| **networkd-gray** | `eks/networkd` | — | `test-20260409` | ⬆️ 升级 |
| **oss-server** | `cube-oss/cuber-oss-server` | — | `v1.7.7` | ⬆️ 升级 |
| **oss-server-test** | `cube-oss/cuber-oss-server` | — | `marvin_test` | ⬆️ 升级 |
| **ritchiechen-perf-test** | `ritchiechen/perf-test` | — | `latest` | ➖ 不变 |

> 图例：🆕 新增 | ⬆️ 升级 | ➖ 不变 | 🔄 镜像仓库迁移

---

## 三、组件功能列举

### 3.1 控制面组件 (Control Plane)

#### CubeMaster（集群调度编排器）

| Deployment | 版本 | 说明 |
|------------|------|------|
| `cube-master-agentrun` | `release-1.7.0` | **正式环境** — AgentRun 业务集群主控 |
| `cube-master-agentrun-gray` | `release-1.7.0` | **灰度环境** — AgentRun 灰度发布集群 |
| `cube-master-agentrun-test` | `dev-1.7.0` | **测试环境** — AgentRun 测试集群 |
| `cube-master-dev-deployment` | `dev-1.6.10` | **开发环境** — 日常开发调试集群 |
| `cube-master-test` | `dev-1.6.10` | **测试环境** — 集成测试用集群 |

**核心功能：**

- 接收沙箱创建/销毁/暂停/恢复请求（gRPC）
- 根据资源可用性（CPU、内存、GPU、节点亲和性）选择目标节点
- 将任务分发到对应 Cubelet
- 将生命周期事件发布到 Redis（供 CubeProxy、CubeOps 消费）
- 管理模板定义、快照元数据、配额与鉴权

**1.7.0 关键特性：**

- pg rest 支持：发货/销毁（含 erofs & 快照）
- 支持 runc 模式
- 支持 pause/resume
- hostpath volume 挂载
- 发货支持指定 hostport 映射
- 支持 tcbs 存储

---

#### CubeAPI（API 网关）

> CubeAPI 组件不在本次 Deployment 清单中（常驻部署），无独立升级项。

**核心功能：**

- 无状态 E2B 兼容 REST API 网关
- 将 SDK 调用（Python/JS/Go SDK）翻译为内部 gRPC 请求转发到 CubeMaster
- 可插拔认证回调（OIDC、自定义 Token）
- 替换 `E2B_API_URL` 即可从 E2B 云迁移

---

### 3.2 数据面组件 (Data Plane)

#### CubeProxy（反向代理）

> CubeProxy 组件不在本次 Deployment 清单中（常驻部署），无独立升级项。

**核心功能：**

- 基于 OpenResty（nginx + Lua）的反向代理
- 支持 Host-based 和 Path-based 两种路由模式
- 从 Redis 读取沙箱元数据，将外部请求路由到正确的沙箱实例
- 兼容 E2B 协议（Connect RPC、WebSocket）

---

#### Networkd（网络守护进程）

| Deployment | 版本 | 说明 |
|------------|------|------|
| `networkd` | `v5.1.1-beta-20260728` | 正式环境 |
| `networkd-2` | `v5.1.1-beta-20260728` | 正式环境（冗余实例） |
| `networkd-gray` | `test-20260409` | 灰度环境 |

**核心功能：**

- 管理节点网络配置（IP 分配、VPC 路由、ENI 绑定）
- 配置 eBPF 虚拟交换机（CubeVS）的转发规则
- 沙箱网络隔离与策略执行
- SNAT/DNAT 规则管理

---

#### CubeNet / CubeVS（虚拟交换机）

> CubeVS 是内核态 eBPF 程序，由 networkd 管理，不在 Deployment 清单中。

**核心功能：**

- 基于 eBPF 的内核态虚拟交换机
- 三个 BPF 程序：from_cube、from_world、from_envoy
- 每沙箱 SNAT/DNAT、有状态连接跟踪
- LPM-trie 网络策略执行
- ARP 代理（无需 iptables 或 Linux Bridge）

---

#### CubeEgress（出口网关）

> CubeEgress 组件不在本次 Deployment 清单中（常驻部署），无独立升级项。

**核心功能：**

- 基于 OpenResty 的 L7 透明出口安全网关
- TPROXY 拦截所有出站 HTTP/HTTPS 流量
- 域名过滤（允许/拒绝列表）
- 凭证注入（自动附加 Authorization 头，密钥不暴露给沙箱）
- 访问审计日志

---

### 3.3 存储与镜像组件

#### image-convert（镜像格式转换）

| Deployment | 版本 | 说明 |
|------------|------|------|
| `image-convert` | `cube-2.0.5-0c4fd84ef` | 镜像格式转换服务 |

**核心功能：**

- 将标准 OCI 容器镜像转换为 erofs 格式
- 支持镜像增量构建与层级复用
- 与 containerd 集成，加速拉取与启动

---

#### cube-image-accel（镜像加速控制面）

| Deployment | 镜像 | 版本 | 说明 |
|------------|------|------|------|
| `cube-image-accel` | image-accel-server-control | `v0.4.0-20260729` | 镜像加速控制服务 |
| `cube-image-accel` | cube-controller | `1.2.1-f1c863f` | Cube 控制器 |
| `cube-image-accel-test` | image-accel-server-control | `v0.4.0-20260729` | 测试环境 |

**核心功能（image-accel-server-control v0.4.0）：**

- 镜像加速任务调度与管理
- 镜像预热（prefetch）到节点本地缓存
- 按需触发 containerd-image-converter 进行格式转换
- 镜像分发状态追踪与监控
- 与 E2B 模板系统集成，加速模板实例化

**核心功能（cube-controller 1.2.1）：**

- Cube 集群元数据管理
- 节点注册与心跳管理
- 资源拓扑维护

---

#### cbskeeper（云硬盘管理）

| Deployment | 版本 | 说明 |
|------------|------|------|
| `cbskeeper` | `1.0.17-beta-3` | 云硬盘生命周期管理 |

**核心功能：**

- CBS（Cloud Block Storage）云硬盘的挂载/卸载/扩容
- 快照创建与回滚
- 沙箱销毁时自动清理关联云硬盘

---

### 3.4 元数据与运维组件

#### cube-metadata（元数据服务）

| Deployment | 版本 | 说明 |
|------------|------|------|
| `cube-metadata-1` | `test-20250717` | 元数据服务实例 1 |
| `cube-metadata-2` | `test-20250717` | 元数据服务实例 2（高可用） |

**核心功能：**

- 提供沙箱元数据查询接口
- 管理实例-用户-模板映射关系
- 与 CubeMaster 协同提供环境变量注入、密钥注入等服务

---

#### cube-migrate（数据迁移服务）

| Deployment | 版本 | 说明 |
|------------|------|------|
| `cube-migrate` | `test-20250604` | 数据库迁移服务 |

**核心功能：**

- 数据库 Schema 升级迁移（goose v3）
- 带内容指纹的防篡改检测
- 集群级会话锁防止并发迁移冲突

---

#### oss-server（对象存储/运维后台）

| Deployment | 版本 | 说明 |
|------------|------|------|
| `oss-server` | `v1.7.7-server-20260728` | OSS 运维后台 |
| `oss-server-test` | `marvin_test-20260727` | 测试环境 OSS 后台 |

**核心功能：**

- 运维管理后台 WebUI API（Gin 框架）
- 集群监控（节点状态、资源使用率、沙箱统计）
- AgentHub 生命周期管理（创建、销毁、扩缩容）
- 模板商店（模板上传、版本管理、发布审批）
- 认证鉴权（登录、权限控制）
- 代理 SDK 请求到 CubeMaster

---

#### kubernetes-proxy（API Server 代理）

| Deployment | 版本 | 说明 |
|------------|------|------|
| `kubernetes-proxy` | `v1.4.6` | K8s API Server 代理 |

**核心功能：**

- 代理外部请求到集群内 K8s API Server
- 身份认证转发与访问控制
- 流量限流与审计

---

#### ritchiechen-perf-test（性能测试）

| Deployment | 版本 | 说明 |
|------------|------|------|
| `ritchiechen-perf-test` | `latest` | 性能压测工具 |

**核心功能：**

- 沙箱并发创建/销毁压测
- 延迟与吞吐量基准测试
- 资源泄漏检测

---

## 四、镜像仓库迁移说明

本次升级涉及镜像仓库迁移：

| 镜像类型 | 旧仓库 | 新仓库 |
|----------|--------|--------|
| cube-master-v2 | `ccr.ccs.tencentyun.com/cube-image/` | `cube-service-repo.tencentcloudcr.com/cubemaster/` |
| cuber-oss-server | `ccr.ccs.tencentyun.com/cube-image/` | `cube-service-repo.tencentcloudcr.com/cube-oss/` |
| containerd-image-converter | `ccr.ccs.tencentyun.com/cube-image/` | `cube-service-repo.tencentcloudcr.com/image-accel/` |
| image-accel-server-control | `ccr.ccs.tencentyun.com/cube-image/` | `cube-service-repo.tencentcloudcr.com/image-accel/` |

> ⚠️ 需要确认集群节点是否已配置对 `cube-service-repo.tencentcloudcr.com` 的拉取权限。

---

## 五、新增组件清单

以下组件在当前集群中不存在，需要新增部署：

| 组件 | 镜像 | 原因 |
|------|------|------|
| **cbskeeper** | `eks/cbskeeper:1.0.17-beta-3` | 新增云硬盘管理能力，支持 tcbs 存储 |
| **cube-image-accel** | `image-accel-server-control:v0.4.0` + `cube-controller:1.2.1` | 新增镜像加速控制面，配合 containerd-image-converter 实现 erofs 按需转换与预热 |
| **cube-image-accel-test** | `image-accel-server-control:v0.4.0` | 测试环境镜像加速控制面 |
| **cube-master-agentrun** | `cube-master-v2:release-1.7.0` | 新增 AgentRun 场景专用集群（正式环境） |
| **cube-master-agentrun-gray** | `cube-master-v2:release-1.7.0` | 新增 AgentRun 场景专用集群（灰度环境） |
| **cube-master-agentrun-test** | `cube-master-v2:dev-1.7.0` | 新增 AgentRun 场景专用集群（测试环境） |

---

## 六、pg rest 支持特性（CubeMaster 1.7.0）

作为本次升级的核心功能载体，CubeMaster `release-1.7.0` 新增以下特性支持 pg rest 场景：

| 特性 | 说明 | 关联组件 |
|------|------|----------|
| **发货/销毁 + erofs** | 使用 erofs 格式镜像进行沙箱创建与销毁，对比传统 OCI 镜像大幅减少启动时间 | image-convert, cube-image-accel |
| **发货/销毁 + 快照** | 基于快照的快速沙箱创建与回滚 | cube-master, oss-server |
| **tcbs 存储** | 支持腾讯云 CBS 作为沙箱持久化存储后端 | cbskeeper |
| **runc 模式** | 除 MicroVM 外，支持标准 runc 容器模式运行沙箱 | cube-master, cubelet |
| **pause/resume** | 沙箱暂停与恢复，暂停时保留内存状态和网络上下文 | cube-master, cubelet |
| **hostpath volume** | 支持将宿主机路径挂载到沙箱内部 | cube-master, cubelet |
| **hostport 映射** | 发货时指定宿主端口到沙箱端口的映射关系 | cube-master, networkd |

---

## 七、升级建议

### 升级顺序

```
第一梯队（基础设施，无业务影响）：
  1. kubernetes-proxy    → v1.4.6（不变，仅确认）
  2. cube-migrate         → test-20250604（不变，仅确认）

第二梯队（镜像与存储，影响新沙箱创建）：
  3. image-convert        → cube-2.0.5（containerd-image-converter 升级）
  4. cube-image-accel     → v0.4.0（新增部署）
  5. cbskeeper            → 1.0.17-beta-3（新增部署）

第三梯队（元数据与网络，影响运行中沙箱）：
  6. cube-metadata-1/2    → test-20250717
  7. networkd / networkd-2 → v5.1.1-beta
  8. networkd-gray        → test-20260409

第四梯队（核心控制面，需灰度发布）：
  9. cube-master-test                → dev-1.7.0
  10. cube-master-agentrun-test      → dev-1.7.0
  11. cube-master-dev-deployment     → dev-1.6.10
  12. cube-master-agentrun-gray      → release-1.7.0
  13. cube-master-agentrun           → release-1.7.0

第五梯队（运维后台）：
  14. oss-server-test      → marvin_test
  15. oss-server           → v1.7.7
```

### 关键风险点

1. **镜像仓库迁移**：需要提前在所有节点配置 `cube-service-repo.tencentcloudcr.com` 的 imagePullSecret
2. **CubeMaster 大版本升级**：`dev-1.6.x` → `release-1.7.0` 跨越多个版本，需充分回归测试
3. **networkd beta 版本**：`v5.1.1-beta` 为测试版，建议先在灰度环境（networkd-gray）验证
4. **新增组件部署**：cube-image-accel、cbskeeper 为全新组件，需要准备相应的 ConfigMap、Secret、PV 等 K8s 资源

---

## 八、待确认项

- [ ] 截图中当前各 Deployment 的具体版本号（请补充到第二节对比表）
- [ ] `cube-service-repo.tencentcloudcr.com` 镜像仓库拉取权限是否已配置
- [ ] cbskeeper 需要的 CBS API 密钥/服务账号是否已准备
- [ ] cube-image-accel 的 ConfigMap（镜像加速策略、缓存大小等）配置是否就绪
- [ ] AgentRun 专用集群（agentrun / agentrun-gray / agentrun-test）的节点池是否已划分
