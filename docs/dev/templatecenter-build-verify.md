# TemplateCenter 打包与验证指南

**适用**：本地开发、CI、生产发布三个场景。

## 一、本地开发

### 1.1 前置依赖

| 依赖 | 版本 | 用途 |
|---|---|---|
| Go | ≥ 1.25.7 | 编译 |
| Docker | ≥ 20.10 | （可选）跑集成测试 |
| MySQL | ≥ 8.0 或 PG ≥ 13 | 集成测试 / 本地运行 |
| Redis | ≥ 6.0 | 集成测试 / 本地运行 |
| make | 任意 | 调用 Makefile |

### 1.2 编译

```bash
cd CubeTemplateCenter

# 默认 Linux/amd64 (生产目标)
make build

# 显式跨编译 (如需要 ARM)
GOOS=linux GOARCH=arm64 make build

# 产物
ls -lh build/templatecenter
# 应输出: ELF 64-bit LSB executable, x86-64, statically linked, ~57MB
```

**为什么必须 GOOS=linux**：源码里 `CubeMaster/pkg/templatecenter/image/disk.go` 用了 Linux-only 的 `unix.F_SETPIPE_SZ` / `unix.CAP_SYS_ADMIN`。Makefile 已经 `export GOOS=linux GOARCH=amd64`。

### 1.3 快速验证（不依赖 DB）

```bash
# 编译通过即可
make build

# 静态检查
gofmt -l ./pkg/ ./cmd/  # 应输出空
GOOS=linux GOARCH=amd64 go vet ./...
```

### 1.4 单元测试

```bash
# 当前没有业务单测, 仅编译检查
make test

# 加 -race 跑竞争检查 (需要 CGO=1, 所以本地 mac 跑不了)
GOOS= GOARCH= CGO_ENABLED=1 go test -race -short ./...
```

### 1.5 本地启动（需要 DB + Redis）

```bash
# 1. 起依赖
docker run -d --name cube-mysql -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=root \
  -e MYSQL_DATABASE=cube_mvp \
  -e MYSQL_USER=cube \
  -e MYSQL_PASSWORD=cube_pass \
  mysql:8.0

docker run -d --name cube-redis -p 6379:6379 redis:7

# 2. 起 TC (用默认 conf.yaml)
./build/templatecenter -conf=conf.yaml &
TC_PID=$!

# 3. 等 ready
for i in $(seq 1 30); do
  curl -sf http://localhost:8090/health && break || sleep 1
done

# 4. 探针检查
curl -s http://localhost:8090/health | jq .
# 期望: {"status":"ok","checks":{"nodemeta":true,"templatecenter_store":true}}

# 5. 关
kill $TC_PID
wait $TC_PID
```

## 二、集成测试

### 2.1 跑 CubeMaster 现有测试（间接覆盖 TC）

```bash
cd CubeMaster

# 跑 templatecenter 相关测试 (不需要 docker)
go test -count=1 -run "TestTemplate|TestImage|TestArtifact|TestJob" ./pkg/templatecenter/...

# 跑需要 docker 的镜像构建测试
CUBEMASTER_REQUIRE_DOCKER_TESTS=1 go test -count=1 -v ./pkg/templatecenter/image/...

# 跑 mysql 集成测试
CUBEMASTER_REQUIRE_MYSQL_TESTS=1 go test -count=1 ./pkg/templatecenter/... -run "Mysql"

# 跑 postgres 集成测试
CUBEMASTER_REQUIRE_POSTGRES_TESTS=1 go test -count=1 ./pkg/templatecenter/... -run "Postgres"
```

### 2.2 TC 自己的集成测试（未来 PR 5 加入）

```bash
cd CubeTemplateCenter

# 起依赖
docker-compose up -d   # (待写 docker-compose.yaml)

# 跑集成测试
make test-integration
```

## 三、CI 打包

### 3.1 GitHub Actions workflow（待写 `.github/workflows/templatecenter.yml`）

