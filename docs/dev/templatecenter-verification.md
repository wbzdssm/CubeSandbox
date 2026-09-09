# CubeTemplateCenter PR #1659 修复验证方案

> 适用分支：`feat/templatecenter-v3`（含 `cbb38849` + `02c10782`）
> 适用环境：Kubernetes 部署（namespace `cube-system`），master/TC 已按 chart 部署
> 日期：2026-09-08

## 一、改动点与验证场景映射

| # | 改动 | 关键文件 | 验证场景 |
|---|------|---------|---------|
| 1 | terraform/打包 TC 支持：build_images 第 7 镜像、create.sh 三处接线、hostPath 共享存储 + podAffinity + precondition、包内 Dockerfile | `deploy/one-click/terraform/tencentcloud/*`、`deploy/one-click/CubeTemplateCenter/Dockerfile` | 静态验证（§四） |
| 2 | Master→TC 内部 API 共享密钥认证（`/tc/api/v1/build`、`/tc/api/v1/artifact/delete`） | `CubeTemplateCenter/pkg/api/internal.go`、`CubeMaster/pkg/tcclient/client.go` | S2 |
| 3 | build payload 与 request_json 快照强绑定（不一致 → 400）；Master 转发规范化请求 | `CubeTemplateCenter/pkg/build/executor.go`、`CubeMaster/pkg/templatecenter/template_image.go` | S3 |
| 4 | S3 URL 使用点重签（分发 / 生成创建请求 / 下载 302 重定向） | `CubeMaster/pkg/templatecenter/artifact_url_refresh.go` | S4 |
| 5 | S3-backed artifact 不再被本地 ext4 存在性门控 | `CubeMaster/pkg/templatecenter/artifact_presence.go` | S5 |
| 6 | backfill 重扫：READY 模板自动补发到缺失副本的健康节点 | `CubeMaster/pkg/templatecenter/replica_backfill_reconciler.go` | S6、S9 |
| 7 | 孤儿副本清理重扫：`cleanup_required=1` 残留行按 NodeID 重解析 IP 后重试 | `CubeMaster/pkg/templatecenter/replica_cleanup_reconciler.go` | S7 |
| 8 | redo template_id 不变（机制确认，作为 S4/S5 触发器） | `CubeMaster/pkg/templatecenter/redo.go` | S8 |
| 9 | CI 接入：CODEOWNERS、build-check、unit-test-check | `.github/` | push 后看 PR checks |

## 二、环境准备

```bash
NS=cube-system
# 确认组件都在
kubectl -n $NS get deploy,pods -o wide

# 端口转发（另开终端保持）
kubectl -n $NS port-forward deploy/cube-master 8089:8089 &
kubectl -n $NS port-forward deploy/cube-templatecenter 18090:8090 &

MASTER=http://127.0.0.1:8089
TC=http://127.0.0.1:18090

# 共享密钥（chart 已下发到两侧）
TOKEN=$(kubectl -n $NS exec deploy/cube-templatecenter -- printenv CUBE_TEMPLATE_CALLBACK_TOKEN)
echo "token len=${#TOKEN}"   # 应 >0

# MySQL 快捷方式
SQL() { kubectl -n $NS exec cube-mysql-0 -- mysql -ucube -p'CubeSandbox123!' cube_mvp -N -e "$1" 2>/dev/null; }

# master 是否拿到 S3 凭据（S4/S5 前置；chart 走 global.env 透传）
kubectl -n $NS exec deploy/cube-master -- sh -c 'env | grep CUBE_S3_' | head -5
```

## 三、验证场景

### S1 基础链路冒烟（所有后续场景的前置）

