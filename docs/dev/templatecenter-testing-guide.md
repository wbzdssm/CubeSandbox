# TemplateCenter 测试指南

**版本**：v0.1.0-alpha  
**日期**：2026-08-18  
**适用**：机器人自动化测试 + 人工验证

---

## 一、快速启动

### 1.1 启动依赖

```bash
# MySQL
docker run -d --name cube-mysql -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=root \
  -e MYSQL_DATABASE=cube_mvp \
  -e MYSQL_USER=cube \
  -e MYSQL_PASSWORD=cube_pass \
  mysql:8.0

# Redis
docker run -d --name cube-redis -p 6379:6379 redis:7
```

### 1.2 启动 CubeMaster

```bash
cd CubeMaster
make build
./build/cubemaster -conf=conf.yaml &
```

### 1.3 启动 CubeTemplateCenter

```bash
cd CubeTemplateCenter
make build
./build/templatecenter -conf=conf.yaml &
```

### 1.4 验证启动

```bash
# CubeMaster 健康检查
curl http://localhost:8089/health

# TC 健康检查
curl http://localhost:8090/health
```

---

## 二、正常流程测试

### 2.1 提交构建请求

```bash
curl -X POST http://localhost:8089/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "test-001",
    "source_image_ref": "nginx:latest",
    "instance_type": "S5.MEDIUM2",
    "alias": "test-nginx",
    "wait": false
  }' | jq .
```

**期望响应**：
```json
{
  "requestID": "test-001",
  "ret": {
    "ret_code": 0,
    "ret_msg": ""
  },
  "job": {
    "job_id": "job-xxx",
    "status": "Pending"
  }
}
```

**记录 `job_id`**，后续查询用。

### 2.2 查询构建进度

```bash
JOB_ID="job-xxx"  # 从上一步获取

# 轮询进度
while true; do
  RESP=$(curl -s http://localhost:8089/cube/template/build/$JOB_ID/status)
  STATUS=$(echo $RESP | jq -r .job.status)
  PHASE=$(echo $RESP | jq -r .job.phase)
  PROGRESS=$(echo $RESP | jq -r .job.progress)
  echo "$(date +%T) status=$STATUS phase=$PHASE progress=$PROGRESS"
  
  case "$STATUS" in
    Ready|Built)
      echo "✓ Build completed"
      break
      ;;
    FAILED|Failed)
      echo "✗ Build failed"
      echo $RESP | jq .job.error_message
      break
      ;;
  esac
  sleep 5
done
```

**期望流转**：
```
Pending → Running → PULLING → UNPACKING → BUILDING_EXT4 → Built
progress: 0 → 5 → 20 → 40 → 100
```

### 2.3 查询模板

```bash
curl http://localhost:8089/cube/template?template_id=<template_id> | jq .
```

**期望响应**：
```json
{
  "template": {
    "template_id": "tpl-xxx",
    "alias": "test-nginx",
    "status": "def_READY",
    "artifact_id": "art-yyy",
    "fingerprint": "fp-abc123"
  }
}
```

### 2.4 删除模板

```bash
curl -X DELETE http://localhost:8089/cube/template \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "test-del-001",
    "template_id": "tpl-xxx"
  }' | jq .
```

**期望响应**：
```json
{
  "ret": {
    "ret_code": 0,
    "ret_msg": ""
  }
}
```

---

## 三、失败 Case 构造

### 3.1 无效镜像地址

**请求**：
```bash
curl -X POST http://localhost:8089/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "test-invalid-001",
    "source_image_ref": "invalid-image-!!!",
    "instance_type": "S5.MEDIUM2"
  }' | jq .
```

**期望**：
- 立即返回错误（`ret_code != 0`）
- 错误信息包含 `invalid image ref` 或 `invalid image_ref`

### 3.2 无效 instance_type

**请求**：
```bash
curl -X POST http://localhost:8089/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "test-invalid-002",
    "source_image_ref": "nginx:latest",
    "instance_type": "INVALID.TYPE"
  }' | jq .
```

**期望**：
- 立即返回错误
- 错误信息包含 `invalid instance_type` 或 `unsupported instance_type`

### 3.3 拉取镜像失败（网络不通）

**请求**：
```bash
curl -X POST http://localhost:8089/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "test-pull-fail-001",
    "source_image_ref": "nonexistent-registry.example.com/image:latest",
    "instance_type": "S5.MEDIUM2"
  }' | jq .

# 轮询进度
JOB_ID="<job_id>"
while true; do
  STATUS=$(curl -s http://localhost:8089/cube/template/build/$JOB_ID/status | jq -r .job.status)
  [ "$STATUS" = "FAILED" ] && break
  sleep 5
done

# 查看错误信息
curl -s http://localhost:8089/cube/template/build/$JOB_ID/status | jq .job.error_message
```