```yaml
name: templatecenter

on:
  push:
    branches: [main, feat/templatecenter]
    paths:
      - 'CubeTemplateCenter/**'
      - 'CubeMaster/pkg/templatecenter/**'
      - 'CubeMaster/pkg/service/httpservice/cube/template*'
      - 'CubeDB/**'
  pull_request:
    paths:
      - 'CubeTemplateCenter/**'
      - 'CubeMaster/pkg/templatecenter/**'

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version: '1.25.7' }

      - name: Build
        working-directory: CubeTemplateCenter
        run: make build

      - name: Vet
        working-directory: CubeTemplateCenter
        run: |
          GOOS=linux GOARCH=amd64 go vet ./...
          test -z "$(gofmt -l ./pkg/ ./cmd/)"

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: templatecenter-linux-amd64
          path: CubeTemplateCenter/build/templatecenter
          retention-days: 30

  test:
    runs-on: ubuntu-latest
    services:
      mysql:
        image: mysql:8.0
        env:
          MYSQL_ROOT_PASSWORD: root
          MYSQL_DATABASE: cube_mvp
        ports: ['3306:3306']
        options: --health-cmd="mysqladmin ping" --health-interval=10s --health-timeout=5s --health-retries=5
      redis:
        image: redis:7
        ports: ['6379:6379']
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-go@v5
        with: { go-version: '1.25.7' }

      - name: Integration tests (templatecenter package)
        working-directory: CubeMaster
        env:
          CUBEMASTER_REQUIRE_MYSQL_TESTS: '1'
        run: |
          go test -count=1 -v ./pkg/templatecenter/... -run "Mysql"
```

### 3.2 镜像打包

`Dockerfile.templatecenter`（待写）：

```dockerfile
# 阶段 1: 编译
FROM golang:1.25.7-alpine AS builder
WORKDIR /workspace
COPY . /workspace
RUN cd CubeTemplateCenter && \
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
      -trimpath \
      -ldflags "-s -w -X github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/version.GitCommit=$(git rev-parse --short HEAD) -X github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/version.BuildTime=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      -o /templatecenter \
      ./cmd/templatecenter/

# 阶段 2: 运行时 (极简)
FROM alpine:3.20
RUN apk add --no-cache ca-certificates e2fsprogs util-linux
# e2fsprogs: mkfs.ext4
# util-linux: losetup / mount / umount

WORKDIR /usr/local/services/cubetoolbox/CubeTemplateCenter
COPY --from=builder /templatecenter /usr/local/services/cubetoolbox/CubeTemplateCenter/templatecenter

EXPOSE 8090
ENTRYPOINT ["/usr/local/services/cubetoolbox/CubeTemplateCenter/templatecenter"]
CMD ["-conf=/usr/local/services/cubetoolbox/CubeTemplateCenter/conf.yaml"]
```

构建：

```bash
docker build -f Dockerfile.templatecenter -t cubetemplatecenter:latest .
docker tag cubetemplatecenter:latest ccr.ccs.tencentyun.com/cube/cubetemplatecenter:v0.1.0
docker push ccr.ccs.tencentyun.com/cube/cubetemplatecenter:v0.1.0
```

**注意**：mkfs.ext4 + losetup 需要 privileged 权限或 SYS_ADMIN cap，容器跑的时候要么 `--privileged` 要么挂 `/dev`。

## 四、生产发布

### 4.1 版本号约定

跟随 `git tag`：`v0.1.0` / `v0.2.0-rc1` 等。

Makefile 自动注入：

```makefile
GIT_COMMIT := $(shell git rev-parse --short HEAD)
BUILD_TIME := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
VERSION := $(shell git describe --tags --always --dirty)
```

二进制里查看：

```bash
./build/templatecenter -version
# 应输出: version=v0.1.0-3-g4685519 commit=4685519 build_time=2026-08-18T10:00:00Z
```

### 4.2 发布 checklist

