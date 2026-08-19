# Issue #1280 对照表

`#1280` 是模板问题的**汇总 meta issue**,索引 14 个子 issue,分 5 类。它**不是本次 TC 拆分的验收清单**。

**本次改动只对应其中 1 个主线 issue（#957）**,另有 2 个被间接影响,**11 个完全未触及**。测试时请按下表区分,不要把未触及项当作回归上报。

---

## 一览

| # | Issue | 分类 | 本次状态 |
|---|---|---|---|
| **957** | 模板构建拆分为独立 TemplateCenter | 主线 | 🟡 **部分完成** |
| 66 | pending/building 模板无法删除,需强制清理 | 生命周期 | 🔴 未解决（有间接缓解） |
| 182 | 新节点按标签自动预热 | 分发 | ⚪ 未触及 |
| 499 | `tpl commit` 模板需分发到其他节点 | 分发 | ⚪ 未触及 |
| 578 | 节点加入回填 + failover + 快照灾备 | 分发 | ⚪ 未触及 |
| 852 | Pod 重启后产物文件丢失但元数据仍 READY | 产物 | 🟡 **部分缓解** |
| 1005 | 集群模式 redo 到计算节点 pmem sha256 不匹配 | 产物 | ⚪ 未触及 |
| 1105 | 快照创建 request_id 幂等永不命中（指纹含随机 ID） | 产物 | ⚪ 未触及 |
| 1159 | 快照模板 redo 失败:source_image_ref is required | 产物 | 🟡 **已止损,未解决** |
| 989 | guest-image 更新后模板/sandbox 兼容性 | 兼容性 | ⚪ 未触及 |
| 1203 | 快照恢复的组件多版本支持 | 兼容性 | ⚪ 未触及 |
| 870 | native export 不支持 HTTP（insecure）仓库 | 镜像接入 | ⚪ 未触及（**已随构建迁移到 TC 侧**） |
| 1227 | 自维护最小化 cube-envd | 镜像接入 | ⚪ 未触及 |
| 1233 | 模板 envs 与创建时 env_vars 可见性割裂 | 运行时 | ⚪ 未触及（**易与本次改动混淆,见下**） |

---

## 需要展开说明的 4 项

### #957 主线 — 部分完成

| 目标 | 状态 |
|---|---|
| 构建能力从 CubeMaster 抽离 | ✅ pull / envd / CA / mkfs 全在 TC 执行 |
| 双向通道 | ✅ 下发 + 回调,含退避重试与 reconciler |
| 故障隔离 | ✅ TC 崩溃不再带走 CubeMaster；resume goroutine 加了 recover |
| 独立部署 | ✅ 镜像 + systemd + Helm + Terraform |
| **独立扩缩容** | ❌ **未达成,且当前架构下不可能达成** |

最后一项要讲清楚:issue 原文的目标之一是 independent scaling,但按定稿的存储架构（产物在节点本地盘、不用 S3/RWX、TC 单实例),**TC 无法水平扩容** —— 第二个副本既读不到第一个的产物,也接不了它的构建。高可用由无状态的 CubeMaster 承担。

这不是漏做,是架构选择与 issue 原始设想不一致。若仍需 independent scaling,得先引入跨节点产物共享层,属于另一个迭代。

另外**控制面仍在 CubeMaster**（写库 + 分发),TC 只做数据面。这也是与 issue 描述的差异。

### #66 pending 模板无法删除 — 未解决,但有间接缓解

**本次验证中会直接遇到这个现象**:`rebuild` 排了 PENDING job → 删除返回 `Conflict`。

服务端行为是正确的（`delete.go:hasActiveJob`),但**强制清理机制仍然没有**。间接缓解有两条:

- `image_job_reconciler` 会把 RUNNING 超过 `snapshotOperationTimeout`(2h) 的 job 置 FAILED,之后即可删除
- 验证脚本会识别 Conflict 并重试至 job 结束（这只是测试侧,不是产品能力）

**测试时**:遇到删除返回 409 是预期的,不要上报。真正缺的是一条能立即取消 job 的接口。

### #852 产物文件丢失 — 部分缓解

根因（Pod 重启丢本地文件）在**部署形态层面被缓解了**:

- Helm:产物目录挂 PVC（CBS),Pod 重启后文件仍在
- Terraform:强制 `use_cfs=true`,plan 期就拦住 emptyDir 的配置

但**元数据与文件的一致性校验仍然没有** —— 依然可能出现 `artifact.status=READY` 而文件不存在。`templatecenter-test-data.md` 第 6 节列了这个特征（`READY` 但 `ext4_size_bytes=0`),但那是人工核对,不是运行时自愈。

### #1159 快照模板 redo — 已止损

这个必须说明,因为**本次改动一度扩大了它的影响面**:

我为修「分发全失败后模板永久无法 redo」加了「产物不可复用则降级为完整重建」。但重建路径 (`redo.go:300 PrepareLocalSource`) 需要 `source_image_ref`,而快照模板没有这个字段 —— 更糟的是,**重建前的清理会先删掉现有 ext4 并把产物置 FAILED**,然后才在下一行发现无法重建。

即:一个可能还能救的快照模板,会被推向永久死亡。

已加守卫（`redo.go` 重建分支入口),放在破坏性清理**之前**:

```
redo cannot rebuild this template: it has no source_image_ref
(templates created from a sandbox snapshot cannot be rebuilt from an image);
the existing artifact was left untouched
```

现在快速失败、产物保持原样、错误信息直接说明真正原因（原来报的是 `record not found`,完全看不出是快照模板的问题）。

**#1159 本身仍未解决** —— 真正的修复需要为快照模板提供另一条重建路径（从快照而非镜像),那是独立工作。

---

## 两个容易混淆的点

### #1233 与本次的环境变量改名无关

两者都叫"环境变量",但完全是两回事:

| | 对象 | 本次是否改动 |
|---|---|---|
| #1233 | **sandbox 运行时**环境变量（模板 `envs` vs 创建时 `env_vars`,在 `commands.run` / PTY / `cubecli exec` 下可见性不同） | ❌ 未触及 |
| 本次 | **进程配置**环境变量（`CUBE_TEMPLATE_CENTER_*`,TC 自己读的配置项） | ✅ 已统一 |

看到 #1233 别以为本次改过。

### #870 的影响面转移了

native export 不支持 HTTP registry 这个问题本身没修,但**拉镜像的动作现在发生在 TC 进程里**。代码是共享的（`image.PrepareLocalSource`),行为一致。

**测试含义**:若用私有 HTTP registry 测 `master-remote`,失败会出现在 TC 日志而不是 CubeMaster 日志 —— 现象与 #870 相同,不是新问题。

---

## 测试建议

1. 本次验证请按 `templatecenter-master-test-plan.md` 执行,它的范围是 **#957 的已完成部分**。
2. 上表 ⚪ 的 11 项**不在本次范围**,不要作为回归上报。
3. 遇到删除返回 409（#66）、HTTP registry 拉取失败（#870）、快照模板 redo 失败（#1159)属于**已知未解决**,可记录但无需当新缺陷。
4. 若想验证 #1159 的止损效果:对一个快照来源的模板执行 `redo`,应看到上面那条明确报错,且 `t_cube_rootfs_artifact` 中该产物的 `status` **不应**被改成 FAILED。
