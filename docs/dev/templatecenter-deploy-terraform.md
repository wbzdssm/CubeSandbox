# TemplateCenter terraform / one-click 部署方案

**适用场景**：没有 k8s 集群、直接落 CVM / 黑石 / Lighthouse 的部署形态。参照 `deploy/one-click/terraform/tencentcloud/` 现有模式。

## 一、当前 one-click 结构

```
deploy/one-click/terraform/tencentcloud/
├── main.tf               # 资源入口 (CVM / VPC / SG / CLB)
├── variables.tf          # 变量
├── env.example           # 用户复制的模板
├── create.sh / destroy.sh / validate.sh
├── build_images.sh       # 构建 + push 镜像
├── lib-phases.sh         # 分阶段执行
└── tke-addons.tf         # (可选) TKE addon
```

**两种部署形态**：

| 形态 | 触发方式 | 适用 |
|---|---|---|
| **CVM 直部**（无 k8s） | terraform 起 CVM + userdata 装 master + node + mysql + redis | 开发 / 小规模 POC |
| **TKE** | terraform 起 TKE + helm install chart | 生产 |

**本文档聚焦 CVM 直部**，TKE 走 chart（见 `templatecenter-deploy-k8s-pvc.md`）。

## 二、TC 在 CVM 直部下怎么落

### 2.1 目录约定（与 CubeMaster 同构）

```
/data/CubeTemplateCenter/         # TC 工作目录
├── storage/                       # ext4 产物 (对应 paths.go:18 的 defaultArtifactStoreDir)
│   └── <artifactID>/
│       └── <artifactID>.ext4
├── log/                           # 业务日志
└── conf.yaml                      # 配置
```

**CVM 直部时 `/data` 必须是数据盘**，不能是系统盘。terraform 创建 CVM 时挂 CBS 数据盘。

### 2.2 terraform 资源（新增）

`deploy/one-click/terraform/tencentcloud/templatecenter.tf`（新文件）：

```hcl
# ============================================================
# TemplateCenter CVM (可选, count = var.enable_templatecenter)
# ============================================================

variable "enable_templatecenter" {
  description = "是否单独部署 TemplateCenter CVM (false = 和 CubeMaster 同 CVM)"
  type        = bool
  default     = false  # 第一版默认同机部署, 拆出去后再改 true
}

variable "templatecenter_instance_type" {
  description = "TC CVM 机型 (建议 4C8G 起步, mkfs ext4 需要 CPU)"
  type        = string
  default     = "S5.LARGE8"
}

variable "templatecenter_data_disk_size" {
  description = "TC 数据盘大小 GiB (100 个模板 × 1-2 GiB, 留冗余)"
  type        = number
  default     = 200
}

# 数据盘
resource "tencentcloud_cbs_storage" "templatecenter_data" {
  count              = var.enable_templatecenter ? 1 : 0
  storage_name       = "${local.name_prefix}-tc-data"
  storage_type       = "CLOUD_PREMIUM"
  storage_size       = var.templatecenter_data_disk_size
  availability_zone  = data.tencentcloud_availability_zones.default.zones[0].name
  encrypt            = true
  tags               = local.common_tags
}

# TC CVM
resource "tencentcloud_instance" "templatecenter" {
  count                      = var.enable_templatecenter ? 1 : 0
  instance_name              = "${local.name_prefix}-tc"
  availability_zone          = data.tencentcloud_availability_zones.default.zones[0].name
  instance_type              = var.templatecenter_instance_type
  image_id                   = data.tencentcloud_images.ubuntu.images[0].image_id
  system_disk_type           = "CLOUD_PREMIUM"
  system_disk_size           = 50
  vpc_id                     = tencentcloud_vpc.main.id
  subnet_id                  = tencentcloud_subnet.main.id
  security_groups            = [tencentcloud_security_group.templatecenter[0].id]
  allocate_public_ip         = false
  orderly_security_groups    = []

  # 数据盘挂载
  data_disks {
    data_disk_type = "CLOUD_PREMIUM"
    data_disk_size = var.templatecenter_data_disk_size
    encrypt        = true
    delete_with_instance = false    # 保留数据盘 (避免误删)
  }

  # 用户数据脚本 (见 §2.3)
  user_data = base64encode(templatefile("${path.module}/templatecenter-userdata.sh.tftpl", {
    version           = var.cube_version
    mysql_addr        = tencentcloud_instance.cubemaster[0].private_ip
    redis_addr        = tencentcloud_instance.cubemaster[0].private_ip
    cubemaster_addr   = tencentcloud_instance.cubemaster[0].private_ip
  }))

  tags = local.common_tags
}

# 安全组 (TC 只暴露 8090 给 VPC 内部)
resource "tencentcloud_security_group" "templatecenter" {
  count       = var.enable_templatecenter ? 1 : 0
  name        = "${local.name_prefix}-tc-sg"
  description = "TemplateCenter security group"
}

resource "tencentcloud_security_group_rule_set" "templatecenter" {
  count             = var.enable_templatecenter ? 1 : 0
  security_group_id = tencentcloud_security_group.templatecenter[0].id
  ingress {
    action      = "ACCEPT"
    cidr_block  = var.vpc_cidr
    protocol    = "TCP"
    port        = "8090"
    description = "TC HTTP (VPC internal)"
  }
  ingress {
    action      = "ACCEPT"
    cidr_block  = var.admin_cidr
    protocol    = "TCP"
    port        = "22"
    description = "SSH"
  }
  egress {
    action      = "ACCEPT"
    cidr_block  = "0.0.0.0/0"
    protocol    = "ALL"
    port        = "ALL"
  }
}

output "templatecenter_private_ip" {
  value = var.enable_templatecenter ? tencentcloud_instance.templatecenter[0].private_ip : "not_enabled"
}
```