**期望**：
- `status=FAILED`
- `phase=PULLING`
- `error_message` 包含 `pull image fail` 或 `connection refused` 或 `timeout`

### 3.4 磁盘空间不足

**构造**：
```bash
# 填满磁盘
dd if=/dev/zero of=/tmp/fill-disk bs=1G count=200

# 提交构建
curl -X POST http://localhost:8089/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "test-disk-full-001",
    "source_image_ref": "nginx:latest",
    "instance_type": "S5.MEDIUM2"
  }' | jq .

# 轮询
JOB_ID="<job_id>"
# ... 等待 FAILED

# 清理
rm /tmp/fill-disk
```

**期望**：
- `status=FAILED`
- `phase=BUILDING_EXT4`
- `error_message` 包含 `no space left on device`

### 3.5 DB 连接断开

**构造**：
```bash
# 停掉 MySQL
docker stop cube-mysql

# 提交构建
curl -X POST http://localhost:8089/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "test-db-fail-001",
    "source_image_ref": "nginx:latest",
    "instance_type": "S5.MEDIUM2"
  }' | jq .

# 恢复 MySQL
docker start cube-mysql
```

**期望**：
- 立即返回错误（`ret_code != 0`）
- 错误信息包含 `database` 或 `connection refused`

### 3.6 Redis 连接断开

**构造**：
```bash
# 停掉 Redis
docker stop cube-redis

# 提交构建
curl -X POST http://localhost:8089/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "test-redis-fail-001",
    "source_image_ref": "nginx:latest",
    "instance_type": "S5.MEDIUM2"
  }' | jq .

# 恢复 Redis
docker start cube-redis
```

**期望**：
- 业务仍可用（进度上报失败，但主流程不阻塞）
- 或立即返回错误（取决于 Redis 依赖程度）

### 3.7 重复提交（alias 冲突）

**请求**：
```bash
# 第一次提交
curl -X POST http://localhost:8089/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "test-dup-001",
    "source_image_ref": "nginx:latest",
    "instance_type": "S5.MEDIUM2",
    "alias": "test-nginx"
  }' | jq .

# 第二次提交 (同 alias)
curl -X POST http://localhost:8089/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "test-dup-002",
    "source_image_ref": "nginx:latest",
    "instance_type": "S5.MEDIUM2",
    "alias": "test-nginx"
  }' | jq .
```

**期望**：
- 第二次返回错误（`ret_code != 0`）
- 错误信息包含 `alias already exists` 或 `template already exists`

### 3.8 构建中途进程崩溃（可重入测试）

**构造**：
```bash
# 1. 提交构建
curl -X POST http://localhost:8089/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "test-crash-001",
    "source_image_ref": "nginx:latest",
    "instance_type": "S5.MEDIUM2"
  }' | jq .

JOB_ID="<job_id>"

# 2. 等到 BUILDING_EXT4 阶段
while true; do
  PHASE=$(curl -s http://localhost:8089/cube/template/build/$JOB_ID/status | jq -r .job.phase)
  [ "$PHASE" = "BUILDING_EXT4" ] && break
  sleep 2
done

# 3. 杀掉 TC 进程
pkill -9 templatecenter

# 4. 重启 TC
cd CubeTemplateCenter && ./build/templatecenter -conf=conf.yaml &

# 5. 观察是否恢复构建
# (当前 TC 没有 reconciler, 所以 job 会卡在 BUILDING_EXT4)
# (未来 TC 有 reconciler 后, 会自动重新入队)
```

**期望**：
- 当前版本：job 卡在 `BUILDING_EXT4`（需要手动重试）
- 未来版本：reconciler 自动重新入队，job 恢复构建

### 3.9 多副本锁冲突（多 TC 实例）

**构造**：
```bash
# 启动 2 个 TC 实例
cd CubeTemplateCenter
./build/templatecenter -conf=conf.yaml -port=8090 &
./build/templatecenter -conf=conf.yaml -port=8091 &

# 同时提交同一个构建
for i in 1 2; do
  curl -X POST http://localhost:8089/cube/template/from-image \
    -H "Content-Type: application/json" \
    -d '{
      "requestID": "test-multi-00'$i'",
      "source_image_ref": "nginx:latest",
      "instance_type": "S5.MEDIUM2"
    }' &
done
wait

# 查 DB 会话锁
docker exec cube-mysql mysql -ucube -pcube_pass -e \
  "SELECT * FROM performance_schema.metadata_locks WHERE lock_type='User-level lock'"
```