```bash
# 1. 创建模板
curl -s -X POST $MASTER/cube/template/from-image -H 'Content-Type: application/json' -d '{
  "request_id":"verify-s1-001",
  "source_image_ref":"cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-code:latest",
  "writable_layer_size":"10Gi",
  "instance_type":"cubebox","network_type":"tap"}'
# 预期：返回 job/template 信息，template_id=tpl-xxx

TPL=tpl-xxx   # 替换为实际值

# 2. 观察状态机 PENDING → RUNNING → BUILT → READY
SQL "SELECT job_id,status,phase,progress,error_message FROM t_cube_template_image_job WHERE template_id='$TPL' ORDER BY id DESC LIMIT 1"

# 3. 终态检查
SQL "SELECT template_id,status FROM t_cube_template_definition WHERE template_id='$TPL'"
SQL "SELECT artifact_id,status,artifact_url<>'' AS s3,ext4_size_bytes FROM t_cube_rootfs_artifact ORDER BY id DESC LIMIT 1"
SQL "SELECT node_id,node_ip,status,cleanup_required FROM t_cube_template_replica WHERE template_id='$TPL'"
```

**预期**：definition READY；artifact READY；每个健康节点一行 replica READY 且 `cleanup_required=0`。

### S2 内部接口认证（改动 2）

```bash
# 2.1 无 token → 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST $TC/tc/api/v1/artifact/delete \
  -H 'Content-Type: application/json' -d '{"artifact_id":"rfs-x"}'

# 2.2 错误 token → 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST $TC/tc/api/v1/artifact/delete \
  -H 'Content-Type: application/json' -H "X-Cube-Template-Callback-Token: wrong" \
  -d '{"artifact_id":"rfs-x"}'

# 2.3 正确 token → 过鉴权（不存在的 artifact 返回业务错误，非 401）
curl -s -w '\n%{http_code}\n' -X POST $TC/tc/api/v1/artifact/delete \
  -H 'Content-Type: application/json' -H "X-Cube-Template-Callback-Token: $TOKEN" \
  -d '{"artifact_id":"rfs-not-exist"}'

# 2.4 build 同理：无 token → 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST $TC/tc/api/v1/build \
  -H 'Content-Type: application/json' -d '{"job_id":"j","request":{}}'
```

**预期**：2.1/2.2/2.4 = 401；2.3 ≠ 401（500/200 均可，关键是不被 401 拦截）。master 自动带 token 已由 S1 构建成功覆盖。

### S3 payload 强绑定（改动 3）

```bash
# 3.1 取一个 PENDING/RUNNING 的 job（没有就新建一个大镜像模板，趁 PULLING 期间操作）
JOB=$(SQL "SELECT job_id FROM t_cube_template_image_job WHERE status IN ('PENDING','RUNNING') ORDER BY id DESC LIMIT 1")
REQ=$(SQL "SELECT request_json FROM t_cube_template_image_job WHERE job_id='$JOB'")
echo "$REQ" | python3 -m json.tool

# 3.2 篡改 writable_layer_size → 400 does not match
python3 - "$TOKEN" "$JOB" "$REQ" "$TC" <<'EOF'
import json,sys,urllib.request,urllib.error
token,job,req,tc=sys.argv[1:5]
r=json.loads(req); r["writable_layer_size"]="999Gi"
body=json.dumps({"job_id":job,"request":r}).encode()
h=urllib.request.Request(tc+"/tc/api/v1/build",data=body,method="POST",
    headers={"Content-Type":"application/json","X-Cube-Template-Callback-Token":token})
try:
    print("resp:",urllib.request.urlopen(h).read().decode())
except urllib.error.HTTPError as e:
    print("status:",e.code,"body:",e.read().decode())
EOF
# 预期：status 400，body 含 "does not match the persisted job snapshot"

# 3.3 原样 request_json 重放 → 200 或 409（重复提交），证明合法路径不被误伤
#     把上面脚本中 r["writable_layer_size"]="999Gi" 一行去掉重跑即可
```

### S4 S3 URL 重签（改动 4，需 master 有 CUBE_S3_*）