- [ ] 本地 `make build` 通过
- [ ] `gofmt -l ./pkg/ ./cmd/` 输出空
- [ ] `GOOS=linux GOARCH=amd64 go vet ./...` 干净
- [ ] `make test` 通过（编译验证）
- [ ] 本地启动 + `/health` 返回 200
- [ ] 至少跑通一次 `POST /cube/template/from-image` 全流程（建议 staging 环境）
- [ ] git tag 打好（`git tag -a v0.1.0 -m "..."`）
- [ ] 镜像 push 到 registry
- [ ] 更新 `deploy/kubernetes/chart/values.yaml` 的 `image.tag`

### 4.3 生产部署

**k8s 场景**：

```bash
helm upgrade --install cube ./deploy/kubernetes/chart \
  --set controlPlane.templatecenter.enabled=true \
  --set controlPlane.templatecenter.image.tag=v0.1.0 \
  -f values-tke.yaml
```

**terraform 场景**：

```bash
cd deploy/one-click/terraform/tencentcloud
# 修改 env: cube_version=v0.1.0
./create.sh
```

## 五、验证清单（端到端）

### 5.1 探针

```bash
curl -sf http://<tc>:8090/health | jq .
# 期望 200, {"status":"ok","checks":{"nodemeta":true,"templatecenter_store":true}}
```

### 5.2 全流程（从提交到 ready）

```bash
# 1. 提交一个 build 请求
curl -X POST http://<tc>:8090/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{
    "image_url": "nginx:latest",
    "size_mb": 1024,
    "instance_type": "S5.MEDIUM2"
  }' | jq .
# 期望: {"job_id":"...","artifact_id":"..."}

# 2. 轮询进度
JOB_ID="<从上一步拿>"
for i in $(seq 1 60); do
  STATUS=$(curl -s http://<tc>:8090/cube/template/build/$JOB_ID/status | jq -r .status)
  PHASE=$(curl -s http://<tc>:8090/cube/template/build/$JOB_ID/status | jq -r .phase)
  echo "[$i] status=$STATUS phase=$PHASE"
  [ "$STATUS" = "Built" ] || [ "$STATUS" = "Ready" ] && break
  sleep 5
done

# 3. 验证产物落盘
kubectl exec -it <tc-pod> -- ls -lh /data/CubeTemplateCenter/storage/
# 期望: <artifactID>/<artifactID>.ext4 存在, 大小 ~1 GiB

# 4. 验证 DB 状态
mysql -h <mysql> -ucube -pcube_pass cube_mvp -e \
  "SELECT id, status, phase, progress FROM template_image_jobs WHERE id='$JOB_ID'"
# 期望: status=Built or Ready, progress=100
```

### 5.3 重启持久化验证

```bash
# 1. 触发一个 build, 等 Built
# 2. 重启 pod
kubectl delete pod <tc-pod>

# 3. 等重启完成
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/component=templatecenter

# 4. 验证 ext4 还在
kubectl exec -it <new-tc-pod> -- ls /data/CubeTemplateCenter/storage/
# 期望: <artifactID>/ 目录还在

# 5. 验证 /health 200
curl -sf http://<tc>:8090/health

# 6. 触发同 artifact 的另一个 build (走 artifact_build.go:56 互斥锁)
# 期望: 命中已有产物, 不重复构建 (log 里 see "artifact already exists, reusing")
```

### 5.4 多副本构建锁验证（PR 5 接入 DB 会话锁后）

```bash
# 起 2 个 TC 副本
kubectl scale deployment cube-templatecenter --replicas=2

# 同时提交同一个 artifact 的 build
for i in 1 2; do
  curl -X POST http://<tc>:8090/cube/template/from-image \
    -H "Content-Type: application/json" \
    -d '{"image_url":"nginx:latest","size_mb":1024}' &
done
wait

# 查 DB 会话锁
mysql -h <mysql> -ucube -pcube_pass -e \
  "SELECT * FROM performance_schema.metadata_locks WHERE lock_type='User-level lock'"

# 期望: 只有 1 个 replica 拿到锁, 另一个 replica 立即返回 (或等锁释放)

# 最终两个 job 的 artifact_id 应相同 (命中同一产物)
```

