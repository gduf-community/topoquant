# 陌生机器安装与启动

> 优先使用 `build/polars-tda-wheels/` 中兼容的本地 wheel，否则从 `plugins/polars-tda` 克隆构建原生插件；固定提交、补丁及构建步骤见 [内核接入说明](BOTTLENECK_KERNEL.md)。

## 1. 支持边界

推荐环境是 Windows 10/11 x64、PowerShell 5.1 或 7、64 位 Python 3.11。项目支持 Python 3.10–3.14；仍需按目标机器核对 polars-tda 与科学计算 wheel 的可用性。

硬件建议：

| 资源 | 最低建议 | 5027 支股票推荐 | 影响 |
|---|---:|---:|---|
| 逻辑内核 | 4 | 8–16 | 持续同调和瓶颈距离速度 |
| 内存 | 8 GiB | 16–32 GiB | 匹配进程各自持有历史持续同调数据 |
| 工作盘空间 | 原始 CSV 大小 + 2 GiB | 10 GiB 以上余量 | SQLite、日志和导出结果 |
| 网络 | 首次安装需要 | 可用离线 wheelhouse | 下载 Python 包 |

不需要安装 PostgreSQL、MySQL、Node.js、Jupyter、Java 或 CUDA。SQLite 随 Python 自带。
使用构建好的 wheel 时无需编译工具；从本地克隆首次构建 polars-tda 时需要 Rust，Windows
还需要 Visual Studio Build Tools 的 C++ 工具链及 Windows SDK。

## 2. Python 库

运行时有五个直接依赖：

| 库 | 版本范围 | 用途 | 是否可移除 |
|---|---|---|---|
| NumPy | `>=1.24,<3` | 数组、标准化和持续同调数值对 | 否 |
| pandas | `>=2,<4` | CSV、日期和行情表处理 | 否 |
| Polars | `==1.44.2` | 原生插件的列式表达式运行时 | 否 |
| polars-tda | `>=0.1.0,<0.2`，需距离绑定 | Vietoris–Rips 与 exact Bottleneck 的 Rust 内核 | 否 |
| Rich | `>=13,<14` | 预检表格、进度条和结果终端界面 | 可替换，但当前 CLI 需要 |

开发/测试额外使用 `pytest>=8,<10`。

## 3. 在线安装（推荐）

先安装 64 位 Python，并按[内核接入说明](BOTTLENECK_KERNEL.md)准备插件 wheel，或克隆插件、应用补丁。
然后在项目根目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

脚本会按 3.13 → 3.12 → 3.11 → 3.10 的顺序寻找解释器，创建项目独占的 `.venv`，优先安装兼容本地 wheel，没有时从插件源码构建，并执行 CLI 自检。指定解释器或同时安装测试依赖：

```powershell
.\scripts\bootstrap.ps1 -PythonExe "C:\Python311\python.exe" -Dev
```

只检查 Python 是否合格而不安装任何内容，可使用 `-CheckOnly`。

脚本不会把依赖装进系统 Python，也不会删除已有数据或实验目录。

已有项目环境需要检查或修复时，用可工作的基础 Python 运行 `setup_env.py`。
它会跳过不可运行的解释器，并在重建前把旧 `.venv` 移至 `build/venv-backup-*`；
健康的 uv 环境无需安装 pip，也不会重复重建。具体启动命令见 [START.md](START.md)。

## 4. 数据与配置放置

推荐目录结构：

```text
项目根目录/
├─ config.example.json
├─ data/
│  └─ stock/
│     ├─ 000001.SZ.csv
│     ├─ 000002.SZ.csv
│     └─ 600000.SH.csv
└─ runs/
   └─ 20240628/          # 程序自动创建
```

复制 `config.example.json` 为自己的配置，例如 `config.20240628.json`。相对路径以配置文件所在目录为基准；数据也可以放在其他盘并使用绝对路径。详细字段和数据质量要求见 [DATA_CONTRACT.md](DATA_CONTRACT.md)。

原始 5027 支股票行情并未随仓库提供，需要用户从有授权的数据源导出。不要把切片 CSV、归一化 CSV、持续同调图或 notebook 临时结果放进 `data/stock`。

## 5. 启动验收顺序

