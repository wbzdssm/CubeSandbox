# TemplateCenter API 快速验证手册

**前置**：TC 已启动（`./build/templatecenter -conf=conf.yaml`），监听 `:8090`。

```bash
# 通用变量 (改这里)
export TC=http://localhost:8090
```

## 一、健康与探针

### 1.1 启动探针

```bash
curl -sf $TC/health | jq .
```

**200 OK 响应**（ready）：
```json
{
  "status": "ok",
  "checks": {
    "nodemeta": true,
    "templatecenter_store": true
  }
}
```

**503 响应**（not ready）：
```json
{
  "status": "not_ready",
  "checks": {
    "nodemeta": false,
    "templatecenter_store": true
  }
}
```

### 1.2 Metrics

```bash
curl -s $TC/metrics | grep template_ | head -20
```

## 二、核心流程：from-image 创建模板

### 2.1 POST /cube/template/from-image — 提交构建任务

**完整参数**（`CreateTemplateFromImageReq`，types.go:634-670）：

```json
{
  "requestID": "req-test-001",
  "source_image_ref": "docker.io/library/nginx:latest",
  "alias": "my-nginx",
  "instance_type": "S5.MEDIUM2",
  "network_type": "vpc",
  "writable_layer_size": "1Gi",
  "registry_username": "",
  "registry_password": "",
  "exposed_ports": [80, 443],
  "distribution_scope": ["host-001", "host-002"],
  "container_overrides": {
    "cmd": ["nginx", "-g", "daemon off;"],
    "env": {"NGINX_HOST": "example.com"},
    "workdir": "/usr/share/nginx/html"
  },
  "wait": false,
  "with_cube_ca": true,
  "enable_ivshmem": false
}
```

**最小可用请求**（90% 场景）：

```bash
curl -X POST $TC/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "req-001",
    "source_image_ref": "nginx:latest",
    "instance_type": "S5.MEDIUM2"
  }' | jq .
```

**响应**（200 OK）：
```json
{
  "requestID": "req-001",
  "ret": {
    "ret_code": 0,
    "ret_msg": ""
  },
  "job": {
    "job_id": "job-a1b2c3d4",
    "template_id": "tpl-xxx",
    "artifact_id": "art-yyy",
    "status": "Pending",
    "phase": "Queued",
    "progress": 0
  }
}
```

**关键字段**：
- `job_id` — 用于轮询进度
- `artifact_id` — ext4 产物标识（同 artifact 命中不重复构建）
- `template_id` — 模板最终 ID（def_READY 后可被 sandbox 引用）

**`wait=true` 同步等待**（适合脚本串行验证）：
```bash
curl -X POST $TC/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "req-002",
    "source_image_ref": "nginx:latest",
    "instance_type": "S5.MEDIUM2",
    "wait": true
  }' | jq .
# 阻塞直到 job 完成 (Ready 或 Failed)
```

### 2.2 GET /cube/template/build/{build_id}/status — 轮询进度

```bash
JOB_ID="job-a1b2c3d4"   # 从上一步 job_id 拿

curl -s $TC/cube/template/build/$JOB_ID/status | jq .
```

**响应**（运行中）：
```json
{
  "job": {
    "job_id": "job-a1b2c3d4",
    "status": "Running",
    "phase": "BUILDING_EXT4",
    "progress": 45,
    "pull_total_bytes": 52428800,
    "pull_downloaded_bytes": 52428800,
    "pull_total_layers": 5,
    "pull_completed_layers": 5,
    "pull_speed_bps": 0,
    "expected_node_count": 2,
    "ready_node_count": 0,
    "failed_node_count": 0
  }
}
```

**响应**（完成）：
```json
{
  "job": {
    "job_id": "job-a1b2c3d4",
    "status": "Ready",
    "phase": "Ready",
    "progress": 100,
    "artifact": {
      "artifact_id": "art-yyy",
      "ext4_path": "/data/CubeMaster/storage/art-yyy/art-yyy.ext4",
      "ext4_sha256": "abc...",
      "ext4_size_bytes": 1073741824
    },
    "template_status": "def_READY"
  }
}
```

**完整 phase 流转**（image_job_runner.go:105-169）：
```
Queued → PULLING → UNPACKING → BUILDING_EXT4 → Distributing → CreatingTemplate → Ready
progress: 0 → 5 → 20 → 40 → 60 → 80 → 100
```

**失败响应**：
```json
{
  "job": {
    "status": "FAILED",
    "phase": "BUILDING_EXT4",
    "progress": 40,
    "error_message": "mkfs.ext4 failed: no space left on device"
  }
}
```

### 2.3 轮询脚本（一键）

