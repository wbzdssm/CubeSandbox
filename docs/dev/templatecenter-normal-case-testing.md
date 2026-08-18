# TemplateCenter 正常 Case 验证方案

**版本**：v0.1.0-alpha  
**日期**：2026-08-18  
**工具**：`cubemastercli` + `curl` + `jq`

---

## 一、前置准备

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

### 1.2 启动服务

```bash
# 启动 CubeMaster
cd CubeMaster
make build
./build/cubemaster -conf=conf.yaml &

# 启动 CubeTemplateCenter
cd CubeTemplateCenter
make build
./build/templatecenter -conf=conf.yaml &

# 验证健康
curl http://localhost:8089/health
curl http://localhost:8090/health
```

### 1.3 编译 cubemastercli

```bash
cd CubeMaster
make cubemastercli
# 产物: build/cubemastercli
```

---

## 二、正常 Case 验证（cubemastercli）

### 2.1 基础创建（最小参数）

```bash
./build/cubemastercli tpl create-from-image \
  --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest \
  --writable-layer-size 1G \
  --detach
```

**期望输出**：
```
job_id: job-xxx
status: Pending
```

**验证点**：
- ✅ 返回 `job_id`
- ✅ 状态为 `Pending`

### 2.2 完整参数创建

```bash
./build/cubemastercli tpl create-from-image \
  --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest \
  --writable-layer-size 1G \
  --alias test-nginx-v1 \
  --expose-port 80 \
  --expose-port 443 \
  --instance-type S5.MEDIUM2 \
  --network-type vpc \
  --detach
```

**期望输出**：
```
job_id: job-yyy
status: Pending
```

**验证点**：
- ✅ 支持多端口暴露
- ✅ 支持自定义 instance_type
- ✅ 支持自定义 network_type

### 2.3 带探针的创建

```bash
./build/cubemastercli tpl create-from-image \
  --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest \
  --writable-layer-size 1G \
  --alias test-nginx-probe \
  --expose-port 49983 \
  --expose-port 80 \
  --probe 49983 \
  --probe-path /health \
  --detach
```

**期望输出**：
```
job_id: job-zzz
status: Pending
```

**验证点**：
- ✅ 支持 HTTP GET 探针
- ✅ 支持自定义探针路径

### 2.4 带容器覆盖的创建

```bash
./build/cubemastercli tpl create-from-image \
  --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest \
  --writable-layer-size 1G \
  --alias test-nginx-override \
  --cmd /bin/sh \
  --arg -c \
  --arg "nginx -g 'daemon off;'" \
  --env NGINX_HOST=example.com \
  --env NGINX_PORT=80 \
  --detach
```

**期望输出**：
```
job_id: job-aaa
status: Pending
```

**验证点**：
- ✅ 支持覆盖容器 ENTRYPOINT (--cmd)
- ✅ 支持覆盖容器 CMD (--arg)
- ✅ 支持环境变量 (--env)

### 2.5 带 envd 注入的创建

```bash
./build/cubemastercli tpl create-from-image \
  --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest \
  --writable-layer-size 1G \
  --alias test-nginx-envd \
  --enable-inject-envd \
  --detach
```

**期望输出**：
```
job_id: job-bbb
status: Pending
```

**验证点**：
- ✅ 支持 envd 注入
- ✅ 自动上传 envd 二进制

---

## 三、进度查询（watch 模式）

### 3.1 同步等待构建完成

```bash
./build/cubemastercli tpl create-from-image \
  --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest \
  --writable-layer-size 1G \
  --alias test-nginx-watch \
  --interval 5s
```

**期望输出**（实时进度）：
```
[2026-08-18 15:30:00] status=Running phase=PULLING progress=5
[2026-08-18 15:30:05] status=Running phase=UNPACKING progress=20
[2026-08-18 15:30:15] status=Running phase=BUILDING_EXT4 progress=40
[2026-08-18 15:30:45] status=Built phase=Built progress=100
✓ Build completed
```

**验证点**：
- ✅ 实时显示进度
- ✅ 状态流转正确（PULLING → UNPACKING → BUILDING_EXT4 → Built）
- ✅ 最终状态为 Built

### 3.2 查询指定 job

```bash
JOB_ID="job-xxx"  # 从之前的创建获取

./build/cubemastercli tpl status --job-id $JOB_ID
```

