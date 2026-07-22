# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""业务模块 — 场景注册、快照管理及压测后资源清理。

与 framework（执行引擎）、reporting（报告输出）、plugins（前端 HTML
生成）解耦。各模块通过 SDK 调用底层 API，不直接拼 HTTP。

模块：
    snapshot.py   — 快照列表、删除（仅 snap-*；不动模板）
    scenarios.py  — 默认脚本导入、注册与清理编排
"""