```bash
JOB_ID="<job_id>"

while true; do
  RESP=$(curl -s $TC/cube/template/build/$JOB_ID/status)
  STATUS=$(echo $RESP | jq -r .job.status)
  PHASE=$(echo $RESP | jq -r .job.phase)
  PROGRESS=$(echo $RESP | jq -r .job.progress)
  echo "$(date +%T) status=$STATUS phase=$PHASE progress=$PROGRESS"

  case "$STATUS" in
    Ready|Built)
      echo "✓ Done"
      break
      ;;
    FAILED|Failed)
      echo "✗ Failed"
      echo $RESP | jq .job.error_message
      break
      ;;
  esac
  sleep 5
done
```

## 三、查询模板

### 3.1 GET /cube/template — 查模板详情

```bash
# 按 template_id 查
curl -s "$TC/cube/template?template_id=tpl-xxx" | jq .

# 按 alias 查
curl -s "$TC/cube/template?alias=my-nginx" | jq .
```

**响应**：
```json
{
  "template": {
    "template_id": "tpl-xxx",
    "alias": "my-nginx",
    "status": "def_READY",
    "instance_type": "S5.MEDIUM2",
    "artifact_id": "art-yyy",
    "fingerprint": "abc123...",
    "created_at": "2026-08-18T10:00:00Z",
    "replicas": [
      {"host_id": "host-001", "status": "Ready"},
      {"host_id": "host-002", "status": "Ready"}
    ]
  }
}
```

### 3.2 GET /cube/template/from-image — 列出 from-image 模板

```bash
curl -s "$TC/cube/template/from-image?alias=my-nginx" | jq .
# 或不带 filter 列全部
curl -s $TC/cube/template/from-image | jq .
```

## 四、删除模板

### 4.1 DELETE /cube/template

```bash
curl -X DELETE $TC/cube/template \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "req-del-001",
    "template_id": "tpl-xxx"
  }' | jq .
```

**响应**：
```json
{
  "ret": {"ret_code": 0, "ret_msg": ""}
}
```

**级联影响**（`template_delete.go`）：
- 软删 `template_definitions` 行
- 各 Cubelet 收到 `DeleteArtifactOnNode` gRPC
- alias 占位释放
- `template_replicas` 行删除

## 五、compat 矩阵

### 5.1 GET /cube/template/compat

```bash
curl -s $TC/cube/template/compat | jq .
```

**响应**：
```json
{
  "rules": [
    {
      "image_ref": "nginx:*",
      "compatible_kernels": ["5.10", "5.15"],
      "required_envd_version": "1.0"
    }
  ]
}
```

### 5.2 POST /cube/template/compat

```bash
curl -X POST $TC/cube/template/compat \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "req-compat-001",
    "image_ref": "my-app:*",
    "compatible_kernels": ["5.15"],
    "required_envd_version": "1.1"
  }' | jq .
```

## 六、redo（重建）

### 6.1 POST /cube/template/redo

适用：fingerprint 没变但要强制重建（比如 artifact 文件损坏）。

```bash
curl -X POST $TC/cube/template/redo \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "req-redo-001",
    "template_id": "tpl-xxx",
    "failed_only": true,
    "wait": false
  }' | jq .
```

**响应**：同 from-image 创建，返回新 job_id。

## 七、下载 artifact（Cubelet 数据面）

### 7.1 GET /cube/template/artifact/download

**这是 Cubelet 拉 ext4 的端点**，master 调，不直接对外。

```bash
curl -I "$TC/cube/template/artifact/download?artifact_id=art-yyy"
```

**HEAD 响应**：
```
HTTP/1.1 200 OK
Content-Length: 1073741824
X-Artifact-SHA256: abc...
```

**GET 响应**（流式下载）：
```bash
curl -o nginx.ext4 "$TC/cube/template/artifact/download?artifact_id=art-yyy"
```

## 八、CA 证书下载

### 8.1 GET /cube/ca/:filename

```bash
curl -s $TC/ca/cube-root-ca.crt > /tmp/cube-root-ca.crt
openssl x509 -in /tmp/cube-root-ca.crt -text | head
```

## 九、rootfs-artifact 状态查询

### 9.1 GET /cube/rootfs-artifact

```bash
curl -s "$TC/cube/rootfs-artifact?artifact_id=art-yyy" | jq .
```

**响应**：
```json
{
  "artifact": {
    "artifact_id": "art-yyy",
    "status": "Ready",
    "ext4_path": "/data/CubeMaster/storage/art-yyy/art-yyy.ext4",
    "ext4_sha256": "abc...",
    "ext4_size_bytes": 1073741824,
    "fingerprint": "abc123..."
  }
}
```

## 十、错误码对照