**期望输出**：
```json
{
  "job": {
    "job_id": "job-xxx",
    "status": "Built",
    "phase": "Built",
    "progress": 100,
    "artifact_id": "art-yyy",
    "ext4_sha256": "abc123...",
    "ext4_size_bytes": 1073741824
  }
}
```

**验证点**：
- ✅ 返回完整 job 信息
- ✅ 包含 artifact_id 和 sha256

---

## 四、模板查询

### 4.1 按 alias 查询

```bash
./build/cubemastercli tpl get --alias test-nginx-v1
```

**期望输出**：
```json
{
  "template": {
    "template_id": "tpl-xxx",
    "alias": "test-nginx-v1",
    "status": "def_READY",
    "instance_type": "S5.MEDIUM2",
    "artifact_id": "art-yyy",
    "fingerprint": "fp-abc123",
    "created_at": "2026-08-18T15:30:00Z"
  }
}
```

**验证点**：
- ✅ 返回模板详情
- ✅ 状态为 def_READY
- ✅ 包含 artifact_id 和 fingerprint

### 4.2 列出所有模板

```bash
./build/cubemastercli tpl list
```

**期望输出**：
```
template_id    alias              status      instance_type    created_at
tpl-xxx        test-nginx-v1      def_READY   S5.MEDIUM2       2026-08-18T15:30:00Z
tpl-yyy        test-nginx-probe   def_READY   S5.MEDIUM2       2026-08-18T15:35:00Z
tpl-zzz        test-nginx-override def_READY  S5.MEDIUM2       2026-08-18T15:40:00Z
```

**验证点**：
- ✅ 返回所有模板列表
- ✅ 包含 alias / status / instance_type

---

## 五、反复创建删除（幂等性测试）

### 5.1 循环创建 10 个模板

```bash
#!/bin/bash
# loop_create.sh

for i in $(seq 1 10); do
  echo "=== Creating template $i ==="
  ./build/cubemastercli tpl create-from-image \
    --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest \
    --writable-layer-size 1G \
    --alias test-nginx-loop-$i \
    --detach
  
  sleep 2
done

echo "=== All 10 templates created ==="
```

**验证点**：
- ✅ 每次创建都返回不同的 job_id
- ✅ 没有报错
- ✅ DB 中有 10 条 image_jobs 记录

### 5.2 循环删除 10 个模板

```bash
#!/bin/bash
# loop_delete.sh

for i in $(seq 1 10); do
  echo "=== Deleting template $i ==="
  ./build/cubemastercli tpl delete --alias test-nginx-loop-$i
  
  sleep 1
done

echo "=== All 10 templates deleted ==="
```

**验证点**：
- ✅ 每次删除都返回成功
- ✅ 没有报错
- ✅ DB 中对应的 template_definitions 行被删除

### 5.3 创建 → 删除 → 再创建（同 alias）

```bash
#!/bin/bash
# recreate.sh

ALIAS="test-nginx-recreate"

# 1. 创建
echo "=== Create 1 ==="
./build/cubemastercli tpl create-from-image \
  --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest \
  --writable-layer-size 1G \
  --alias $ALIAS \
  --detach

sleep 30  # 等待构建完成

# 2. 删除
echo "=== Delete ==="
./build/cubemastercli tpl delete --alias $ALIAS

sleep 5

# 3. 再创建 (同 alias)
echo "=== Create 2 (same alias) ==="
./build/cubemastercli tpl create-from-image \
  --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest \
  --writable-layer-size 1G \
  --alias $ALIAS \
  --detach

echo "=== Recreate test passed ==="
```

**验证点**：
- ✅ 删除后可以重新创建同 alias
- ✅ 不会报 `alias already exists`
- ✅ 新的 job_id 与旧的不同

---

## 六、并发创建（压力测试）

### 6.1 并发创建 20 个模板

```bash
#!/bin/bash
# concurrent_create.sh

for i in $(seq 1 20); do
  (
    ./build/cubemastercli tpl create-from-image \
      --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest \
      --writable-layer-size 1G \
      --alias test-nginx-concurrent-$i \
      --detach
  ) &
done

wait
echo "=== All 20 concurrent creates completed ==="
```

**验证点**：
- ✅ 20 个并发请求都成功
- ✅ 没有 DB 死锁
- ✅ 没有 alias 冲突
- ✅ 每个模板都有独立的 artifact_id

