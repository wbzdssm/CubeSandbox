# CubeSandbox Python SDK 性能压测套件 · 设计说明

> 这份文档讲 `sdk/python/tests/perf/` 压测套件是怎么搭起来的、当初为什么这么选。
> README 管「怎么用」，这里管「为什么这么设计、内部怎么跑」。
> 内容和代码一一对得上，改了代码记得回来同步。

---

## 1. 背景与目标

给 CubeSandbox Python SDK 做性能基准测试，就三个目标：**自己就能跑、跑几次结果稳、换台机器也能比**。
输出和官方性能报告（blog / iWiki）对得上：

- 覆盖沙箱全生命周期的关键操作：创建 / 快照 / 恢复 / 回滚 / 克隆 / 暂停恢复 / Volume / ivshmem。
- 每个场景都做**并发扫描**，给出 avg/min/p50/p95/p99/max、wall time、单次均摊、吞吐量。
- 报告出三份：机器读的 JSON、人读的 Markdown、能看图的 HTML。
- 多次跑、多台机器的 JSON 能合到同一张 HTML 里对比。
- 组件版本参与环境指纹，所以**同一台机器上不同 cube 组件版本**也能横向对比——换个 CubeMaster /
  CubeAPI / … 版本会自动拆成独立序列（而非被平均掉），图例上直接标出差异版本，便于定位版本引入的性能回退。

### 设计原则

1. **自包含**：`config` / `env` / `runner` / `report` 全放在 `perf/` 目录里，不碰同级的 `tests/e2e/`。
   两者是各自独立的 CLI 入口，只共用底层 SDK。
2. **零外部依赖**：只用标准库加 SDK 本来就有的 `httpx`。TOML 走 stdlib `tomllib`（3.11+），
   老版本自动降级成「不读 TOML」。
3. **加一个场景 = 丢一个文件**：场景像 pytest 那样自动发现——在 `cases/` 任意子包下放个
   `bench_<名字>.py`，`cases/__init__.py` 导入时就会自动收进来，不用手动维护导入列表。
   文件里的 `@benchmark`（或一行式 `@sandbox_benchmark`）把这个场景的别名、开关、报告分组
   都写在一处，注册表 / 运行顺序 / 报告分组全都自动派生出来。
4. **配置只走 env**：所有可调项用环境变量注入，也支持 `.env` 文件；真实环境变量永远优先。

---

## 2. 整体架构与数据流

```
                        ┌──────────────┐
  CUBE_* env / .env ───▶│  __init__.py │  sys.path 引导 + .env 加载
                        └──────┬───────┘
                               │
                     ┌─────────▼──────────┐
   python3 -m perf ─▶│    __main__.py     │  CLI：参数解析 / 模式分发
                     └─────────┬──────────┘
                               │  import perf.cases → 自动发现并导入所有 bench_*
                               │  （装饰顺序 == 运行顺序，填充 registry 三张派生表）
              ┌────────────────┼─────────────────────┐
              ▼                ▼                     ▼
       resolve_config()  collect_env_info()    registry.run_all()
    (framework/config)   (framework/env)       (framework/registry)
              │                │                     │
              │                │            select_benchmarks() 选择/排除
              │                │                     │
              │                │            逐场景执行 → 写入 PERF_RESULTS
              │                │                     │  (framework/runner 全局态)
              └────────────────┴──────────┬──────────┘
                                          ▼
                                  build_report_data(env)
                                   (reporting/report)
                                          │
                     ┌────────────────────┼─────────────────────┐
                     ▼                    ▼                     ▼
              report.json          report.md / .zh.md      generate_html()
           (带时间戳的原始数据)     (Markdown，中英双语)   (reporting/report_html)
                                                                │
                                                    多文件按环境指纹分组/合并
                                                                ▼
                                                        perf_report.html
```

关键点：

- **运行态是进程级全局**：`runner.PERF_RESULTS` 是个模块级列表，各场景往里 `append`，
  `report` / `__main__` 从里面读；重复跑之前先 `reset()` 清零。
- **JSON 是唯一权威数据**：`--html-only` / `--compare` 都只读 JSON，跟具体跑没关系——所以
  「跑一次存下 JSON，之后想重画 HTML、想多机合并都行」是这套的核心用法。

---

## 3. 模块职责

套件是一个自包含的包，按「框架核心 / 场景 / 报告」三层拆到三个子包：