```bash
# 4.1 当前存储的 URL 及其签名时间
SQL "SELECT artifact_id, artifact_url FROM t_cube_rootfs_artifact WHERE artifact_url<>'' ORDER BY id DESC LIMIT 1"
ART=rfs-xxx   # 替换

# 4.2 模拟"旧 URL"：签名日期改到 8 天前（已过期）
SQL "UPDATE t_cube_rootfs_artifact SET artifact_url=REPLACE(artifact_url, SUBSTRING_INDEX(SUBSTRING_INDEX(artifact_url,'X-Amz-Date=',-1),'&',1), 'X-Amz-Date=20260830T000000Z') WHERE artifact_id='$ART'"

# 4.3 下载重定向应返回**新签名** URL（X-Amz-Date 是当前时间）
TOKEN_DL=$(SQL "SELECT download_token FROM t_cube_rootfs_artifact WHERE artifact_id='$ART'")
curl -s -D- -o /dev/null "$MASTER/cube/template/artifact/download?artifact_id=$ART&token=$TOKEN_DL" | grep -iE 'HTTP/|location'
```

**预期**：302，Location 的 `X-Amz-Date` ≈ 当前时间（而非 20260830）。redo 分发同理走新 URL（见 S8）。

### S5 S3-backed 不被本地文件门控（改动 5）

```bash
# 5.1 在 master pod 内把本地 ext4 移走（S3 模式下它只是缓存）
kubectl -n $NS exec deploy/cube-master -- sh -c "ls /data/CubeMaster/storage/$ART.ext4 && mv /data/CubeMaster/storage/$ART.ext4 /data/CubeMaster/storage/$ART.ext4.bak"

# 5.2 触发 redo → 应成功分发（旧逻辑会 demote READY→FAILED / 报 artifact missing）
curl -s -X POST $MASTER/cube/template/redo -H 'Content-Type: application/json' \
  -d "{\"request_id\":\"verify-s5-001\",\"template_id\":\"$TPL\"}"
SQL "SELECT job_id,status,error_message FROM t_cube_template_image_job WHERE template_id='$TPL' ORDER BY id DESC LIMIT 1"
SQL "SELECT artifact_id,status,last_error FROM t_cube_rootfs_artifact WHERE artifact_id='$ART'"
```

**预期**：job 最终 READY；artifact 行保持 READY 未被 demote。

### S6 backfill 重扫（改动 6）

```bash
# 6.1 模拟某节点缺失：删掉一个节点的 replica 行
#     （更真实的做法：停掉一个 cubelet 使其 NotReady 再恢复，等价于新节点加入）
SQL "DELETE FROM t_cube_template_replica WHERE template_id='$TPL' AND node_id='vm-xxx' LIMIT 1"

# 6.2 等 ≤5 分钟 reconcile tick，观察 master 日志
kubectl -n $NS logs deploy/cube-master --tail=200 | grep -i 'backfill'
# 预期："template replica backfill: N node(s) missing a READY replica, distributed ready=N failed=0"

# 6.3 副本行自动恢复 READY
SQL "SELECT node_id,status,phase FROM t_cube_template_replica WHERE template_id='$TPL'"

# 6.4 该节点上 artifact 文件已就位
# ssh 到节点确认 ext4 存在
```

### S7 孤儿副本清理重扫（改动 7）

```bash
# 7.1 构造残留：为一个已不存在的模板造一行 cleanup_required=1
SQL "INSERT INTO t_cube_template_replica (created_at,updated_at,template_id,node_id,node_ip,instance_type,status,phase,cleanup_required,error_message) VALUES (NOW(),NOW(),'tpl-orphan-test','vm-xxx','10.x.x.x','cubebox','FAILED','FAILED',1,'injected for verify')"

# 7.2 等 ≤5 分钟，观察日志与行去向
kubectl -n $NS logs deploy/cube-master --tail=200 | grep -i 'orphan replica cleanup'
SQL "SELECT * FROM t_cube_template_replica WHERE template_id='tpl-orphan-test'"
```