### 6.2 并发查询进度

```bash
#!/bin/bash
# concurrent_query.sh

JOB_IDS=("job-xxx" "job-yyy" "job-zzz")  # 从之前的创建获取

for job_id in "${JOB_IDS[@]}"; do
  (
    while true; do
      STATUS=$(curl -s http://localhost:8089/cube/template/build/$job_id/status | jq -r .job.status)
      [ "$STATUS" = "Built" ] || [ "$STATUS" = "Ready" ] && break
      sleep 2
    done
    echo "job_id=$job_id completed"
  ) &
done

wait
echo "=== All concurrent queries completed ==="
```

**验证点**：
- ✅ 并发查询不会阻塞
- ✅ 每个 job 都能正确返回状态

---

## 七、TC 核心 Build 逻辑验证

### 7.1 验证 TC 是否真的在做构建

**步骤**：

1. **提交构建请求**（通过 CubeMaster）：
```bash
./build/cubemastercli tpl create-from-image \
  --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest \
  --writable-layer-size 1G \
  --alias test-tc-build \
  --detach
```

2. **查看 TC 日志**：
```bash
tail -F /data/log/CubeTemplateCenter/templatecenter.INFO | grep "build"
```

**期望日志**：
```
[INFO] received build job: job_id=job-xxx image=cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest
[INFO] report status to master: job_id=job-xxx phase=PULLING progress=5
[INFO] report status to master: job_id=job-xxx phase=UNPACKING progress=20
[INFO] report status to master: job_id=job-xxx phase=BUILDING_EXT4 progress=40
[INFO] report status to master: job_id=job-xxx status=Built progress=100 artifact_id=art-yyy
[INFO] template build completed: artifact_id=art-yyy sha256=abc123 size=1073741824
```

**验证点**：
- ✅ TC 收到构建请求
- ✅ TC 上报进度到 CubeMaster
- ✅ TC 完成构建并上报 Built 状态

### 7.2 验证 TC 回调 CubeMaster

**步骤**：

1. **查看 CubeMaster 日志**：
```bash
tail -F /data/log/CubeMaster/cubemaster.INFO | grep "internal/template/jobs"
```

**期望日志**：
```
[INFO] received TC status update: job_id=job-xxx fields=map[phase:PULLING progress:5 status:Running]
[INFO] update image job: job_id=job-xxx status=Running phase=PULLING progress=5
[INFO] received TC status update: job_id=job-xxx fields=map[phase:UNPACKING progress:20]
[INFO] update image job: job_id=job-xxx phase=UNPACKING progress=20
...
[INFO] received TC status update: job_id=job-xxx fields=map[artifact_id:art-yyy status:Built progress:100]
[INFO] update image job: job_id=job-xxx status=Built progress=100
```

**验证点**：
- ✅ CubeMaster 收到 TC 的回调
- ✅ CubeMaster 更新 image_jobs 表
- ✅ 状态流转正确

### 7.3 验证 artifact 落盘

**步骤**：

1. **查看 TC 的存储目录**：
```bash
ls -lh /data/CubeTemplateCenter/storage/
```

**期望输出**：
```
drwxr-xr-x  3 root  root   4.0K Aug 18 15:30 art-yyy
```

2. **查看 artifact 文件**：
```bash
ls -lh /data/CubeTemplateCenter/storage/art-yyy/
```

**期望输出**：
```
-rw-r--r--  1 root  root   1.0G Aug 18 15:30 art-yyy.ext4
-rw-r--r--  1 root  root    64B Aug 18 15:30 art-yyy.sha256
```

3. **验证 sha256**：
```bash
cat /data/CubeTemplateCenter/storage/art-yyy/art-yyy.sha256
```

**期望输出**：
```
abc123def456...
```

**验证点**：
- ✅ artifact 文件存在
- ✅ 大小正确（1GB）
- ✅ sha256 文件存在

---

## 八、DB 状态验证

### 8.1 查询 image_jobs 表

```bash
docker exec cube-mysql mysql -ucube -pcube_pass cube_mvp -e \
  "SELECT job_id, status, phase, progress, artifact_id, created_at FROM template_image_jobs ORDER BY created_at DESC LIMIT 5"
```