```
perf/
├── __init__.py          # sys.path 引导 + .env 加载
├── __main__.py          # CLI 入口
├── framework/           # 框架核心（与具体场景无关）
│   ├── config.py        # resolve_config() + 运行期可调项常量
│   ├── env.py           # 环境信息 + 三级组件版本采集
│   ├── runner.py        # 计时/统计原语 + 场景生命周期 fixtures + 全局 PERF_RESULTS
│   └── registry.py      # 四个可组合装饰器 + sandbox_benchmark 糖 + 注册表 + select_benchmarks() + run_all()
├── cases/               # 场景包（pytest 风格自动发现 bench_*）
│   ├── __init__.py      # 遍历子包导入所有 bench_* 模块（排序后 == 运行顺序）
│   ├── clone/bench_clone.py
│   ├── ivshmem/bench_ivshmem.py + probe.py（ivshmem mmap 探针，非场景）
│   ├── lifecycle/bench_create.py · bench_density.py · bench_pause_resume.py
│   ├── snapshot/bench_create.py · bench_create_from.py · bench_dirty.py · bench_rollback.py
│   └── volume/bench_volume.py（4 个 volume-* 场景）
└── reporting/           # 报告体系（吃 JSON，出 MD/HTML）
    ├── report.py        # Markdown + JSON 报告（中英双语）
    ├── report_html.py   # 自包含交互式 HTML（多环境并排 + 折线图）
    └── report_config.py # HTML 报告的 TOML/env 可定制层
```

