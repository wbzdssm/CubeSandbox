# Template Center 总测试计划

> **执行者须知**：本文档面向自动化执行。每一步都给出确切命令、确切判定标准、失败后的动作。
> 遇到 `⛔ STOP` 时**必须停止后续阶段**并报告 —— 后面的阶段依赖前面的结论，跳过会得出错误判断。
> 第 0 节列出的**已知问题不要作为新缺陷上报**。

## 被测形态

TC 从 CubeMaster 拆分后共 5 种运行形态，全部应表现一致：

| # | 形态 | 入口 | 构建执行方 | 开关 |
|---|---|---|---|---|
| A | `master-local` | CubeMaster | CubeMaster 进程内 | 默认（基准线） |
| B | `master-remote` | CubeMaster | **TC** | `template_build_mode: remote` |
| C | `master-proxy` | CubeMaster→透传 | TC | `template_route_mode: proxy` |
| D | `tc` | **直连 TC** | TC | `CUBE_TEMPLATE_CENTER_SERVE_TEMPLATE_API=true` |
| E | `sdk-*` | Python SDK→CubeAPI | 随 B/A | — |

**A 是基准线**：所有其他形态都与 A 逐字段比对。A 不通过则一切结论无效。

## 配套文档

| 文档 | 用途 |
|---|---|
`templatecenter-issue-1280-mapping.md` | **#1280 对照表：哪些在本次范围内，哪些不是** |
`templatecenter-testing-handoff.md` | 双向通道单独验证（curl 级） |
`templatecenter-test-data.md` | 5 张表的期望数据与一致性 SQL |
`templatecenter-testing-guide.md` | 失败场景的手工构造方法 |

> `#1280` 是索引 14 个子 issue 的汇总 issue，**不是本次的验收清单**。本次只覆盖 `#957`
> 的已完成部分；其余 11 项未触及，不要作为回归上报。详见对照表。

---

## 0. 已知问题（不要上报为新缺陷）

| # | 现象 | 性质 |
|---|---|---|
| K1 | `failed to create shim task: ttrpc err: Receive packet timeout` | CubeShim 硬编码 10s 超时（`shim/src/container/mod.rs:107`），254 机器 cube-agent 响应慢。**非本次改动引入**，无配置项可调 |
| K2 | 多节点下 `ready=0`，Cubelet 拉取失败 | 下载基址取自 HTTP `Host` 头。用 `localhost` 访问 CubeMaster 时会原样下发给远端节点。**验证时必须用节点可达的内网 IP** |
| K3 | `GET /cube/template/from-image` 不带 `job_id` 返回 400 | 该接口无列表模式，与 `/cube/template` 不对称。待评审 |
| K4 | 删除后按 id/别名查均 404，无 `DELETING` 中间态 | 物理删除，设计如此 |
| K5 | `PARTIALLY_READY` 不会自愈 | 失败副本无自动重试，需人工 `redo --failed-only` |
| K6 | `test_package_layout.sh` 有 3 条 cube-proxy 相关失败 | 预存问题，与 TC 无关 |
| K7 | 失败模板的 `definition`/`replica` 表无行、别名查 404 | 设计如此，详见 test-data 文档第 4 节 |
| K8 | 删除 pending/building 模板返回 409 | issue #66，强制清理机制未实现；reconciler 2h 后置 FAILED 才可删 |
| K9 | 私有 HTTP（insecure）registry 拉取失败 | issue #870。注意失败点已随构建迁移到 **TC 日志** |
| K10 | 快照来源的模板 `redo` 失败，报 `no source_image_ref` | issue #1159，未解决但已止损（不再破坏产物） |

---

## 1. 阶段 1：环境就绪（⛔ 阻塞门）

```bash
export MASTER=http://127.0.0.1:8089      # 多节点改为内网 IP，见 K2
export TC=http://127.0.0.1:8090
export MYSQL="docker exec -i cube-mysql mysql -ucube -pcube_pass cube_mvp"

curl -sf $MASTER/notify/health >/dev/null && echo "master OK"   || echo "master DOWN"
curl -sf $TC/health           >/dev/null && echo "tc OK"        || echo "tc DOWN"
$MYSQL -e "SELECT 1" >/dev/null 2>&1     && echo "db OK"        || echo "db DOWN"
```

**判定**：三项全 OK。

**失败动作**：任一 DOWN → ⛔ STOP，报告哪项不可达。

### 1.2 构建工具链（TC 侧必须齐全）

```bash
for t in skopeo umoci mkfs.ext4; do command -v $t >/dev/null && echo "$t OK" || echo "$t MISSING"; done
```

