# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
"""业务模块 — 快照创建、清理、模板管理等 CubeSandbox 平台操作的封装。

与 framework（核心执行引擎）、reporting（报告输出）、plugins（前端 HTML
生成）解耦。 各模块通过 SDK 调用底层 API，不直接访问 HTTP。
"""