### 2.3 userdata 脚本 `templatecenter-userdata.sh.tftpl`

```bash
#!/bin/bash
set -euxo pipefail

VERSION="${version}"
MYSQL_ADDR="${mysql_addr}"
REDIS_ADDR="${redis_addr}"
CUBEMASTER_ADDR="${cubemaster_addr}"

# ========== 1. 挂载数据盘到 /data ==========
# terraform 已经在 data_disks 里创建 CBS, 但默认不会自动挂载
DATA_DISK="/dev/vdb"   # 第一块数据盘
MOUNT_POINT="/data"

if ! mountpoint -q "$MOUNT_POINT"; then
    mkfs.ext4 "$DATA_DISK" || true
    mkdir -p "$MOUNT_POINT"
    mount "$DATA_DISK" "$MOUNT_POINT"
    # fstab 持久化
    echo "$DATA_DISK $MOUNT_POINT ext4 defaults 0 0" >> /etc/fstab
fi

# ========== 2. 创建 TC 工作目录 ==========
mkdir -p /data/CubeTemplateCenter/{storage,log}

# ========== 3. 加载 loop 模块 (mkfs ext4 需要) ==========
modprobe loop max_loop=32 || true
for i in $(seq 0 31); do
    [ -b "/dev/loop$i" ] || mknod "/dev/loop$i" b 7 "$i"
done
# 持久化
echo "loop" > /etc/modules-load.d/loop.conf
echo "options loop max_loop=32" > /etc/modprobe.d/loop.conf

# ========== 4. 下载 TC 二进制 ==========
mkdir -p /usr/local/services/cubetoolbox/CubeTemplateCenter
cd /usr/local/services/cubetoolbox/CubeTemplateCenter

curl -fsSL "https://github.com/TencentCloud/CubeSandbox/releases/download/v$VERSION/templatecenter-linux-amd64" \
    -o templatecenter
chmod +x templatecenter

# ========== 5. 写配置 ==========
cat > conf.yaml <<EOF
common:
  http_port: 8090
  http_readtimeout: 120
  http_writetimeout: 360
  http_idletimeout: 360

log:
  module: templatecenter
  path: /data/CubeTemplateCenter/log
  file_size: 100
  file_num: 10
  level: info

instance_db_config:
  addr: "$MYSQL_ADDR:3306"
  user: cube
  pwd: cube_pass
  db_name: cube_mvp
  conn_timeout: 5
  read_timeout: 5
  write_timeout: 5
  max_idle_conns: 5
  max_open_conns: 20
  max_conn_life_time_seconds: 300

redis:
  nodes: "$REDIS_ADDR:6379"
  password: ceuhvu123
  db_no: 0
  max_idle: 8
  max_active: 32
  idle_timeout: 30
  max_retry: 2

auth:
  enable: false
EOF

# ========== 6. systemd 服务 ==========
cat > /etc/systemd/system/templatecenter.service <<EOF
[Unit]
Description=CubeSandbox TemplateCenter
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/usr/local/services/cubetoolbox/CubeTemplateCenter
ExecStart=/usr/local/services/cubetoolbox/CubeTemplateCenter/templatecenter -conf=/usr/local/services/cubetoolbox/CubeTemplateCenter/conf.yaml
Restart=on-failure
RestartSec=5
LimitNOFILE=65536
StandardOutput=append:/data/CubeTemplateCenter/log/stdout.log
StandardError=append:/data/CubeTemplateCenter/log/stderr.log

# 关键: 数据盘必须在 TC 启动前挂载完成
RequiresMountsFor=/data

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable templatecenter
systemctl start templatecenter

# ========== 7. 等待启动 ==========
for i in $(seq 1 30); do
    if curl -sf http://localhost:8090/health > /dev/null; then
        echo "TemplateCenter is ready"
        exit 0
    fi
    sleep 2
done
echo "TemplateCenter failed to become ready in 60s"
systemctl status templatecenter
exit 1
```

