# Template Center 测试数据核对

创建一个模板会跨 5 张表落库。这份文档说明**每个阶段应该看到什么数据**，以及数据不一致时说明哪个环节坏了。

配套文档：`templatecenter-testing-handoff.md`（怎么发请求）。

```bash
export MYSQL="docker exec -i cube-mysql mysql -ucube -pcube_pass cube_mvp"
```

---

## 1. 五张表的分工

| 表 | 主键 | 作用 | 生命周期 |
|---|---|---|---|
| `t_cube_template_image_job` | `job_id` | 构建任务与进度 | **删除模板后仍保留**（构建历史） |
| `t_cube_rootfs_artifact` | `artifact_id` | ext4 产物元数据 | 引用归零后由 GC 回收 |
| `t_cube_template_definition` | `template_id` | 模板定义（含别名） | 随模板删除 |
| `t_cube_template_replica` | `template_id`+`node_id` | 每节点副本状态 | 随模板删除 |
| `t_cube_artifact_node_placement` | `artifact_id`+`node_id` | 产物物理落盘位置 | **比 replica 行活得久**，用于副本行消失后仍能定位节点做清理 |

别名不是独立表：存在 `t_cube_template_definition.display_name`，并有一个 STORED 生成列 `alias_key`（仅 `kind='template'` 且 `display_name` 非空时非 NULL），因此快照的 `display_name` 永远不会抢占模板别名。

---

## 2. 四套独立的状态枚举（最容易混）

这四组值域互不相同，别互相套用。

**job.status** — `PENDING` → `RUNNING` → `BUILT`* → `READY` / `FAILED`

> `BUILT` **仅 remote 模式出现**，是 TC 报完产物、CubeMaster 尚未分发的中间态。

**job.phase** — `PULLING` `UNPACKING` `BUILDING_EXT4` `GENERATING_JSON` `DISTRIBUTING` `CREATING_TEMPLATE` `REGISTERING` `READY` `DELETING`（另有 3 个 `ROLLBACK_*`）

**artifact.status** — `PENDING` `BUILDING` `READY` `FAILED` `CLEANUP_PENDING` `ORPHANED`

> 只有 `READY` 可复用。`CLEANUP_PENDING`/`ORPHANED` 表示文件已删或正在删，redo 必须重建。

**definition.status / replica.status**

| definition.status | 含义 |
|---|---|
| `READY` | 全部节点就绪 |
| `PARTIALLY_READY` | 部分节点就绪，可用，可 redo 补齐 |
| `FAILED` | 无任何节点就绪 |
| `CREATING` / `PENDING` / `DELETING` | 过渡态 |

`replica.status` 只有 `READY` / `FAILED` 两种（更细的进度在 `replica.phase`）。

---

## 3. 分阶段应看到的数据

### 阶段 1：提交后立刻

```sql
SELECT job_id, template_id, status, phase, attempt_no, operation,
       template_spec_fingerprint, artifact_id, request_json IS NOT NULL AS has_req
FROM t_cube_template_image_job ORDER BY id DESC LIMIT 1\G
```

| 字段 | 期望 | 说明 |
|---|---|---|
| `status` | `PENDING` | 接口是异步的，不该已是 RUNNING |
| `template_id` | 非空 `tpl-…` | 提交时即分配 |
| `template_spec_fingerprint` | 非空 | 产物去重的键 |
| `artifact_id` | **空** | 尚未产出 |
| `attempt_no` | `1` | redo 才会递增 |
| `operation` | `CREATE` | |

此时其余 4 张表**都应无本模板的行** —— 提交路径只写 job 表。

### 阶段 2：构建中

```sql
SELECT status, phase, progress, pull_total_bytes, pull_downloaded_bytes,
       pull_total_layers, pull_completed_layers, pull_speed_bps
FROM t_cube_template_image_job WHERE job_id='<JOB>'\G
```

- `status=RUNNING`，`phase` 依次经过 `PULLING` → `BUILDING_EXT4`
- 5 个 `pull_*` 字段**必须是整数**（JSON 数字解码为 float64，回调里有专门的 int64 归一化，这里曾出过问题）
- `progress` 单调不减

### 阶段 3：BUILT（仅 remote）

```sql
SELECT status, phase, artifact_id, artifact_status,
       JSON_EXTRACT(result_json,'$.status') AS rj_status,
       JSON_EXTRACT(result_json,'$.ext4_sha256') AS rj_sha
FROM t_cube_template_image_job WHERE job_id='<JOB>'\G
```

