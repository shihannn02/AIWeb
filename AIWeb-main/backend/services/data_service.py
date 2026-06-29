from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from config import UPLOAD_DIR


def read_dataframe(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        for encoding in ("utf-8", "gbk", "gb18030", "latin1"):
            try:
                return pd.read_csv(path, encoding=encoding)
            except UnicodeDecodeError:
                continue
        return pd.read_csv(path)
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path)
    raise ValueError(f"不支持的文件格式: {suffix}")


def save_upload(filename: str, content: bytes) -> Tuple[str, Path]:
    file_id = uuid.uuid4().hex
    suffix = Path(filename).suffix.lower() or ".xlsx"
    stored = UPLOAD_DIR / f"{file_id}{suffix}"
    stored.write_bytes(content)
    return file_id, stored


def get_upload_path(file_id: str) -> Optional[Path]:
    matches = list(UPLOAD_DIR.glob(f"{file_id}.*"))
    return matches[0] if matches else None


def _normalize_binary_label(series: pd.Series) -> pd.Series:
    """将标签列统一为 0/1 数值，便于统计有效样本和坏率。"""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")

    cleaned = series.astype(str).str.strip()
    mapping = {"0": 0, "1": 1, "0.0": 0, "1.0": 1, "True": 1, "False": 0, "true": 1, "false": 0}
    return cleaned.map(mapping)


def _binary_score(series: pd.Series) -> tuple[int, float]:
    """返回 (有效0/1样本数, 坏率)。"""
    normalized = _normalize_binary_label(series)
    valid_mask = normalized.isin([0, 1])
    valid_count = int(valid_mask.sum())
    if valid_count == 0:
        return 0, 0.0
    bad_rate = float(normalized.loc[valid_mask].mean())
    return valid_count, bad_rate


