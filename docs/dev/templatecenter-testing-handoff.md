# Template Center 双向通道验证

TC 与 CubeMaster 之间只有两条 HTTP 通道。这份文档只覆盖怎么验证它们通不通。

| 方向 | 端点 | 作用 |
|---|---|---|
| CubeMaster → TC | `POST {tc}/tc/api/v1/build` | 下发构建任务 |
| TC → CubeMaster | `POST {master}/internal/template/jobs/{job_id}/status` | 回传进度与终态 |

> 回调挂在**根路径**，不在 `/cube` 下。

```bash
export MASTER=http://127.0.0.1:8089
export TC=http://127.0.0.1:8090
export MYSQL="docker exec -i cube-mysql mysql -ucube -pcube_pass cube_mvp"
```

---

## 1. 前置检查

```bash
curl -s $TC/health | jq .
curl -s $MASTER/notify/health | jq .

grep -E "template_build_mode|template_center_endpoint" \
  /usr/local/services/cubetoolbox/CubeMaster/conf.yaml
```

两个开关必须**同时**配好，只配一边不会报错、行为也不变：

| 开关 | 位置 | 作用 |
|---|---|---|
| `template_build_mode: remote` | CubeMaster conf | 构建交给 TC |
| `template_center_endpoint` | CubeMaster conf | TC 地址 |

TC 的 `/health` 里 `checks` 能反推开关：只有 `templatecenter_store` = `SERVE_TEMPLATE_API=false`（默认，正确）；多出 `nodemeta` 说明开关开了。

环境变量已统一为 `CUBE_TEMPLATE_CENTER_*`，旧名仍兼容但会告警：

```bash
journalctl -u cube-sandbox-cubetemplatecenter.service \
  | grep -iE "deprecated|disagree|node identity"
```

`disagree` 必须处理 —— 说明产物目录配置有分歧，会导致下载 404。

---

## 2. 正向通道

`job_id` 与 `request` 为必填。

### 2.1 契约校验（不触发构建，安全）

```bash
# 缺 job_id
curl -s -o /dev/null -w "%{http_code}\n" -X POST $TC/tc/api/v1/build \
  -H 'Content-Type: application/json' -d '{"request":{"source_image_ref":"x"}}'

# 缺 request
curl -s -o /dev/null -w "%{http_code}\n" -X POST $TC/tc/api/v1/build \
  -H 'Content-Type: application/json' -d '{"job_id":"probe-1"}'
```

都应为 `400`。返回 `404` 说明路由没挂上。

### 2.2 真实提交

会跑真实构建，且 `job_id` 在库里不存在 —— 正好顺带看 CubeMaster 如何处理未知 job 的回调。

```bash
JOB="probe-$(date +%s)"
curl -s -X POST $TC/tc/api/v1/build -H 'Content-Type: application/json' -d @- <<EOF | jq .
{
  "job_id": "$JOB",
  "request": {
    "requestID": "$JOB",
    "source_image_ref": "cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest",
    "instance_type": "cubebox",
    "writable_layer_size": "1G",
    "exposed_ports": [80]
  },
  "download_base_url": "$MASTER"
}
EOF
```

期望 `{"status":"accepted","job_id":"probe-..."}`。

---

## 3. 反向通道

### 3.1 白名单校验

回调是内部端点、信任载荷，白名单是唯一的写入边界，所以这条最值得测。

```bash
# 全是白名单外字段 → 400 "no updatable fields in payload"
curl -s -X POST $MASTER/internal/template/jobs/probe-x/status \
  -H 'Content-Type: application/json' -d '{"totally_unknown":1}' | jq .
```

### 3.2 进度回调

需要一个真实 `job_id`。

```bash
REAL_JOB=<真实 job_id>

curl -s -X POST $MASTER/internal/template/jobs/$REAL_JOB/status \
  -H 'Content-Type: application/json' -d '{
    "status":"RUNNING","phase":"PULLING","progress":42,
    "pull_total_bytes":1000,"pull_downloaded_bytes":420,
    "pull_speed_bps":100
  }' | jq .

$MYSQL -e "SELECT job_id,status,phase,progress,pull_downloaded_bytes,pull_speed_bps
FROM t_cube_template_image_job WHERE job_id='$REAL_JOB'\G"
```

重点确认那几个 pull 字段**以整数落库**（JSON 数字解码为 float64，此处有专门的 int64 归一化）。

> **不要**手工发 `status=BUILT`：会触发 `ResumeTemplateImageJobAfterRemoteBuild`，走真实的产物注册与跨节点分发。

---

## 4. 完整闭环

```bash
./scripts/verify_templatecenter.py run --link master-local  --force-build --out /tmp/local.json
./scripts/verify_templatecenter.py run --link master-remote --force-build --compare /tmp/local.json
```

`--force-build` **必须加**。它给创建请求加一个本次运行独有的标记端口，而端口属于产物指纹的一部分，因此能绕开产物复用。不加的话 TC 会直接命中已有产物、pull 与 mkfs 一次都不执行，等于在比 local vs local。

| 记录 | 期望 |
|---|---|
| `create.build_provenance` | `confirmed` |
| `create.poll` timeline | remote 出现 `BUILT/*` |
| `cleanup.leak_check` | `net_template_delta = 0` |

`build_provenance` 是三值的（`confirmed` / `overwritten` / `unknown`）：BUILT 只能靠轮询观测，而复用产物时一秒内就过去了，所以**没观测到不等于没走 TC**。

---

## 5. 日志证据

```bash
# 正向
journalctl -u cube-sandbox-cubemaster.service | grep "submit build job to TC"
grep "received build job" /data/log/CubeTemplateCenter/templatecenter-req.log

# 反向
grep "report status to master" /data/log/CubeTemplateCenter/templatecenter-req.log
journalctl -u cube-sandbox-cubemaster.service \
  | grep -E "status callback applied|resume step|distribute artifact"
```

`distribute artifact: expected=N ready=M failed=K` 一行即可判断分发结果。若 `ready=0`，看 job 行上的原因：

```bash
$MYSQL -e "SELECT job_id,status,phase,expected_node_count,ready_node_count,
failed_node_count,error_message FROM t_cube_template_image_job
ORDER BY created_at DESC LIMIT 5\G"
```

`expected_node_count=0` 表示压根没找到节点；`≥1` 且 `ready=0` 表示节点拉取失败。

---

## 6. 已知坑：多节点下载基址

产物下载基址取自 CubeMaster 的 HTTP `Host` 头，会原样下发给远端 Cubelet：

| 部署 | 用 `localhost` 访问的结果 |
|---|---|
| 单节点 | Cubelet 同机 → 能下载，**掩盖问题** |
| 多节点 | `localhost` 指向 Cubelet 自己 → 下载失败 → `ready=0` |

多节点验证时用**节点可达的内网 IP** 访问 CubeMaster，不要用 loopback。