`result_json` 此刻保存的是 TC 回传的 BUILT 原文（含 `ext4_sha256` / `ext4_size_bytes` / `image_config_json`）。

**这是判断构建是否真的在 TC 侧执行的唯一持久证据。** 但它会被 finalize 覆盖 —— 见阶段 5。

### 阶段 4：分发中

```sql
SELECT node_id, node_ip, status, phase, artifact_id, last_job_id,
       last_error_phase, cleanup_required, LEFT(error_message,80) AS err
FROM t_cube_template_replica WHERE template_id='<TPL>' ORDER BY node_id\G
```

每个目标节点一行，**成功失败都写**。此时 `t_cube_artifact_node_placement` 应已有对应行。

### 阶段 5：终态

```sql
SELECT j.status, j.phase, j.expected_node_count, j.ready_node_count, j.failed_node_count,
       j.template_status, j.artifact_status,
       JSON_EXTRACT(j.result_json,'$.remote_build_report.ext4_sha256') AS tc_sha,
       a.status AS art_status, a.ext4_size_bytes, a.download_token IS NOT NULL AS has_token,
       a.cube_egress_ca_baked,
       d.status AS def_status, d.display_name, d.rootfs_artifact_id
FROM t_cube_template_image_job j
LEFT JOIN t_cube_rootfs_artifact a ON a.artifact_id = j.artifact_id
LEFT JOIN t_cube_template_definition d ON d.template_id = j.template_id
WHERE j.job_id='<JOB>'\G
```

`result_json` 已被 finalize 的模板载荷覆盖，但 TC 的 BUILT 报告被折叠保留在 **`remote_build_report`** 子键下 —— 所以 `tc_sha` 非空即证明这次是 remote 构建（`NULL` 不代表 local，只代表无证据）。

---

## 4. 终态矩阵

| 场景 | job.status | expected / ready | definition | replica 行 | display_name |
|---|---|---|---|---|---|
| 单节点成功 | `READY` | 1 / 1 | `READY` | 1 × READY | 已写 |
| 多节点全成功 | `READY` | 3 / 3 | `READY` | 3 × READY | 已写 |
| 部分成功 | `READY` | 3 / 1 | `PARTIALLY_READY` | 1 READY + 2 FAILED | 已写 |
| **全节点失败** | `FAILED` | 3 / 0 | **无行** | **无行** | **空** |
| **未找到节点** | `FAILED` | **0 / 0** | **无行** | **无行** | **空** |

后两行是刻意设计，不是 bug：

- 分发零成功时，代码在写 definition/replica **之前**就返回了，所以那两张表**本就该是空的**。诊断信息在 `job.error_message`。
- 别名仅在状态非 FAILED 时才领取，所以失败的模板**按别名查必然 404**。
- `expected=0` 与 `expected≥1` 要分开看：前者是压根没找到节点（instance_type 无健康节点 / `distribution_scope` 匹配不到），后者是节点拉取失败。

单节点是**合法**部署形态，没有最小节点数限制；1/1 就是成功。

---

## 5. 跨表一致性检查

这几条比看单表更有价值 —— 单表各自正常但对不上，说明某个环节漏写了。

```sql
-- 1) job 指向的产物必须存在
SELECT j.job_id, j.artifact_id FROM t_cube_template_image_job j
LEFT JOIN t_cube_rootfs_artifact a ON a.artifact_id=j.artifact_id
WHERE j.artifact_id<>'' AND a.artifact_id IS NULL;

-- 2) definition 与 job 必须指向同一产物
SELECT d.template_id, d.rootfs_artifact_id, j.artifact_id
FROM t_cube_template_definition d JOIN t_cube_template_image_job j USING(template_id)
WHERE d.rootfs_artifact_id <> j.artifact_id AND j.status='READY';

-- 3) ready_node_count 必须等于 READY 副本数
SELECT j.job_id, j.ready_node_count, COUNT(r.id) AS actual
FROM t_cube_template_image_job j
LEFT JOIN t_cube_template_replica r
  ON r.template_id=j.template_id AND r.status='READY' AND r.deleted_at IS NULL
WHERE j.status='READY' GROUP BY j.job_id, j.ready_node_count
HAVING j.ready_node_count <> actual;

-- 4) 相同指纹必须复用同一产物（去重生效）
SELECT template_spec_fingerprint, COUNT(DISTINCT artifact_id) AS n
FROM t_cube_rootfs_artifact WHERE status='READY'
GROUP BY template_spec_fingerprint HAVING n > 1;

-- 5) READY 的产物必须有下载令牌，否则 Cubelet 无法拉取
SELECT artifact_id FROM t_cube_rootfs_artifact
WHERE status='READY' AND (download_token IS NULL OR download_token='');

-- 6) 删除后的残留（API 层看不见）
SELECT 'definition' t, COUNT(*) n FROM t_cube_template_definition
  WHERE template_id='<TPL>' AND deleted_at IS NULL
UNION ALL SELECT 'replica', COUNT(*) FROM t_cube_template_replica
  WHERE template_id='<TPL>' AND deleted_at IS NULL;
```