### 5.5 错误场景

| 场景 | 期望行为 | 验证 |
|---|---|---|
| 无效 image_url | 立即返回 `image_ref_invalid` | `curl -X POST ... -d '{"image_url":"bad url"}'` |
| CA 证书缺失 | 失败 phase=PULLING | 删掉 CA 文件后 POST |
| 磁盘满 | 失败 phase=BUILDING_EXT4 | `dd if=/dev/zero of=/data/fill bs=1G count=200` |
| DB 连接断 | /health 503 + 日志报错 | `systemctl stop mysqld` |
| Redis 连接断 | 业务仍可用（进度上报失败，主流程不阻塞） | `systemctl stop redis` |
| 构建中途 kill -9 | 下次启动时 artifact_gc 清理脏行 | `kill -9 <tc-pid>` |

## 六、性能基准

### 6.1 构建耗时（参考）

| 镜像大小 | ext4 大小 | 预估耗时 |
|---|---|---|
| nginx:latest (~150MB) | 1 GiB | 30-60s |
| ubuntu:22.04 (~80MB) | 1 GiB | 25-50s |
| python:3.11 (~900MB) | 1.5 GiB | 90-150s |

### 6.2 并发构建

- 单 TC 副本：`max_concurrent`（`image/disk.go:61`）默认 runtime.NumCPU()，实际 mkfs ext4 是 IO bound，4-8 并发足够
- 多 TC 副本：受 DB 会话锁保护，同 artifact 串行，不同 artifact 并行

### 6.3 资源消耗

| 指标 | 单 build |
|---|---|
| CPU | 1-2 core（mkfs + sha256 计算） |
| 内存 | 500MB - 1GB（vda 写入缓冲） |
| 磁盘 | 1-2 GiB（产物）+ 临时 vda 文件（写完即删） |
| 网络 | 拉镜像 + 推产物到 cbs（取决于 registry / cbs 带宽） |

## 七、故障排查

### 7.1 日志位置

```
/data/CubeTemplateCenter/log/
├── templatecenter.INFO         # 业务日志
├── templatecenter.WARN
├── templatecenter.ERROR
├── stdout.log                  # systemd stdout
└── stderr.log                  # systemd stderr
```

### 7.2 常用排查命令

```bash
# 看实时日志
tail -F /data/CubeTemplateCenter/log/templatecenter.INFO | grep -E "artifact_id|job_id"

# 查 loop 设备占用
losetup -a | grep -v deleted

# 查 DB 会话锁
mysql -e "SELECT * FROM performance_schema.metadata_locks WHERE lock_type='User-level lock'"

# 查磁盘
df -h /data/CubeTemplateCenter/storage
du -sh /data/CubeTemplateCenter/storage/*

# 看 metrics
curl http://<tc>:8090/metrics | grep template_
```

### 7.3 panic / goroutine 泄漏排查

```bash
# 发 SIGQUIT 拿 goroutine dump
kill -QUIT <tc-pid>
# 日志里会有完整 goroutine 栈

# 或 pprof (如果开启)
go tool pprof http://<tc>:8090/debug/pprof/goroutine
```

## 八、回归测试 checklist（每次发版前）

- [ ] `make build` 通过
- [ ] 二进制能启动，`/health` 200
- [ ] POST from-image → 全流程到 Ready
- [ ] 重启 pod 后 ext4 还在
- [ ] 多副本下同 artifact 不重复构建（看锁）
- [ ] DB 会话锁正确释放（`metadata_locks` 表无残留）
- [ ] metrics 端点有数据
- [ ] 日志级别可调（`kubectl edit configmap` 改 log.level，无需重启）