**期望输出**：
```
+---------+--------+--------+----------+-------------+---------------------+
| job_id  | status | phase  | progress | artifact_id | created_at          |
+---------+--------+--------+----------+-------------+---------------------+
| job-xxx | Built  | Built  |      100 | art-yyy     | 2026-08-18 15:30:00 |
| job-yyy | Built  | Built  |      100 | art-zzz     | 2026-08-18 15:35:00 |
+---------+--------+--------+----------+-------------+---------------------+
```

**验证点**：
- ✅ status 为 Built
- ✅ phase 为 Built
- ✅ progress 为 100
- ✅ artifact_id 不为空

### 8.2 查询 rootfs_artifacts 表

```bash
docker exec cube-mysql mysql -ucube -pcube_pass cube_mvp -e \
  "SELECT artifact_id, status, ext4_sha256, ext4_size_bytes, created_at FROM rootfs_artifacts ORDER BY created_at DESC LIMIT 5"
```

**期望输出**：
```
+-------------+--------+--------------+-----------------+---------------------+
| artifact_id | status | ext4_sha256  | ext4_size_bytes | created_at          |
+-------------+--------+--------------+-----------------+---------------------+
| art-yyy     | Ready  | abc123...    |      1073741824 | 2026-08-18 15:30:00 |
| art-zzz     | Ready  | def456...    |      1073741824 | 2026-08-18 15:35:00 |
+-------------+--------+--------------+-----------------+---------------------+
```

**验证点**：
- ✅ status 为 Ready
- ✅ ext4_sha256 不为空
- ✅ ext4_size_bytes 正确（1GB = 1073741824）

### 8.3 查询 template_definitions 表

```bash
docker exec cube-mysql mysql -ucube -pcube_pass cube_mvp -e \
  "SELECT template_id, alias, status, artifact_id, fingerprint, created_at FROM template_definitions ORDER BY created_at DESC LIMIT 5"
```

**期望输出**：
```
+-------------+------------------+-----------+-------------+-------------+---------------------+
| template_id | alias            | status    | artifact_id | fingerprint | created_at          |
+-------------+------------------+-----------+-------------+-------------+---------------------+
| tpl-xxx     | test-nginx-v1    | def_READY | art-yyy     | fp-abc123   | 2026-08-18 15:30:00 |
| tpl-yyy     | test-nginx-probe | def_READY | art-zzz     | fp-def456   | 2026-08-18 15:35:00 |
+-------------+------------------+-----------+-------------+-------------+---------------------+
```

**验证点**：
- ✅ status 为 def_READY
- ✅ artifact_id 与 rootfs_artifacts 表对应
- ✅ fingerprint 不为空

---

## 九、性能基准

### 9.1 构建耗时

| 镜像大小 | ext4 大小 | 预估耗时 |
|---|---|---|
| nginx:latest (~150MB) | 1 GiB | 30-60s |
| ubuntu:22.04 (~80MB) | 1 GiB | 25-50s |
| python:3.11 (~900MB) | 1.5 GiB | 90-150s |

**验证方法**：
```bash
time ./build/cubemastercli tpl create-from-image \
  --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest \
  --writable-layer-size 1G \
  --alias test-nginx-benchmark
```

### 9.2 并发构建

**验证方法**：
```bash
# 并发 10 个构建
for i in $(seq 1 10); do
  (
    time ./build/cubemastercli tpl create-from-image \
      --image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-nginx:latest \
      --writable-layer-size 1G \
      --alias test-nginx-concurrent-$i
  ) &
done
wait
```

**期望**：
- 10 个并发构建都能完成
- 总耗时 < 单个构建耗时 × 10（有并发加速）

---

## 十、回归测试 Checklist

每次发版前必须验证：

- [ ] **基础创建**（2.1）
- [ ] **完整参数创建**（2.2）
- [ ] **带探针创建**（2.3）
- [ ] **进度查询**（3.1, 3.2）
- [ ] **模板查询**（4.1, 4.2）
- [ ] **反复创建删除**（5.1, 5.2, 5.3）
- [ ] **并发创建**（6.1, 6.2）
- [ ] **TC 核心 Build 逻辑**（7.1, 7.2, 7.3）
- [ ] **DB 状态**（8.1, 8.2, 8.3）
- [ ] **性能基准**（9.1, 9.2）

---

**完成！** 这份文档包含了所有正常 case 的验证方案，可以直接交给测试同学执行。
