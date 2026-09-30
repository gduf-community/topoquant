# TopoQuant 启动说明

> 安装器优先使用 `build/polars-tda-wheels/` 中兼容的本地 wheel，没有时才从 `plugins/polars-tda` 构建。首次安装前按[内核接入说明](BOTTLENECK_KERNEL.md)准备 wheel 或源码和补丁。

## 1. 安装

需要 Windows 10/11 和 64 位 Python 3.10–3.14，推荐 Python 3.11；其他版本仍取决于 polars-tda/Polars 是否提供对应 wheel。

本机主 `.venv` 已修复，在项目根目录可直接检查环境并进入交互界面：

```powershell
.\.venv\Scripts\python.exe .\setup_env.py
.\.venv\Scripts\python.exe .\run.py
```

首次安装或 `.venv` 已不能启动时，用可工作的基础 Python 执行 `setup_env.py`。
本机已验证的命令如下（其他机器请替换解释器路径）：

```powershell
& "$env:USERPROFILE\.local\bin\python3.12.exe" .\setup_env.py
```

脚本会发现并校验可用 Python，创建 `.venv`，安装 NumPy、pandas、Polars、polars-tda、Rich 和本项目。
可用环境会直接通过，不会因 uv 环境未安装 pip 而重建；重建前的旧环境保存在 `build/venv-backup-*`。
在 VSCode 中选择 `.venv\Scripts\python.exe`，再右键运行整个 `run.py`。
其他机器的安装及 `bootstrap.ps1` 用法见 [SETUP.md](SETUP.md)。

## 2. 放置数据

把每支股票一个 CSV 放到：

```text
data\stock\
```

文件名示例：`000001.SZ.csv`、`600000.SH.csv`。

每个 CSV 至少包含：

```text
EventDate,money,volume,high,close,prev_close
```

数据需要包含截止日前至少 240 个交易日，以及截止日后至少 5 个交易日。详细要求见 [DATA_CONTRACT.md](DATA_CONTRACT.md)。

## 3. 修改配置

复制配置文件：

```powershell
Copy-Item .\config.example.json .\config.20240628.json
```

确认以下字段正确：

```json
{
  "source_dir": "./data/stock",
  "work_dir": "./runs/20240628",
  "as_of_date": "2024-06-28"
}
```

并发参数保持 `0` 即可，程序会在启动前根据运行机器自动选择。

当前实验只需要 H0/H1，`max_homology_dimension` 固定为 `1`，不再计算 H2。
旧配置中的值为 `2` 时请改成 `1`。维度变化后，`run.py` 会保留旧实验并自动创建
递增目录；CLI 请指定新的 `work_dir`。

## 4. 检查并运行

```powershell
# 检查环境、依赖和 CSV 表头
.venv\Scripts\python -m topoquant --config .\config.20240628.json preflight

# 首次使用一批新数据时执行完整检查
.venv\Scripts\python -m topoquant --config .\config.20240628.json validate-data

# 执行全部流程
.venv\Scripts\python -m topoquant --config .\config.20240628.json run

# 查看进度和结果
.venv\Scripts\python -m topoquant --config .\config.20240628.json status
```

## 5. 结果位置

结果保存在 `runs\20240628\`：

- `artifacts.sqlite3`：持续同调、匹配和预测实验库；
- `outputs\selected_matches.csv`：相似点云；
- `outputs\predictions.csv`：预测明细及实际/策略对数收益率；
- `outputs\metrics.json`：总体和逐预测日的准确率、平均策略对数收益率；
- `outputs\report.txt`：文本报告。

仅补齐旧实验的对数收益率字段时，可重新执行 `forecast`、`report`。
切换到 polars-tda 内核则需要新实验目录重算；交互入口会保留旧目录并自动递增，CLI 需显式指定新 `work_dir`：

```powershell
.venv\Scripts\python -m topoquant --config .\config.20240628.json forecast
.venv\Scripts\python -m topoquant --config .\config.20240628.json report
```

安装或离线部署问题见 [SETUP.md](SETUP.md)。
