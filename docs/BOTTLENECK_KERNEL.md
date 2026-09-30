# 计算内核接入记录

## 当前迁移：polars-tda / cocycle

TopoQuant 的运行依赖与测试已移除 GUDHI、Ripser、Topp。持续同调使用
`polars_tda.rips(*coordinates, max_dimension=..., max_edge_length=..., coefficient=2, method="exact")`，
有限图距离使用 `polars_tda.finite_bottleneck_distance(left, right)`，两侧参数都是
有限的 `[birth, death]` 数对序列。空图、本质/截断无穷端点、pivot 下界和严格阈值由现有外层处理。

### 本地源码插件

- polars-tda main：[`6f291b8`](https://github.com/Aequiludium/polars-tda/tree/6f291b82697b2351102667ea0685e4187ae46b62)。
- 发版迁移分支：[`fac9382`](https://github.com/Aequiludium/polars-tda/tree/fac9382807beafdaf14831e96f6f9e315c391323)，使用 `cocycle = "=0.1.1"`。
- Rust 内核已有 `cocycle::diagram_distances::bottleneck_distance`，即本次需要的 Rust 距离实现。
- 上述两个提交的 `src/lib.rs` 尚未导出距离函数；这不仅是文档缺失。
  [本地补丁](../patches/polars-tda-finite-bottleneck.patch) 为发版提供最小 PyO3 导出、类型声明和测试，
  不复制距离算法，也不引入另一个计算后端。`finite_bottleneck_distance` 是该补丁新增的接口，
  不能假定现有 v0.1.0 源码预览已支持。补丁已应用到本地克隆，尚未合入上游或发布。

`plugins/polars-tda/` 是独立 Git 克隆，由主仓库忽略；主仓库保存固定提交和补丁。
这样可以直接构建本地原生插件，不必等待 PyPI 发版。Python 直接导入构建后的
`polars_tda` 包，不能只把 Rust 源码目录加入 `PYTHONPATH`。

新机器先安装 Git、Python 3.10+、rustup；Windows 源码构建还需要 Visual Studio Build Tools
的 C++ 工具链和 Windows SDK。插件的 `rust-toolchain.toml` 会选择 Rust 1.95.0。
在 TopoQuant 根目录执行一次：

```powershell
git clone --branch codex/use-cocycle-release https://github.com/Aequiludium/polars-tda.git plugins/polars-tda
git -C plugins/polars-tda checkout --detach fac9382807beafdaf14831e96f6f9e315c391323
git -C plugins/polars-tda apply --check ../../patches/polars-tda-finite-bottleneck.patch
git -C plugins/polars-tda apply ../../patches/polars-tda-finite-bottleneck.patch
.\scripts\bootstrap.ps1 -Dev
```

`setup_env.py` 和 `scripts/bootstrap.ps1` 的在线安装优先使用
`build/polars-tda-wheels/` 中与目标 Python/平台兼容的 abi3 wheel，没有时才使用这个本地克隆，
与 TopoQuant 一起交给安装器解析和构建；离线模式继续从 wheelhouse 安装。
已经克隆和应用补丁的目录不要重复执行上述 clone/apply。升级上游时先检查本地改动，
不要覆盖补丁；重新核对 API、构建和测试，再更新固定提交记录。

也可以按上游贡献文档用 `maturin build --release --locked` 生成 wheel，
再用 `uv pip install --python <项目Python路径> <wheel路径> -e ".[dev]"` 安装。
有 wheel 的运行机器不需要 Rust 或 C++ 编译工具。TopoQuant 预检会明确拦截缺少距离
接口的预览包，不会退回其他库。当前操作没有修改远端仓库或发行标签。

### 数据与缓存语义

适配层读取 schema 1/2，按维度输出连续 `float64` 的 `(n, 2)` 数组，空维度为 `(0, 2)`。
`essential` 的 null death 转成 `+inf`。`censored` 也按原实验定义记为截断复形上的 `+inf`，
不把未知死亡时间伪造为截止值，也不宣称它是完整过滤中的本质类。现有数对存储会失去这两种
端点的区别，因此这些数据只适用于项目已有的截断比较口径。

有限距离调用前剥离无穷端点并去掉零长度对角线点；逆序、非有限输入和异常返回值均报错。
非空有限图通过 Rust exact Bottleneck 计算，仍是 binary64 数值计算，未承诺与旧内核逐位相同。
拓扑、匹配和 pivot 签名都已更新，旧结果必须在新实验目录重算或显式授权重建。

### 本地构建和验证（2026-09-30）

已从上述克隆及补丁构建完整 Release 插件，并安装到独立环境 `build/tda-test-env`。
该环境没有安装 GUDHI、Ripser 或 Topp。验证平台为 Windows x64 / Python 3.12.13，
Rust 1.95.0、Polars 1.44.2、cocycle 0.1.1、PyO3 0.29.0。

- Wheel：`build/polars-tda-wheels/polars_tda-0.1.0-cp310-abi3-win_amd64.whl`。
- Wheel SHA-256：`55B92D44ACC5C95A06110A71215303E453E8DCCE0A9C434E6F71E356409F39CF`。
- TopoQuant：32 项通过，零跳过，包含真实 H0/H1/H2、穷举距离 oracle、无穷端点、
  多进程端到端和启用/禁用 pivot 对拍。
- polars-tda：96 项通过，包含分组、惰性表达式、数据契约与新增距离绑定。
- Rustfmt、Clippy（Release / `-D warnings`）、Ruff 检查与格式检查通过；安装依赖检查通过。

```powershell
.\build\tda-test-env\Scripts\python.exe -m pytest --basetemp=build/pytest-tda-native
```

这是本地源码插件验证，不是上游发行验证。其他 Python/平台、真实行情全量流程和
PyInstaller 便携 EXE 尚未验证；便携包仍需运行两个真实自检后才能交付。

### 主环境与启动修复验证（2026-09-30）

已用 `setup_env.py` 重建实际入口使用的 `.venv`，采用 Python 3.12.13 和上述本地 wheel。
原环境保存在 `build/venv-backup-*`。主环境没有 pip、GUDHI、Ripser 或 Topp，
uv 依赖检查通过；没有 pip 不再被误判为环境损坏。

- 主 `.venv`：43 项测试通过，零跳过，包含 launcher 默认版本标记、缺失 launcher、
  本地 wheel 兼容性、旧环境保留以及 uv/pip 安装路径。
- 使用外部 Python 和项目 Python 分别重复运行 `setup_env.py`，均显示环境就绪，
  没有更改 `pyvenv.cfg` 或新增环境备份。
- 从外部 Python 启动 `run.py`，自动切换主 `.venv` 后，原生内核自检和合成数据
  多进程流水线自检均通过；普通交互入口可进入参数界面并正常取消，`config.json` 未改动。
- `scripts/bootstrap.ps1` 语法检查通过；本轮实际安装通过 `setup_env.py` 完成。

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=build/pytest-startup-full
.\.venv\Scripts\python.exe .\setup_env.py
.\.venv\Scripts\python.exe .\run.py --portable-self-test
.\.venv\Scripts\python.exe .\run.py --portable-pipeline-self-test
```

这次确认的是源码入口和合成数据流程；尚未运行真实行情全量实验或构建便携 EXE。

### 性能复核（2026-09-30）

功能和启动验证不表示与 Ripser 等速。对当前行情的三个最新窗口
（000001.SZ、000002.SZ、000004.SZ，各 60×4 点，阈值 3.0、F2），
使用相同标准化数组分别运行当前 Release 插件和备份环境的 Ripser 0.6.15。
每个后端/维度在独立进程运行四次，排除第一次加载后的三次耗时取中位数：

| 计算范围 | polars-tda | Ripser |
| --- | ---: | ---: |
| H0/H1/H2 | 134.7–151.6 ms/窗口 | 5.7–6.6 ms/窗口 |
| H0/H1 | 1.4–2.0 ms/窗口 | 0.77–0.97 ms/窗口 |

这三个窗口的 H0/H1/H2 数对数量相同，端点在 `atol=rtol=1e-6` 内一致；
三者 H2 均为空，因此不能据此证明全部行情图一致。H2 请求实测慢约 22–24 倍，
本次有限样本尚未复现用户报告的数百倍全流程差距。
polars-tda 测量使用 Python 3.12.13，Ripser 使用原备份环境的 Python 3.10.11。

cocycle 0.1.1 的 `persistence/flag/dispatch.rs` 在维度大于 1 时，
将整个请求交给 `simplicial::cohomology::compute` 的通用约化，绕过专用 F2 H1 路径。
当前绑定在有限阈值时还使用 threshold graph 路径。因而 Rust 实现本身不保证
比 Ripser 更快；保留 H2 的性能改进需要对该实际路径进行优化和复测。
上述性能复核时配置仍为 H0/H1/H2，使用 exact 过滤；后续移除 H2 的修改见下一节。
本地原始测量和同图检查记录在 `build/topology-performance/summary.json`，
不会把 Ripser 恢复为运行依赖。

### 移除未使用的 H2（2026-09-30）

用户确认移除未参与匹配和预测的 H2。当前配置、示例配置、Python 默认值和交互入口
统一只计算 H0/H1，`max_homology_dimension` 固定为 `1`，便携内核自检也使用相同维度。
配置校验会拦截旧的 `2`，避免无意中恢复慢的 H2 路径。
SQLite 和 mmap 的现有动态维度逻辑只保存 H0/H1，不生成 `diagrams_h2.npy`。
H1 仍处理三角形 cofacets；没有改成只构建边，也没有改为 sparse 近似。

最大同调维度已包含在拓扑签名中，旧 H2 缓存会失配。保留已有实验目录，
交互运行时自动创建递增目录；CLI 应使用新的 `work_dir`。
底层适配函数保留原有请求维度能力，供独立 API 测试使用；正式流水线只接受 H0/H1。

本轮主 `.venv` 45 项测试通过、零跳过；端到端测试确认数据库只有维度 0/1、
counts 为 `(N, 2)`，没有 H2 mmap，并完成匹配、预测和报告。
从外部 Python 启动的 `run.py --portable-self-test` 与
`run.py --portable-pipeline-self-test` 均通过，验证了实际环境切换和 H0/H1 入口。
测试命令为 `.venv\Scripts\python.exe -m pytest --basetemp=build/pytest-h0-h1-only`。

按当前配置重新计算上述三个 60×4 行情窗口，排除首次加载后的耗时中位数为
0.96–1.00 ms/窗口；H0/H1 数对与移除前保存的结果在 `atol=rtol=1e-12` 内一致。
记录位于 `build/topology-performance/h2-removal-check.json`。
这是单窗口样本复核，不是完整行情流水线的总耗时测量。

下文仅保留历史研究证据，不是当前安装步骤或后端。

## 历史 GUDHI small-N 实验

当 `Persistence_graph::size() <= 256` 时，候选点保存在连续数组中，以闭合的 L-infinity 方框做线性扫描，命中后通过 swap-pop 删除；更大的图仍使用原 CGAL kd-tree。阈值可在编译时通过 `GUDHI_BOTTLENECK_LINEAR_SCAN_MAX_SIZE` 覆盖。

### 历史本机产物

- 安装版本：`gudhi 3.13.0+topoquant.smalln2`
- Python ABI：CPython 3.12 / Windows x64
- wheel：`build/wheelhouse/gudhi-3.13.0+topoquant.smalln2-cp312-cp312-win_amd64.whl`
- wheel SHA-256：`6642AC9B9F6A776B78235ECB69D9B1786AA3D7A573B0D585CD9338B21D22419B`
- 上游补丁：`patches/gudhi-3.13-small-n-neighbors.patch`

该 wheel 使用 GUDHI 3.13.0 标签、Nanobind 2.13.0、NumPy 2.5.0、CGAL 6.2，并关闭 TBB，以尽量对齐官方 wheel 的构建条件。它只适用于 CPython 3.12 Windows x64，不能用于 Python 3.10、3.11 或其他平台。

### 验证结果

真实已有持久图按流水线口径剥离本质类后固定抽样 128 对，使用 GUDHI 默认 `e=None` 路径：

| 维度 | 图总点数 | 官方 3.13.0 | small-N | 加速 |
|---|---:|---:|---:|---:|
| H0 | 中位数 118，范围 58–118 | 895.976 μs | 558.716 μs | 1.60x |
| H1 | 中位数 18，范围 6–26 | 153.778 μs | 102.412 μs | 1.50x |

两组各 128 个距离的二进制 SHA-256 在官方版与 small-N 版之间完全一致。另有 250 组随机图的自动后端/强制 kd-tree 差分对拍，差异数为 0；GUDHI Bottleneck_distance 的 8 项测试及 TopoQuant 的 12 项测试均通过。

这些数字是内核及 Python 入口的隔离基准，不代表完整 `match` 阶段会获得同等倍数加速；完整阶段仍包含进程调度、mmap、pivot 和 SQLite 开销。本次没有运行真实行情流水线。

### Hera 对比

2026-08-10 使用 GUDHI 3.13 wheel 自带的 Hera C++ 扩展，对同一批真实有限持久图做了对比。该扩展与 Giotto 使用的 Hera 算法内核属于同一路线，因此无需为测试额外安装整套 Giotto-TDA。

| 维度 | 当前 small-N GUDHI | Hera `delta=0` | Hera `delta=0.01` |
|---|---:|---:|---:|
| H0 | 558.716 μs | 2748.643 μs | 1968.354 μs |
| H1 | 102.412 μs | 406.170 μs | 357.677 μs |

Hera 精确模式在 128 对 H0/H1 图上的距离哈希与当前实现一致，但 H0 约慢 4.9 倍、H1 约慢 4.0 倍。Hera 默认的 1% 相对近似模式仍慢约 3.5 倍，而且会改变距离。

进一步对 H0/H1 各抽样 2048 对真实图：Hera 默认模式在 H1 上产生了 3 次严格 `<0.1` 判定翻转，均为当时 exact GUDHI 距离小于 0.1、Hera 近似值大于 0.1；例如 `0.099738895893096924` 被估为 `0.10071811097441241`。因此近似 Hera 没有进入正式流程。

### 历史安装与回退

安装本机 wheel：

```powershell
.venv\Scripts\python -m pip install --force-reinstall --no-deps `
  build\wheelhouse\gudhi-3.13.0+topoquant.smalln2-cp312-cp312-win_amd64.whl
```

恢复官方 GUDHI：

```powershell
.venv\Scripts\python -m pip install --force-reinstall "gudhi==3.13.0"
```

重新构建时，在 GUDHI 3.13.0 源码上应用补丁后构建 wheel。不要对 3.14 开发分支直接打包，也不要把默认调用改成 `e=0`；后者会进入 exact `sorted_distances()` 路径，算法和性能边界都不同。
