from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import replace
from datetime import date
from itertools import permutations
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from topoquant.config import ConfigError, PipelineConfig
from topoquant.data import iter_cloud_windows, load_future_directions, standardized_points
from topoquant.pipeline import _exact_bottleneck, _pivot_signature, match_clouds, run_all
from topoquant.preflight import inspect_environment, inspect_results
from topoquant.storage import ExperimentIdentityError, check_or_set_identity, connect
from topoquant.topology import TopologyError, bottleneck_distance, compute_persistence, finite_bottleneck_distance
from topoquant.validation import validate_dataset


@pytest.fixture
def native_tda():
    tda = pytest.importorskip("polars_tda", reason="等待 polars-tda 发行包")
    if not hasattr(tda, "finite_bottleneck_distance"):
        pytest.skip("当前 polars-tda 尚未包含 Rust 距离绑定")
    return tda


def make_stock(path: Path, rows: int = 12) -> None:
    dates = pd.bdate_range("2024-01-01", periods=rows)
    values = np.arange(rows, dtype=float) + 10.0
    pd.DataFrame(
        {
            "EventDate": dates,
            "money": values * 100,
            "volume": values * 10,
            "high": values + 1,
            "close": values,
            "prev_close": values - 0.5,
        }
    ).to_csv(path, index=False)


def test_identity_mismatch_has_specific_error(tmp_path: Path) -> None:
    with connect(tmp_path / "work" / "artifacts.sqlite3") as db:
        check_or_set_identity(db, "topology-old", "source", {})
        db.execute(
            "INSERT INTO clouds VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("cloud", "2024-01-01", "000001.SZ", "stock.csv", 3, "complete", None),
        )
        db.execute(
            "INSERT INTO diagrams VALUES (?, ?, ?, ?)",
            ("cloud", 0, 0, b""),
        )
        db.commit()

        with pytest.raises(ExperimentIdentityError, match="新的 work_dir"):
            check_or_set_identity(db, "topology-new", "source", {})


def test_pipeline_requires_only_h0_h1_and_invalidates_h2_signature(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "source_dir": "stock", "work_dir": "work", "as_of_date": "2024-06-28",
    }), encoding="utf-8")
    config = PipelineConfig.from_json(config_path)
    assert config.max_homology_dimension == 1
    assert PipelineConfig(tmp_path, tmp_path / "work", date(2024, 6, 28)).max_homology_dimension == 1
    legacy = replace(config, max_homology_dimension=2)
    assert legacy.topology_signature() != config.topology_signature()
    with pytest.raises(ConfigError, match="只计算 H0/H1"):
        legacy.validate()


def test_windows_are_non_overlapping_and_newest_first(tmp_path: Path) -> None:
    source = tmp_path / "stock"
    source.mkdir()
    make_stock(source / "000001.SZ.csv")
    config = PipelineConfig(
        source_dir=source,
        work_dir=tmp_path / "work",
        as_of_date=date(2024, 1, 16),
        window_size=3,
        lookback_trading_days=12,
        min_windows=2,
    )
    windows = list(iter_cloud_windows(config, sorted(source.glob("*.csv"))))
    assert len(windows) == 4
    assert windows[0].cloud_id == "20240116_000001SZ"
    assert windows[0].frame["EventDate"].dt.date.tolist() == [
        date(2024, 1, 16), date(2024, 1, 15), date(2024, 1, 12)
    ]
    assert set(windows[0].frame.index).isdisjoint(set(windows[1].frame.index))
    assert windows[1].cloud_date < windows[0].cloud_date


def test_standardization_matches_population_zscore(tmp_path: Path) -> None:
    source = tmp_path / "stock"
    source.mkdir()
    make_stock(source / "000001.SZ.csv", rows=6)
    config = PipelineConfig(source, tmp_path / "work", date(2024, 1, 8), window_size=3, lookback_trading_days=6, min_windows=1)
    window = next(iter_cloud_windows(config, [source / "000001.SZ.csv"]))
    points = standardized_points(window, config.features)
    np.testing.assert_allclose(points.mean(axis=0), 0.0, atol=1e-12)
    np.testing.assert_allclose(points.std(axis=0), 1.0, atol=1e-12)


def test_future_direction_uses_first_prev_close_as_baseline(tmp_path: Path) -> None:
    path = tmp_path / "000001.SZ.csv"
    make_stock(path, rows=8)
    dates, differences, directions, log_returns = load_future_directions(
        path, date(2024, 1, 4), 3
    )
    assert dates == [date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 9)]
    np.testing.assert_allclose(differences, [0.5, 1.5, 2.5])
    np.testing.assert_array_equal(directions, [1, 1, 1])
    np.testing.assert_allclose(log_returns, np.log(np.array([14.0, 15.0, 16.0]) / 13.5))