| ret_code | 含义 | 场景 |
|---|---|---|
| 0 | 成功 | 正常 |
| 1001 | 参数错误 | source_image_ref 格式不对 |
| 1002 | DB 错误 | instance_db_config 连接失败 |
| 1003 | Redis 错误 | wrapredis 不可用 |
| 1004 | 模板不存在 | template_id / alias 查不到 |
| 1005 | 模板已存在 | alias 冲突 |
| 1101 | 拉镜像失败 | registry 不通 / 认证失败 |
| 1102 | mkfs 失败 | 磁盘满 / loop 设备耗尽 |
| 1103 | cbs 上传失败 | artifact store 写不进去 |
| 1104 | 分发失败 | Cubelet 拉取失败 |
| 1105 | fingerprint 不一致 | R3 重试触发 |

**错误响应格式**：
```json
{
  "ret": {
    "ret_code": 1102,
    "ret_msg": "mkfs.ext4 failed: no space left on device"
  }
}
```

## 十一、端到端验证脚本（一键 copy-paste）

```bash
#!/bin/bash
# 端到端验证：提交 → 轮询 → 验证产物 → 查模板 → 删除
set -e

TC=${TC:-http://localhost:8090}
REQUEST_ID="e2e-$(date +%s)"
SOURCE_IMAGE="nginx:latest"
INSTANCE_TYPE="S5.MEDIUM2"

echo "=== 1. /health ==="
curl -sf $TC/health | jq .

echo "=== 2. POST /cube/template/from-image ==="
RESP=$(curl -s -X POST $TC/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d "{
    \"requestID\": \"$REQUEST_ID\",
    \"source_image_ref\": \"$SOURCE_IMAGE\",
    \"instance_type\": \"$INSTANCE_TYPE\",
    \"alias\": \"e2e-nginx-$(date +%s)\",
    \"wait\": false
  }")
echo $RESP | jq .

JOB_ID=$(echo $RESP | jq -r .job.job_id)
TEMPLATE_ID=$(echo $RESP | jq -r .job.template_id)
ARTIFACT_ID=$(echo $RESP | jq -r .job.artifact_id)
echo "job_id=$JOB_ID template_id=$TEMPLATE_ID artifact_id=$ARTIFACT_ID"

if [ "$JOB_ID" = "null" ] || [ -z "$JOB_ID" ]; then
  echo "✗ Failed to get job_id"
  echo $RESP | jq .
  exit 1
fi

echo "=== 3. 轮询进度 ==="
for i in $(seq 1 60); do
  RESP=$(curl -s $TC/cube/template/build/$JOB_ID/status)
  STATUS=$(echo $RESP | jq -r .job.status)
  PHASE=$(echo $RESP | jq -r .job.phase)
  PROGRESS=$(echo $RESP | jq -r .job.progress)
  echo "[$i/60] status=$STATUS phase=$PHASE progress=$PROGRESS"

  case "$STATUS" in
    Ready|Built)
      echo "✓ Build completed"
      break
      ;;
    FAILED|Failed)
      echo "✗ Build failed"
      echo $RESP | jq .
      exit 1
      ;;
  esac
  sleep 5

  if [ $i -eq 60 ]; then
    echo "✗ Timeout"
    exit 1
  fi
done

echo "=== 4. 查模板 ==="
curl -s "$TC/cube/template?template_id=$TEMPLATE_ID" | jq .

echo "=== 5. 查 artifact ==="
curl -s "$TC/cube/rootfs-artifact?artifact_id=$ARTIFACT_ID" | jq .

echo "=== 6. 验证 ext4 落盘 (k8s 场景) ==="
if command -v kubectl >/dev/null 2>&1; then
  kubectl exec -it $(kubectl get pod -l app.kubernetes.io/component=templatecenter -o name | head -1) \
    -- ls -lh /data/CubeTemplateCenter/storage/$ARTIFACT_ID/ || echo "k8s pod not found, skip"
fi

echo "=== 7. 删除模板 ==="
curl -s -X DELETE $TC/cube/template \
  -H "Content-Type: application/json" \
  -d "{\"requestID\": \"$REQUEST_ID-del\", \"template_id\": \"$TEMPLATE_ID\"}" | jq .

echo "=== ✓ 全流程通过 ==="
```

## 十二、常见问题

### Q1: POST 返回 `ret_code: 1005 alias already exists`
A: alias 冲突，换一个 alias 或先 DELETE 旧的。

### Q2: 轮询进度卡在 `phase=PULLING` 不动
A: 检查 registry 网络可达 + 凭证正确（`registry_username` / `registry_password`）。

### Q3: 进度到 `phase=BUILDING_EXT4` 失败 `no space left`
A: PVC 满 → 扩容（`kubectl edit pvc`）或清理旧 artifact。

### Q4: `phase=Distributing` 卡住
A: Cubelet 拉取失败 → 查 Cubelet 日志 + 网络 + `/cube/template/artifact/download` 端点可达。

### Q5: 删除模板后 `artifact_id` 还在磁盘上
A: 正常，artifact GC（`artifact_gc.go`）每 30s 扫一次，超时后清理。