**判定**：三项全 OK。缺任一项 → ⛔ STOP。缺工具会表现为构建失败，但错误信息不提真正原因。

### 1.3 配置与告警

```bash
grep -E "template_build_mode|template_center_endpoint|template_route_mode" \
  /usr/local/services/cubetoolbox/CubeMaster/conf.yaml

journalctl -u cube-sandbox-cubetemplatecenter.service --since "-1h" \
  | grep -iE "deprecated|disagree"
```

**判定**：
- `deprecated` 告警 → 记录但**不阻塞**（旧环境变量名仍兼容）
- **`disagree` 告警 → ⛔ STOP**。说明产物目录配置有分歧，TC 写一处、CubeMaster 从另一处提供下载，会导致所有下载 404

---

## 2. 阶段 2：基准线 A（⛔ 阻塞门）

```bash
TARGET=master BUILD_MODE=local ./scripts/test-templatecenter.sh 2>&1 | tee /tmp/A.log
```

**判定**：全部用例通过。

**失败动作**：⛔ STOP。基准线不通过时，后续形态的差异无法归因 —— 无法区分是拆分引入的问题还是本来就坏的。

**例外**：若失败原因匹配 K1（shim 超时），标记为 `ENV-BLOCKED` 并继续 —— 这是机器环境问题，但要注意此时**阶段 4 之后的数据不可信**（副本创建这一步没跑成功过）。

---

## 3. 阶段 3：双向通道（形态 B 的前提）

详细步骤见 `templatecenter-testing-handoff.md`。此处只做最小连通性判定。

### 3.1 正向：CubeMaster → TC

```bash
# 契约校验，不触发构建
for body in '{"request":{"source_image_ref":"x"}}' '{"job_id":"probe-1"}'; do
  curl -s -o /dev/null -w "%{http_code} " -X POST $TC/tc/api/v1/build \
    -H 'Content-Type: application/json' -d "$body"
done; echo
```

**判定**：输出 `400 400`。若为 `404 404` → 路由未挂载，TC 版本不对 → ⛔ STOP。

### 3.2 反向：TC → CubeMaster

```bash
curl -s -X POST $MASTER/internal/template/jobs/probe-x/status \
  -H 'Content-Type: application/json' -d '{"totally_unknown":1}'
```

**判定**：返回 400，消息含 `no updatable fields`。这证明写入白名单在生效（回调是内部端点、信任载荷，白名单是唯一的写入边界）。

---

## 4. 阶段 4：形态 B/C/D 逐一对比

### 4.1 shell 用例

```bash
TARGET=master BUILD_MODE=remote ./scripts/test-templatecenter.sh 2>&1 | tee /tmp/B.log   # 形态 B
TARGET=tc                       ./scripts/test-templatecenter.sh 2>&1 | tee /tmp/D.log   # 形态 D
```

形态 C（proxy）用形态 A 的命令即可 —— 请求打 CubeMaster、由它透传给 TC，对调用方完全透明。

**判定**：与 `/tmp/A.log` 通过的用例集合相同。

### 4.2 双链路逐字段比对（本阶段核心）

```bash
./scripts/verify_templatecenter.py run --link master-local  --force-build --out /tmp/local.json
./scripts/verify_templatecenter.py run --link master-remote --force-build --compare /tmp/local.json
```

> **`--force-build` 必须加。** 它给创建请求附加一个本次运行独有的标记端口，而端口是产物指纹的组成部分，因此能绕开产物复用。不加的话 TC 直接命中已有产物、pull 与 mkfs 一次都不执行 —— 那是在比 local vs local，什么都证明不了。

**判定标准**（退出码 0，且以下三条成立）：

| 记录 | 期望值 | 不满足的含义 |
|---|---|---|
| `create.build_provenance` | `confirmed` | 无法证明构建发生在 TC 侧（见下方说明） |
| `create.poll` timeline | remote 含 `BUILT/*` | — |
| `cleanup.leak_check` | `net_template_delta = 0` | 本次运行泄漏了模板 |

`build_provenance` 是**三值**的：`confirmed` / `overwritten` / `unknown`。BUILT 只能靠轮询观测，而复用产物时一秒内就过去了 —— 所以 **`unknown` 不等于走了 local，只代表无证据**。此时改用日志确认（阶段 6）。

其余形态：

```bash
./scripts/verify_templatecenter.py run --link master-proxy --force-build --compare /tmp/local.json
./scripts/verify_templatecenter.py run --link tc          --force-build --compare /tmp/local.json
```

### 4.3 SDK 链路（形态 E）