```powershell
# 快速检查环境、所有文件名和所有 CSV 表头
.venv\Scripts\python -m topoquant --config config.20240628.json preflight

# 首次换数据时完整扫描日期、重复行、数值和窗口数量
.venv\Scripts\python -m topoquant --config config.20240628.json validate-data

# 正式运行
.venv\Scripts\python -m topoquant --config config.20240628.json run

# 随时查看实验库和准确率
.venv\Scripts\python -m topoquant --config config.20240628.json status
```

`preflight` 每次现场检测 CPU、内存、磁盘、依赖、数据位置和表头，然后计算实际并发数；检查失败不会进入计算。`validate-data` 会完整读取全部行情，结果写入 `runs/<实验>/outputs/data_validation.csv` 和 `data_validation.json`。

匹配默认使用 `matching_pivots=8` 建立可复用的精确距离下界缓存。首次匹配会额外计算历史候选到 pivots 的距离；同一实验目录后续直接复用。机器较慢或只做一次查询时可设为 `0` 禁用，候选库较大且会重复运行时可尝试 `8` 或 `16`。

## 6. 离线安装

在联网构建机器上先生成插件 wheel，再准备 wheelhouse。以下命令以本地构建的
Windows x64 / CPython 3.10+ abi3 wheel 为例：

```powershell
python -m pip download -d wheelhouse `
  "numpy>=1.24,<3" "pandas>=2,<4" "polars==1.44.2" `
  .\build\polars-tda-wheels\polars_tda-0.1.0-cp310-abi3-win_amd64.whl `
  "rich>=13,<14" "setuptools>=68" wheel
```

复制整个项目和 `wheelhouse` 到离线机器，然后运行：

```powershell
.\scripts\bootstrap.ps1 -Wheelhouse .\wheelhouse
```

不同 Python 小版本或不同 CPU/操作系统的二进制 wheel 不能混用。

## 7. 常见失败

- `No matching distribution found for polars-tda`：先准备本地插件克隆或本地 wheel；当前上游源码预览没有 PyPI 包，单独从索引安装不会成功。
- `finite_bottleneck_distance` 导入失败：预览包未导出 Rust 距离绑定，需要发行时接入 [绑定补丁](../patches/polars-tda-finite-bottleneck.patch)。
- `python/py 不是命令`：重新安装 Python Launcher，或通过 `-PythonExe` 指定完整路径。
- `.venv` 解释器不可用：使用可工作的基础 Python 完整路径执行 `setup_env.py`，不要用已损坏的 `.venv` 启动修复脚本。
- `No module named pip`：uv 环境可以没有 pip，使用 `uv pip install --python .venv\Scripts\python.exe ...`；这本身不表示运行环境损坏。
- 预检报告 CSV 为 0：检查 `source_dir` 是配置文件相对路径还是绝对路径。
- 文件契约错误：文件名必须带 SZ/SH，列名大小写必须与契约一致。
- 内存压力大：优先调小 `topology_workers`；匹配阶段已改为 `mmap` 只读共享持续同调数据，进程数不会成倍放大内存。
- 换了行情数据后被签名检查拦住：加 `--reset`（无条件重算）或 `--force-rebuild`（失配时授权清空），也可在 `run.py` 的"重算控制"里选择。
- 预测失败：确认原始 CSV 包含截止日之后至少 `forecast_horizon` 个交易日，而不只是截至截止日的数据。

安装完成的最低验收是：CLI 帮助可显示、`preflight` 无红色错误、`validate-data` 的 `error=0`。这三项通过后再启动完整计算。

## 8. 可选的 MLflow 实验记录

MLflow 不属于核心运行依赖，只在需要上传实验参数和指标时安装。项目使用 uv 环境时执行：

```powershell
uv pip install --python .\.venv\Scripts\python.exe "mlflow==3.16.0"
.\.venv\Scripts\python.exe .\uplooad_mlflow.py
```

上传脚本写入实验参数、准确率和平均策略对数收益率，同时上传 `config.json` 与 `outputs/` 结果；源码不再上传。旧实验结果需先重新执行 `forecast` 和 `report` 生成带 `log_return` 的 `metrics.json`。本地脚本已被 Git 忽略，其中的连接设置不得提交或作为 artifact 上传。
