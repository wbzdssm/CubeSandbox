# `perf` — CubeSandbox Python SDK 性能压测套件

[English](./README.md) · 架构细节见 [DESIGN.zh.md](./DESIGN.zh.md)

一条命令跑完所有场景，产出 **JSON + Markdown + HTML** 报告。

## 快速开始

```bash
cd sdk/python/tests

# 打本地后端（默认 http://127.0.0.1:3000），直接跑
python3 -m perf

# 打远端后端
CUBE_API_URL=http://9.135.79.34:3000 CUBE_API_KEY=sk-xxx python3 -m perf --html
```

不指定场景即跑全部。`CUBE_TEMPLATE_ID` 留空自动发现 READY 模板。

## 指定场景

```bash
python3 -m perf --only snapshot rollback       # 只跑部分
python3 -m perf --only ivshmem                 # 显式点名默认关闭的场景
python3 -m perf --scenarios all no-density     # 全部默认开启但排除 density
python3 -m perf --list-scenarios               # 列出全部场景
```

## 并发阶梯

| 变量 | 默认值 | 控制场景 |
|------|--------|---------|
| `CUBE_PERF_CONCURRENCY` | `1,5,10` | snapshot-create、rollback、pause-resume |
| `CUBE_CREATE_CONCURRENCY` | `1,10,20,50` | template-create、snapshot-create-from、clone |

```bash
CUBE_CREATE_CONCURRENCY=1,3,5 CUBE_PERF_CONCURRENCY=1,3,5 python3 -m perf --html
```

高并发超资源的档位会自动标记 `errors=N/total`（红色），不影响其他档位和报告。

## 场景一览

| 键 | 别名 | 默认 | 说明 |
|---|---|:---:|---|
| `template-create` | `create` | 开 | 基于模板创建沙箱冷启动 |
| `snapshot-create-from` | `cold-start`、`coldstart`、`restore` | 开 | 基于快照启动沙箱 |
| `clone` | — | 开 | 从运行中沙箱 clone 派生 |
| `snapshot-create` | `snapshot` | 开 | 并发制作快照 |
| `rollback` | — | 开 | 原地回滚到指定快照 |
| `pause-resume` | `pause`、`resume` | 开 | 并发暂停 & 恢复 |
| `snapshot-dirty` | `dirty` | 开 | 快照耗时 vs 脏页规模 |
| `density` | — | 开 | 部署密度：累积起沙箱测内存开销 |
| `ivshmem` | — | **关** | host 侧 ivshmem mmap（需节点支持） |
| `volume-create` | `volume` | **关** | 创建 Volume |
| `volume-destroy` | `volume` | **关** | 删除 Volume |
| `volume-metadata` | `volume` | **关** | Volume 元数据操作 |
| `volume-mount-sandbox` | `volume` | **关** | 挂载 Volume 的沙箱创建 |

### 场景开关

| 目的 | `.env` | CLI |
|------|--------|-----|
| 启用 ivshmem | `CUBE_RUN_IVSHMEM=1` | `--only ivshmem` |
| 启用 Volume | `CUBE_RUN_VOLUME=1` | `--only volume` |
| 跳过 density | `CUBE_SKIP_DENSITY=1` | `--scenarios all no-density` |
| 跳过 snapshot-dirty | `CUBE_SKIP_SNAPSHOT_DIRTY=1` | `--scenarios all no-dirty` |

## 输出

| 文件 | 格式 | 说明 |
|------|------|------|
| `report_<时间戳>.json` | JSON | 原始数据 |
| `report.md` / `report.zh.md` | Markdown | 完整报告 |
| `report.json` / `report.zh.json` | JSON | 报告摘要 |
| `perf_report.html` | HTML | 交互式可视化（加 `--html` 产出） |

```bash
# 从已有 JSON 重渲染（不连后端）
python3 -m perf --md-only report_xxx.json
python3 -m perf --html-only report_xxx.json
python3 -m perf --compare run1.json run2.json --output diff.html
```

## CLI 选项

| 选项 | 说明 |
|------|------|
| `--scenarios / --only KEY...` | 指定场景，`no-`/`skip-` 前缀排除 |
| `--rounds N` | 每场景轮数（默认 `CUBE_PERF_ROUNDS=10`） |
| `--html` | 运行后生成 HTML 报告 |
| `--list-scenarios` | 列出全部场景 |
| `--cleanup` | 跑前删除所有 `snap-*` 快照 |
| `--cleanup-dry-run` | 预览 `--cleanup` 将删除的快照，不执行 |
| `--cleanup-older-than N` | 配合 `--cleanup`，只删 N 分钟前的 |
| `--scripts DIR` | 跑 `DIR/` 下所有 `.py`（按 `CUBE_CREATE_CONCURRENCY` 阶梯并发计时） |
| `--md-only JSON` | 从已有 JSON 重渲染 Markdown |
| `--html-only JSON...` | 从已有 JSON 生成 HTML |
| `--compare JSON...` | 生成对比 HTML |