```bash
./scripts/verify_templatecenter.py run --link sdk-local  --force-build --out /tmp/sdk.json
./scripts/verify_templatecenter.py run --link sdk-remote --force-build --compare /tmp/sdk.json
./scripts/verify_templatecenter.py compare /tmp/local.json /tmp/sdk.json   # 跨方言
```

跨方言比对（SDK vs `/cube/*`）只比语义结论、DB 落库与提取事实 —— `/cube/*` 用 ret 信封、CubeAPI 用 HTTP 状态码，响应体本身不可比。

---

## 5. 阶段 5：数据一致性

判定标准与完整 SQL 见 `templatecenter-test-data.md` 第 5 节。最小集：

```bash
$MYSQL <<'SQL'
SELECT 'artifact 悬空' k, COUNT(*) v FROM t_cube_template_image_job j
  LEFT JOIN t_cube_rootfs_artifact a ON a.artifact_id=j.artifact_id
  WHERE j.artifact_id<>'' AND a.artifact_id IS NULL
UNION ALL SELECT 'READY 无下载令牌', COUNT(*) FROM t_cube_rootfs_artifact
  WHERE status='READY' AND (download_token IS NULL OR download_token='')
UNION ALL SELECT '同指纹多产物', COUNT(*) FROM (
  SELECT template_spec_fingerprint FROM t_cube_rootfs_artifact WHERE status='READY'
  GROUP BY template_spec_fingerprint HAVING COUNT(DISTINCT artifact_id)>1) x;
SQL
```

**判定**：三项全为 `0`。

第 3 项（同指纹多产物）尤其关键：它证明去重逻辑在 local 与 remote 两条路径上算出了**相同的指纹** —— 指纹算法是共享代码而非各自实现，这条不为 0 说明共享失效了。

---

## 6. 阶段 6：日志证据

```bash
# 正向
journalctl -u cube-sandbox-cubemaster.service | grep "submit build job to TC"
grep "received build job" /data/log/CubeTemplateCenter/templatecenter-req.log

# 反向 + resume
grep "report status to master" /data/log/CubeTemplateCenter/templatecenter-req.log
journalctl -u cube-sandbox-cubemaster.service \
  | grep -E "resume step|distribute artifact|status callback applied"
```

**判定**：四组都有输出，且 `resume step` 出现 `1/3` `2/3` `3/3`。

失败时看这一行定位环节：

```
distribute artifact: expected=N ready=M failed=K err=...
```

| 观察 | 结论 |
|---|---|
| `expected=0 ready=0` | 没找到匹配 instance_type 的健康节点 |
| `expected≥1 ready=0` | 产物已推送但节点拉取失败 → 优先怀疑 K2 |
| `ready≥1` 但卡 `CREATING_TEMPLATE` | 分发成功，卡在从模板启沙箱 → K1 |

---

## 7. 阶段 7：单元测试

```bash
make cubetemplatecenter-test     # TC，含 tcconfig 43 项
make cubemaster-test             # CubeMaster
python3 scripts/vfy_selftest.py  # 验证工具自身 79 项
```

**判定**：全部通过。

`vfy_selftest.py` 必须跑 —— 它是唯一能发现「对比引擎本身失效」的手段。一个永远报告"一致"的比对工具比没有工具更糟。

---

## 8. 结果模板

```
## 环境
机器: ______   MASTER: ______   多节点: 是/否

## 阶段结论
| 阶段 | 结果 | 备注 |
|---|---|---|
| 1 环境就绪 | PASS/FAIL | |
| 2 基准线 A | PASS/FAIL/ENV-BLOCKED | |
| 3 双向通道 | PASS/FAIL | |
| 4 形态对比 B/C/D/E | PASS/FAIL | 差异条数: __ |
| 5 数据一致性 | PASS/FAIL | |
| 6 日志证据 | PASS/FAIL | |
| 7 单元测试 | PASS/FAIL | |

## build_provenance
confirmed / overwritten / unknown

## 新发现问题（排除第 0 节 K1-K10 与 #1280 对照表中未触及项）
1. 现象 / 复现命令 / 原始输出

## 未覆盖
（例：单节点环境，K2 未验证）
```

---

## 9. 判定原则

1. **A 是唯一基准线**。其他形态只与 A 比，不互相比。
2. **一次运行不产生结论**，结论来自两次运行的比对。单次运行只能"没跑通"，不能"失败"。
3. **不加 `--force-build` 的 remote 结果不作为证据**。
4. **两次都在同一点 FAILED 时，其后的步骤未被验证** —— 此时干净的 diff 是窄证据，不能推广。
5. **产物被复用时，pull + mkfs 路径未被验证**。
6. 上报前先对照第 0 节。
