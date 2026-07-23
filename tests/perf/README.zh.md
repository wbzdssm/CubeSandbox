# `perf` — CubeSandbox 性能压测套件

一条命令跑完所有场景，产出 **JSON + Markdown** 报告。

## 快速开始

```bash
cd CubeSandbox/tests
cp perf/.env.example perf/.env      # 编辑填入 CUBE_API_URL / CUBE_TEMPLATE_ID
python3 -m perf
```

`CUBE_TEMPLATE_ID` 留空自动发现 READY 模板。

## 常用命令

| 命令 | 说明 |
|------|------|
| `python3 -m perf` | 跑全部场景 |
| `python3 -m perf --rounds 20` | 每场景 20 轮 |
| `python3 -m perf --scenarios clone-concurrency` | 只跑指定场景 |
| `python3 -m perf --list-scenarios` | 列出全部场景 |
| `python3 -m perf --cleanup` | 清理 `snap-*` 快照 |
| `python3 -m perf --md-only report.json` | 从 JSON 重渲染报告 |

## 接入新场景

详见 [Perf 脚本集成契约](../../docs/guide/perf-integration.zh.md)。

简单来说：写一个接受 `-c <并发>` `-n <次数>` 参数的脚本，在 `.env` 里注册即可。

## 配置

所有配置见 `tests/perf/.env.example`，首次运行自动生成 `.env`。

关键变量：

| 变量 | 说明 |
|------|------|
| `CUBE_API_URL` | API 地址 |
| `CUBE_TEMPLATE_ID` | 模板 ID |
| `CUBE_EXTERNAL_SCRIPTS` | 压测脚本路径（逗号分隔，支持 `*` 通配） |
| `CUBE_PERF_ROUNDS` | 每场景轮数 |
| `CUBE_PERF_CONCURRENCY` | 默认并发度阶梯 |