### 2.4 在 `main.tf` 引用

```hcl
# main.tf 末尾追加
module "templatecenter" {
  source = "./templatecenter.tf"
  count  = var.enable_templatecenter ? 1 : 0
  # ... 传入依赖
}
```

或者直接合并到 `main.tf`（不分模块，和现状一致）。

## 三、TC 和 CubeMaster 同 CVM 还是分 CVM？

### 3.1 同 CVM（默认，第一版推荐）

```
CVM (4C8G+)
├── CubeMaster  :8089
├── TemplateCenter :8090     ← 同机部署
├── MySQL       :3306
├── Redis       :6379
└── /data/
    ├── CubeMaster/storage/     (master 用)
    └── CubeTemplateCenter/storage/  (TC 用, 同一个数据盘)
```

**优势**：
- 资源复用（master 和 TC 都不忙）
- master 直接 `os.Open` 读 TC 写的 ext4，零网络
- 数据盘共享一个，运维简单

**劣势**：
- 单点故障（一台挂了 master 和 TC 都没了）
- 无法独立扩缩容

**默认**：`enable_templatecenter=false`（同机），直接在 master 的 userdata 里加一个 systemd service：

```bash
# master 的 userdata.sh.tftpl 追加
cat > /etc/systemd/system/templatecenter.service <<EOF
# ... 同上 §2.3
EOF
systemctl daemon-reload
systemctl enable templatecenter
systemctl start templatecenter
```

### 3.2 分 CVM（`enable_templatecenter=true`，生产推荐）

```
CVM-1 (CubeMaster)          CVM-2 (TemplateCenter)
├── CubeMaster  :8089       ├── TemplateCenter :8090
├── MySQL       :3306       └── /data/CubeTemplateCenter/storage/
├── Redis       :6379           (独立数据盘)
└── /data/CubeMaster/storage/
```

**优势**：
- 独立扩缩容（TC 是 CPU/磁盘密集，master 是 IO 密集）
- 独立升级（升级 TC 不影响 master）
- 故障域隔离

**劣势**：
- master 拉 ext4 必须走 HTTP（跨网络）
- 资源浪费（两台 CVM）

**关键改动**：分 CVM 时 master 的 `GET /cube/template/artifact/download` 必须转发到 TC 的 8090：

```yaml
# master 的 conf.yaml 加
templatecenter_endpoint: "http://<tc-private-ip>:8090"
```

或者直接在 master 的 handler 里改成 redirect（master 返回 302，Cubelet 直连 TC 拉 ext4）。

## 四、与 k8s 场景的存储对比