**期望**：
- 只有 1 个 TC 实例拿到锁（`tc_build_<artifactID>`）
- 另一个实例立即返回（或等待锁释放）
- 最终只有 1 个 artifact 被构建

### 3.10 超时场景

**构造**：
```bash
# 提交一个大镜像（构建时间 > 30min）
curl -X POST http://localhost:8089/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "test-timeout-001",
    "source_image_ref": "very-large-image:10GB",
    "instance_type": "S5.MEDIUM2"
  }' | jq .

# 等待 30min+
# artifact_gc 会把 status=BUILDING 且 updated_at > 2h 的行标为 FAILED
```

**期望**：
- 30min 后 job 仍在 `BUILDING_EXT4`
- 2h 后 artifact_gc 把 job 标为 `FAILED`
- `error_message` 包含 `timeout` 或 `exceeded deadline`

---

## 四、压测方案

### 4.1 工具

```bash
# macOS
brew install wrk k6

# Linux
sudo apt-get install wrk
# k6: https://k6.io/docs/getting-started/installation/
```

### 4.2 场景 1：并发提交构建

**脚本**（`pressure_submit.lua`）：

```lua
wrk.method = "POST"
wrk.headers["Content-Type"] = "application/json"

local counter = 0

request = function()
  counter = counter + 1
  body = string.format([[{
    "requestID": "pressure-test-%d",
    "source_image_ref": "nginx:latest",
    "instance_type": "S5.MEDIUM2"
  }]], counter)
  return wrk.format("POST", wrk.path, wrk.headers, body)
end
```

**压测**：

```bash
# 100 并发, 持续 60s
wrk -t10 -c100 -d60s -s pressure_submit.lua http://localhost:8089/cube/template/from-image
```

**监控**：
- QPS
- 延迟（P50 / P95 / P99）
- 错误率
- CubeMaster CPU / 内存
- TC CPU / 内存
- DB 连接数

### 4.3 场景 2：并发查询进度

```bash
# 1000 并发, 持续 30s
wrk -t10 -c1000 -d30s http://localhost:8089/cube/template/build/test-job-id/status
```

### 4.4 场景 3：混合场景（k6）

**脚本**（`mixed_test.js`）：

```javascript
import http from 'k6/http';
import { check } from 'k6';

export let options = {
  stages: [
    { duration: '30s', target: 100 },
    { duration: '1m', target: 100 },
    { duration: '30s', target: 0 },
  ],
};

export default function () {
  // 70% 查询
  if (Math.random() < 0.7) {
    let res = http.get('http://localhost:8089/cube/template/build/test-job-id/status');
    check(res, { 'status is 200': (r) => r.status === 200 });
  } else {
    // 30% 提交
    let payload = JSON.stringify({
      requestID: `test-${__VU}-${__ITER}`,
      source_image_ref: 'nginx:latest',
      instance_type: 'S5.MEDIUM2',
    });
    let res = http.post('http://localhost:8089/cube/template/from-image', payload, {
      headers: { 'Content-Type': 'application/json' },
    });
    check(res, { 'status is 200': (r) => r.status === 200 });
  }
}
```

**压测**：

```bash
k6 run mixed_test.js
```

---

## 五、监控指标

### 5.1 Prometheus 指标

```bash
# CubeMaster metrics
curl http://localhost:8089/metrics | grep template_

# TC metrics
curl http://localhost:8090/metrics | grep template_
```

**关键指标**：
- `template_build_total`（构建总数）
- `template_build_duration_seconds`（构建耗时）
- `template_build_failed_total`（构建失败数）
- `template_artifact_size_bytes`（产物大小）

### 5.2 日志

```bash
# CubeMaster 日志
tail -F /data/log/CubeMaster/cubemaster.INFO | grep -E "template|artifact"

# TC 日志
tail -F /data/log/CubeTemplateCenter/templatecenter.INFO | grep -E "build|artifact"
```

### 5.3 DB 查询