| 模块 | 职责 |
|---|---|
| `__init__.py` | `sys.path` 引导（定位 `sdk/python`）+ 零依赖 `.env` 加载（在任何子模块读 env 前执行） |
| `__main__.py` | CLI 入口：参数解析、`run` / `--html` / `--html-only` / `--compare` / `--list-scenarios` 模式分发；`import perf.cases` 触发所有场景注册 |
| `framework/config.py` | `resolve_config()` 解析 `Config`（含自动发现 READY 模板）+ 运行期可调项常量 |
| `framework/env.py` | 环境信息采集：主机 / CPU / 内存 / 磁盘 / 模板元数据 + 三级组件版本采集 |
| `framework/runner.py` | 计时与统计原语：`PerfResult` / `PerfSample` / `measure_parallel` / `percentile` / `print_parallel_stats` / `skip` / 全局 `PERF_RESULTS`；外加场景生命周期 fixtures（`sandbox()` / `snapshot()` / `sandbox_op()` / `sandbox_pool()` / `snapshot_pool()` / `volume_pool()`，见 [§4.5](#45-场景-fixtures沙箱--快照生命周期原语)） |
| `framework/registry.py` | 四个可组合装饰器（`@benchmark` / `@parallel_sweep` / `@metrics` / `@sandbox_action`）+ 一行式糖 `sandbox_benchmark` + 三张派生注册表（registry / aliases / report）+ `select_benchmarks()` + `run_all()` + 轻量 `collect_component_versions()`（控制台打印用，走 `/health`） |
| `cases/__init__.py` | 场景自动发现：`pkgutil.walk_packages` 收集所有 `bench_*` 模块，按点分路径**排序后**导入（== 运行/报告顺序） |
| `cases/**/bench_*.py` | 13 个场景，按业务域分子包（clone / ivshmem / lifecycle / snapshot / volume）；每个文件用 `@benchmark` 或 `@sandbox_benchmark` 声明 |
| `cases/ivshmem/probe.py` | ivshmem 共享内存 host 侧 mmap 探针（单字节 + 100B/1KB/100KB 块读写）；非 `bench_` 前缀，不被自动导入 |
| `reporting/report.py` | Markdown + JSON 报告生成（中英双语） |
| `reporting/report_html.py` | 自包含交互式 HTML 报告：多环境并排、折线图、按环境指纹分组合并 |
| `reporting/report_config.py` | HTML 报告的 TOML/env 可定制层（标题、字段列表、列数） |

---

## 4. 核心设计：`@benchmark` 装饰器与场景注册

一个场景的全部元数据都写在这一个装饰器里，省得「加个场景要改注册表、别名表、skip 块、报告模块」四处跑。
（四个装饰器和三张注册表都在 `framework/registry.py`；场景函数散在 `cases/**/bench_*.py`，
`cases/__init__.py` 导入时自动收集，装饰顺序就是运行顺序。）

```python
@benchmark(key, aliases=[...], opt_in_env="...", opt_out_env="...",
           skip_reason="...", available=bool, report=ReportGroup(...))
def bench_xxx(cfg: Config) -> None: ...
```

| 参数 | 作用 |
|---|---|
| `key` | 规范场景键——既是 CLI 选择用的键，也是运行顺序的锚点 |
| `aliases` | 友好别名，多个场景可共享（比如所有 `volume-*` 都挂 `volume`） |
| `opt_in_env` | 这个 env `== "1"` 才跑，否则跳过（默认关的场景：volume / ivshmem） |
| `opt_out_env` | 这个 env `== "1"` 就跳过（默认开的场景：density / snapshot-dirty） |
| `skip_reason` | opt-in 跳过时附带的人读提示 |
| `available` | import 期求值，`False` 就无条件跳过（比如可选的 `Volume` 类型没导入成功） |
| `report` | 给 HTML 报告贡献的图表 / 表格分组，可传一个或多个 `ReportGroup` |

装饰器的行为：

- **零成本包装**：只有声明了 gate（`available=False` / `opt_in_env` / `opt_out_env`）时才用
  `functools.wraps` 包一层做跳过判断；普通场景直接注册原函数，无额外开销。
- **三张派生表**由装饰器自动填充，业务代码从不手动维护：
  - `BENCHMARK_REGISTRY: dict[key, fn]` —— 有序（== 源码装饰顺序 == 运行顺序）。
  - `BENCHMARK_ALIASES: dict[alias, [key...]]` —— 别名到键的展开。
  - `REPORT_SCENARIOS: list[dict]` —— HTML 报告的图表分组（经 `default_report_scenarios()` 暴露给 `report_html`）。
- **重复 key 直接抛错**（`duplicate benchmark key`），防止静默覆盖。

### `ReportGroup`

一个场景可声明 0 / 1 / 多个图表分组（如 `pause-resume` 同时喂「暂停」和「恢复」两张图）：

```python
@dataclass(frozen=True)
class ReportGroup:
    title: str                    # 图表标题
    prefix: str | None = None     # 匹配的 scenario 名前缀（默认取 benchmark key）
    x_key: str = "c"              # x 轴键（<prefix>-<x_key><N>）
    x_label: str = "并发数"
    fallback: tuple[int, ...] = (1, 2, 4)
```

### 场景选择：`select_benchmarks()`

- 输入是键 / 别名列表，大小写不敏感；`no-` / `skip-` / `!` / `^` 前缀表示**排除**。
- 特殊别名 `all` 展开为全量。
- 只给排除项时，从全量集合减去。
- 未知 token 抛 `ValueError` 并列出全部合法选项。
- 结果**保持注册表顺序**去重。
- CLI 的 `--scenarios` / `--only` 与 env `CUBE_PERF_SCENARIOS` 由 `_resolve_selected()` 合并
  （CLI 优先），并在真正运行前先 `select_benchmarks()` 校验一次，拼写错误可及早报错。

---

## 4.5 场景 fixtures：沙箱 / 快照生命周期原语

很多场景里「建沙箱」只是铺垫、或者跑完要善后的东西，真正被测的是别的操作——回滚 / 暂停恢复 /
克隆 / ivshmem 上的操作，或者创建本身。这类场景都要一个稳定骨架：**建箱 → 跑被测操作 → 保证把箱杀掉**。
有的还更复杂：rollback 得顺手删掉快照，并发场景得攒一批资源最后统一清。

要是每个场景各写各的 `try/finally`，样板会到处都是，还特别容易漏杀、泄漏资源。所以
`framework/runner.py` 把这些生命周期套路抽成一组 `contextlib` 原语（不引新依赖），按「单个 / 批量」
和「沙箱 / 快照」两个维度铺开，同时给 [§4.6](#46-可组合装饰器报告--指标--并发--业务四层拆分)
的 `@sandbox_action` 当底座：

| 原语 | 形态 | yield | 退出清理 |
|---|---|---|---|
| `sandbox(cfg, ...)` | 单个沙箱上下文 | `Sandbox` | `kill()` |
| `snapshot(cfg, ...)` | 单沙箱 + 它的一个快照 | `(Sandbox, snap_id)` | 先删快照，再 `kill()` |
| `sandbox_op(cfg, action, ...)` | 把「建箱→做一件事→杀箱」折成一个可计时 callable | —（返回 `op()`） | 每次 `op()` 内建内杀 |
| `sandbox_pool()` | 一整批沙箱的收集器（线程安全 `add`） | `_Pool` | 批量 `kill()` |
| `snapshot_pool(cfg)` | 一整批快照 id 的收集器（线程安全 `add`） | `_Pool` | 批量删快照 |
| `volume_pool(cfg)` | 一整批卷的收集器（线程安全 `add`） | `_Pool` | 批量 `Volume.destroy()` |

三个 pool 共用同一个跟类型无关的 `_Pool`（`list + lock + add/len/iter`）和同一个
`_pool(teardown)` 引擎，差别只剩一行 teardown 闭包：`sb.kill()` / `delete_snapshot(id)` /
`Volume.destroy(id)`。这跟 `Sandbox.create(..., **create_opts)` 是一个路子——把变化点当参数传进去。

```python
@contextmanager
def sandbox(cfg, template_id=None, *, timeout=120, **create_opts) -> Iterator[Sandbox]:
    # 建箱 → yield sb → 退出时 best-effort kill()

@contextmanager
def snapshot(cfg, template_id=None, *, timeout=120, **create_opts) -> Iterator[tuple[Sandbox, str]]:
    # 在 sandbox() 之上打快照 → yield (sb, snap_id) → 退出时先删快照再杀箱

def sandbox_op(cfg, action, template_id=None, **create_opts) -> Callable[[], Any]:
    # 返回 op()：每次调用 `with sandbox(...) as sb: return action(sb)`，透传 action 返回值

def snapshot_op(cfg, action, template_id=None, **create_opts) -> Callable[[], Any]:
    # sandbox_op 的 snapshot 版：`with snapshot(...) as (sb, snap_id): return action(sb, snap_id)`

@contextmanager
def _pool(teardown) -> Iterator[_Pool]:                # 共享引擎：退出时对每个 item 跑 teardown
    # _Pool 类型无关（list + lock + add/len/iter）；teardown 逐个 best-effort（吞异常）

def sandbox_pool() -> AbstractContextManager[_Pool]:   # 无 cfg：只按对象 kill
    return _pool(lambda sb: sb.kill())

def snapshot_pool(cfg) -> AbstractContextManager[_Pool]:   # 带 cfg：删快照要 config
    return _pool(lambda snap_id: Sandbox.delete_snapshot(snap_id, config=cfg))

def volume_pool(cfg) -> AbstractContextManager[_Pool]:     # 带 cfg：destroy 要 config；存整个 Volume
    return _pool(lambda vol: Volume.destroy(vol.volume_id, config=cfg))
```

设计要点：

- **复用 pytest 的 setup/teardown 思路，但适配压测循环**：单个原语（`sandbox` / `snapshot`）
  每个 `with` 块产出**全新资源**，契合「每轮换新沙箱」，与 pytest「一个 fixture 用到底」相反。
- **单个 / 批量两条线**：`sandbox()` / `snapshot()` 服务「一轮一个、用完即弃」；
  `sandbox_pool()` / `snapshot_pool()` / `volume_pool()` 服务「创建本身即被测项、产物需批量清理」
  的并发场景——worker 线程各自加锁 `add()`，整档扫描结束一次性清理。三个 pool 与沙箱 / 快照 / 卷
  三条资源线一一对应，但共用同一个 `_Pool` + `_pool(teardown)` 引擎：新增一条资源线仅需一行
  teardown 闭包，无需重复 `list + lock + add/len/iter` + `try/finally` 样板。
- **`sandbox_op()` / `snapshot_op()` 是 §4.6 业务层的底座**：将「建箱→action→杀箱」
  （`snapshot_op` 额外打一个快照、action 拿 `(sb, snap_id)`）压成单个 callable，`@sandbox_action`
  的 `parallel_sweep` 生成器一句 `yield sandbox_op(...)` / `yield snapshot_op(...)` 即可，无需嵌套
  `def op(): with sandbox(...)`。action 返回值原样透传，便于把产物喂进 pool。
- **清理一律 best-effort**：`kill()` / `delete_snapshot()` 均吞异常，teardown 失败既不掩盖
  循环体的真实错误、也不打断整轮（`except Exception: pass` 语义）。
- **`**create_opts` / `timeout` 直通 `Sandbox.create`**：如 `volume_mounts=[...]` /
  `timeout=300`；`template_id` 默认取 `cfg.template_id`，也可显式传（ivshmem 用专用模板）。
- **池的签名差异**：`sandbox_pool()` 无参（清理只需对象自身 `kill()`），`snapshot_pool(cfg)` /
  `volume_pool(cfg)` 需 `cfg`（`delete_snapshot` / `Volume.destroy` 要 config）；`@sandbox_action`
  的 `_open_pool` 自动适配两种形态。

各场景具体用哪个：

- **pause-resume / clone / ivshmem**：`sb.kill()` 骨架收进 `sandbox()` / `snapshot()`。
- **snapshot-create / rollback**：走 §4.6 的 `@sandbox_benchmark` 一行声明——前者 `pool=snapshot_pool`
  落到 `sandbox_op`，后者 `fixture="snapshot"` 落到 `snapshot_op`。
- **template-create / density**：用 `sandbox_pool()` 批量清。
- **snapshot-create-from**：用 `@parallel_sweep` 生成器驱动扫描，`snapshot_pool()` 管每档的基准快照、
  `sandbox_pool()` 收恢复出来的沙箱。
- **volume-\***（create / destroy / metadata / mount-sandbox 四个）：统一走 `volume_pool()`；其中
  `volume-mount-sandbox` 把 `sandbox_pool()` 套在 `volume_pool()` 里面，保证退出时「先杀沙箱、
  再销毁它挂的卷」这个顺序。
- **snapshot-dirty 故意不套这些原语**：它比较特殊（要按脏页量分级、还要读 `vmm.log`），留着自己的骨架反而更清楚。

---

## 4.6 可组合装饰器：报告 / 指标 / 并发 / 业务四层拆分

「开箱 → 做一件事 → 关箱」是最常见的沙箱场景（snapshot-create 就是）。为了让每个
关注点都**能单独读、能复用、能替换**，把它拆成四个**各管一件事、能随意叠**的装饰器；
`sandbox_benchmark` 则是把这四层一次叠好的语法糖，两种写法都留着。

| 层 | 装饰器 | 只管什么 | 关键参数 |
|---|---|---|---|
| 业务 | `@sandbox_action` | 把「对一次性 fixture 做一件事」的 plain action 包成 `parallel_sweep` 要的生成器（消灭 `yield`） | `pool` / `fixture` / `template_id` / `**create_opts` |
| 指标 | `@metrics` | 声明统计行显示哪些延迟字段 | `"avg"`/`"min"`/`"p50"`/`"p95"`/`"p99"`/`"max"` |
| 并发/时序 | `@parallel_sweep` | 驱动并发扫描 + 计时 + 收集 + 打印 | `levels` / `rounds` / `warmup` / `settle` / `header` |
| 报告/注册 | `@benchmark` | 注册进运行表 + 贡献 HTML 图表分组 | `key` / `aliases` / `report` / gate |

堆叠顺序（自内向外 == 数据自底向上）：

```python
@benchmark("snapshot-create", aliases=["snapshot"],            # 报告 + 注册
           report=ReportGroup("创建快照（并发）"))
@parallel_sweep("snapshot-create", header=" [Perf] Snapshot Creation",  # 并发/时序
                levels=[1, 5, 10], rounds=20, warmup=2, settle=1.0)
@metrics("avg", "p50", "p95", "max")                           # 指标
@sandbox_action(pool=snapshot_pool)                            # 业务
def snapshot_create(sb, snaps):
    snaps.add(sb.create_snapshot().snapshot_id)
```

设计要点：

- **各层只碰自己的关注点**：`sandbox_action` 管沙箱建/清与「action→单次 op」的转换，`metrics`
  管显示，`parallel_sweep` 管扫描/计时，`benchmark` 管注册/报告；任一层均可单独替换或复用。
- **层间通信极窄**：`@metrics` 仅在生成器上挂一个 `_perf_metrics` 属性，`parallel_sweep` 在未显式
  给 `metrics=` 时读它作默认；除此之外各层零耦合。
- **action 签名随 `fixture` / `pool` 变**：`fixture="sandbox"`（默认）给 `(sb)`、`fixture="snapshot"`
  给 `(sb, snap_id)`；带 `pool` 时各追加尾参 `pool`（`(sb, pool)` / `(sb, snap_id, pool)`），
  通常把产物喂 `pool.add(...)`。`_open_pool` 自动适配 `sandbox_pool()`（无参）/
  `snapshot_pool(cfg)`（带 cfg）两种池形态。新增一种 fixture 只需在 `runner` 补一个对称的 `*_op`
  底座、在 `sandbox_action` 加一个 `fixture` 分支。
- **时序参数按场景覆盖，缺省回落全局默认**：`levels` / `rounds` / `warmup` / `settle` 为
  `None` 时回落到 [§6](#6-运行期可调项configpy) 的全局默认（`CONCURRENCY_LEVELS` /
  `PERF_ROUNDS` / `PERF_WARMUP` / `PERF_SETTLE`）。`warmup` 在每档计时前空跑 N 次、不计入统计，
  削冷启动尖刺；`settle` 在并发档间 sleep，让节点回稳。
- **`sandbox_benchmark` = 四层糖**：即
  `benchmark(...)(parallel_sweep(...)(metrics(...)(sandbox_action(...)(action))))`，供无需拆层的
  场景一行声明；需精细分层、复用中间层或替换某一层时改用四段式。

---

## 5. 场景清单（按运行顺序，共 13 个）

**运行顺序 == `cases/` 下 `bench_*` 模块的点分路径排序**（先按子包名、再按文件名的字典序），
不再是手写的源码顺序。所以下表顺序等价于：`clone` → `ivshmem` → `lifecycle/*` → `snapshot/*` → `volume`。

| # | 键 | 函数 | 模块 | 默认 | 说明 |
|---|---|---|---|---|---|
| 1 | `clone` | `bench_clone` | `cases/clone/bench_clone.py` | 开 | 单克隆基线 + 按并发档位扇出（刻意小以免耗尽单节点配额） |
| 2 | `ivshmem` | `bench_ivshmem` | `cases/ivshmem/bench_ivshmem.py` | 关（`CUBE_RUN_IVSHMEM=1`，或 `--only ivshmem`） | host 侧 ivshmem mmap 读写；需 ivshmem 模板且必须在 host 上跑 |
| 3 | `template-create` | `bench_template_create` | `cases/lifecycle/bench_create.py` | 开 | 基于模板创建沙箱，冷启动，并发扫描 |
| 4 | `density` | `bench_deployment_density` | `cases/lifecycle/bench_density.py` | 开（`CUBE_SKIP_DENSITY=1` 跳过） | 累积起箱，按 `MemAvailable` 差算单 VM 内存均摊（上限 100） |
| 5 | `pause-resume` | `bench_pause_resume` | `cases/lifecycle/bench_pause_resume.py` | 开 | 暂停（full-memory-copy）+ 经 `connect` 恢复 |
| 6 | `snapshot-create` | `snapshot_create` | `cases/snapshot/bench_create.py` | 开 | 每轮开箱→拍快照→删箱，按并发档位（默认 1/2/4）扫描；用 [§4.6](#46-可组合装饰器报告--指标--并发--业务四层拆分) 四层装饰器（`sandbox_benchmark` 糖）声明 |
| 7 | `snapshot-create-from` | `bench_snapshot_create_from` | `cases/snapshot/bench_create_from.py` | 开 | 每档拍一个基准快照，再按并发档位从它并发恢复沙箱；经 [§4.6](#46-可组合装饰器报告--指标--并发--业务四层拆分) 的 `@parallel_sweep` 生成器驱动 |
| 8 | `snapshot-dirty` | `bench_snapshot_dirty` | `cases/snapshot/bench_dirty.py` | 开（`CUBE_SKIP_SNAPSHOT_DIRTY=1` 跳过） | 写不同脏页量后测快照 + 恢复耗时；可读 `vmm.log` 反查实际写入字节 |
| 9 | `rollback` | `bench_rollback` | `cases/snapshot/bench_rollback.py` | 开 | 原地回滚到快照，按并发档位扫描；用 [§4.6](#46-可组合装饰器报告--指标--并发--业务四层拆分) 的 `@sandbox_benchmark(fixture="snapshot")` 一行声明 |
| 10 | `volume-create` | `bench_volume_create` | `cases/volume/bench_volume.py` | 关（`CUBE_RUN_VOLUME=1`） | Volume 创建 |
| 11 | `volume-destroy` | `bench_volume_destroy` | `cases/volume/bench_volume.py` | 关（`CUBE_RUN_VOLUME=1`） | Volume 删除 |
| 12 | `volume-metadata` | `bench_volume_metadata` | `cases/volume/bench_volume.py` | 关（`CUBE_RUN_VOLUME=1`） | list / get_info / connect 元数据操作 |
| 13 | `volume-mount-sandbox` | `bench_volume_mount_sandbox` | `cases/volume/bench_volume.py` | 关（`CUBE_RUN_VOLUME=1`） | 挂载 Volume 的端到端起箱 |

设计要点：

- **运行顺序由文件路径派生**：调整顺序只需重命名子包或文件，无需改任何列表；`bench_` 前缀是
  「是否为场景」的唯一判据（故 `ivshmem/probe.py` 不计入）。
- **`--only` / `--scenarios` 显式点名 = 强制开启**：显式点名默认关闭的场景（如 `--only ivshmem` /
  `volume`）会绕过其 opt-in env（`CUBE_RUN_IVSHMEM` / `CUBE_RUN_VOLUME`），无需另设开关；
  但绕不过 `available=False`——可选依赖缺失仍跳过。
- **pause-resume / clone / ivshmem 用 [§4.5](#45-场景-fixtures沙箱--快照生命周期原语)
  的 `sandbox()` / `snapshot()` fixture** 托管沙箱生命周期，场景体只写被测操作；`rollback` /
  `snapshot-create` 进一步用 [§4.6](#46-可组合装饰器报告--指标--并发--业务四层拆分) 的
  `@sandbox_benchmark` 一行声明。pause-resume（一轮测暂停 + 恢复两指标）/ clone（`concurrency`
  为 clone 内部扇出、非线程扫描）/ density / snapshot-dirty / ivshmem 形态与「n 线程各跑一次
  op」的扫描模型不同，保留自有骨架。
- **Volume 四场景默认关**：后端 `/volumes` 属 SDK / 文档先行的 roadmap，可能尚未部署；
  `available=Volume is not None` 保证 SDK 未导出 `Volume` 时无条件跳过。
- **ivshmem 默认关**：需 ivshmem 模板（`CUBE_IVSHMEM_TEMPLATE_ID`，回落默认模板）且必须
  在 host 上运行才能访问 `/dev/shm/ivshmem-{id}`。
- **`snapshot-dirty` 的 `_grep_snapshot_bytes()`** 为 best-effort：off-host 运行时 `grep`
  `vmm.log` 失败返回 -1，报告中脏页量显示为「未知」，不影响耗时数据。

---

## 6. 运行期可调项（`framework/config.py`）

| 常量 | 环境变量 | 默认 |
|---|---|---|
| `PERF_ROUNDS` | `CUBE_PERF_ROUNDS` | `10` |
| `DENSITY_COUNT` | `CUBE_DENSITY_COUNT` | `100` |
| `PERF_WARMUP` | `CUBE_PERF_WARMUP` | `1` |
| `PERF_SETTLE` | `CUBE_PERF_SETTLE` | `0` |
| `DIRTY_SWEEP` | `CUBE_DIRTY_SWEEP` | `0,10,50,100,200,500,800,1024` |
| `CONCURRENCY_LEVELS` | `CUBE_PERF_CONCURRENCY` | `1,2,4` |
| `CLEANUP_ENABLED` | `CUBE_PERF_CLEANUP` | `1`（`"0"` 关闭） |
| `CLEANUP_CMD` | `CUBE_CLEANUP_CMD` | `echo y \| cubecli unsafe destroyall -f` |

- **并发档位刻意小**（默认 1/2/4）：单节点资源有限，过大会触发 CubeMaster `130597 no more resource`；
  跨机压力测试请显式抬高 `CUBE_PERF_CONCURRENCY`。
- **`resolve_config()`**：未设 `CUBE_API_URL` 直接退出；未设 `CUBE_TEMPLATE_ID` 时查 `/templates`
  自动挑第一个 `READY` 模板，挑不到则退出。
- **这些是全局兜底值**：`PERF_ROUNDS` / `PERF_WARMUP` / `PERF_SETTLE` / `CONCURRENCY_LEVELS`
  可被 [§4.6](#46-可组合装饰器报告--指标--并发--业务四层拆分) 的 `@parallel_sweep` /
  `sandbox_benchmark` 用 `rounds` / `warmup` / `settle` / `levels` **按场景覆盖**，未指定时才回落到这里。

---

## 7. 报告体系

### 7.1 数据快照 `build_report_data(env)`

把 `EnvInfo` + `PERF_RESULTS` + 功能结果 + 配置固化成一个 dict：

- 每个 `PerfResult` 经 `_perf_result_to_dict()` 展平成 avg/min/p50/p95/p99/max/wall/per +
  **保留原始 latencies**（供 HTML 散点图）+ `extra`。
- `environment` 段落极全（硬件 + OS + 工具链 + 模板规格 + 所有组件版本 + release manifest）。

### 7.2 Markdown / JSON（`reporting/report.py`）

`write_reports()` 一次写四份：`report.md`（英）/ `report.zh.md`（中）/ `report.json` / `report.zh.json`。

- 特化渲染：`_template_table`（带吞吐量列）、`_dirty_page_tables`（快照 + 恢复双子表）、
  `_density_table`（单 VM 内存均摊）。

### 7.3 HTML（`reporting/report_html.py`）

- **多环境并排**：输入多个 JSON，按**环境指纹**（hostname + cpu_model + arch + kernel +
  一组组件版本字段：release / CubeMaster / CubeAPI / Cubelet / CubeShim / CubeRuntime /
  GuestImage / GuestAgent / NodeKernel）分组：同指纹的多次运行取平均得到一条稳定线，不同指纹成为
  独立对比序列。**组件版本进指纹**是刻意的——同一台机器换个组件版本会拆成独立序列而非被平均掉，
  否则版本引入的性能回退会被掩盖；`_disambiguate_labels()` 还会把各环境间**实际有差异**的版本字段
  追加到图例标签（如 `… · CubeMaster 0.5.1`），一眼看出每条线属于哪个 build。
- 每个场景一张折线图：各实测环境一条实线；下方汇总表逐 (并发, 环境) 行带
  「vs 首个环境」徽标。
- 单文件输入自动退化成单序列（兼容旧行为）。
- `--compare` 就是喂两个及以上 JSON 的 HTML 模式。

### 7.4 报告定制 `reporting/report_config.py`

分层优先级：`CLI 参数 > 环境变量 > report.toml > 内置默认`。

- TOML 文件搜索序：`./report.toml` → `perf/` → `tests/` → `sdk/python/`；`CUBE_REPORT_CONFIG` 可显式指定。
- 可定制：标题 / 副标题、环境卡字段列表、env 卡列数。

---

## 8. 环境与组件版本采集（`framework/env.py`）

`collect_env_info()` 采集主机 / CPU / 内存 / 磁盘（Linux 下走 `/proc`、`df`、`lsblk`、`dmidecode`）、
主 IPv4、机型（优先腾讯云 IMDS → DMI → virt-what）、模板元数据，以及组件版本。

**组件版本三级来源**（后者只补前者的空缺，保证权威源优先）：

1. **`release-manifest.json`**（`/usr/local/services/cubetoolbox/release-manifest.json`，
   `CUBE_RELEASE_MANIFEST` 可覆盖）—— 安装态单一事实源，最权威，先读。
2. **CubeAPI `/cluster/versions`** —— 控制面「实际在跑」的版本矩阵（注意字段是 camelCase：
   `controlPlane` / `buildTime` / `nodeID`）。
3. **本地二进制 `-V`/`-v`** —— 兜底（workstation 恰好装了工具时）。

> 注意：`/health` 只返回 `{status, sandboxes}`，**不**用于取版本；组件名 → 字段前缀映射由
> `_MANIFEST_COMPONENT_MAP` 统一维护，三级来源共用。

---

## 9. 节点清理机制

SDK 的 `kill()` 不总能回收残留 micro-VM，长跑会逐步耗尽节点资源。套件默认在每轮计时前
shell 调用节点本地 `cubecli` 强制 `destroyall`，回到干净冷启动态：

- `CUBE_PERF_CLEANUP=0` 关闭；`CUBE_CLEANUP_CMD` 覆盖命令。
- 这是**节点本地**动作，仅在 host 上运行 perf 时有效。

---

## 10. 设计约束与取舍

- **不追求绝对精度，只求可复现、能横向比**：预热轮（`PERF_WARMUP`）削掉冷启动尖刺、
  轮间静默（`PERF_SETTLE`）让节点缓过来、每轮前清一遍回到干净态——都是为了让数据在同一把尺子下能比。
- **默认参数给得保守**：并发档位小、Volume / ivshmem 默认关，免得在共享节点上把资源打爆、或者去调还没部署的后端。
- **报告和运行分开**：JSON 是权威数据，HTML 想重画就重画、想跨机合并就合并。
- **配置只走 env**：不往磁盘写 secret；`.env` 只补没设的项，真实环境变量优先。

---

## 11. 典型用法速查

```bash
# 全量跑 + JSON + Markdown（在 tests/ 目录下）
CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf

# 跑完顺带出 HTML
CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --html

# 只跑冷启动 + 回滚
CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --only template-create rollback

# 全量但排除 ivshmem
CUBE_API_URL=... CUBE_API_KEY=... python3 -m perf --scenarios all no-ivshmem

# 列出所有场景键 / 别名
python3 -m perf --list-scenarios

# 用已有 JSON 重画 HTML / 多机对比
python3 -m perf --html-only run1.json
python3 -m perf --compare bmi5.json vera.json kunpeng.json
```

编程方式调用（自包含，`from perf.*`）：

```python
import sys
sys.path.insert(0, "tests")

from perf.framework.config import resolve_config
from perf.framework.env import collect_env_info
from perf.framework import registry
from perf import cases  # noqa: F401 — 导入即注册所有 bench_* 场景
from perf.reporting import report
from perf.reporting.report_html import generate_html

cfg = resolve_config()
env = collect_env_info(cfg)
registry.run_all(cfg)

report.write_reports(env)          # 写 report.md / report.json（中英）
generate_html(["report.json"], output_path="my_report.html")
```
