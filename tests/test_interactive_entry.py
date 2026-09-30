from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import run
import setup_env


def test_saved_protocol_takes_priority_over_example(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "config.example.json").write_text(
        json.dumps({"source_dir": "example", "work_dir": "example-work"}),
        encoding="utf-8",
    )
    (tmp_path / "config.json").write_text(
        json.dumps({"source_dir": "saved", "work_dir": "saved-work"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(run, "_PROJECT_ROOT", tmp_path)

    defaults = run.load_defaults()

    assert defaults["source_dir"] == "saved"
    assert defaults["work_dir"] == "saved-work"


def test_interactive_parameters_drop_unused_h2(monkeypatch) -> None:
    defaults = {**run.DEFAULTS, "max_homology_dimension": 2}
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    params = run.collect_params(defaults)
    assert params["max_homology_dimension"] == 1
    assert params["distance_dimensions"] == [0, 1]


def test_invalid_saved_protocol_is_not_silently_replaced(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text("not-json", encoding="utf-8")
    (tmp_path / "config.example.json").write_text(
        json.dumps({"source_dir": "example", "work_dir": "example-work"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(run, "_PROJECT_ROOT", tmp_path)

    with pytest.raises(RuntimeError, match="实验协议文件无效"):
        run.load_defaults()


def test_next_incremental_work_dir_uses_next_sibling(tmp_path: Path) -> None:
    (tmp_path / "runs" / "20240628").mkdir(parents=True)
    (tmp_path / "runs" / "20240628_2").mkdir()
    (tmp_path / "runs" / "20240628_4").mkdir()

    selected = run.next_incremental_work_dir("runs/20240628", tmp_path)

    assert selected == str(Path("runs") / "20240628_5")
    assert (tmp_path / selected).is_dir()


def test_next_incremental_work_dir_continues_from_generated_path(tmp_path: Path) -> None:
    (tmp_path / "runs" / "experiment").mkdir(parents=True)
    (tmp_path / "runs" / "experiment_2").mkdir()

    selected = run.next_incremental_work_dir("runs/experiment_2", tmp_path)

    assert selected == str(Path("runs") / "experiment_3")
    assert (tmp_path / selected).is_dir()


def test_setup_accepts_working_uv_environment_without_pip(monkeypatch, tmp_path) -> None:
    python = tmp_path / "python.exe"
    python.touch()
    monkeypatch.setattr(setup_env, "get_venv_python", lambda: python)

    def probe(command, **kwargs):
        if command == ["-m", "pip", "--version"]:
            return subprocess.CompletedProcess(command, 1, "", "No module named pip")
        return subprocess.CompletedProcess(command, 0, "3.12", "")

    monkeypatch.setattr(setup_env, "run_venv", probe)
    assert setup_env.check_venv().venv_ready


def test_setup_reports_broken_interpreter(monkeypatch, tmp_path) -> None:
    python = tmp_path / "python.exe"
    python.touch()
    monkeypatch.setattr(setup_env, "get_venv_python", lambda: python)
    monkeypatch.setattr(setup_env, "run_venv", lambda command, **kw:
                        subprocess.CompletedProcess(command, 1, "", "Python310 is missing"))
    status = setup_env.check_venv()
    assert not status.venv_ready
    assert "解释器不可用" in status.reason
    assert "Python310 is missing" in status.reason


def test_setup_finds_base_python_without_launcher(monkeypatch, tmp_path) -> None:
    base_python = tmp_path / "base-python.exe"
    base_python.touch()

    def missing_launcher(command, **kwargs):
        if command[0] == "py":
            raise FileNotFoundError("py launcher not installed")
        assert command[0] == str(base_python)
        return subprocess.CompletedProcess(command, 0, "[[3, 12, 13], true]", "")

    monkeypatch.setattr(setup_env.subprocess, "run", missing_launcher)
    monkeypatch.setattr(setup_env.sys, "_base_executable", str(base_python))
    versions = setup_env.discover_python_versions()
    assert len(versions) == 1
    assert versions[0].path == tmp_path / "base-python.exe"


def test_setup_discovers_launcher_default_and_skips_broken_python(monkeypatch, tmp_path) -> None:
    current = tmp_path / "current.exe"
    default = tmp_path / "default python.exe"
    broken = tmp_path / "broken.exe"
    for path in (current, default, broken):
        path.touch()

    def probe(command, **kwargs):
        if command[0] == "py":
            output = f" -V:3.13 * {default}\n -V:3.10 {broken}\n -V:Astral/CPython3.12 {current}\n"
        elif command[0] == str(broken):
            raise OSError("broken interpreter")
        else:
            output = "[[3, 13, 0], true]" if command[0] == str(default) else "[[3, 12, 13], true]"
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(setup_env.sys, "_base_executable", str(current))
    monkeypatch.setattr(setup_env.subprocess, "run", probe)
    assert [version.path for version in setup_env.discover_python_versions()] == [default, current]


@pytest.mark.parametrize("minor,platform_tag,free_threaded,use_wheel", [
    (12, "win_amd64", False, True),
    (9, "win_amd64", False, False),
    (12, "win32", False, False),
    (12, "linux_x86_64", False, False),
    (13, "win_amd64", True, False),
])
def test_setup_selects_only_compatible_local_wheel(
    monkeypatch, tmp_path, minor, platform_tag, free_threaded, use_wheel
) -> None:
    wheel = tmp_path / "build/polars-tda-wheels/polars_tda-0.1.0-cp310-abi3-win_amd64.whl"
    wheel.parent.mkdir(parents=True)
    wheel.touch()
    source = tmp_path / "plugins/polars-tda"
    source.mkdir(parents=True)
    (source / "pyproject.toml").touch()
    monkeypatch.setattr(setup_env, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(setup_env.subprocess, "run", lambda command, **kw:
                        subprocess.CompletedProcess(command, 0, json.dumps([
                            "cpython", [3, minor], platform_tag, free_threaded
                        ]), ""))
    assert setup_env.local_plugin_target(Path(sys.executable)) == (wheel if use_wheel else source)


@pytest.mark.parametrize("use_uv", [True, False])
def test_setup_preserves_old_environment_and_installs_wheel(monkeypatch, tmp_path, use_uv) -> None:
    environment = tmp_path / ".venv"
    environment.mkdir()
    (environment / "previous-packages.txt").write_text("preserve", encoding="utf-8")
    wheel = tmp_path / "plugin.whl"
    calls = []

    def install(command, **kwargs):
        calls.append(command)
        if command[:2] == ["uv", "venv"] or command[1:3] == ["-m", "venv"]:
            python = setup_env.get_venv_python()
            python.parent.mkdir(parents=True)
            python.touch()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(setup_env, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(setup_env, "VENV_DIR", environment)
    monkeypatch.setattr(setup_env, "check_uv", lambda: "0.11" if use_uv else None)
    monkeypatch.setattr(setup_env, "local_plugin_target", lambda python: wheel)
    monkeypatch.setattr(setup_env.subprocess, "run", install)
    python = setup_env.PythonVersion(tmp_path / "selected-python.exe", "3.12.13", 3, 12, "64-bit")
    assert setup_env.create_and_install(python)
    backups = list((tmp_path / "build").glob("venv-backup-*"))
    assert len(backups) == 1
    assert (backups[0] / "previous-packages.txt").read_text(encoding="utf-8") == "preserve"
    prefix = (["uv", "pip", "install", "--python", str(setup_env.get_venv_python())]
              if use_uv else [str(setup_env.get_venv_python()), "-m", "pip", "install"])
    assert calls[-1] == [*prefix, "-e", ".", str(wheel)]
    if not use_uv:
        assert calls[0][:3] == [str(python.path), "-m", "venv"]