```sql
-- 查询构建中的 job
SELECT job_id, status, phase, progress, created_at, updated_at
FROM template_image_jobs
WHERE status IN ('Pending', 'Running')
ORDER BY created_at DESC;

-- 查询失败的 job
SELECT job_id, status, phase, error_message, created_at, updated_at
FROM template_image_jobs
WHERE status = 'FAILED'
ORDER BY created_at DESC
LIMIT 10;

-- 查询 artifact
SELECT artifact_id, status, ext4_sha256, ext4_size_bytes, created_at
FROM rootfs_artifacts
ORDER BY created_at DESC
LIMIT 10;

-- 查询 DB 会话锁
SELECT * FROM performance_schema.metadata_locks
WHERE lock_type = 'User-level lock';
```

---

## 六、机器人自动化测试

### 6.1 测试脚本（Bash）

```bash
#!/bin/bash
# robot_test.sh

set -e

TC=${TC:-http://localhost:8090}
MASTER=${MASTER:-http://localhost:8089}

# Test 1: Health check
echo "=== Test 1: Health check ==="
curl -sf $MASTER/health | jq .
curl -sf $TC/health | jq .

# Test 2: Submit build
echo "=== Test 2: Submit build ==="
RESP=$(curl -s -X POST $MASTER/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "robot-test-001",
    "source_image_ref": "nginx:latest",
    "instance_type": "S5.MEDIUM2"
  }')
JOB_ID=$(echo $RESP | jq -r .job.job_id)
echo "job_id=$JOB_ID"

# Test 3: Poll progress
echo "=== Test 3: Poll progress ==="
for i in $(seq 1 60); do
  STATUS=$(curl -s $MASTER/cube/template/build/$JOB_ID/status | jq -r .job.status)
  PHASE=$(curl -s $MASTER/cube/template/build/$JOB_ID/status | jq -r .job.phase)
  PROGRESS=$(curl -s $MASTER/cube/template/build/$JOB_ID/status | jq -r .job.progress)
  echo "[$i/60] status=$STATUS phase=$PHASE progress=$PROGRESS"
  
  [ "$STATUS" = "Ready" ] || [ "$STATUS" = "Built" ] && break
  [ "$STATUS" = "FAILED" ] && exit 1
  sleep 5
done

# Test 4: Query template
echo "=== Test 4: Query template ==="
curl -s "$MASTER/cube/template?alias=test-nginx" | jq .

# Test 5: Delete template
echo "=== Test 5: Delete template ==="
curl -s -X DELETE $MASTER/cube/template \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "robot-test-del-001",
    "template_id": "tpl-xxx"
  }' | jq .

echo "=== All tests passed ==="
```

### 6.2 失败 Case 自动化

```bash
#!/bin/bash
# robot_test_failures.sh

# Test invalid image ref
echo "=== Test: Invalid image ref ==="
RESP=$(curl -s -X POST $MASTER/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "test-invalid-001",
    "source_image_ref": "invalid-!!!",
    "instance_type": "S5.MEDIUM2"
  }')
RET_CODE=$(echo $RESP | jq -r .ret.ret_code)
[ "$RET_CODE" != "0" ] && echo "✓ Pass" || echo "✗ Fail"

# Test pull image fail
echo "=== Test: Pull image fail ==="
RESP=$(curl -s -X POST $MASTER/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "requestID": "test-pull-fail-001",
    "source_image_ref": "nonexistent.example.com/image:latest",
    "instance_type": "S5.MEDIUM2"
  }')
JOB_ID=$(echo $RESP | jq -r .job.job_id)

# Wait for FAILED
for i in $(seq 1 30); do
  STATUS=$(curl -s $MASTER/cube/template/build/$JOB_ID/status | jq -r .job.status)
  [ "$STATUS" = "FAILED" ] && break
  sleep 5
done

ERROR_MSG=$(curl -s $MASTER/cube/template/build/$JOB_ID/status | jq -r .job.error_message)
echo "error_message: $ERROR_MSG"
[[ "$ERROR_MSG" == *"pull image fail"* ]] && echo "✓ Pass" || echo "✗ Fail"
```

---

## 七、故障排查

### 7.1 查看日志

```bash
# CubeMaster
tail -F /data/log/CubeMaster/cubemaster.ERROR

# TC
tail -F /data/log/CubeTemplateCenter/templatecenter.ERROR
```

### 7.2 查看 goroutine

```bash
# 发送 SIGQUIT 获取 goroutine dump
kill -QUIT <pid>
# 日志里会有完整 goroutine 栈
```

### 7.3 查看 DB 锁

```sql
SELECT * FROM performance_schema.metadata_locks
WHERE lock_type = 'User-level lock';
```

---

**完成！** 这份文档包含了所有正常流程 + 失败 case + 压测方案 + 机器人自动化测试脚本。