| 维度 | CVM 直部（terraform） | k8s（helm chart） |
|---|---|---|
| 数据盘 | terraform `tencentcloud_cbs_storage` 资源 | k8s `PersistentVolumeClaim` |
| 挂载 | userdata 脚本 `mount /dev/vdb /data` | volumeMount `/data/CubeTemplateCenter/storage` |
| 数据盘删除保护 | `delete_with_instance = false` | PV `persistentVolumeReclaimPolicy: Retain` |
| 故障转移 | 手动（terraform 重建 CVM，数据盘重新 attach） | 自动（pod 重调度，PV 自动 attach） |
| 多副本 | terraform `count = 3` + LB | `replicas: 3` + Service |
| loop 设备 | userdata `modprobe loop` | daemonset `node-loop-preflight` |

## 五、数据库 / Redis 部署形态

CVM 直部时 MySQL / Redis 通常和 master 同机部署（小规模），或外挂云上 TencentDB（生产）：

```hcl
# 小规模: 同机 docker-compose / systemd
# 生产: 外挂
resource "tencentcloud_mysql_instance" "main" {
  instance_name = "${local.name_prefix}-mysql"
  engine_version = "8.0"
  # ...
}

resource "tencentcloud_redis_instance" "main" {
  type_id = 6   # 标准版
  # ...
}
```

TC 的 `conf.yaml` 填外挂地址即可，无差别。

## 六、验证

```bash
# 1. terraform 起 CVM
cd deploy/one-click/terraform/tencentcloud
cp env.example env
# 编辑 env: enable_templatecenter=true, templatecenter_data_disk_size=200
source env
./create.sh

# 2. SSH 到 TC CVM 验证
ssh ubuntu@<tc-private-ip>

# 2.1 数据盘挂载
df -h /data
# 应输出 /dev/vdb on /data

# 2.2 TC 进程
systemctl status templatecenter
# 应输出 active (running)

# 2.3 /health 探针
curl http://localhost:8090/health | jq .
# 应输出 {"status":"ok","checks":{"nodemeta":true,"templatecenter_store":true}}

# 2.4 重启后 ext4 保留
systemctl restart templatecenter
ls /data/CubeTemplateCenter/storage/
# 应有 <artifactID>/ 目录

# 3. 触发一个 build, 看 ext4 落盘
curl -X POST http://localhost:8090/cube/template/from-image \
  -H "Content-Type: application/json" \
  -d '{"image_url":"nginx:latest","size_mb":1024}'

# 等 30s 后
ls /data/CubeTemplateCenter/storage/
# 应有新 artifact 目录

# 4. 模拟 CVM 重启
reboot
# SSH 回来
ls /data/CubeTemplateCenter/storage/
# 文件还在 (数据盘持久化)
systemctl status templatecenter
# 应输出 active (running), systemd 自动拉起
```

## 七、回滚 / 故障处理

| 场景 | 处理 |
|---|---|
| 数据盘满 | 控制台扩容 CBS → `resize2fs /dev/vdb` |
| TC 起不来 | `journalctl -u templatecenter -n 100 --no-pager` |
| loop 设备满 | `losetup -a \| grep deleted \| awk '{print $1}' \| xargs -I{} losetup -d {}`（PR #1295 自动 reclaim） |
| 误删 CVM | 重新跑 `./create.sh`，数据盘 `delete_with_instance=false` 保留，重挂即可 |
| 多副本构建冲突 | 查 `SELECT * FROM performance_schema.metadata_locks WHERE lock_type='User-level lock'` |

## 八、升级流程

```bash
# 1. 上传新二进制
scp templatecenter-linux-amd64 ubuntu@<tc-ip>:/tmp/

# 2. SSH 升级
ssh ubuntu@<tc-ip>
sudo systemctl stop templatecenter
sudo cp /tmp/templatecenter-linux-amd64 /usr/local/services/cubetoolbox/CubeTemplateCenter/templatecenter
sudo systemctl start templatecenter
curl http://localhost:8090/health
```

自动化：用 `build_images.sh` 模式做一个 `deploy_templatecenter.sh`。
