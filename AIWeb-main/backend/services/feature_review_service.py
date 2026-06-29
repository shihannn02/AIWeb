"""分箱后变量筛选：概览报告、候选特征聚类、分箱明细与月度稳定性。"""
from __future__ import annotations

import importlib.util
import math
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import PROJECT_ROOT
from services.data_service import (
    _normalize_binary_label,
    read_dataframe,
    split_dataframe,
)
from services.binning_runner import get_job

_FF_SCRIPTS = PROJECT_ROOT / "feature-filter" / "scripts"
_FF_REFS = PROJECT_ROOT / "feature-filter" / "references"


def _load_ff_module():
    for p in (_FF_REFS, _FF_SCRIPTS):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    spec = importlib.util.spec_from_file_location("feature_filter_mod", _FF_SCRIPTS / "feature_filter.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


_FF = _load_ff_module()
get_chinese_name = _FF.get_chinese_name
normalize_cols = _FF.normalize_cols
read_binning_sheets = _FF.read_binning_sheets
calc_overall_bad_rate = _FF.calc_overall_bad_rate
check_head_tail = _FF.check_head_tail
check_u_shape = _FF.check_u_shape
classify_category = _FF.classify_category
parse_bin_boundary = _FF.parse_bin_boundary
assign_bin = _FF.assign_bin
assign_bin_with_fallback = getattr(_FF, "assign_bin_with_fallback", _FF.assign_bin)
SUB_COLS = _FF.SUB_COLS
SPECIAL_VALUES = getattr(_FF, "SPECIAL_VALUES", [-999, -9999, -999999])

# 分箱结果 / 原始 Train 帧内存缓存（同一 job 内复用，避免重复读 Excel/CSV）
_REVIEW_CACHE: Dict[str, Dict[str, Any]] = {}
_REVIEW_RESULT_CACHE: Dict[str, Dict[str, Any]] = {}
_FEATURE_DETAIL_CACHE: Dict[str, Dict[str, Any]] = {}
_REVIEW_CACHE_MAX_JOBS = 3
_FEATURE_DETAIL_CACHE_MAX = 48
_REVIEW_RESULT_CACHE_MAX = 12
_REVIEW_CACHE_ORDER: List[str] = []


def _trim_review_cache(keep_job_id: Optional[str] = None) -> None:
    """LRU：仅保留最近使用的若干 job，释放 Excel/CSV 等大对象。"""
    if keep_job_id and keep_job_id in _REVIEW_CACHE_ORDER:
        _REVIEW_CACHE_ORDER.remove(keep_job_id)
        _REVIEW_CACHE_ORDER.append(keep_job_id)
    while len(_REVIEW_CACHE_ORDER) > _REVIEW_CACHE_MAX_JOBS:
        evict = _REVIEW_CACHE_ORDER.pop(0)
        if evict == keep_job_id:
            _REVIEW_CACHE_ORDER.append(evict)
            break
        clear_review_cache(evict)


def _trim_feature_detail_cache() -> None:
    if len(_FEATURE_DETAIL_CACHE) <= _FEATURE_DETAIL_CACHE_MAX:
        return
    overflow = len(_FEATURE_DETAIL_CACHE) - _FEATURE_DETAIL_CACHE_MAX
    for key in list(_FEATURE_DETAIL_CACHE.keys())[:overflow]:
        _FEATURE_DETAIL_CACHE.pop(key, None)


def _trim_review_result_cache() -> None:
    if len(_REVIEW_RESULT_CACHE) <= _REVIEW_RESULT_CACHE_MAX:
        return
    overflow = len(_REVIEW_RESULT_CACHE) - _REVIEW_RESULT_CACHE_MAX
    for key in list(_REVIEW_RESULT_CACHE.keys())[:overflow]:
        _REVIEW_RESULT_CACHE.pop(key, None)


def clear_review_cache(job_id: Optional[str] = None) -> None:
    if job_id:
        _REVIEW_CACHE.pop(job_id, None)
        prefix = f"{job_id}|"
        for key in list(_REVIEW_RESULT_CACHE):
            if key.startswith(prefix):
                _REVIEW_RESULT_CACHE.pop(key, None)
        for key in list(_FEATURE_DETAIL_CACHE):
            if key.startswith(prefix):
                _FEATURE_DETAIL_CACHE.pop(key, None)
    else:
        _REVIEW_CACHE.clear()
        _REVIEW_RESULT_CACHE.clear()
        _FEATURE_DETAIL_CACHE.clear()


def _get_job_binning_bundle(job_id: str) -> Dict[str, Any]:
    bucket = _REVIEW_CACHE.setdefault(job_id, {})
    if "train_raw" in bucket:
        return bucket

    job = get_job(job_id)
    if not job or not job.output_path or not job.output_path.exists():
        raise ValueError("分箱任务不存在或结果文件未就绪")

    train_raw, test_raw = read_binning_sheets(str(job.output_path))
    train_norm = normalize_cols(train_raw)
    test_norm = normalize_cols(test_raw) if test_raw is not None else None
    bucket.update({
        "job": job,
        "train_raw": train_raw,
        "test_raw": test_raw,
        "train_norm": train_norm,
        "test_norm": test_norm,
    })
    _trim_review_cache(job_id)
    return bucket


def _get_job_raw_df(job_id: str) -> Optional[pd.DataFrame]:
    bucket = _get_job_binning_bundle(job_id)
    if "raw_df" not in bucket:
        params = (bucket.get("job") or get_job(job_id)).run_params or {}
        file_path = params.get("file_path")
        raw_df = None
        if file_path and Path(file_path).exists():
            try:
                raw_df = read_dataframe(Path(file_path))
            except Exception:
                raw_df = None
        bucket["raw_df"] = raw_df
    return bucket["raw_df"]


def _group_label(category_key: str) -> str:
    if not category_key:
        return "其他"
    if "_" in category_key:
        return category_key.split("_", 1)[1]
    return category_key


def _json_safe(value: Any) -> Any:
    """确保 FastAPI 可 JSON 序列化（numpy / NaN / inf）。"""
    if value is None:
        return None
    if isinstance(value, (str, bool)):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _format_threshold(val: float) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "0"
    if float(val).is_integer():
        return str(int(val))
    return f"{float(val):g}"


def _format_rule_display(rule: Dict[str, Any], *, full: bool = False) -> str:
    feat = rule.get("feature", "")
    op = str(rule.get("operator", ">")).strip()
    if op == "in":
        vals = rule.get("values") or []
        if not vals:
            return f"{feat} in (未选择)"
        shown = [str(v) for v in vals] if full else [str(v) for v in vals[:6]]
        inner = ", ".join(shown)
        if not full and len(vals) > 6:
            inner += f", …共{len(vals)}类"
        return f"{feat} in ({inner})"
    th = rule.get("threshold", 0)
    return f"{feat}{op}{_format_threshold(float(th or 0))}"


def _is_missing_bin_label(bl: str) -> bool:
    s = str(bl)
    return any(k in s for k in ("MISSING", "缺失", "nan", "NA", "特殊", "special"))


def _is_interval_bin_label(bl: str) -> bool:
    s = str(bl).strip()
    if s.startswith("special("):
        return False
    if any(k in s for k in ("缺失", "nan", "NA", "特殊")):
        return False
    return s.startswith("(") or (s.startswith("[") and "," in s)


def _is_numeric_bin_label(bl: str) -> bool:
    s = str(bl).strip()
    if s.startswith("special("):
        return False
    if any(k in s for k in ("缺失", "nan", "NA", "特殊")):
        return False
    if _is_interval_bin_label(s):
        return True
    # headtail5 单值箱如 [1.0]、[2.0]
    if (s.startswith("[") or s.startswith("(")) and re.search(r"-?\d+\.?\d*", s):
        return True
    return bool(re.match(r"^-?\d+\.?\d*$", s))


_DETAIL_CACHE_VER = "v2"  # bump when value_type / detail shape changes


def _infer_series_value_type(series: pd.Series) -> str:
    """object/string 列若绝大多数可转数值，仍视为 numeric（CSV 常见）。"""
    if pd.api.types.is_numeric_dtype(series):
        return "numeric"
    if not (
        pd.api.types.is_object_dtype(series)
        or pd.api.types.is_categorical_dtype(series)
        or pd.api.types.is_string_dtype(series)
    ):
        return "numeric"
    s = series.replace(SPECIAL_VALUES, np.nan)
    coerced = pd.to_numeric(s, errors="coerce")
    valid_ratio = float(coerced.notna().mean()) if len(s) else 0.0
    if valid_ratio >= 0.8:
        return "numeric"
    return "categorical"


def _column_value_type(df: pd.DataFrame, feat: str) -> str:
    """按原始列 dtype 判断数值/类别（与 _feature_value_type 一致）。"""
    if feat not in df.columns:
        return "numeric"
    return _infer_series_value_type(df[feat])


def _feature_value_type(
    feat: str,
    raw_df: Optional[pd.DataFrame],
    train_norm: pd.DataFrame,
) -> str:
    """按原始列存储类型区分：数字列走头尾箱；object/category/string 走类别勾选。"""
    if raw_df is not None and feat in raw_df.columns:
        return _infer_series_value_type(raw_df[feat])

    # 无原始数据时，根据分箱标签形态兜底（头尾5% 含 [1.0] 单值箱，须用 _is_numeric_bin_label）
    ft = train_norm[train_norm["feature"] == feat]
    labels = ft["bin_label"].astype(str).tolist()
    if not labels:
        return "numeric"
    numeric_like = sum(1 for bl in labels if _is_numeric_bin_label(bl))
    return "numeric" if numeric_like > len(labels) / 2 else "categorical"


def _collect_high_bad_bins(
    feature_df: pd.DataFrame,
    threshold: float,
    min_hit: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for _, r in feature_df.iterrows():
        bl = str(r.get("bin_label", ""))
        tot = int(r.get("total", 0) or 0)
        br = float(r.get("bad_rate", 0) or 0)
        if tot < min_hit or br <= threshold:
            continue
        rows.append({
            "bin": bl,
            "obs": tot,
            "bad": int(r.get("bad", 0) or 0),
            "bad_rate": br,
            "is_missing": _is_missing_bin_label(bl),
        })
    rows.sort(key=lambda x: (-x["bad_rate"], -x["obs"], x["bin"]))
    return rows


def _categorical_value_matches_bin(value: Any, bin_label: str) -> bool:
    if _is_missing_bin_label(bin_label):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return True
        if value in SPECIAL_VALUES:
            return True
        return str(value).strip().upper() in ("MISSING", "NAN", "NONE", "")
    return str(value).strip() == str(bin_label).strip()


def _assign_categorical_bin(value: Any, bin_order: List[str]) -> Optional[str]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        for bl in bin_order:
            if _is_missing_bin_label(bl):
                return bl
        return None
    if value in SPECIAL_VALUES:
        for bl in bin_order:
            if _is_missing_bin_label(bl):
                return bl
        return None
    s = str(value).strip()
    for bl in bin_order:
        if str(bl).strip() == s:
            return bl
    return None


def _bins_use_categorical(bin_order: List[str]) -> bool:
    if not bin_order:
        return False
    numeric_like = sum(1 for bl in bin_order if _is_numeric_bin_label(str(bl)))
    return numeric_like <= len(bin_order) / 2


def _parse_rule_from_bin(
    bin_label: str,
    bin_index: int,
    n_bins: int,
    rule_type: Optional[str] = None,
) -> Tuple[str, float]:
    """从分箱标签推导拒绝条件（命中则拒绝）。头箱/低值坏 → <= 上界；尾箱/高值坏 → > 下界。连续头箱见 _parse_head_run_rule。"""
    s = str(bin_label).strip()
    if any(k in s for k in ("缺失", "nan", "NA", "特殊")):
        return ">", 0.0

    nums = re.findall(r"-?\d+\.?\d*", s)
    lower = s.lower()

    if rule_type == "head":
        if len(nums) >= 2:
            return "<=", float(nums[1])
        if nums:
            return "<=", float(nums[0])
        return "<=", 0.0

    if rule_type == "tail":
        if lower.endswith("inf)") or re.search(r",\s*inf\s*\)", lower):
            lo = float(nums[0]) if nums else 0.0
            return ">", lo
        if len(nums) >= 2:
            return ">", float(nums[0])
        if nums:
            return ">", float(nums[0])
        return ">", 0.0

    # 尾箱 (X, inf) — 必须先于含 inf 的头箱判断
    if lower.endswith("inf)") or re.search(r",\s*inf\s*\)", lower):
        lo = float(nums[0]) if nums else 0.0
        return ">", lo

    # 头箱 (-inf, X]
    if lower.startswith("(-inf") or "(-inf" in lower:
        hi = float(nums[-1]) if nums else 0.0
        return "<=", hi

    is_head = rule_type == "head" or bin_index == 0
    is_tail = rule_type == "tail" or bin_index >= n_bins - 1

    # 单值箱 [5.0] — 尾箱 [1.0] 应对齐 serial_strategy：整数计数用 >0 而非 >1
    if s.startswith("[") and s.endswith("]") and len(nums) == 1:
        v = float(nums[0])
        if is_head and not is_tail:
            return "<=", v
        if is_tail or rule_type == "tail":
            if v == 1.0:
                return ">", 0.0
            if v > 1.0:
                return ">", v - 1.0
            return ">", 0.0
        return ">", v

    # 区间 (A, B]
    if len(nums) >= 2:
        lo, hi = float(nums[0]), float(nums[1])
        if is_head and not is_tail:
            return "<=", hi
        if is_tail and not is_head:
            return ">", lo
        return (">", lo) if bin_index >= n_bins // 2 else ("<=", hi)

    if nums:
        v = float(nums[0])
        if is_tail and not is_head:
            return ">", v
        if is_head and not is_tail:
            return "<=", v
        return (">", v) if bin_index >= n_bins // 2 else ("<=", v)
    return ">", 0.0


def _bin_meets_threshold(row: pd.Series, threshold: float, min_hit: int) -> bool:
    return int(row["total"]) >= min_hit and float(row["bad_rate"]) > threshold


def _collect_bad_run_from_end(
    normal: pd.DataFrame,
    from_head: bool,
    threshold: float,
    min_hit: int,
) -> List[Tuple[int, pd.Series]]:
    """从头部或尾部收集连续超阈值的分箱。"""
    run: List[Tuple[int, pd.Series]] = []
    n = len(normal)
    if from_head:
        for i in range(n):
            row = normal.iloc[i]
            if _bin_meets_threshold(row, threshold, min_hit):
                run.append((i, row))
            else:
                break
    else:
        for i in range(n - 1, -1, -1):
            row = normal.iloc[i]
            if _bin_meets_threshold(row, threshold, min_hit):
                run.insert(0, (i, row))
            else:
                break
    return run


def _stats_from_bin_run(run: List[Tuple[int, pd.Series]]) -> Tuple[int, int, float]:
    total = sum(int(r["total"]) for _, r in run)
    bad = sum(int(r.get("bad", 0) or 0) for _, r in run)
    if bad == 0 and total:
        bad = sum(int(round(float(r["bad_rate"]) * int(r["total"]))) for _, r in run)
    br = bad / total if total else 0.0
    return total, bad, br


def _parse_head_run_rule(
    run: List[Tuple[int, pd.Series]], n_bins: int
) -> Tuple[str, float]:
    """头箱拒绝：单箱 <= 上界；连续多箱 <= 末箱上界（如 65%+70% → 拒绝 <= 第二箱上界）。"""
    idx, row = run[-1]
    return _parse_rule_from_bin(
        str(row["bin_label"]), idx, n_bins, rule_type="head"
    )


def _parse_tail_run_rule(
    run: List[Tuple[int, pd.Series]], n_bins: int
) -> Tuple[str, float]:
    """尾箱拒绝：单箱 > 下界；连续多箱 > 首箱（靠左）下界，覆盖整段尾端坏区。"""
    idx, row = run[0]
    return _parse_rule_from_bin(
        str(row["bin_label"]), idx, n_bins, rule_type="tail"
    )


def _format_head_tail_run_reason(
    rtype: str,
    run: List[Tuple[int, pd.Series]],
    threshold: float,
) -> str:
    side = "头" if rtype == "head" else "尾"
    if len(run) == 1:
        _, r = run[0]
        return (
            f"{side}箱坏率{float(r['bad_rate']):.2%} > {threshold:.0%}，"
            f"{int(r['total'])}人"
        )
    total, _, combined_br = _stats_from_bin_run(run)
    bin_labels = "、".join(str(r["bin_label"]) for _, r in run)
    return (
        f"{side}连续{len(run)}箱超阈值（{bin_labels}），"
        f"合并坏率{combined_br:.2%} > {threshold:.0%}，共{total}人"
    )


def check_head_tail_run(
    bins_info: Dict[str, Any],
    threshold: float,
    min_samples: int = 20,
) -> Optional[Tuple[str, List[str], float, int, str, str, float]]:
    """
    头端：第一箱必须超阈值，且从第一箱起连续各箱均超阈值 → 候选；否则不看头端。
    尾端：最后一箱必须超阈值，且从最后一箱起连续各箱均超阈值 → 候选；否则不看尾端。
    中间单独飙高（如 50%→70%→40%）因第一箱/最后一箱未连续超阈值 → 不选。
    """
    if bins_info is None or bins_info["n_normal"] < 1:
        return None
    normal = bins_info["normal"]
    n = len(normal)

    head = normal.iloc[0]
    if _bin_meets_threshold(head, threshold, min_samples):
        run = _collect_bad_run_from_end(normal, True, threshold, min_samples)
        if not run:
            return None
        rtype = "head"
    else:
        tail = normal.iloc[n - 1]
        if not _bin_meets_threshold(tail, threshold, min_samples):
            return None
        run = _collect_bad_run_from_end(normal, False, threshold, min_samples)
        if not run:
            return None
        rtype = "tail"

    combined_total, _, combined_br = _stats_from_bin_run(run)
    bin_labels = [str(r["bin_label"]) for _, r in run]
    reason = _format_head_tail_run_reason(rtype, run, threshold)

    if rtype == "head":
        op, val = _parse_head_run_rule(run, n)
    else:
        op, val = _parse_tail_run_rule(run, n)
    return rtype, bin_labels, combined_br, combined_total, reason, op, val


def _is_rule_bin(
    bl: str,
    rule_source_bin: Optional[str],
    rule_source_bins: Optional[List[str]] = None,
) -> bool:
    if rule_source_bins:
        return bl in rule_source_bins
    return rule_source_bin is not None and bl == rule_source_bin


def _suggest_reject_rule_from_head_tail_run(
    feature: str,
    normal: pd.DataFrame,
    threshold: float,
    min_hit: int,
    ht_run: Tuple[str, List[str], float, int, str, str, float],
) -> Dict[str, Any]:
    rtype, bin_labels, combined_br, combined_total, _, op, val = ht_run
    meets = combined_total >= min_hit and combined_br > threshold
    anchor = bin_labels[0]
    return {
        "rule_operator": op,
        "rule_threshold": val,
        "rule_display": f"{feature}{op}{_format_threshold(val)}",
        "rule_source_bin": anchor,
        "rule_source_bins": bin_labels,
        "rule_source_bad_rate": combined_br,
        "rule_source_obs": combined_total,
        "rule_meets_min_hit": meets,
    }


def _suggest_reject_rule(
    feature: str,
    normal_bins: pd.DataFrame,
    bad_rate_threshold: float,
    min_hit: int,
    rule_type: Optional[str] = None,
    rule_bin_label: Optional[str] = None,
) -> Dict[str, Any]:
    """建议拒绝规则：优先头/尾箱（与 check_head_tail 一致），否则取坏率最高箱。"""
    normal = normal_bins.sort_values("min_bin").reset_index(drop=True)
    n = len(normal)
    if n == 0:
        return {
            "rule_operator": ">",
            "rule_threshold": 0.0,
            "rule_display": feature,
            "rule_source_bin": "",
            "rule_meets_min_hit": False,
        }

    best = None
    rel_idx = 0
    if rule_type and rule_bin_label:
        matched = normal[normal["bin_label"].astype(str) == str(rule_bin_label)]
        if not matched.empty:
            best = matched.iloc[0]
            rel_idx = int(matched.index[0])

    if best is None:
        best_idx = int(normal["bad_rate"].idxmax())
        best = normal.loc[best_idx]
        rel_idx = int(normal.index.get_loc(best_idx))

    op, val = _parse_rule_from_bin(
        str(best["bin_label"]), rel_idx, n, rule_type=rule_type
    )
    meets = int(best["total"]) >= min_hit and float(best["bad_rate"]) > bad_rate_threshold

    return {
        "rule_operator": op,
        "rule_threshold": val,
        "rule_display": f"{feature}{op}{_format_threshold(val)}",
        "rule_source_bin": str(best["bin_label"]),
        "rule_source_bad_rate": float(best["bad_rate"]),
        "rule_source_obs": int(best["total"]),
        "rule_meets_min_hit": meets,
    }


def _feature_col(df: pd.DataFrame) -> str:
    if "feature" in df.columns:
        return "feature"
    if "变量英文名" in df.columns:
        return "变量英文名"
    raise ValueError("分箱结果缺少 feature / 变量英文名 列")


def _bin_sort_key(bl: str) -> Tuple[int, float]:
    s = str(bl).strip().lower()
    if s.startswith("special(") or any(k in bl for k in ("缺失", "nan", "NA", "特殊")):
        return (-1, 0)
    nums = re.findall(r"-?\d+\.?\d*", bl)
    if bl.startswith("(-inf"):
        return (0, float(nums[-1]) if nums else 0)
    if "inf)" in bl.lower():
        return (0, float(nums[0]) if nums else 999999)
    return (0, float(nums[0]) if nums else 0)


def _bins_need_train_recalc(train_bins: List[Dict[str, Any]]) -> bool:
    """Excel 分箱表无样本量时需用 Train 原始帧重算（头尾5% 等）。"""
    if not train_bins:
        return False
    return sum(int(b.get("obs") or 0) for b in train_bins) == 0


def _bins_for_feature(
    raw_df: pd.DataFrame,
    feat: str,
    rule_source_bin: Optional[str] = None,
    bad_rate_threshold: float = 0.0,
    rule_source_bins: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    col = _feature_col(raw_df)
    sub = raw_df[raw_df[col] == feat].copy()
    if sub.empty:
        return []

    br_col = "%Bad_Rate" if "%Bad_Rate" in sub.columns else "bad_rate"
    obs_col = "#Obs" if "#Obs" in sub.columns else "total"
    bad_col = "#Bad" if "#Bad" in sub.columns else "bad"
    bin_col = "Bin" if "Bin" in sub.columns else "bin_label"

    sub = sub.copy()
    sub["_sort"] = sub[bin_col].astype(str).apply(_bin_sort_key)
    sub = sub.sort_values("_sort")

    rows: List[Dict[str, Any]] = []
    for _, r in sub.iterrows():
        bl = str(r.get(bin_col, ""))
        br = float(pd.to_numeric(r.get(br_col, 0), errors="coerce") or 0)
        rows.append({
            "bin": bl,
            "obs": int(pd.to_numeric(r.get(obs_col, 0), errors="coerce") or 0),
            "bad": int(pd.to_numeric(r.get(bad_col, 0), errors="coerce") or 0),
            "bad_rate": br,
            "lift": _safe_float(r.get("Lift", r.get("lift"))),
            "iv_bin": _safe_float(r.get("IV(bin)", r.get("IV(bin)"))),
            "woe": _safe_float(r.get("WOE")),
            "is_rule_bin": _is_rule_bin(bl, rule_source_bin, rule_source_bins),
            "is_high_bad": br > bad_rate_threshold and not any(
                k in bl for k in ("缺失", "nan", "NA", "特殊")
            ),
        })
    return rows


def _bins_for_feature_from_train_defs(
    data: pd.DataFrame,
    train_detail_df: pd.DataFrame,
    feat: str,
    rule_source_bin: Optional[str] = None,
    bad_rate_threshold: float = 0.0,
    rule_source_bins: Optional[List[str]] = None,
    value_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """用 Train 分箱边界在 Test（或其它）样本上重新统计，保证箱标签与 Train 一致。"""
    col = _feature_col(train_detail_df)
    bin_col = "Bin" if "Bin" in train_detail_df.columns else "bin_label"
    vd = train_detail_df[train_detail_df[col] == feat].copy()
    if vd.empty:
        return []

    vd["_sort"] = vd[bin_col].astype(str).apply(_bin_sort_key)
    vd = vd.sort_values("_sort")
    bin_order = vd[bin_col].astype(str).tolist()

    if data.empty or feat not in data.columns or "overdue_flag" not in data.columns:
        return [
            {
                "bin": bl,
                "obs": 0,
                "bad": 0,
                "bad_rate": 0.0,
                "lift": None,
                "iv_bin": None,
                "woe": None,
                "is_rule_bin": _is_rule_bin(bl, rule_source_bin, rule_source_bins),
                "is_high_bad": False,
            }
            for bl in bin_order
        ]

    bdefs = [(bl, parse_bin_boundary(bl)) for bl in bin_order]

    ds = data[[feat, "overdue_flag"]].copy()
    bin_order = [str(bl) for bl in bin_order]
    use_cate = (value_type or _column_value_type(data, feat)) == "categorical"
    if use_cate:
        ds["_bin"] = ds[feat].apply(lambda v: _assign_categorical_bin(v, bin_order))
    else:
        ds[feat] = ds[feat].replace(SPECIAL_VALUES, np.nan)
        ds["_bin"] = ds[feat].apply(
            lambda v: assign_bin_with_fallback(v, bdefs, bin_order)
        )
        miss = ds[feat].isna()
        if miss.any():
            sp = [
                bl for bl, m in bdefs
                if m and ((len(m) == 3 and m[2]) or (len(m) >= 5 and m[4]))
            ]
            if sp:
                ds.loc[miss, "_bin"] = sp[0]

    overall = float(ds["overdue_flag"].mean()) if len(ds) else 0.0
    rows: List[Dict[str, Any]] = []
    for bl in bin_order:
        sub = ds[ds["_bin"].astype(str) == bl]
        obs = int(len(sub))
        bad = int(sub["overdue_flag"].sum()) if obs else 0
        br = bad / obs if obs else 0.0
        lift = br / overall if overall else None
        rows.append({
            "bin": bl,
            "obs": obs,
            "bad": bad,
            "bad_rate": br,
            "lift": _safe_float(lift),
            "iv_bin": None,
            "woe": None,
            "is_rule_bin": _is_rule_bin(bl, rule_source_bin, rule_source_bins),
            "is_high_bad": br > bad_rate_threshold and not any(
                k in bl for k in ("缺失", "nan", "NA", "特殊")
            ),
        })
    return rows


def _finalize_test_bins_from_train(
    train_bins: List[Dict[str, Any]],
    test_bins: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """保证 Test 与 Train 箱标签、顺序完全一致；缺失箱补 0。"""
    if not train_bins:
        return test_bins
    by_bin = {str(b["bin"]): b for b in test_bins}
    out: List[Dict[str, Any]] = []
    for tb in train_bins:
        bl = str(tb["bin"])
        if bl in by_bin:
            row = dict(by_bin[bl])
            row["bin"] = bl
            out.append(row)
        else:
            out.append({
                "bin": bl,
                "obs": 0,
                "bad": 0,
                "bad_rate": 0.0,
                "lift": None,
                "iv_bin": None,
                "woe": None,
                "is_rule_bin": tb.get("is_rule_bin", False),
                "is_high_bad": False,
            })
    return out


def _bin_col_name(df: pd.DataFrame) -> str:
    if "Bin" in df.columns:
        return "Bin"
    if "bin_label" in df.columns:
        return "bin_label"
    raise ValueError("分箱结果缺少 Bin / bin_label 列")


def _merge_computed_bin_row(row: Dict[str, Any], computed: Dict[str, Any]) -> Dict[str, Any]:
    """将重算后的 obs/bad/bad_rate 写回 Train 行结构（供 Test 表对齐）。"""
    obs = int(computed.get("obs", 0) or 0)
    bad = int(computed.get("bad", 0) or 0)
    br = float(computed.get("bad_rate", 0) or 0)
    row = dict(row)
    if "#Obs" in row:
        row["#Obs"] = obs
        row["#Bad"] = bad
        row["%Bad_Rate"] = br
        if "#Good" in row:
            row["#Good"] = obs - bad
    else:
        row["total"] = obs
        row["bad"] = bad
        row["bad_rate"] = br
    if computed.get("lift") is not None:
        if "Lift" in row:
            row["Lift"] = computed["lift"]
        elif "lift" in row:
            row["lift"] = computed["lift"]
    return row


def rebuild_test_binning_sheet(
    train_detail: pd.DataFrame,
    test_data: pd.DataFrame,
) -> pd.DataFrame:
    """用 Train 分箱边界在 Test 上重算，行结构与 Train 一致（与 Step5 网页展示相同）。"""
    if train_detail.empty or test_data.empty:
        return pd.DataFrame()
    col = _feature_col(train_detail)
    bin_col = _bin_col_name(train_detail)
    features = train_detail[col].drop_duplicates().tolist()
    chunks: List[pd.DataFrame] = []
    for feat in features:
        feat_rows = train_detail[train_detail[col] == feat].copy()
        if feat_rows.empty:
            continue
        if feat not in test_data.columns:
            computed_list = [
                {"bin": str(r[bin_col]), "obs": 0, "bad": 0, "bad_rate": 0.0, "lift": None}
                for _, r in feat_rows.iterrows()
            ]
        else:
            computed_list = _bins_for_feature_from_train_defs(
                test_data, train_detail, str(feat)
            )
        by_bin = {str(c["bin"]): c for c in computed_list}
        rebuilt = []
        for _, r in feat_rows.iterrows():
            bl = str(r[bin_col])
            computed = by_bin.get(bl, {"obs": 0, "bad": 0, "bad_rate": 0.0, "lift": None})
            rebuilt.append(_merge_computed_bin_row(r.to_dict(), computed))
        chunks.append(pd.DataFrame(rebuilt))
    return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()


def _load_test_frame(job_id: str) -> pd.DataFrame:
    job = get_job(job_id)
    if not job:
        raise ValueError("分箱任务不存在")
    params = job.run_params or {}
    file_path = params.get("file_path")
    if not file_path or not Path(file_path).exists():
        return pd.DataFrame()
    raw_df = read_dataframe(Path(file_path))
    label = params.get("label") or "target"
    _, te = _prepare_stability_frames(
        raw_df,
        label,
        params.get("time_col", "apply_time"),
        params.get("split_mode", "ai"),
        float(params.get("oot_ratio", 0.2)),
        params.get("cutoff_date"),
        Path(params["train_file_path"]) if params.get("train_file_path") else None,
        Path(params["test_file_path"]) if params.get("test_file_path") else None,
    )
    if te is None or te.empty:
        return pd.DataFrame()
    if "overdue_flag" not in te.columns and label in te.columns:
        te = te.copy()
        te["overdue_flag"] = _normalize_binary_label(te[label]).astype(int)
    return te


def _job_has_test_split(job) -> bool:
    params = job.run_params or {}
    if params.get("split_mode") == "manual" and params.get("test_file_path"):
        return True
    if params.get("split_mode") == "cutoff" and params.get("cutoff_date"):
        return True
    if params.get("split_mode") == "ai" and float(params.get("oot_ratio", 0.2)) > 0:
        return True
    return False


def replace_test_binning_sheet_in_workbook(path: Path, test_df: pd.DataFrame) -> None:
    """替换 Test 分箱明细，并重写 Excel 使 Test 与 Train 格式一致。"""
    if test_df.empty:
        return
    from services.binning_excel_format import (
        resolve_binning_sheet_names,
        write_formatted_binning_workbook,
    )

    train_raw, _ = read_binning_sheets(str(path))
    train_sheet, test_sheet = resolve_binning_sheet_names(str(path))
    write_formatted_binning_workbook(
        path,
        train_raw,
        test_df,
        train_sheet=train_sheet,
        test_sheet=test_sheet or "Test分箱明细",
    )


def align_test_binning_workbook(
    path: Path,
    test_data: pd.DataFrame,
) -> bool:
    """将 workbook 内 Test 分箱明细对齐为 Train 边界重算结果；成功返回 True。"""
    if test_data.empty or not path.exists():
        return False
    train_raw, test_raw = read_binning_sheets(str(path))
    if train_raw is None or train_raw.empty:
        return False
    if test_raw is None or test_raw.empty:
        return False
    aligned = rebuild_test_binning_sheet(train_raw, test_data)
    if aligned.empty:
        return False
    replace_test_binning_sheet_in_workbook(path, aligned)
    return True


def ensure_test_binning_aligned_for_job(job_id: str) -> None:
    """下载或串联分析前：保证 Test 分箱与网页展示一致（Train 边界 + Test 重算）。"""
    job = get_job(job_id)
    if not job or not job.output_path or not job.output_path.exists():
        return
    if not _job_has_test_split(job):
        return
    if job.summary.get("test_aligned"):
        return
    method = (job.run_params or {}).get("method")
    if method in ("quantile", "chisquare", "headtail5"):
        # 分箱任务完成时已写入对齐后的 Test，无需在下载时再全量重算
        return
    try:
        te = _load_test_frame(job_id)
        if te.empty:
            return
        if align_test_binning_workbook(job.output_path, te):
            job.summary["test_aligned"] = True
    except Exception:
        return


def _align_test_bins_to_train(
    train_bins: List[Dict[str, Any]],
    test_bins: List[Dict[str, Any]],
    rule_source_bin: Optional[str],
    bad_rate_threshold: float,
    rule_source_bins: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Excel 对齐兜底：按 Train 箱顺序补全 Test（无原始数据时使用）。"""
    by_bin = {b["bin"]: b for b in test_bins}
    out: List[Dict[str, Any]] = []
    for tb in train_bins:
        bl = tb["bin"]
        if bl in by_bin:
            row = dict(by_bin[bl])
        else:
            row = {
                "bin": bl,
                "obs": 0,
                "bad": 0,
                "bad_rate": 0.0,
                "lift": None,
                "iv_bin": None,
                "woe": None,
            }
        row["is_rule_bin"] = _is_rule_bin(bl, rule_source_bin, rule_source_bins)
        br = float(row.get("bad_rate") or 0)
        row["is_high_bad"] = br > bad_rate_threshold and not any(
            k in bl for k in ("缺失", "nan", "NA", "特殊")
        )
        out.append(row)
    return out


def _safe_float(v) -> Optional[float]:
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _analyze_feature_categorical(
    feat: str,
    train_norm: pd.DataFrame,
    train_total: int,
    threshold: float,
    min_hit: int,
) -> Optional[Dict[str, Any]]:
    ft = train_norm[train_norm["feature"] == feat]
    if ft.empty:
        return None

    high_bins = _collect_high_bad_bins(ft, threshold, min_hit)
    if not high_bins:
        return None

    max_br = max(b["bad_rate"] for b in high_bins)
    cn_name = get_chinese_name(feat)
    bin_names = [b["bin"] for b in high_bins]
    reason = (
        f"共 {len(high_bins)} 个类别坏率 > {threshold:.0%}，"
        f"最高 {max_br:.2%}（请在明细中勾选要拒绝的类别）"
    )
    eff = "好" if max_br >= 0.65 else ("一般" if max_br >= threshold else "不好")

    return {
        "feature": feat,
        "chinese_name": cn_name,
        "value_type": "categorical",
        "max_bad_rate": max_br,
        "effect_label": eff,
        "reason": reason,
        "rule_type": "categorical",
        "hit_count": max(b["obs"] for b in high_bins),
        "sample_pct": max(b["obs"] for b in high_bins) / train_total if train_total else 0.0,
        "category_key": classify_category(cn_name, feat),
        "high_bad_categories": high_bins,
        "rule_operator": "in",
        "rule_threshold": 0.0,
        "rule_values": [],
        "rule_display": _format_rule_display({
            "feature": feat, "operator": "in", "values": [],
        }),
        "rule_source_bin": bin_names[0] if bin_names else "",
        "rule_source_bad_rate": max_br,
        "rule_source_obs": high_bins[0]["obs"] if high_bins else 0,
        "rule_meets_min_hit": True,
    }


def _analyze_feature(
    feat: str,
    train_norm: pd.DataFrame,
    train_total: int,
    train_overall: float,
    threshold: float,
    min_hit: int,
    value_type: str = "numeric",
) -> Optional[Dict[str, Any]]:
    if value_type == "categorical":
        return _analyze_feature_categorical(
            feat, train_norm, train_total, threshold, min_hit
        )

    ft = train_norm[train_norm["feature"] == feat]
    normal = ft[~ft["is_special"]].sort_values("min_bin").reset_index(drop=True)
    if len(normal) < 1:
        return None

    max_br = float(normal["bad_rate"].max())
    cn_name = get_chinese_name(feat)
    bins_info = {"normal": normal, "n_normal": len(normal), "all": ft}
    ht = check_head_tail_run(bins_info, threshold, min_samples=min_hit)
    if not ht:
        return None

    rtype, _bin_labels, r_br, hit, ht_reason, _, _ = ht
    if hit < min_hit:
        return None

    rule_info = _suggest_reject_rule_from_head_tail_run(
        feat, normal, threshold, min_hit, ht
    )
    u_shape = check_u_shape(bins_info, train_overall, min_samples=min_hit)

    if u_shape:
        _, _, u_reason = u_shape
        return {
            "feature": feat,
            "chinese_name": cn_name,
            "value_type": "numeric",
            "max_bad_rate": r_br,
            "effect_label": "U型人工判断",
            "reason": f"{ht_reason}；{u_reason}",
            "rule_type": "u_shape",
            "hit_count": hit,
            "sample_pct": hit / train_total if train_total else 0,
            "category_key": classify_category(cn_name, feat),
            **rule_info,
        }

    eff = "好" if r_br >= 0.65 else ("一般" if r_br >= threshold else "不好")
    return {
        "feature": feat,
        "chinese_name": cn_name,
        "value_type": "numeric",
        "max_bad_rate": r_br,
        "effect_label": eff,
        "reason": ht_reason,
        "rule_type": rtype,
        "hit_count": hit,
        "sample_pct": hit / train_total if train_total else 0,
        "category_key": classify_category(cn_name, feat),
        **rule_info,
    }


def _analyze_feature_forced_inclusion(
    feat: str,
    train_norm: pd.DataFrame,
    train_total: int,
    value_type: str = "numeric",
) -> Optional[Dict[str, Any]]:
    """手动追加：不因阈值过滤，基于分箱结果生成候选条目。"""
    ft = train_norm[train_norm["feature"] == feat]
    if ft.empty:
        return None
    cn_name = get_chinese_name(feat)

    if value_type == "categorical":
        bins: List[Dict[str, Any]] = []
        for _, row in ft.iterrows():
            obs = int(row.get("total", 0) or 0)
            if obs <= 0:
                continue
            br = float(row.get("bad_rate", 0) or 0)
            bins.append({"bin": str(row["bin_label"]), "obs": obs, "bad_rate": br})
        if not bins:
            return None
        best = max(bins, key=lambda b: b["bad_rate"])
        max_br = float(best["bad_rate"])
        return {
            "feature": feat,
            "chinese_name": cn_name,
            "value_type": "categorical",
            "max_bad_rate": max_br,
            "effect_label": "手动追加",
            "reason": f"手动追加（最高类别坏率 {max_br:.2%}）",
            "rule_type": "categorical",
            "hit_count": int(best["obs"]),
            "sample_pct": int(best["obs"]) / train_total if train_total else 0.0,
            "category_key": classify_category(cn_name, feat),
            "high_bad_categories": sorted(bins, key=lambda b: -b["bad_rate"]),
            "rule_operator": "in",
            "rule_threshold": 0.0,
            "rule_values": [],
            "rule_display": _format_rule_display({"feature": feat, "operator": "in", "values": []}),
            "rule_source_bin": str(best["bin"]),
            "rule_source_bad_rate": max_br,
            "rule_source_obs": int(best["obs"]),
            "rule_meets_min_hit": int(best["obs"]) >= 10,
            "manual_include": True,
        }

    normal = ft[~ft["is_special"]].sort_values("min_bin").reset_index(drop=True)
    if normal.empty:
        return None
    best_idx = int(normal["bad_rate"].idxmax())
    best = normal.loc[best_idx]
    rel_idx = int(normal.index.get_loc(best_idx))
    n = len(normal)
    op, val = _parse_rule_from_bin(str(best["bin_label"]), rel_idx, n)
    hit = int(best["total"])
    max_br = float(best["bad_rate"])
    return {
        "feature": feat,
        "chinese_name": cn_name,
        "value_type": "numeric",
        "max_bad_rate": max_br,
        "effect_label": "手动追加",
        "reason": f"手动追加（最高箱坏率 {max_br:.2%}，命中 {hit} 人）",
        "rule_type": "manual",
        "hit_count": hit,
        "sample_pct": hit / train_total if train_total else 0.0,
        "category_key": classify_category(cn_name, feat),
        "rule_operator": op,
        "rule_threshold": val,
        "rule_display": f"{feat}{op}{_format_threshold(val)}",
        "rule_source_bin": str(best["bin_label"]),
        "rule_source_bad_rate": max_br,
        "rule_source_obs": hit,
        "rule_meets_min_hit": hit >= 10,
        "manual_include": True,
    }


def _feature_qualifies_at_threshold(
    feat: str,
    train_norm: pd.DataFrame,
    threshold: float,
    min_hit: int,
    raw_df: Optional[pd.DataFrame],
    feature_df: Optional[pd.DataFrame] = None,
) -> Optional[int]:
    """特征是否进入候选；返回代表命中人数（数值=头尾箱，类别=最高坏率类别样本量）。"""
    ft = feature_df if feature_df is not None else train_norm[train_norm["feature"] == feat]
    vtype = _feature_value_type(feat, raw_df, ft if feature_df is not None else train_norm)
    if vtype == "categorical":
        high = _collect_high_bad_bins(ft, threshold, min_hit)
        if not high:
            return None
        return int(max(b["obs"] for b in high))

    normal = ft[~ft["is_special"]].sort_values("min_bin").reset_index(drop=True)
    if len(normal) < 1:
        return None
    bins_info = {"normal": normal, "n_normal": len(normal), "all": ft}
    ht = check_head_tail_run(bins_info, threshold, min_samples=min_hit)
    if not ht:
        return None
    return int(ht[3])


def _threshold_overview(
    train_norm: pd.DataFrame,
    train_total: int,
    train_overall: float,
    min_hit: int,
    raw_df: Optional[pd.DataFrame] = None,
) -> List[Dict[str, Any]]:
    """在不同坏率阈值下，统计可进入候选的变量数（数值看头尾箱，类别看任一超阈值类别）。"""
    thresholds = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
    overview = []
    feature_groups = {
        name: grp for name, grp in train_norm.groupby("feature", sort=False)
    }

    for th in thresholds:
        count = 0
        total_hit = 0
        for feat, ft in feature_groups.items():
            hit = _feature_qualifies_at_threshold(
                feat, train_norm, th, min_hit, raw_df, feature_df=ft
            )
            if hit is not None:
                count += 1
                total_hit += hit
        overview.append({
            "threshold": th,
            "threshold_pct": f"{th:.0%}",
            "feature_count": count,
            "avg_hit": int(total_hit / count) if count else 0,
            "hint": (
                f"约 {count} 个变量满足条件（数值：头/尾箱；类别：任一类别）"
                f"坏率 > {th:.0%}，且单箱 ≥ {min_hit} 人"
            ),
        })
    return overview


def _prepare_stability_frames(
    raw_df: pd.DataFrame,
    label: str,
    time_col: str,
    split_mode: str,
    oot_ratio: float,
    cutoff_date: Optional[str],
    train_file_path: Optional[Path],
    test_file_path: Optional[Path],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    norm = _normalize_binary_label(raw_df[label])
    df = raw_df[norm.isin([0, 1])].copy()
    df["_label"] = norm.loc[df.index].astype(int)

    if split_mode == "manual" and train_file_path and test_file_path:
        tr = read_dataframe(train_file_path)
        te = read_dataframe(test_file_path)
        tr_norm = _normalize_binary_label(tr[label])
        te_norm = _normalize_binary_label(te[label])
        tr = tr[tr_norm.isin([0, 1])].copy()
        te = te[te_norm.isin([0, 1])].copy()
        tr["overdue_flag"] = tr_norm.loc[tr.index].astype(int)
        te["overdue_flag"] = te_norm.loc[te.index].astype(int)
    else:
        tr, te = split_dataframe(df, label, time_col, split_mode, oot_ratio, cutoff_date)
        if tr is None:
            tr = df.copy()
            te = df.iloc[0:0].copy()
        tr["overdue_flag"] = _normalize_binary_label(tr[label]).astype(int)
        if len(te):
            te["overdue_flag"] = _normalize_binary_label(te[label]).astype(int)

    if "apply_month" not in tr.columns:
        month_src = None
        for c in ("apply_month", "apply_date", time_col, "create_time_x"):
            if c in tr.columns and c != "apply_month":
                month_src = c
                break
        if month_src:
            for frame in (tr, te):
                if len(frame):
                    src = frame[month_src]
                    if pd.api.types.is_numeric_dtype(src):
                        dt = pd.to_datetime(src, unit="s", errors="coerce")
                    else:
                        dt = pd.to_datetime(src, errors="coerce")
                    frame["apply_month"] = dt.dt.strftime("%Y-%m")

    if "money" not in tr.columns:
        tr["money"] = 1000
    if len(te) and "money" not in te.columns:
        te["money"] = 1000

    for frame in (tr, te):
        if len(frame) and "apply_month" in frame.columns:
            frame["apply_month"] = frame["apply_month"].astype(str).str.strip()

    return tr, te


def _stability_table(
    data: pd.DataFrame,
    detail_df: pd.DataFrame,
    feature: str,
    months: List[str],
    value_type: Optional[str] = None,
) -> Dict[str, Any]:
    """将 build_stability_data 输出转为前端表格结构。"""
    if data.empty or "apply_month" not in data.columns:
        return {"months": [], "bins": [], "columns": SUB_COLS}

    col = _feature_col(detail_df)
    bin_col = "Bin" if "Bin" in detail_df.columns else "bin_label"
    vd = detail_df[detail_df[col] == feature]
    if vd.empty or feature not in data.columns:
        return {"months": months, "bins": [], "columns": SUB_COLS}

    vd = vd.copy()
    vd["_sort"] = vd[bin_col].astype(str).apply(_bin_sort_key)
    vd = vd.sort_values("_sort")
    bin_order = vd[bin_col].astype(str).tolist()
    bdefs = [(bl, parse_bin_boundary(bl)) for bl in bin_order]

    ds = data[[feature, "apply_month", "overdue_flag", "money"]].copy()
    bin_order = [str(bl) for bl in bin_order]
    if (value_type or _column_value_type(data, feature)) == "categorical":
        ds["_bin"] = ds[feature].apply(lambda v: _assign_categorical_bin(v, bin_order))
    else:
        ds[feature] = ds[feature].replace(SPECIAL_VALUES, np.nan)
        ds["_bin"] = ds[feature].apply(lambda v: assign_bin(v, bdefs))
        miss = ds[feature].isna()
        if miss.any():
            sp = [
                bl for bl, m in bdefs
                if m and ((len(m) == 3 and m[2]) or (len(m) >= 5 and m[4]))
            ]
            if sp:
                ds.loc[miss, "_bin"] = sp[0]

    grp = ds.groupby(["_bin", "apply_month"]).agg(
        obs=("overdue_flag", "count"),
        bad=("overdue_flag", "sum"),
        money=("money", "sum"),
    ).reset_index()
    bdf = ds[ds["overdue_flag"] == 1].groupby(["_bin", "apply_month"])["money"].sum().reset_index().rename(columns={"money": "bad_money"})
    grp = grp.merge(bdf, on=["_bin", "apply_month"], how="left")
    grp["bad_money"] = grp["bad_money"].fillna(0.0)

    bins_out: List[Dict[str, Any]] = []
    months = [str(m) for m in months]

    for bl in bin_order:
        row: Dict[str, Any] = {"bin": bl, "months": {}, "total": {}}
        rt = {"obs": 0, "bad": 0, "money": 0.0, "bad_money": 0.0}
        for m in months:
            sub = grp[(grp["_bin"].astype(str) == bl) & (grp["apply_month"].astype(str) == m)]
            obs = int(sub.iloc[0]["obs"]) if len(sub) else 0
            bad = int(sub.iloc[0]["bad"]) if len(sub) else 0
            money = float(sub.iloc[0]["money"]) if len(sub) else 0.0
            bm = float(sub.iloc[0]["bad_money"]) if len(sub) else 0.0
            row["months"][m] = {
                "obs": obs,
                "bad": bad,
                "bad_rate": bad / obs if obs else 0,
                "money_bad_rate": bm / money if money else 0,
                "money": money,
                "bad_money": bm,
            }
            rt["obs"] += obs
            rt["bad"] += bad
            rt["money"] += money
            rt["bad_money"] += bm
        row["total"] = {
            "obs": rt["obs"],
            "bad": rt["bad"],
            "bad_rate": rt["bad"] / rt["obs"] if rt["obs"] else 0,
            "money_bad_rate": rt["bad_money"] / rt["money"] if rt["money"] else 0,
            "money": rt["money"],
            "bad_money": rt["bad_money"],
        }
        bins_out.append(row)

    cs = {m: {"obs": 0, "bad": 0, "money": 0.0, "bad_money": 0.0} for m in months}
    at = {"obs": 0, "bad": 0, "money": 0.0, "bad_money": 0.0}
    for b in bins_out:
        for m in months:
            c = b["months"].get(m, {})
            cs[m]["obs"] += c.get("obs", 0)
            cs[m]["bad"] += c.get("bad", 0)
            cs[m]["money"] += float(c.get("money", 0) or 0)
            cs[m]["bad_money"] += float(c.get("bad_money", 0) or 0)
        t = b.get("total", {})
        at["obs"] += t.get("obs", 0)
        at["bad"] += t.get("bad", 0)
        at["money"] += float(t.get("money", 0) or 0)
        at["bad_money"] += float(t.get("bad_money", 0) or 0)

    summary: Dict[str, Any] = {"bin": "合计", "months": {}, "total": {}}
    for m in months:
        o, bd = cs[m]["obs"], cs[m]["bad"]
        mo, bm = cs[m]["money"], cs[m]["bad_money"]
        summary["months"][m] = {
            "obs": o,
            "bad": bd,
            "bad_rate": bd / o if o else 0,
            "money_bad_rate": bm / mo if mo else 0,
        }
    summary["total"] = {
        "obs": at["obs"],
        "bad": at["bad"],
        "bad_rate": at["bad"] / at["obs"] if at["obs"] else 0,
        "money_bad_rate": at["bad_money"] / at["money"] if at["money"] else 0,
    }

    return {
        "months": months,
        "bins": bins_out,
        "summary": summary,
        "columns": SUB_COLS,
    }


def build_threshold_overview(
    job_id: str,
    min_hit_count: int = 10,
) -> Dict[str, Any]:
    bundle = _get_job_binning_bundle(job_id)
    train = bundle["train_norm"]
    first_feat = train["feature"].iloc[0] if len(train) else None
    train_total = int(train[train["feature"] == first_feat]["total"].sum()) if first_feat else 0
    train_overall = calc_overall_bad_rate(train) or 0.0
    raw_df = _get_job_raw_df(job_id)
    return {
        "threshold_overview": _threshold_overview(
            train, train_total, train_overall, min_hit_count, raw_df=raw_df
        ),
    }


def warmup_review_cache(job_id: str) -> Dict[str, str]:
    """预加载分箱 Excel 与 Train 样本帧，供拒绝预览/特征明细复用。"""
    _get_job_binning_bundle(job_id)
    try:
        _load_train_frame(job_id)
        train_ok = True
    except Exception:
        train_ok = False
    return {"status": "ok", "train_frame": train_ok}


def _review_result_cache_key(
    job_id: str,
    bad_rate_threshold: float,
    min_hit_count: int,
    include_features: Optional[List[str]],
    include_threshold_overview: bool,
) -> str:
    inc = ",".join(sorted(include_features or []))
    return (
        f"{job_id}|{bad_rate_threshold:.4f}|{min_hit_count}|{inc}|"
        f"{int(include_threshold_overview)}"
    )


def build_feature_review(
    job_id: str,
    bad_rate_threshold: float = 0.60,
    min_hit_count: int = 10,
    include_features: Optional[List[str]] = None,
    include_threshold_overview: bool = True,
) -> Dict[str, Any]:
    cache_key = _review_result_cache_key(
        job_id, bad_rate_threshold, min_hit_count,
        include_features, include_threshold_overview,
    )
    if cache_key in _REVIEW_RESULT_CACHE:
        return _REVIEW_RESULT_CACHE[cache_key]

    bundle = _get_job_binning_bundle(job_id)
    job = bundle["job"]
    train = bundle["train_norm"]
    test = bundle["test_norm"]

    params = job.run_params or {}
    train_overall = calc_overall_bad_rate(train) or 0.0
    test_overall = calc_overall_bad_rate(test) if test is not None else None

    first_feat = train["feature"].iloc[0] if len(train) else None
    train_total = int(train[train["feature"] == first_feat]["total"].sum()) if first_feat else 0

    raw_df = _get_job_raw_df(job_id)

    forced_set = {f.strip() for f in (include_features or []) if f and str(f).strip()}
    all_feature_names = train["feature"].unique().tolist()
    feature_groups = {
        name: grp for name, grp in train.groupby("feature", sort=False)
    }
    all_features_light: List[Dict[str, Any]] = []
    results: List[Dict[str, Any]] = []
    seen: set = set()

    for feat in all_feature_names:
        ft = feature_groups[feat]
        vtype = _feature_value_type(feat, raw_df, ft)
        item = _analyze_feature(
            feat,
            train,
            train_total,
            train_overall,
            bad_rate_threshold,
            min_hit_count,
            value_type=vtype,
        )
        is_auto = item is not None
        all_features_light.append({
            "feature": feat,
            "chinese_name": get_chinese_name(feat),
            "value_type": vtype,
            "is_candidate": is_auto,
        })
        if item:
            results.append(item)
            seen.add(feat)

    manual_added = 0
    for feat in forced_set:
        if feat in seen or feat not in all_feature_names:
            continue
        vtype = _feature_value_type(feat, raw_df, train)
        forced_item = _analyze_feature_forced_inclusion(
            feat, train, train_total, value_type=vtype
        )
        if forced_item:
            results.append(forced_item)
            seen.add(feat)
            manual_added += 1

    results.sort(key=lambda r: (r["category_key"], -r["max_bad_rate"]))

    clusters_map: Dict[str, List[Dict[str, Any]]] = {}
    for r in results:
        gkey = r["category_key"]
        clusters_map.setdefault(gkey, []).append(r)

    clusters = [
        {
            "group_id": gkey,
            "group_name": _group_label(gkey),
            "features": feats,
        }
        for gkey, feats in sorted(clusters_map.items(), key=lambda x: x[0])
    ]

    result = {
        "train_bad_rate": train_overall,
        "test_bad_rate": test_overall,
        "portfolio_bad_rate": train_overall,
        "bad_rate_threshold": bad_rate_threshold,
        "min_hit_count": min_hit_count,
        "total_candidates": len(results),
        "auto_candidates": len(results) - manual_added,
        "manual_includes": manual_added,
        "all_features": all_features_light,
        "threshold_overview": (
            _threshold_overview(train, train_total, train_overall, min_hit_count, raw_df=raw_df)
            if include_threshold_overview
            else []
        ),
        "report_summary": _build_report_text(
            train_overall, test_overall, bad_rate_threshold, min_hit_count, len(results), manual_added
        ),
        "clusters": clusters,
        "params": params,
    }

    _REVIEW_RESULT_CACHE[cache_key] = result
    _trim_review_result_cache()
    try:
        _load_train_frame(job_id)
    except Exception:
        pass
    return result


def _build_report_text(
    train_br: float,
    test_br: Optional[float],
    threshold: float,
    min_hit: int,
    n_candidates: int,
    manual_added: int = 0,
) -> str:
    auto_n = n_candidates - manual_added
    lines = [
        f"Train 大盘坏率 {train_br:.2%}，当前拒绝阈值 {threshold:.0%}。",
        f"单箱最少命中 {min_hit} 人（可在上方调整）。",
        f"数值变量（数字列）看头/尾箱，类别变量（文本/类别列）请在明细中勾选要拒绝的类别。",
        f"在该阈值下共筛出 {auto_n} 个候选变量",
    ]
    if manual_added:
        lines[-1] += f"，另手动追加 {manual_added} 个"
    lines[-1] += "，请结合业务判断切割点。"
    if test_br is not None:
        lines.insert(1, f"Test 大盘坏率 {test_br:.2%}。")
    lines.append("建议：先看「阈值概览」表格，再点开特征查看 Train/Test 分箱与月度稳定性。")
    return " ".join(lines)


def get_feature_detail(
    job_id: str,
    feature: str,
    bad_rate_threshold: float = 0.60,
    include_stability: bool = False,
) -> Dict[str, Any]:
    detail_key = (
        f"{_DETAIL_CACHE_VER}|{job_id}|{feature}|{bad_rate_threshold:.4f}|{int(include_stability)}"
    )
    if detail_key in _FEATURE_DETAIL_CACHE:
        return _FEATURE_DETAIL_CACHE[detail_key]

    bundle = _get_job_binning_bundle(job_id)
    job = bundle["job"]
    train_raw = bundle["train_raw"]
    test_raw = bundle["test_raw"]
    train_norm = bundle["train_norm"]

    params = job.run_params or {}
    file_path = params.get("file_path")
    raw_df = _get_job_raw_df(job_id)

    ft = train_norm[train_norm["feature"] == feature]
    value_type = _feature_value_type(feature, raw_df, ft if len(ft) else train_norm)
    normal = ft[~ft["is_special"]].sort_values("min_bin").reset_index(drop=True)
    high_bad_categories = _collect_high_bad_bins(ft, bad_rate_threshold, 10)

    if value_type == "categorical":
        rule_info = {
            "rule_operator": "in",
            "rule_threshold": 0.0,
            "rule_values": [],
            "rule_display": _format_rule_display({
                "feature": feature, "operator": "in", "values": [],
            }),
            "rule_source_bin": high_bad_categories[0]["bin"] if high_bad_categories else "",
            "rule_source_bad_rate": high_bad_categories[0]["bad_rate"] if high_bad_categories else 0.0,
            "rule_source_obs": high_bad_categories[0]["obs"] if high_bad_categories else 0,
            "rule_meets_min_hit": bool(high_bad_categories),
        }
        source_bin = rule_info.get("rule_source_bin")
        source_bins = None
    else:
        bins_info = {"normal": normal, "n_normal": len(normal), "all": ft}
        ht = check_head_tail_run(bins_info, bad_rate_threshold, min_samples=10)
        if ht:
            rule_info = _suggest_reject_rule_from_head_tail_run(
                feature, normal, bad_rate_threshold, 10, ht
            )
        else:
            rule_info = _suggest_reject_rule(feature, normal, bad_rate_threshold, 10)
        source_bin = rule_info.get("rule_source_bin")
        source_bins = rule_info.get("rule_source_bins")
    train_bins = _bins_for_feature(
        train_raw, feature, source_bin, bad_rate_threshold, rule_source_bins=source_bins
    )
    if _bins_need_train_recalc(train_bins):
        try:
            tr_frame = _load_train_frame(job_id)
            train_bins_recalc = _bins_for_feature_from_train_defs(
                tr_frame,
                train_raw,
                feature,
                source_bin,
                bad_rate_threshold,
                rule_source_bins=source_bins,
                value_type=value_type,
            )
            if train_bins_recalc and sum(int(b.get("obs") or 0) for b in train_bins_recalc) > 0:
                for i, row in enumerate(train_bins_recalc):
                    if i < len(train_bins):
                        row["is_rule_bin"] = train_bins[i].get("is_rule_bin", False)
                        row["is_high_bad"] = train_bins[i].get("is_high_bad", False)
                train_bins = train_bins_recalc
        except Exception:
            pass

    test_bins: List[Dict[str, Any]] = []
    te: Optional[pd.DataFrame] = None

    if test_raw is not None:
        test_bins = _align_test_bins_to_train(
            train_bins,
            _bins_for_feature(
                test_raw, feature, source_bin, bad_rate_threshold,
                rule_source_bins=source_bins,
            ),
            source_bin,
            bad_rate_threshold,
            rule_source_bins=source_bins,
        )

    if include_stability and file_path and Path(file_path).exists():
        try:
            label = params.get("label") or "target"
            tr, te = _prepare_stability_frames(
                raw_df if raw_df is not None else read_dataframe(Path(file_path)),
                label,
                params.get("time_col", "apply_time"),
                params.get("split_mode", "ai"),
                float(params.get("oot_ratio", 0.2)),
                params.get("cutoff_date"),
                Path(params["train_file_path"]) if params.get("train_file_path") else None,
                Path(params["test_file_path"]) if params.get("test_file_path") else None,
            )
            if len(te):
                test_bins = _bins_for_feature_from_train_defs(
                    te, train_raw, feature, source_bin, bad_rate_threshold,
                    rule_source_bins=source_bins, value_type=value_type,
                )
        except Exception:
            te = None

    if not test_bins and _job_has_test_split(job):
        try:
            te = _load_test_frame(job_id)
            if len(te):
                test_bins = _bins_for_feature_from_train_defs(
                    te, train_raw, feature, source_bin, bad_rate_threshold,
                    rule_source_bins=source_bins, value_type=value_type,
                )
        except Exception:
            pass

    if not test_bins and train_bins and _job_has_test_split(job):
        test_bins = _finalize_test_bins_from_train(train_bins, [])

    test_bins = _finalize_test_bins_from_train(train_bins, test_bins)

    test_obs_sum = sum(int(b.get("obs") or 0) for b in test_bins)
    test_row_count = int(len(te)) if te is not None else 0

    detail: Dict[str, Any] = {
        "feature": feature,
        "chinese_name": get_chinese_name(feature),
        "value_type": value_type,
        "rule": rule_info,
        "high_bad_categories": high_bad_categories,
        "high_bad_bins": [
            {
                "bin": str(r["bin_label"]),
                "obs": int(r["total"]),
                "bad_rate": float(r["bad_rate"]),
            }
            for _, r in normal.iterrows()
            if float(r["bad_rate"]) > bad_rate_threshold
        ] if value_type == "numeric" else high_bad_categories,
        "bins": {
            "train": train_bins,
            "test": test_bins,
        },
        "test_row_count": test_row_count,
        "test_obs_sum": test_obs_sum,
        "bins_aligned_to_train": True,
    }

    empty_stab = {
        "train": {"months": [], "bins": [], "columns": SUB_COLS},
        "test": {"months": [], "bins": [], "columns": SUB_COLS},
    }

    if not include_stability:
        detail["stability"] = empty_stab
        detail["stability_deferred"] = True
        detail["stability_note"] = "点击「月度稳定性」标签按需加载（加快变量浏览）"
        result = _json_safe(detail)
        _FEATURE_DETAIL_CACHE[detail_key] = result
        _trim_feature_detail_cache()
        return result

    if not file_path or not Path(file_path).exists():
        detail["stability"] = empty_stab
        detail["stability_note"] = "缺少原始数据文件，月度稳定性不可用（分箱明细仍完整展示）"
        return _json_safe(detail)

    try:
        label = params.get("label") or "target"
        tr, te = _prepare_stability_frames(
            raw_df if raw_df is not None else read_dataframe(Path(file_path)),
            label,
            params.get("time_col", "apply_time"),
            params.get("split_mode", "ai"),
            float(params.get("oot_ratio", 0.2)),
            params.get("cutoff_date"),
            Path(params["train_file_path"]) if params.get("train_file_path") else None,
            Path(params["test_file_path"]) if params.get("test_file_path") else None,
        )
        if len(te) and not test_bins:
            test_bins = _bins_for_feature_from_train_defs(
                te, train_raw, feature, source_bin, bad_rate_threshold,
                rule_source_bins=source_bins, value_type=value_type,
            )
            test_bins = _finalize_test_bins_from_train(train_bins, test_bins)
            detail["bins"]["test"] = test_bins
            detail["test_obs_sum"] = sum(int(b.get("obs") or 0) for b in test_bins)
            detail["test_row_count"] = int(len(te))

        train_months = [
            str(m) for m in sorted(tr["apply_month"].dropna().unique().tolist())
        ] if "apply_month" in tr.columns else []
        test_months = [
            str(m) for m in sorted(te["apply_month"].dropna().unique().tolist())
        ] if len(te) and "apply_month" in te.columns else []

        stab_train = _stability_table(tr, train_raw, feature, train_months, value_type=value_type)
        stab_test = _stability_table(te, train_raw, feature, test_months, value_type=value_type)
        detail["stability"] = {"train": stab_train, "test": stab_test}

        def _stab_obs(stab: Dict[str, Any]) -> int:
            return int(stab.get("summary", {}).get("total", {}).get("obs", 0))

        train_bin_obs = sum(b["obs"] for b in detail["bins"]["train"])
        test_bin_obs = sum(b["obs"] for b in detail["bins"]["test"])
        detail["stability_check"] = {
            "train": {
                "binning_obs": train_bin_obs,
                "stability_obs": _stab_obs(stab_train),
                "match": train_bin_obs == _stab_obs(stab_train),
            },
            "test": {
                "binning_obs": test_bin_obs,
                "stability_obs": _stab_obs(stab_test),
                "match": test_bin_obs == _stab_obs(stab_test),
            },
        }
        if not train_months and not test_months:
            detail["stability_note"] = "数据中无 apply_month / 时间列，月度稳定性不可用"
    except Exception as exc:
        detail["stability"] = empty_stab
        detail["stability_note"] = f"月度稳定性计算失败（分箱明细已完整展示）: {exc}"

    result = _json_safe(detail)
    _FEATURE_DETAIL_CACHE[detail_key] = result
    _trim_feature_detail_cache()
    return result


def _load_train_frame(job_id: str) -> pd.DataFrame:
    bucket = _REVIEW_CACHE.setdefault(job_id, {})
    if "train_frame" in bucket:
        return bucket["train_frame"]

    job = get_job(job_id)
    if not job:
        raise ValueError("分箱任务不存在")
    params = job.run_params or {}
    file_path = params.get("file_path")
    if not file_path or not Path(file_path).exists():
        raise ValueError("原始数据文件不可用，无法计算拒绝影响")

    raw_df = _get_job_raw_df(job_id)
    if raw_df is None:
        raw_df = read_dataframe(Path(file_path))
    label = params.get("label") or "target"
    tr, _ = _prepare_stability_frames(
        raw_df,
        label,
        params.get("time_col", "apply_time"),
        params.get("split_mode", "ai"),
        float(params.get("oot_ratio", 0.2)),
        params.get("cutoff_date"),
        Path(params["train_file_path"]) if params.get("train_file_path") else None,
        Path(params["test_file_path"]) if params.get("test_file_path") else None,
    )
    if "overdue_flag" not in tr.columns and label in tr.columns:
        tr["overdue_flag"] = _normalize_binary_label(tr[label]).astype(int)
    if "money" not in tr.columns:
        tr["money"] = 1000.0
    bucket["train_frame"] = tr
    _trim_review_cache(job_id)
    return tr


def _portfolio_stats(df: pd.DataFrame) -> Dict[str, Any]:
    n = len(df)
    if n == 0:
        return {"count": 0, "bad_rate": 0.0, "money_bad_rate": 0.0}
    bad = int(df["overdue_flag"].sum())
    money = float(df["money"].sum())
    bad_money = float(df.loc[df["overdue_flag"] == 1, "money"].sum())
    return {
        "count": n,
        "bad_rate": bad / n,
        "money_bad_rate": bad_money / money if money else 0.0,
    }


def _feature_bin_defs_from_detail(
    train_detail_df: pd.DataFrame, feature: str
) -> Tuple[List[str], List[Tuple[str, Any]]]:
    col = _feature_col(train_detail_df)
    bin_col = "Bin" if "Bin" in train_detail_df.columns else "bin_label"
    vd = train_detail_df[train_detail_df[col] == feature].copy()
    if vd.empty:
        return [], []
    vd["_sort"] = vd[bin_col].astype(str).apply(_bin_sort_key)
    vd = vd.sort_values("_sort")
    bin_order = vd[bin_col].astype(str).tolist()
    bdefs = [(bl, parse_bin_boundary(bl)) for bl in bin_order]
    return bin_order, bdefs


def _resolve_rule_source_bins(
    train_norm: pd.DataFrame,
    feature: str,
    operator: str,
    threshold: float,
    bad_rate_threshold: float = 0.60,
    min_hit: int = 10,
) -> Optional[List[str]]:
    """与候选规则一致：头/尾连续超阈值箱标签（用于按分箱边界统计命中，而非裸数值比较）。"""
    ft = train_norm[train_norm["feature"] == feature]
    if ft.empty:
        return None
    normal = ft[~ft["is_special"]].sort_values("min_bin").reset_index(drop=True)
    if normal.empty:
        return None
    ht = check_head_tail_run(
        {"normal": normal, "n_normal": len(normal), "all": ft},
        bad_rate_threshold,
        min_samples=min_hit,
    )
    if not ht:
        return None
    _, bin_labels, _, _, _, op, val = ht
    if op != operator.strip():
        return None
    if abs(float(val) - float(threshold)) > 1e-6:
        return None
    return list(bin_labels)


def _assign_row_bins(
    df: pd.DataFrame,
    feature: str,
    bin_order: List[str],
    bdefs: List[Tuple[str, Any]],
    value_type: Optional[str] = None,
) -> pd.Series:
    use_cate = (value_type or _column_value_type(df, feature)) == "categorical"
    if use_cate:
        return df[feature].apply(lambda v: _assign_categorical_bin(v, bin_order))
    s = df[feature].replace(SPECIAL_VALUES, np.nan)
    assigned = s.apply(lambda v: assign_bin_with_fallback(v, bdefs, bin_order))
    miss = s.isna()
    if miss.any():
        sp = [
            bl for bl, meta in bdefs
            if meta and ((len(meta) == 3 and meta[2]) or (len(meta) >= 5 and meta[4]))
        ]
        if sp:
            assigned = assigned.copy()
            assigned.loc[miss] = sp[0]
    return assigned


def _numeric_rule_target_bins(
    bdefs: List[Tuple[str, Any]], operator: str, threshold: float
) -> Optional[List[str]]:
    """按 Train 分箱边界推导 operator/threshold 应命中的箱（与分箱表一致，避免裸数值多/少计）。"""
    op = operator.strip()
    th = float(threshold)
    targets: List[str] = []
    for bl, meta in bdefs:
        if not meta or len(meta) < 5 or meta[4]:
            continue
        lo, hi, cl, cr, _ = meta
        if op == ">":
            if hi == float("inf") and (lo == th or (lo < th and not cl)):
                targets.append(bl)
            elif lo > th or (lo == th and not cl):
                targets.append(bl)
        elif op == ">=":
            if hi == float("inf") and lo >= th:
                targets.append(bl)
            elif lo >= th:
                targets.append(bl)
        elif op == "<":
            if lo == float("-inf") and hi < th:
                targets.append(bl)
            elif hi < th or (hi == th and not cr):
                targets.append(bl)
        elif op == "<=":
            if lo == float("-inf") and hi <= th:
                targets.append(bl)
            elif hi <= th:
                targets.append(bl)
    return targets or None


def _rule_hit_mask(
    df: pd.DataFrame,
    feature: str,
    operator: str,
    threshold: float = 0.0,
    values: Optional[List[Any]] = None,
    *,
    train_detail_df: Optional[pd.DataFrame] = None,
    train_norm: Optional[pd.DataFrame] = None,
    source_bins: Optional[List[str]] = None,
    bad_rate_threshold: float = 0.60,
    min_hit: int = 10,
) -> pd.Series:
    if feature not in df.columns:
        return pd.Series(False, index=df.index)
    op = operator.strip()
    if op == "in":
        if not values:
            return pd.Series(False, index=df.index)
        selected = [str(v) for v in values]
        if train_detail_df is not None:
            bin_order, bdefs = _feature_bin_defs_from_detail(train_detail_df, feature)
            if bin_order:
                assigned = _assign_row_bins(df, feature, bin_order, bdefs, "categorical")
                return assigned.astype(str).isin(selected)
        return df[feature].apply(
            lambda v: any(_categorical_value_matches_bin(v, b) for b in selected)
        )

    # 仅当阈值与自动建议规则一致时，按分箱边界统计（与分箱表一致）
    suggested_bins = source_bins
    if suggested_bins is None and train_norm is not None:
        suggested_bins = _resolve_rule_source_bins(
            train_norm, feature, op, threshold, bad_rate_threshold, min_hit
        )
    if suggested_bins and train_detail_df is not None:
        bin_order, bdefs = _feature_bin_defs_from_detail(train_detail_df, feature)
        if bin_order:
            assigned = _assign_row_bins(df, feature, bin_order, bdefs)
            target = {str(b) for b in suggested_bins}
            return assigned.astype(str).isin(target)

    # 手动改阈值：按原始数值比较（避免把 (202,inf] 整箱误当作 >1400）
    s = pd.to_numeric(
        df[feature].replace(SPECIAL_VALUES, np.nan), errors="coerce"
    )
    th = float(threshold)
    if op == ">":
        return s > th
    if op == ">=":
        return s >= th
    if op == "<":
        return s < th
    if op == "<=":
        return s <= th
    return pd.Series(False, index=df.index)


def evaluate_reject_preview(
    job_id: str, rules: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Train 全量：拒绝前/后样本量、逾期率、金额逾期率；支持多规则并集拒绝。"""
    tr = _load_train_frame(job_id)
    baseline = _portfolio_stats(tr)

    if not rules:
        return {
            "baseline": baseline,
            "after": baseline,
            "rejected": {"count": 0, "bad_rate": 0.0, "money_bad_rate": 0.0},
            "per_rule": [],
        }

    try:
        bundle = _get_job_binning_bundle(job_id)
        train_raw = bundle["train_raw"]
        train_norm = bundle["train_norm"]
    except ValueError:
        train_raw = None
        train_norm = None

    combined = pd.Series(False, index=tr.index)
    per_rule: List[Dict[str, Any]] = []
    for r in rules:
        feat = r.get("feature", "")
        op = r.get("operator", ">")
        th = float(r.get("threshold", 0))
        vals = r.get("values")
        if op == "in" and not vals:
            continue
        mask = _rule_hit_mask(
            tr,
            feat,
            op,
            th,
            values=vals,
            train_detail_df=train_raw,
            train_norm=train_norm,
            source_bins=r.get("source_bins"),
        )
        combined |= mask
        hit_stats = _portfolio_stats(tr[mask])
        bad_n = int(tr.loc[mask, "overdue_flag"].sum()) if hit_stats["count"] else 0
        per_rule.append({
            "feature": feat,
            "operator": op,
            "threshold": th,
            "values": vals,
            "hit_count": hit_stats["count"],
            "bad_count": bad_n,
            "bad_rate": hit_stats["bad_rate"],
            "money_bad_rate": hit_stats["money_bad_rate"],
            "rule_display": _format_rule_display(r),
        })

    rejected_df = tr[combined]
    remaining_df = tr[~combined]
    rejected_stats = _portfolio_stats(rejected_df)

    return {
        "baseline": baseline,
        "after": _portfolio_stats(remaining_df),
        "rejected": {
            "count": rejected_stats["count"],
            "bad_rate": rejected_stats["bad_rate"],
            "money_bad_rate": rejected_stats["money_bad_rate"],
        },
        "per_rule": per_rule,
    }