def detect_label_candidates(columns: List[str], df: Optional[pd.DataFrame] = None) -> List[str]:
    """按列名优先级 + 0/1 含量排序，返回最可能的标签列。"""
    priority_keywords = (
        ("overdue", 100),
        ("fpd", 90),
        ("target", 80),
        ("label", 70),
        ("flag", 60),
        ("bad", 50),
        ("y", 10),
    )

    scored: List[tuple[int, str]] = []
    for col in columns:
        name_score = 0
        lower = col.lower()
        for keyword, weight in priority_keywords:
            if keyword == "y":
                # 避免 apply_id / apply_time 因含字母 y 被误匹配
                if lower in ("y", "target_y") or lower.endswith("_y") or lower.startswith("y_"):
                    name_score = max(name_score, weight)
            elif keyword in lower:
                name_score = max(name_score, weight)

        binary_score = 0
        if df is not None and col in df.columns:
            valid_count, _ = _binary_score(df[col])
            if valid_count > len(df) * 0.5:
                binary_score = 200
            elif valid_count > len(df) * 0.1:
                binary_score = 100

        if name_score > 0 or binary_score > 0:
            # 仅有 0/1 分布、列名不像标签的字段降权，避免挤掉 target / overdue 等
            total = name_score + (binary_score // 2 if name_score == 0 else binary_score)
            scored.append((total, col))

    scored.sort(key=lambda x: (-x[0], x[1]))
    return [col for _, col in scored]


def guess_best_time_col(columns: List[str]) -> Optional[str]:
    """优先选择真正的时间列，避免 apply_id 等误匹配。"""
    candidates = detect_time_candidates(columns)
    if not candidates:
        return None

    def score(col: str) -> int:
        lower = col.lower()
        if lower.endswith("_id") or lower == "id":
            return -100
        if lower.endswith("_date") or "apply_date" in lower or "datetime" in lower:
            return 110
        if "date" in lower:
            return 100
        if "apply_time" in lower or "create_time" in lower:
            return 85
        if lower.endswith("_time") or lower.endswith("_time_x") or lower.endswith("_time_y"):
            return 75
        if "month" in lower:
            return 50
        if "apply" in lower:
            return 10
        return 0

    ranked = sorted(candidates, key=lambda c: (-score(c), c))
    return ranked[0]


def guess_best_label(df: pd.DataFrame) -> str:
    """优先选择列名明确且 0/1 分布合理的标签列。"""
    preferred = (
        "target", "target3", "target2", "target1",
        "overdue_flag2", "overdue_flag", "fpd7", "fpd30",
        "label", "y",
    )
    n = len(df)
    for name in preferred:
        if name not in df.columns:
            continue
        valid, bad = _binary_score(df[name])
        if valid > n * 0.5 and 0 < bad < 1:
            return name

    candidates = detect_label_candidates(df.columns.tolist(), df)
    for col in candidates:
        valid, bad = _binary_score(df[col])
        if valid > n * 0.1 and 0 < bad < 1:
            return col

    if candidates:
        return candidates[0]
    return df.columns[-1]


def detect_time_candidates(columns: List[str]) -> List[str]:
    keywords = ("time", "date", "apply", "create", "dt", "month")
    return [c for c in columns if any(k in c.lower() for k in keywords)]


def _coerce_time_series(series: pd.Series) -> pd.Series:
    """将时间列解析为可比较的 datetime（兼容 2026/3/7、2026-05-01、Unix 秒/毫秒时间戳等）。"""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series

    as_num = pd.to_numeric(series, errors="coerce")
    numeric_ratio = float(as_num.notna().mean()) if len(series) else 0.0
    if numeric_ratio >= 0.5:
        median = float(as_num.median()) if as_num.notna().any() else 0.0
        abs_med = abs(median)
        if 1e12 <= abs_med <= 2e13:
            parsed = pd.to_datetime(as_num, unit="ms", errors="coerce")
            if parsed.notna().any():
                return parsed
        if 1e9 <= abs_med <= 2e10:
            parsed = pd.to_datetime(as_num, unit="s", errors="coerce")
            if parsed.notna().any():
                return parsed

    parsed = pd.to_datetime(series, errors="coerce", dayfirst=False)
    if parsed.notna().any() and numeric_ratio >= 0.5:
        if parsed.max().year < 1980 and as_num.median() > 1e9:
            return pd.to_datetime(as_num, unit="s", errors="coerce")
    if parsed.notna().any():
        return parsed
    if as_num.notna().any():
        return pd.to_datetime(as_num, unit="s", errors="coerce")
    return parsed


def _parse_cutoff_timestamp(cutoff_date: str) -> pd.Timestamp:
    ts = pd.to_datetime(cutoff_date, errors="coerce")
    if pd.isna(ts):
        raise ValueError(f"无法解析日期截止点: {cutoff_date}")
    return ts


def _stats_for_frame(df: pd.DataFrame, label: str) -> Dict[str, Any]:
    normalized = _normalize_binary_label(df[label])
    valid_mask = normalized.isin([0, 1])
    valid_count = int(valid_mask.sum())
    bad_rate = float(normalized.loc[valid_mask].mean()) if valid_count else 0.0
    return {
        "rows": len(df),
        "columns": len(df.columns),
        "valid_samples": valid_count,
        "bad_rate": bad_rate,
    }


def split_dataframe(
    df: pd.DataFrame,
    label: str,
    time_col: str,
    split_mode: str,
    oot_ratio: float,
    cutoff_date: Optional[str],
) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    if split_mode == "manual":
        return None, None

    normalized = _normalize_binary_label(df[label])
    valid_df = df[normalized.isin([0, 1])].copy()

    if split_mode == "cutoff":
        if not cutoff_date:
            return None, None
        if time_col not in valid_df.columns:
            raise ValueError(f"时间列 '{time_col}' 不存在，无法切分 Train/Test")
        times = _coerce_time_series(valid_df[time_col])
        cutoff = _parse_cutoff_timestamp(cutoff_date)
        if times.isna().all():
            raise ValueError(f"时间列 '{time_col}' 无法解析为日期，请检查格式")
        train = valid_df[times < cutoff].copy()
        test = valid_df[times >= cutoff].copy()
        return train, test

    if split_mode == "ai":
        if oot_ratio <= 0 or oot_ratio >= 1:
            # OOT=0：不切 Test，但 Train 展示全量有效样本
            return valid_df.copy(), valid_df.iloc[0:0].copy()
        if time_col not in valid_df.columns:
            raise ValueError(f"时间列 '{time_col}' 不存在，无法切分 Train/Test")
        sort_key = _coerce_time_series(valid_df[time_col])
        sorted_df = valid_df.assign(__sort_time=sort_key).sort_values("__sort_time").drop(
            columns="__sort_time"
        ).reset_index(drop=True)
        split_idx = int(len(sorted_df) * (1 - oot_ratio))
        train = sorted_df.iloc[:split_idx].copy()
        test = sorted_df.iloc[split_idx:].copy()
        return train, test

    return None, None


def validate_dataset(
    df: pd.DataFrame,
    label: str,
    split_mode: str = "none",
    time_col: Optional[str] = None,
    oot_ratio: float = 0.0,
    cutoff_date: Optional[str] = None,
    train_df: Optional[pd.DataFrame] = None,
    test_df: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    if label not in df.columns:
        raise ValueError(f"标签列 '{label}' 不存在")

    label_series = df[label]
    normalized = _normalize_binary_label(label_series)
    value_counts = label_series.value_counts(dropna=False).head(10)
    valid_mask = normalized.isin([0, 1])
    valid_count = int(valid_mask.sum())
    bad_rate = float(normalized.loc[valid_mask].mean()) if valid_count else 0.0

    candidates = detect_label_candidates(df.columns.tolist(), df)
    suggested_label = guess_best_label(df)

    result: Dict[str, Any] = {
        "rows": len(df),
        "columns": len(df.columns),
        "column_names": df.columns.tolist(),
        "label": label,
        "suggested_label": suggested_label,
        "suggested_time_col": guess_best_time_col(df.columns.tolist()),
        "label_distribution": {str(k): int(v) for k, v in value_counts.items()},
        "valid_samples": valid_count,
        "portfolio_bad_rate": bad_rate,
        "label_candidates": candidates[:50],
        "time_candidates": detect_time_candidates(df.columns.tolist()),
        "label_warning": (
            None
            if valid_count > 0
            else f"当前标签列「{label}」不含有效 0/1 值，建议改用「{suggested_label}」"
        ),
        "split": None,
    }

    try:
        if split_mode == "manual" and train_df is not None and test_df is not None:
            if label not in train_df.columns or label not in test_df.columns:
                raise ValueError(f"标签列 '{label}' 在 Train/Test 文件中不存在")
            train_stats = _stats_for_frame(train_df, label)
            test_stats = _stats_for_frame(test_df, label)
            result["split"] = {
                "mode": "manual",
                "train": train_stats,
                "test": test_stats,
            }
        elif split_mode in ("ai", "cutoff"):
            train, test = split_dataframe(df, label, time_col or "", split_mode, oot_ratio, cutoff_date)
            if train is not None and test is not None:
                split_info: Dict[str, Any] = {
                    "mode": split_mode,
                    "train": _stats_for_frame(train, label),
                }
                if len(test) > 0:
                    split_info["test"] = _stats_for_frame(test, label)
                else:
                    split_info["test"] = {
                        "rows": 0,
                        "columns": len(df.columns),
                        "valid_samples": 0,
                        "bad_rate": 0.0,
                    }
                if len(train) > 0 or len(test) > 0:
                    result["split"] = split_info
                    if split_mode == "cutoff" and len(train) > 0 and len(test) == 0 and time_col:
                        normalized = _normalize_binary_label(df[label])
                        valid_df = df[normalized.isin([0, 1])].copy()
                        if time_col in valid_df.columns:
                            times = _coerce_time_series(valid_df[time_col])
                            if times.notna().any():
                                max_dt = times.max().strftime("%Y-%m-%d")
                                result["split_hint"] = (
                                    f"截止点 {cutoff_date} 之后无有效 0/1 样本"
                                    f"（有效样本最晚日期约 {max_dt}），请调早截止日"
                                )
    except ValueError as exc:
        result["split_error"] = str(exc)

    if result["split"] is None and split_mode in ("ai", "cutoff", "manual"):
        if valid_count == 0:
            result["split_hint"] = f"标签列「{label}」无有效 0/1 样本，请改选如「{suggested_label}」"
        elif split_mode == "cutoff" and not cutoff_date:
            result["split_hint"] = "请填写日期截止点以切分 Train/Test"
        elif split_mode == "manual":
            result["split_hint"] = "请上传 Train 和 Test 两个文件"
        elif split_mode in ("ai", "cutoff"):
            result["split_hint"] = "切分后 Test 样本为空，请检查时间列或 OOT 比例"

    return result