## 环境变量

### 连接

| 变量 | 默认 | 说明 |
|------|------|------|
| `CUBE_API_URL` | `http://127.0.0.1:3000` | CubeAPI 地址 |
| `CUBE_API_KEY` | — | API Key |
| `CUBE_TEMPLATE_ID` | 自动发现 | 指定模板 |
| `CUBE_PROXY_NODE_IP` | — | CubeProxy 节点 IP |
| `CUBE_PROXY_PORT_HTTP` | `80` | CubeProxy HTTP 端口 |
| `CUBE_SANDBOX_DOMAIN` | `cube.app` | 沙箱域名 |

### 运行

| 变量 | 默认 | 说明 |
|------|------|------|
| `CUBE_PERF_ROUNDS` | `10` | 每场景轮数 |
| `CUBE_PERF_CONCURRENCY` | `1,5,10` | 轻量场景并发阶梯 |
| `CUBE_CREATE_CONCURRENCY` | `1,10,20,50` | 重量场景并发阶梯 |
| `CUBE_PERF_WARMUP` | `1` | 预热轮数 |
| `CUBE_PERF_SETTLE` | `0` | 档间静默秒数 |
| `CUBE_DENSITY_COUNT` | `100` | 密度测试最大沙箱数 |
| `CUBE_PERF_CLEANUP` | `1` | `0` 关闭轮间清理 |
| `CUBE_EXTERNAL_SCRIPTS` | — | 逗号分隔的外部脚本路径 |

### 外部脚本

| 变量 | 默认 | 说明 |
|------|------|------|
| `CUBE_EXTERNAL_SCRIPTS` | — | 逗号分隔的外部脚本（`.py` 路径） |

## `.env` 配置

首次启动无 `.env` 时自动在 `tests/` 下生成（含所有常用注释占位），跑完后实际用到的值二次写回，下次直接 `python3 -m perf` 复用。CLI 参数和真实环境变量始终优先。

```bash
# 调小并发跑通后会自动固化，之后无需再 export
CUBE_CREATE_CONCURRENCY=1,3,5 python3 -m perf

# 手动改 tests/.env 固化场景开关
CUBE_RUN_IVSHMEM=1
CUBE_SKIP_DENSITY=1
```

## 接入新脚本

> **框架方只负责执行 + 统计**。脚本方定义压测方案。

### 约定

脚本接受这四个参数，框架负责按并发阶梯逐一调用、测 wall-clock、统结果：

```bash
python bench_xxx.py -c <并发度> -n <操作数> --rounds <轮数> --no-header
```

| 参数 | 必选 | 框架行为 |
|------|:---:|------|
| `-c N` | 是 | 框架按 `CUBE_CREATE_CONCURRENCY` 阶梯逐一调用 |
| `-n N` | 是 | 对应 `CUBE_PERF_ROUNDS` |
| `--rounds N` | 否 | 同上 |
| `--no-header` | 否 | 抑制重复表头 |

### 脚本示例

```python
# /root/my_benchmarks/bench_clone.py
"""Clone concurrency benchmark."""        # 注释首行 → 报告标题

import argparse
ap = argparse.ArgumentParser()
ap.add_argument("-c", type=int, default=1)
ap.add_argument("-n", type=int, default=5)
ap.add_argument("--rounds", type=int, default=3)
ap.add_argument("--no-header", action="store_true")
args = ap.parse_args()

from cubesandbox import Sandbox
sb = Sandbox.create("tpl-xxx")
sb.clone(n=args.n, concurrency=args.c)
sb.kill()
```

### 接入

```bash
# .env 里加一行（逗号分隔多个脚本）
CUBE_EXTERNAL_SCRIPTS=/root/my_benchmarks/bench_clone.py,/path/to/bench_create.py
```

或 CLI 直接跑一个目录：

```bash
python3 -m perf --scripts /root/my_benchmarks/
```

> `examples/snapshot-rollback-clone/bench_*.py` 已默认纳入，无需额外配置。

### 结果示例

```
============================================================
 [Perf] Clone concurrency benchmark
============================================================
  concurrency= 1: wall=512ms
  concurrency=10: wall=158ms
  concurrency=20: wall=140ms
  concurrency=50: wall=160ms
============================================================
```

统计和内置场景一起写入同一份报告。

---

> 需要更复杂的框架内场景（带自定义报告章节、图表），见 [DESIGN.zh.md](./DESIGN.zh.md) §4。