def test_automatic_worker_counts_follow_cpu_count(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("topoquant.config.os.cpu_count", lambda: 16)
    config = PipelineConfig(tmp_path, tmp_path / "work", date(2024, 1, 1))
    assert config.resolved_topology_workers == 8
    # mmap 零拷贝匹配不再受 4 进程内存上限约束
    assert config.resolved_matching_workers == 15
    assert config.resolved_forecast_workers == 32

    manual = PipelineConfig(
        tmp_path,
        tmp_path / "manual",
        date(2024, 1, 1),
        topology_workers=3,
        matching_workers=2,
        forecast_workers=5,
    )
    assert manual.resolved_topology_workers == 3
    assert manual.resolved_matching_workers == 2
    assert manual.resolved_forecast_workers == 5


def test_preflight_builds_runtime_plan(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("topoquant.config.os.cpu_count", lambda: 12)
    monkeypatch.setattr("topoquant.preflight._dependency_versions", lambda: {
        name: "test" for name in ("numpy", "pandas", "rich", "polars", "polars-tda")
    })
    monkeypatch.setitem(sys.modules, "polars_tda", SimpleNamespace(
        rips=lambda: None, finite_bottleneck_distance=lambda: None
    ))
    source = tmp_path / "stock"
    source.mkdir()
    make_stock(source / "000001.SZ.csv")
    config = PipelineConfig(source, tmp_path / "work", date(2024, 1, 16))
    report = inspect_environment(config)
    assert report.ready
    assert report.logical_cpu_count == 12
    assert [(item.stage, item.workers) for item in report.stages] == [
        ("持续同调", 8), ("瓶颈匹配", 11), ("行情预测", 24)
    ]
    assert report.source_file_count == 1
    assert report.schema_valid_file_count == 1


def test_full_data_validation_writes_audit(tmp_path: Path) -> None:
    source = tmp_path / "stock"
    source.mkdir()
    make_stock(source / "000001.SZ.csv", rows=12)
    config = PipelineConfig(
        source,
        tmp_path / "work",
        date(2024, 1, 8),
        window_size=3,
        lookback_trading_days=6,
        min_windows=1,
        forecast_horizon=3,
        forecast_workers=1,
    )
    events: list[tuple[int, int]] = []
    result = validate_dataset(
        config,
        lambda stage, current, total, stats: events.append((current, total)),
    )
    assert result == {"total": 1, "valid": 1, "excluded": 0, "error": 0, "workers": 1}
    assert events == [(0, 1), (1, 1)]
    assert (config.output_dir / "data_validation.csv").is_file()


def test_schema_v1_adds_log_return_column_without_dropping_database(tmp_path: Path) -> None:
    database = tmp_path / "artifacts.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO metadata(key, value) VALUES ('schema_version', '1');
            CREATE TABLE forecasts (
                target_id TEXT NOT NULL,
                horizon INTEGER NOT NULL,
                target_date TEXT NOT NULL,
                actual_difference REAL NOT NULL,
                actual_direction INTEGER NOT NULL,
                predicted_direction INTEGER NOT NULL,
                vote_up INTEGER NOT NULL,
                vote_count INTEGER NOT NULL,
                correct INTEGER NOT NULL,
                PRIMARY KEY (target_id, horizon)
            );
            """
        )

    with connect(database) as db:
        columns = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(forecasts)").fetchall()
        }
        assert "actual_log_return" in columns
        assert db.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()[0] == "2"


def test_small_pipeline_reaches_report(tmp_path: Path, native_tda) -> None:
    source = tmp_path / "stock"
    source.mkdir()
    dates = pd.bdate_range("2024-01-01", periods=24)
    angle = np.arange(24, dtype=float) * (2 * np.pi / 8)
    for offset, code in enumerate(("000001.SZ", "000002.SZ", "000003.SZ")):
        close = 20.0 + offset + np.sin(angle)
        pd.DataFrame(
            {
                "EventDate": dates,
                "money": np.cos(angle) * 100 + offset,
                "volume": np.sin(angle) * 100 + offset,
                "high": 21.0 + np.cos(angle),
                "close": close,
                "prev_close": np.roll(close, 1),
            }
        ).to_csv(source / f"{code}.csv", index=False)

    config = PipelineConfig(
        source_dir=source,
        work_dir=tmp_path / "work",
        as_of_date=dates[15].date(),
        window_size=8,
        lookback_trading_days=16,
        min_windows=2,
        distance_threshold=0.1,
        top_k=1,
        forecast_horizon=3,
        topology_workers=2,
        matching_workers=2,
        forecast_workers=2,
    )
    events: list[tuple[str, int, int]] = []
    result = run_all(
        config,
        lambda stage, current, total, stats: events.append((stage, current, total)),
    )
    assert result["topology"]["complete"] == 6
    assert result["matching"]["selected"] == 3
    assert result["topology"]["workers"] == 2

    # build 阶段应产出供匹配复用的 mmap 持久图
    index = json.loads((config.work_dir / "diagram_index.json").read_text(encoding="utf-8"))
    assert len(index) == result["topology"]["mmap_diagrams"] == 6
    counts = np.load(config.work_dir / "diagram_counts.npy", mmap_mode="r")
    assert counts.shape == (6, 2)
    assert not (config.work_dir / "diagrams_h2.npy").exists()
    with connect(config.database_path) as db:
        assert {row[0] for row in db.execute("SELECT DISTINCT dimension FROM diagrams")} == {0, 1}
    for dimension in range(config.max_homology_dimension + 1):
        block = np.load(config.work_dir / f"diagrams_h{dimension}.npy", mmap_mode="r")
        assert block.shape[0] == 6 and block.shape[2] == 2
        assert block.shape[1] >= int(counts[:, dimension].max())

    for dimension in config.distance_dimensions:
        spec = np.load(config.work_dir / f"match_spec_h{dimension}.npy")
        pivot_distance = np.load(
            config.work_dir / f"match_pivot_distance_h{dimension}.npy"
        )
        assert spec.shape == (6, 8)
        assert pivot_distance.shape[1] == min(
            config.matching_pivots, result["matching"]["candidates"]
        )
    assert (config.work_dir / "match_summary_signature.txt").is_file()
    assert (config.work_dir / "match_pivot_signature.txt").is_file()

    assert result["matching"]["workers"] == 2
    assert result["forecast"] == {"selected": 3, "complete": 3, "error": 0, "workers": 2}
    assert result["report"]["predictions"] == 9
    metrics = json.loads((config.output_dir / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["target_count"] == 3
    assert isinstance(metrics["overall"]["log_return"], float)
    assert all("log_return" in item for item in metrics["by_horizon"].values())
    predictions_header = (config.output_dir / "predictions.csv").read_text(
        encoding="utf-8-sig"
    ).splitlines()[0]
    assert "actual_log_return" in predictions_header
    assert "strategy_log_return" in predictions_header
    predictions = pd.read_csv(config.output_dir / "predictions.csv")
    expected_strategy_returns = predictions["actual_log_return"] * np.where(
        predictions["predicted_direction"] == 1, 1.0, -1.0
    )
    np.testing.assert_allclose(
        predictions["strategy_log_return"], expected_strategy_returns
    )
    assert metrics["overall"]["log_return"] == pytest.approx(
        float(expected_strategy_returns.mean())
    )
    for stage in ("topology", "matching", "forecast"):
        stage_events = [event for event in events if event[0] == stage]
        assert stage_events[0][1] == 0
        assert stage_events[-1][1] == stage_events[-1][2]
    assert inspect_results(config)["predictions"] == 9

    def saved_matches() -> list[tuple[object, ...]]:
        with sqlite3.connect(config.database_path) as db:
            return db.execute(
                "SELECT target_id, similar_id, rank, distance_dim0, distance_dim1 "
                "FROM matches ORDER BY target_id, rank"
            ).fetchall()

    pivot_matches = saved_matches()
    pivot_signature = config.work_dir / "match_pivot_signature.txt"
    pivot_cache_mtime = pivot_signature.stat().st_mtime_ns
    match_clouds(config)
    assert pivot_signature.stat().st_mtime_ns == pivot_cache_mtime
    assert saved_matches() == pivot_matches
    match_clouds(replace(config, matching_pivots=0))
    assert saved_matches() == pivot_matches


def test_polars_tda_persistence_returns_requested_dimensions(native_tda) -> None:
    points = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    diagrams = compute_persistence(points, max_edge_length=2.0, max_homology_dimension=2)
    assert set(diagrams) == {0, 1, 2}
    assert all(diagram.ndim == 2 and diagram.shape[1] == 2 for diagram in diagrams.values())
    assert np.isposinf(diagrams[0][:, 1]).sum() == 1
    np.testing.assert_allclose(diagrams[1], [[1.0, np.sqrt(2.0)]])
    assert diagrams[2].shape == (0, 2)


def test_exact_bottleneck_preserves_full_diagram_semantics(native_tda) -> None:
    def split(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        finite = np.isfinite(points[:, 0]) & np.isfinite(points[:, 1])
        essential = np.isfinite(points[:, 0]) & ~np.isfinite(points[:, 1])
        births = np.sort(np.asarray(points[essential, 0], dtype=np.float64))[::-1]
        return (
            np.ascontiguousarray(points[finite], dtype=np.float64),
            np.ascontiguousarray(births),
        )

    inf = float("inf")
    cases = [
        (
            np.array([[0.0, 1.0], [0.0, 2.0], [0.0, inf]]),
            np.array([[0.0, 1.05], [0.0, 1.9], [0.0, inf]]),
        ),
        (np.array([[1.5, inf]]), np.array([[1.5, inf]])),
        (np.array([[1.5, inf]]), np.array([[1.4, inf]])),
        (np.array([[1.5, inf]]), np.array([[1.5, inf], [0.3, inf]])),
        (np.array([[0.0, 1.0], [0.0, inf]]), np.array([[0.0, inf]])),
    ]
    expected = [0.1, 0.0, 0.1, float("inf"), 0.5]
    for (left, right), truth in zip(cases, expected):
        finite_a, essential_a = split(left)
        finite_b, essential_b = split(right)
        actual = _exact_bottleneck(
            finite_a, finite_b, essential_a, essential_b
        )
        assert np.isinf(truth) == np.isinf(actual)
        if not np.isinf(truth):
            assert actual == pytest.approx(truth)


def test_empty_diagram_semantics_are_separated() -> None:
    empty = np.empty((0, 2))
    one = np.array([[0.0, 1.0]])
    assert bottleneck_distance(empty, empty) == float("inf")
    assert bottleneck_distance(empty, one) == float("inf")
    assert finite_bottleneck_distance(empty, empty) == 0.0
    assert finite_bottleneck_distance(empty, one) == pytest.approx(0.5)


def test_polars_tda_bottleneck_matches_exhaustive_matching(native_tda) -> None:
    # 小图穷举增强二分图的全部匹配，不依赖其他 TDA 库作 oracle。
    rng = np.random.default_rng(20260811)
    for left_size, right_size in ((1, 1), (1, 3), (3, 2), (3, 3)):
        left_birth = rng.uniform(-1.0, 1.0, left_size)
        right_birth = rng.uniform(-1.0, 1.0, right_size)
        left = np.column_stack((left_birth, left_birth + rng.uniform(0.0, 2.0, left_size)))
        right = np.column_stack((right_birth, right_birth + rng.uniform(0.0, 2.0, right_size)))
        actual = finite_bottleneck_distance(left, right)
        size = left_size + right_size
        cost = np.zeros((size, size))
        cost[:left_size, :right_size] = np.max(
            np.abs(left[:, None, :] - right[None, :, :]), axis=2
        )
        cost[:left_size, right_size:] = ((left[:, 1] - left[:, 0]) / 2)[:, None]
        cost[left_size:, :right_size] = ((right[:, 1] - right[:, 0]) / 2)[None, :]
        expected = min(
            max(cost[row, column] for row, column in enumerate(order))
            for order in permutations(range(size))
        )
        assert actual == pytest.approx(expected, abs=1e-14)


@pytest.mark.parametrize("schema_version", [1, 2])
def test_persistence_adapter_preserves_cutoff_contract(monkeypatch, schema_version) -> None:
    import polars as pl

    result = {
        "schema_version": schema_version,
        "max_dimension": 2,
        "complete": False,
        "through": 1.0,
        "intervals": [
            {"dimension": 0, "birth": 0.0, "death": 0.5, "end": "finite"},
            {"dimension": 0, "birth": 0.0, "death": None, "end": "censored"},
            {"dimension": 1, "birth": 1.0, "death": None, "end": "censored"},
        ],
    }

    def rips(*coordinates, **options):
        assert coordinates == ("x0", "x1")
        assert options == {
            "max_dimension": 2, "max_edge_length": 1.0,
            "coefficient": 2, "method": "exact",
        }
        return pl.lit(result)

    monkeypatch.setitem(sys.modules, "polars_tda", SimpleNamespace(rips=rips))
    actual = compute_persistence(np.array([[0, 0], [1, 1]]), 1.0, 2)
    np.testing.assert_array_equal(actual[0], [[0.0, 0.5], [0.0, np.inf]])
    np.testing.assert_array_equal(actual[1], [[1.0, np.inf]])
    assert actual[2].shape == (0, 2)
    assert all(a.dtype == np.float64 and a.flags.c_contiguous for a in actual.values())


@pytest.mark.parametrize("end,death", [("unknown", None), ("finite", None), ("finite", -1.0)])
def test_persistence_adapter_rejects_invalid_intervals(monkeypatch, end, death) -> None:
    import polars as pl

    result = {"schema_version": 2, "max_dimension": 1, "intervals": [
        {"dimension": 0, "birth": 0.0, "death": death, "end": end}
    ]}
    monkeypatch.setitem(sys.modules, "polars_tda", SimpleNamespace(rips=lambda *a, **kw: pl.lit(result)))
    with pytest.raises(TopologyError, match="polars-tda 持续同调计算失败"):
        compute_persistence(np.array([[0.0]]), 1.0, 1)


def test_finite_distance_adapter_uses_rust_binding(monkeypatch) -> None:
    def distance(left, right):
        assert left == [[0.0, 2.0]]
        assert right == [[0.0, 3.0]]
        return 1.0

    monkeypatch.setitem(sys.modules, "polars_tda", SimpleNamespace(finite_bottleneck_distance=distance))
    assert finite_bottleneck_distance(
        np.array([[0.0, 2.0], [2.0, 2.0]]), np.array([[0.0, 3.0]])
    ) == 1.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_finite_distance_rejects_invalid_backend_result(monkeypatch, value) -> None:
    monkeypatch.setitem(sys.modules, "polars_tda", SimpleNamespace(
        finite_bottleneck_distance=lambda left, right: value
    ))
    with pytest.raises(TopologyError, match="非负有限数"):
        finite_bottleneck_distance(np.array([[0., 1.]]), np.array([[0., 2.]]))


def test_finite_distance_reports_missing_binding(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "polars_tda", SimpleNamespace())
    with pytest.raises(TopologyError, match="Rust 绑定"):
        finite_bottleneck_distance(np.array([[0., 1.]]), np.array([[0., 2.]]))


@pytest.mark.parametrize("left", [np.array([[1., 0.]]), np.array([[0., np.nan]])])
def test_finite_distance_rejects_invalid_input(left) -> None:
    with pytest.raises(TopologyError):
        finite_bottleneck_distance(left, np.empty((0, 2)))


@pytest.mark.parametrize("installed", [False, True])
def test_preflight_reports_unavailable_polars_tda(monkeypatch, tmp_path, installed) -> None:
    versions = {name: "test" for name in ("numpy", "pandas", "rich", "polars", "polars-tda")}
    if not installed:
        versions["polars-tda"] = None
    monkeypatch.setattr("topoquant.preflight._dependency_versions", lambda: versions)
    monkeypatch.setitem(sys.modules, "polars_tda", SimpleNamespace(rips=lambda: None))
    config = PipelineConfig(tmp_path, tmp_path / "work", date(2024, 1, 1))
    report = inspect_environment(config, require_source=False)
    assert not report.ready
    assert any("polars-tda" in error for error in report.errors)
    assert inspect_environment(config, require_source=False, require_topology_backend=False).ready


def test_kernel_migration_invalidates_topology_matching_and_pivots(monkeypatch, tmp_path) -> None:
    config = PipelineConfig(Path("stock"), Path("work"), date(2024, 1, 1))
    # 同一配置在迁移前实际生成的签名；防止旧持续图与匹配结果被静默复用。
    old_topology = "bf001c16361277b8607879dd3fdb58b8cfdf3a482d8f0689d54db17dc1bf888d"
    old_matching = "f0234cec62e6c3299fa012583a7d8ebc39c3f2e8c9c464fc5b25f2c10cac271b"
    assert config.topology_signature() != old_topology
    assert config.matching_signature() != old_matching
    with connect(tmp_path / "artifacts.sqlite3") as db:
        check_or_set_identity(db, old_topology, "source", {})
        db.execute("INSERT INTO clouds VALUES (?, ?, ?, ?, ?, ?, ?)",
                   ("cloud", "2024-01-01", "000001.SZ", "stock.csv", 3, "complete", None))
        db.execute("INSERT INTO diagrams VALUES (?, ?, ?, ?)", ("cloud", 0, 0, b""))
        db.commit()
        with pytest.raises(ExperimentIdentityError):
            check_or_set_identity(db, config.topology_signature(), "source", {})
        assert db.execute("SELECT COUNT(*) FROM diagrams").fetchone()[0] == 1
    monkeypatch.setattr("topoquant.pipeline._compute_source_signature", lambda config: "source")
    old_pivot = "source:pivot-v3:topp-0.1.0-exact:dims=(0, 1):candidates=e3b0c44298fc1c14:count=0"
    assert _pivot_signature(config, (0, 1), (), 0) != old_pivot
