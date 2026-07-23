# `perf` — CubeSandbox 性能压测套件

一条命令跑完所有场景，产出 **JSON + Markdown** 报告。

## 快速开始

```bash
cd CubeSandbox/tests
cp perf/.env.example perf/.env      # 编辑填入 CUBE_API_URL / CUBE_TEMPLATE_ID
python3 -m perf

# 只跑指定场景
python3 -m perf --scenarios clone-concurrency

# 列出全部场景
python3 -m perf --list-scenarios
```

## 常用命令

| 命令 | 说明 |
|------|------|
| `python3 -m perf` | 跑全部场景 |
| `python3 -m perf --rounds 20` | 每场景 20 轮 |
| `python3 -m perf --scenarios clone-concurrency` | 只跑指定场景 |
| `python3 -m perf --list-scenarios` | 列出已注册场景 |
| `python3 -m perf --cleanup` | 清理 `snap-*` 快照 |
| `python3 -m perf --cleanup-dry-run` | 预览将要清理的快照 |
| `python3 -m perf --md-only report.json` | 从 JSON 重渲染报告 |

## 输出文件

运行后，报告写入当前目录（`tests/`）：

```
tests/
├── report.json      # 完整数据的 JSON（含环境、配置、性能指标）
├── report.md        # Markdown 报告（英文）
└── report.zh.md     # Markdown 报告（中文）
```

通过 `CUBE_OUTPUT_REPORT` 可自定义输出基名（默认 `report`）。

## 清理行为

框架在**每轮结束后**和**所有场景完成后**会自动清理压测残留。具体操作：

| 时机 | 清理内容 | 如何关闭 |
|------|---------|---------|
| 每轮结束 | 本轮创建的沙箱（逐个 kill） | `CUBE_PERF_CLEANUP=0` |
| 全部场景后 | `snap-*` 快照模板（通过 SDK 删除） | `CUBE_PERF_AUTO_CLEANUP=0` |
| 手动触发 | 全部 `snap-*` 快照，不区分新旧 | `python3 -m perf --cleanup` |

**注意**：
- 只删除 ID 以 `snap-` 开头的快照模板，不触碰用户自有模板（`tpl-*`）
- 有活跃沙箱引用的快照（`replicas` 非空）自动跳过，不会报错
- 清理前等待 `CUBE_PERF_AUTO_CLEANUP_WAIT` 秒（默认 5s），确保异步操作完成
- `--cleanup-dry-run` 预览将被删除的快照列表，不执行实际删除

首次运行自动生成 `tests/perf/.env`，所有变量均可在 `.env.example` 中找到完整注释。参数优先级：CLI > 环境变量 > .env。

### 连接

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CUBE_API_URL` | `http://127.0.0.1:3000` | CubeMaster API 地址 |
| `CUBE_API_KEY` | — | API 密钥（可选） |
| `CUBE_TEMPLATE_ID` | 自动发现 | 模板 ID（留空自动查找 READY 模板） |
| `CUBE_PROXY_NODE_IP` | — | 直连节点 IP（跳过 DNS） |
| `CUBE_PROXY_PORT_HTTP` | `80` | 代理端口 |
| `CUBE_SANDBOX_DOMAIN` | `cube.app` | 沙箱域名 |

### 运行参数

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CUBE_PERF_ROUNDS` | `3` | 每场景轮数 |
| `CUBE_PERF_WARMUP` | `1` | 预热轮数（不计统计） |
| `CUBE_PERF_SETTLE` | `0` | 档间静默秒数 |
| `CUBE_PERF_CONCURRENCY` | `1,5,10` | 默认并发度阶梯 |
| `CUBE_CREATE_CONCURRENCY` | `1,10,20,50` | 创建场景并发阶梯 |
| `CUBE_DENSITY_COUNT` | `100` | 密度测试沙箱数上限 |
| `CUBE_PERF_CLEANUP` | `1` | 轮间清理沙箱（设 0 关闭） |

### 自动清理

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CUBE_PERF_AUTO_CLEANUP` | `1` | 压测后自动清除残留快照（只清 `snap-*`，不过滤非 snap-* 模板） |
| `CUBE_PERF_AUTO_CLEANUP_WAIT` | `5` | 清理前等待异步任务完成的秒数 |

### 外部脚本

| 变量 | 说明 |
|------|------|
| `CUBE_EXTERNAL_SCRIPTS` | 逗号分隔的脚本路径，支持 `*` glob |

## 内置场景

框架自带 6 个场景，通过 `CUBE_EXTERNAL_SCRIPTS` 默认注册，位于 `../examples/snapshot-rollback-clone/`：

| 场景 | Key | 说明 |
|------|-----|------|
| 克隆（并发） | `clone-concurrency` | 多并发克隆沙箱 |
| 创建（并发） | `create-concurrency` | 多并发创建沙箱 |
| 快照创建（并发） | `snapshot-concurrency` | 多并发创建快照 |
| 快照回滚（并发） | `rollback-concurrency` | 多并发回滚到快照 |
| 暂停 & 恢复（并发） | `pause-resume-concurrency` | 多并发暂停和恢复 |
| 脏页快照 | `snapshot-dirty` | 不同脏页大小下的快照制作耗时 |

## 接入新场景

详见 [脚本集成契约](docs/guide/perf-design.zh.md)。简单来说：写一个接受 `-c <并发>` `-n <次数>` 参数的脚本，在 `.env` 注册即可。
