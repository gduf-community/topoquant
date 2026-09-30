from __future__ import annotations

import numpy as np

from .domain import Diagram


class TopologyError(RuntimeError):
    """持续同调计算失败。"""


def compute_persistence(
    points: np.ndarray,
    max_edge_length: float,
    max_homology_dimension: int,
) -> Diagram:
    try:
        import polars as pl
        import polars_tda as tda
    except ImportError as exc:
        raise TopologyError("缺少 polars-tda 或 Polars，请先安装项目依赖") from exc

    try:
        points = np.asarray(points, dtype=np.float64)
        if points.ndim != 2 or points.shape[1] == 0:
            raise ValueError("点云必须是至少包含一个坐标列的二维数组")
        coordinates = [f"x{index}" for index in range(points.shape[1])]
        frame = pl.DataFrame(points, schema=coordinates, orient="row")
        result = frame.select(
            tda.rips(
                *coordinates,
                max_dimension=max_homology_dimension,
                max_edge_length=max_edge_length,
                coefficient=2,
                method="exact",
            ).alias("diagram")
        ).item()
        if result["schema_version"] not in (1, 2):
            raise ValueError(f"不支持的持续图 schema_version：{result['schema_version']}")
        if result["max_dimension"] != max_homology_dimension:
            raise ValueError("持续图未包含全部请求的同调维度")
        diagrams: dict[int, list[tuple[float, float]]] = {
            dimension: [] for dimension in range(max_homology_dimension + 1)
        }
        for interval in result["intervals"]:
            dimension = interval["dimension"]
            birth = float(interval["birth"])
            end = interval["end"]
            if end == "finite":
                death = float(interval["death"])
                if not np.isfinite(death) or death <= birth:
                    raise ValueError("有限持续区间必须满足 birth < death < +inf")
            elif end in ("essential", "censored"):
                if interval["death"] is not None:
                    raise ValueError("未终止区间的 death 必须为 null")
                # SQLite 沿用截断复形上的 birth/death 对：截止时仍存活记为 +inf。
                # censored 不代表完整过滤中的本质类，不能据此推断其最终死亡时间。
                if end == "censored" and (
                    result["complete"]
                    or result["through"] is None
                    or birth > result["through"]
                ):
                    raise ValueError("截断区间与持续图覆盖范围不一致")
                death = float("inf")
            else:
                raise ValueError(f"未知持续区间终点类型：{end}")
            if not np.isfinite(birth) or dimension not in diagrams:
                raise ValueError("持续区间包含无效 birth 或未请求的维度")
            diagrams[dimension].append((birth, death))
    except Exception as exc:
        raise TopologyError(f"polars-tda 持续同调计算失败：{exc}") from exc

    return {
        dimension: np.ascontiguousarray(diagrams[dimension], dtype=np.float64).reshape(-1, 2)
        for dimension in range(max_homology_dimension + 1)
    }


def bottleneck_distance(left: np.ndarray, right: np.ndarray) -> float:
    """整张持续图之间的瓶颈距离。

    沿用 notebook 的筛选口径：任一整图为空即判为无穷远（视为不可比）。
    """
    if left.size == 0 or right.size == 0:
        return float("inf")

    left = np.asarray(left, dtype=np.float64).reshape(-1, 2)
    right = np.asarray(right, dtype=np.float64).reshape(-1, 2)
    left_finite = np.isfinite(left).all(axis=1)
    right_finite = np.isfinite(right).all(axis=1)
    left_essential = np.isfinite(left[:, 0]) & np.isposinf(left[:, 1])
    right_essential = np.isfinite(right[:, 0]) & np.isposinf(right[:, 1])
    if not np.all(left_finite | left_essential) or not np.all(
        right_finite | right_essential
    ):
        return float("inf")

    left_births = np.sort(left[left_essential, 0])[::-1]
    right_births = np.sort(right[right_essential, 0])[::-1]
    if len(left_births) != len(right_births):
        return float("inf")
    essential_distance = (
        float(np.max(np.abs(left_births - right_births)))
        if len(left_births)
        else 0.0
    )
    finite_distance = finite_bottleneck_distance(
        left[left_finite], right[right_finite]
    )
    return max(finite_distance, essential_distance)


def finite_bottleneck_distance(left: np.ndarray, right: np.ndarray) -> float:
    """两组有限持久对之间的精确瓶颈距离。"""
    left = np.asarray(left, dtype=np.float64).reshape(-1, 2)
    right = np.asarray(right, dtype=np.float64).reshape(-1, 2)
    if not np.isfinite(left).all() or not np.isfinite(right).all():
        raise TopologyError("finite_bottleneck_distance 只接受有限 birth-death 对")
    if np.any(left[:, 1] < left[:, 0]) or np.any(right[:, 1] < right[:, 0]):
        raise TopologyError("finite_bottleneck_distance 要求 birth <= death")
    # cocycle 的区间类型要求严格正持续度；对角线点不影响瓶颈距离。
    left = left[left[:, 1] > left[:, 0]]
    right = right[right[:, 1] > right[:, 0]]
    if left.size == 0 and right.size == 0:
        return 0.0
    if left.size == 0:
        return float(np.max((right[:, 1] - right[:, 0]) / 2.0))
    if right.size == 0:
        return float(np.max((left[:, 1] - left[:, 0]) / 2.0))
    try:
        from polars_tda import finite_bottleneck_distance as distance
    except ImportError as exc:
        raise TopologyError(
            "需要包含 finite_bottleneck_distance Rust 绑定的 polars-tda；"
            "当前源码预览版尚未导出该接口，见 docs/BOTTLENECK_KERNEL.md"
        ) from exc
    try:
        result = float(distance(left.tolist(), right.tolist()))
        if not np.isfinite(result) or result < 0:
            raise ValueError("有限持续图的距离必须是非负有限数")
        return result
    except Exception as exc:
        raise TopologyError(f"polars-tda Bottleneck distance 计算失败：{exc}") from exc