1–5 应全部**返回 0 行**。第 6 条删除后应全为 0。

> `t_cube_artifact_node_placement` 有残留是**正常的** —— 它就是为了在副本行消失后还能定位节点做物理清理。

---

## 6. 故障特征对照

| 观察到的数据 | 结论 |
|---|---|
| `expected=0, ready=0` | 没有匹配 instance_type 的健康节点，或 `distribution_scope` 匹配不到 |
| `expected≥1, ready=0`，`error_message` 含 Cubelet 报错 | 产物已推到节点但拉取/启动失败 → 常见于下载基址不可达 |
| `phase=CREATING_TEMPLATE` + shim 超时 | 分发成功，卡在从模板启真实沙箱（containerd 层） |
| `status=BUILT` 停留 >15min | resume 未跑或挂了；reconciler 会重放 |
| `artifact.status=READY` 但 `ext4_size_bytes=0` | 产物注册了但元数据没回填 |
| 两次相同 spec 得到不同 `artifact_id` | 指纹计算不一致（检查 CA 指纹是否轮换） |
| `definition` 有行但 `replica` 为 0 | 异常组合：正常流程二者同时写 |
| `display_name` 为空但 `status=READY` | 别名领取失败，查 job 的告警 |

---

## 7. 一键巡检

```bash
$MYSQL <<'SQL'
SELECT '=== 最近 5 个任务 ===' AS '';
SELECT job_id, status, phase, progress,
       expected_node_count exp, ready_node_count rdy, failed_node_count fail,
       LEFT(error_message,60) err, created_at
FROM t_cube_template_image_job ORDER BY id DESC LIMIT 5;

SELECT '=== 产物 ===' AS '';
SELECT artifact_id, status, ext4_size_bytes, cube_egress_ca_baked,
       download_token<>'' has_token, LEFT(template_spec_fingerprint,16) fp
FROM t_cube_rootfs_artifact ORDER BY id DESC LIMIT 5;

SELECT '=== 副本分布 ===' AS '';
SELECT template_id, node_id, status, phase, LEFT(error_message,50) err
FROM t_cube_template_replica WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 10;

SELECT '=== 一致性（应全为 0） ===' AS '';
SELECT 'artifact 悬空' k, COUNT(*) v FROM t_cube_template_image_job j
  LEFT JOIN t_cube_rootfs_artifact a ON a.artifact_id=j.artifact_id
  WHERE j.artifact_id<>'' AND a.artifact_id IS NULL
UNION ALL SELECT 'READY 无令牌', COUNT(*) FROM t_cube_rootfs_artifact
  WHERE status='READY' AND (download_token IS NULL OR download_token='')
UNION ALL SELECT '指纹重复产物', COUNT(*) FROM (
  SELECT template_spec_fingerprint FROM t_cube_rootfs_artifact WHERE status='READY'
  GROUP BY template_spec_fingerprint HAVING COUNT(DISTINCT artifact_id)>1) x;
SQL
```

---

## 8. 自动化对比

上面所有检查已内建在验证脚本里，它会记录每张表的 `SELECT *` 原始行并跨链路逐字段比对：

```bash
./scripts/verify_templatecenter.py run --link master-local  --force-build --out /tmp/local.json
./scripts/verify_templatecenter.py run --link master-remote --force-build --compare /tmp/local.json

# 只看某阶段的原始数据
./scripts/verify_templatecenter.py show /tmp/local.json --op create.final_db --full
```

用 `SELECT *` 而非固定列清单是刻意的：**拆分后某列不再写入**正是最该被发现的回归，手写投影会把它藏起来。