**预期**：节点可达 → `converged` 日志，行被删除；节点不可达 → warn 且行保留（下轮继续重试）。

### S8 redo 不变性 + 定向补发（改动 8）

```bash
curl -s -X POST $MASTER/cube/template/redo -H 'Content-Type: application/json' \
  -d "{\"request_id\":\"verify-s8-001\",\"template_id\":\"$TPL\",\"distribution_scope\":[\"vm-xxx\"]}"
SQL "SELECT job_id,template_id,attempt_no,operation,status FROM t_cube_template_image_job WHERE template_id='$TPL' ORDER BY id DESC LIMIT 2"
SQL "SELECT COUNT(*) FROM t_cube_template_definition WHERE template_id='$TPL'"
```

**预期**：template_id 不变；operation=REDO；attempt_no 递增；definition 始终 1 行。

### S9 整体业务终局

```bash
# 9.1 用模板创建沙箱（可指定到刚补发的节点）
curl -s -X POST $MASTER/cube/sandbox -H 'Content-Type: application/json' -d '{
  "request_id":"verify-s9-001","instance_type":"cubebox",
  "template_id":"'$TPL'","containers":[{"name":"c0","resources":{"cpu":"500m","mem":"512Mi"}}]}'
# 预期：沙箱 RUNNING；master 日志 getTemplateParam / dealCubeboxCreateReqWithTemplateCenter 成功

# 9.2 清理：删除模板（节点全部可达时应成功）
curl -s -X DELETE "$MASTER/cube/template?template_id=$TPL&instance_type=cubebox"
SQL "SELECT COUNT(*) FROM t_cube_template_definition WHERE template_id='$TPL'"
SQL "SELECT COUNT(*) FROM t_cube_template_replica WHERE template_id='$TPL' AND cleanup_required=0"
```

**预期**：两处 COUNT 均为 0。

## 四、静态 / CI 验证（本地已完成，供复核）

- `terraform -chdir=deploy/one-click/terraform/tencentcloud validate` 通过；`bash -n create.sh build_images.sh build-release-bundle.sh` 通过
- `helm template` 渲染：master.yaml 含 `global.env`（CUBE_S3_*）、`CUBE_TEMPLATE_CENTER_ADDR` + callback token；templatecenter.yaml 只受 `controlPlane.enabled` 控制
- `deploy/one-click/tests/test_package_layout.sh`：7 镜像契约通过（剩余 5 个 cube-proxy nginx 失败为 HEAD 预存在问题，与本 PR 无关）
- 单元测试：`TestSharedTokenHeader`、`TestInternalAPI*`、`TestVerifyRequestMatchesSnapshot`、`TestArtifactDownloadURL*`、`TestS3ArtifactObjectKeyMatchesS3Store`、`TestNodesWithoutReadyReplica*`、`TestReconcileOrphanReplicaCleanups*` 全部通过
- push 后检查 PR #1659：build-check / unit-test-check 出现 CubeTemplateCenter 矩阵项

## 五、建议执行顺序与依赖

```
S1（冒烟） → S2（认证） → S3（绑定） → S8（redo 不变性）
   → S4 / S5（S3 配置下；无 S3 可跳过，行为自动回退本地盘模式）
   → S6（backfill） → S7（孤儿清理） → S9（终局 + 清理）
```

**依赖说明**：

- S4/S5 需要 master pod 有 `CUBE_S3_*` 环境变量（chart 通过 `global.env` 透传；one-click 全 unit 共享 `.one-click.env`）。
- S3 需要 job 处于 PENDING/RUNNING；若环境构建太快，可用大镜像制造 PULLING 窗口。
- S6/S7 的重扫周期为 5 分钟（image job reconciler tick），验证时注意等待。
- 删除路径当前为严格模式：节点不可达时 `DeleteTemplate` 会报 130593（本 PR 未改变该行为；孤儿重扫只负责收敛已产生的 `cleanup_required` 残留行）。
