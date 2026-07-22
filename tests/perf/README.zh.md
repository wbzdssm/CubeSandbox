# `perf` — CubeSandbox 性能压测套件

一条命令跑完所有场景，产出 **JSON + Markdown** 报告（加 `--html` 生成可视化）。

## 快速开始

```bash
cd CubeSandbox/tests
python3 -m perf                     # 本地后端
CUBE_API_URL=http://1.2.3.4:3000 python3 -m perf   # 远端
python3 -m perf --html              # 加 HTML 报告
```

`CUBE_TEMPLATE_ID` 留空自动发现 READY 模板。

## 场景来源

所有压测场景来自外部脚本，在 `.env` 中配置 `CUBE_EXTERNAL_SCRIPTS`（逗号分隔）：

```bash
# tests/.env
CUBE_EXTERNAL_SCRIPTS=../sdk/python/examples/snapshot-rollback-clone/bench_clone_concurrency.py,\
                      ../sdk/python/examples/snapshot-rollback-clone/bench_create_concurrency.py
```

也可 CLI 一次性跑目录：

```bash
python3 -m perf --scripts /my/dir/
```

每个脚本按文件名独立注册，`--list-scenarios` 看全部，`--only X Y` 只跑指定。

## 并发阶梯

| 变量 | 默认值 | 作用域 |
|------|--------|--------|
| `CUBE_CREATE_CONCURRENCY` | `1,10,20,50` | 所有外部脚本的默认阶梯 |
| `CUBE_PERF_CONCURRENCY` | `1,5,10` | 轻量场景备用 |

按场景单独覆盖（覆盖以上全局默认）：

```bash
# CUBE_CLONE_CONCURRENCY=1,5,10
# CUBE_TEMPLATE_CREATE_CONCURRENCY=1,10,20
# CUBE_SNAPSHOT_CREATE_FROM_CONCURRENCY=1,10,20
# CUBE_SNAPSHOT_CREATE_CONCURRENCY=1,3,5
# CUBE_ROLLBACK_CONCURRENCY=1,3,5
# CUBE_PAUSE_RESUME_CONCURRENCY=1,5,10
```

命名规则：`CUBE_<SCENARIO_KEY大写以_分隔>_CONCURRENCY`。不设走全局。

高并发超资源的档位自动 `errors=N/total`（红色），不中断。

## CLI

| 选项 | 说明 |
|------|------|
| `--only KEY...` | 只跑指定场景 |
| `--rounds N` | 每场景轮数（默认 `CUBE_PERF_ROUNDS`） |
| `--html` | 生成 HTML 可视化报告（插件，懒加载） |
| `--list-scenarios` | 列出已注册的全部场景 |
| `--scripts DIR` | 跑目录下所有 `.py` |
| `--cleanup` | 跑前删全部 `snap-*` 快照 |
| `--cleanup-dry-run` | 预览 `--cleanup` |
| `--md-only JSON` | 从 JSON 重渲染 Markdown |
| `--html-only JSON...` | 从 JSON 生成 HTML |
| `--compare JSON...` | 多轮对比 HTML |

## 环境变量

### 连接

| 变量 | 默认 |
|------|------|
| `CUBE_API_URL` | `http://127.0.0.1:3000` |
| `CUBE_API_KEY` | — |
| `CUBE_TEMPLATE_ID` | 自动发现 |
| `CUBE_PROXY_NODE_IP` | — |
| `CUBE_PROXY_PORT_HTTP` | `80` |
| `CUBE_SANDBOX_DOMAIN` | `cube.app` |

### 运行参数

| 变量 | 默认 | 说明 |
|------|------|------|
| `CUBE_PERF_ROUNDS` | `3` | 每场景轮数 |
| `CUBE_PERF_WARMUP` | `1` | 预热轮数（不计统计） |
| `CUBE_PERF_SETTLE` | `0` | 档间静默秒数 |
| `CUBE_CREATE_CONCURRENCY` | `1,10,20,50` | 默认并发阶梯 |
| `CUBE_PERF_CONCURRENCY` | `1,5,10` | 轻量备选阶梯 |
| `CUBE_PERF_CLEANUP` | `1` | `0` 关闭轮间清理 |

### 外部脚本

| 变量 | 说明 |
|------|------|
| `CUBE_EXTERNAL_SCRIPTS` | 逗号分隔 `.py` 路径 |

## `.env`

首次启动在 `tests/` 下自动生成，跑完后实际用到的值二次写回，下次直接复用。详见 `.env.example`。

## 接入新脚本

> 框架只负责执行 + 统计。脚本方定义压测方案。

### 约定

```bash
python bench_xxx.py -c <并发度> -n <操作数> --rounds <轮数> --no-header
```

| 参数 | 必选 | 框架行为 |
|------|:---:|------|
| `-c N` | 是 | 按 `CUBE_CREATE_CONCURRENCY` 阶梯逐一调用 |
| `-n N` | 是 | 对应 `CUBE_PERF_ROUNDS` |
| `--rounds N` | 否 | 同上（脚本内部轮数） |
| `--no-header` | 否 | 抑制重复表头 |

### 示例

```python
# bench_clone.py
"""Clone concurrency benchmark."""    # 注释首行 → 报告标题

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

### 接入方式

```bash
# .env (推荐，跑完写回)
CUBE_EXTERNAL_SCRIPTS=/path/to/bench_clone.py,/path/to/bench_create.py

# CLI 一次性
python3 -m perf --scripts /path/to/scripts/
```
