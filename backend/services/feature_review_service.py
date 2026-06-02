"""分箱后变量筛选：概览报告、候选特征聚类、分箱明细与月度稳定性。"""
from __future__ import annotations

import importlib.util
import math
import re
import sys
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
SUB_COLS = _FF.SUB_COLS
SPECIAL_VALUES = getattr(_FF, "SPECIAL_VALUES", [-999, -9999, -999999])


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


def _parse_rule_from_bin(
    bin_label: str,
    bin_index: int,
    n_bins: int,
    rule_type: Optional[str] = None,
) -> Tuple[str, float]:
    """从分箱标签推导拒绝条件（命中则拒绝）。头箱/低值坏 → <= 上界；尾箱/高值坏 → > 下界。"""
    s = str(bin_label).strip()
    if any(k in s for k in ("缺失", "nan", "NA", "特殊")):
        return ">", 0.0

    nums = re.findall(r"-?\d+\.?\d*", s)
    lower = s.lower()

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
    if any(k in bl for k in ("缺失", "nan", "NA", "特殊")):
        return (2, 0)
    nums = re.findall(r"-?\d+\.?\d*", bl)
    if bl.startswith("(-inf"):
        return (0, float(nums[-1]) if nums else 0)
    if "inf)" in bl.lower():
        return (0, float(nums[0]) if nums else 999999)
    return (0, float(nums[0]) if nums else 0)


def _bins_for_feature(
    raw_df: pd.DataFrame,
    feat: str,
    rule_source_bin: Optional[str] = None,
    bad_rate_threshold: float = 0.0,
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
            "is_rule_bin": rule_source_bin is not None and bl == rule_source_bin,
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
) -> List[Dict[str, Any]]:
    """用 Train 分箱边界在 Test（或其它）样本上重新统计，保证箱标签与 Train 一致。"""
    if data.empty:
        return []
    col = _feature_col(train_detail_df)
    bin_col = "Bin" if "Bin" in train_detail_df.columns else "bin_label"
    vd = train_detail_df[train_detail_df[col] == feat].copy()
    if vd.empty or feat not in data.columns or "overdue_flag" not in data.columns:
        return []

    vd["_sort"] = vd[bin_col].astype(str).apply(_bin_sort_key)
    vd = vd.sort_values("_sort")
    bin_order = vd[bin_col].astype(str).tolist()
    bdefs = [(bl, parse_bin_boundary(bl)) for bl in bin_order]

    ds = data[[feat, "overdue_flag"]].copy()
    ds[feat] = ds[feat].replace(SPECIAL_VALUES, np.nan)
    ds["_bin"] = ds[feat].apply(lambda v: assign_bin(v, bdefs))
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
            "is_rule_bin": rule_source_bin is not None and bl == rule_source_bin,
            "is_high_bad": br > bad_rate_threshold and not any(
                k in bl for k in ("缺失", "nan", "NA", "特殊")
            ),
        })
    return rows


def _align_test_bins_to_train(
    train_bins: List[Dict[str, Any]],
    test_bins: List[Dict[str, Any]],
    rule_source_bin: Optional[str],
    bad_rate_threshold: float,
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
        row["is_rule_bin"] = rule_source_bin is not None and bl == rule_source_bin
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


def _analyze_feature(
    feat: str,
    train_norm: pd.DataFrame,
    train_total: int,
    train_overall: float,
    threshold: float,
    min_hit: int,
) -> Optional[Dict[str, Any]]:
    ft = train_norm[train_norm["feature"] == feat]
    normal = ft[~ft["is_special"]].sort_values("min_bin").reset_index(drop=True)
    if len(normal) < 1:
        return None

    max_br = float(normal["bad_rate"].max())
    cn_name = get_chinese_name(feat)
    bins_info = {"normal": normal, "n_normal": len(normal), "all": ft}
    u_shape = check_u_shape(bins_info, train_overall, min_samples=min_hit)
    rule = check_head_tail(bins_info, threshold, min_samples=min_hit) if u_shape is None else None

    if u_shape:
        _, u_total, reason = u_shape
        hit = int(u_total)
        rule_info = _suggest_reject_rule(feat, normal, threshold, min_hit)
        return {
            "feature": feat,
            "chinese_name": cn_name,
            "max_bad_rate": max_br,
            "effect_label": "U型人工判断",
            "reason": reason,
            "rule_type": "u_shape",
            "hit_count": hit,
            "sample_pct": hit / train_total if train_total else 0,
            "category_key": classify_category(cn_name, feat),
            **rule_info,
        }

    if rule:
        rtype, _bin_label, r_br, r_total, reason = rule
        hit = int(r_total)
        if hit < min_hit:
            return None
        eff = "好" if r_br >= 0.65 else ("一般" if r_br >= threshold else "不好")
        rule_info = _suggest_reject_rule(
            feat, normal, threshold, min_hit,
            rule_type=rtype, rule_bin_label=_bin_label,
        )
        return {
            "feature": feat,
            "chinese_name": cn_name,
            "max_bad_rate": max_br,
            "effect_label": eff,
            "reason": reason,
            "rule_type": rtype,
            "hit_count": hit,
            "sample_pct": hit / train_total if train_total else 0,
            "category_key": classify_category(cn_name, feat),
            **rule_info,
        }
    return None


def _threshold_overview(
    train_norm: pd.DataFrame,
    train_total: int,
    train_overall: float,
    min_hit: int,
) -> List[Dict[str, Any]]:
    """在不同坏率阈值下，统计有多少特征头/尾箱可拒绝。"""
    thresholds = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]
    overview = []
    features = train_norm["feature"].unique()

    for th in thresholds:
        count = 0
        total_hit = 0
        for feat in features:
            ft = train_norm[train_norm["feature"] == feat]
            normal = ft[~ft["is_special"]].sort_values("min_bin").reset_index(drop=True)
            if len(normal) < 1:
                continue
            bins_info = {"normal": normal, "n_normal": len(normal), "all": ft}
            if check_u_shape(bins_info, train_overall, min_samples=min_hit):
                count += 1
                continue
            rule = check_head_tail(bins_info, th, min_samples=min_hit)
            if rule:
                count += 1
                total_hit += int(rule[3])
        overview.append({
            "threshold": th,
            "threshold_pct": f"{th:.0%}",
            "feature_count": count,
            "avg_hit": int(total_hit / count) if count else 0,
            "hint": (
                f"约 {count} 个变量头/尾箱坏率 > {th:.0%}，"
                f"且单箱命中 ≥ {min_hit} 人"
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


def build_feature_review(
    job_id: str,
    bad_rate_threshold: float = 0.60,
    min_hit_count: int = 10,
) -> Dict[str, Any]:
    job = get_job(job_id)
    if not job or not job.output_path or not job.output_path.exists():
        raise ValueError("分箱任务不存在或结果文件未就绪")

    params = job.run_params or {}
    train_raw, test_raw = read_binning_sheets(str(job.output_path))
    train = normalize_cols(train_raw)
    test = normalize_cols(test_raw) if test_raw is not None else None

    train_overall = calc_overall_bad_rate(train) or 0.0
    test_overall = calc_overall_bad_rate(test) if test is not None else None

    first_feat = train["feature"].iloc[0] if len(train) else None
    train_total = int(train[train["feature"] == first_feat]["total"].sum()) if first_feat else 0

    results: List[Dict[str, Any]] = []
    for feat in train["feature"].unique():
        item = _analyze_feature(
            feat, train, train_total, train_overall, bad_rate_threshold, min_hit_count
        )
        if item:
            results.append(item)

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

    return {
        "train_bad_rate": train_overall,
        "test_bad_rate": test_overall,
        "portfolio_bad_rate": train_overall,
        "bad_rate_threshold": bad_rate_threshold,
        "min_hit_count": min_hit_count,
        "total_candidates": len(results),
        "threshold_overview": _threshold_overview(train, train_total, train_overall, min_hit_count),
        "report_summary": _build_report_text(train_overall, test_overall, bad_rate_threshold, min_hit_count, len(results)),
        "clusters": clusters,
        "params": params,
    }


def _build_report_text(
    train_br: float,
    test_br: Optional[float],
    threshold: float,
    min_hit: int,
    n_candidates: int,
) -> str:
    lines = [
        f"Train 大盘坏率 {train_br:.2%}，当前拒绝阈值 {threshold:.0%}。",
        f"头/尾箱命中人数下限 {min_hit} 人（可在上方调整）。",
        f"在该阈值下共筛出 {n_candidates} 个候选变量，请结合业务判断切割点。",
    ]
    if test_br is not None:
        lines.insert(1, f"Test 大盘坏率 {test_br:.2%}。")
    lines.append("建议：先看「阈值概览」表格，再点开特征查看 Train/Test 分箱与月度稳定性。")
    return " ".join(lines)


def get_feature_detail(
    job_id: str,
    feature: str,
    bad_rate_threshold: float = 0.60,
) -> Dict[str, Any]:
    job = get_job(job_id)
    if not job or not job.output_path or not job.output_path.exists():
        raise ValueError("分箱任务不存在或分箱结果文件已丢失，请重新执行分箱")

    params = job.run_params or {}
    train_raw, test_raw = read_binning_sheets(str(job.output_path))
    train_norm = normalize_cols(train_raw)

    ft = train_norm[train_norm["feature"] == feature]
    normal = ft[~ft["is_special"]].sort_values("min_bin").reset_index(drop=True)
    bins_info = {"normal": normal, "n_normal": len(normal), "all": ft}
    ht = check_head_tail(bins_info, bad_rate_threshold, min_samples=10)
    if ht:
        rtype, bin_label, _, _, _ = ht
        rule_info = _suggest_reject_rule(
            feature, normal, bad_rate_threshold, 10,
            rule_type=rtype, rule_bin_label=bin_label,
        )
    else:
        rule_info = _suggest_reject_rule(feature, normal, bad_rate_threshold, 10)
    source_bin = rule_info.get("rule_source_bin")
    train_bins = _bins_for_feature(
        train_raw, feature, source_bin, bad_rate_threshold
    )

    file_path = params.get("file_path")
    test_bins: List[Dict[str, Any]] = []
    tr: Optional[pd.DataFrame] = None
    te: Optional[pd.DataFrame] = None

    if file_path and Path(file_path).exists():
        try:
            raw_df = read_dataframe(Path(file_path))
            label = params.get("label", "target3")
            tr, te = _prepare_stability_frames(
                raw_df,
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
                    te, train_raw, feature, source_bin, bad_rate_threshold
                )
        except Exception:
            tr, te = None, None

    if not test_bins and test_raw is not None:
        test_bins = _align_test_bins_to_train(
            train_bins,
            _bins_for_feature(test_raw, feature, source_bin, bad_rate_threshold),
            source_bin,
            bad_rate_threshold,
        )

    detail: Dict[str, Any] = {
        "feature": feature,
        "chinese_name": get_chinese_name(feature),
        "rule": rule_info,
        "high_bad_bins": [
            {
                "bin": str(r["bin_label"]),
                "obs": int(r["total"]),
                "bad_rate": float(r["bad_rate"]),
            }
            for _, r in normal.iterrows()
            if float(r["bad_rate"]) > bad_rate_threshold
        ],
        "bins": {
            "train": train_bins,
            "test": test_bins,
        },
        "bins_aligned_to_train": True,
    }

    if not file_path or not Path(file_path).exists():
        detail["stability"] = {
            "train": {"months": [], "bins": [], "columns": SUB_COLS},
            "test": {"months": [], "bins": [], "columns": SUB_COLS},
        }
        detail["stability_note"] = "缺少原始数据文件，月度稳定性不可用（分箱明细仍完整展示）"
        return _json_safe(detail)

    try:
        if tr is None or te is None:
            raw_df = read_dataframe(Path(file_path))
            label = params.get("label", "target3")
            tr, te = _prepare_stability_frames(
                raw_df,
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
                    te, train_raw, feature, source_bin, bad_rate_threshold
                )
                detail["bins"]["test"] = test_bins

        train_months = [
            str(m) for m in sorted(tr["apply_month"].dropna().unique().tolist())
        ] if "apply_month" in tr.columns else []
        test_months = [
            str(m) for m in sorted(te["apply_month"].dropna().unique().tolist())
        ] if len(te) and "apply_month" in te.columns else []

        stab_train = _stability_table(tr, train_raw, feature, train_months)
        stab_test = _stability_table(te, train_raw, feature, test_months)
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
        detail["stability"] = {
            "train": {"months": [], "bins": [], "columns": SUB_COLS},
            "test": {"months": [], "bins": [], "columns": SUB_COLS},
        }
        detail["stability_note"] = f"月度稳定性计算失败（分箱明细已完整展示）: {exc}"

    return _json_safe(detail)


def _load_train_frame(job_id: str) -> pd.DataFrame:
    job = get_job(job_id)
    if not job:
        raise ValueError("分箱任务不存在")
    params = job.run_params or {}
    file_path = params.get("file_path")
    if not file_path or not Path(file_path).exists():
        raise ValueError("原始数据文件不可用，无法计算拒绝影响")

    raw_df = read_dataframe(Path(file_path))
    label = params.get("label", "target3")
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


def _rule_hit_mask(
    df: pd.DataFrame, feature: str, operator: str, threshold: float
) -> pd.Series:
    if feature not in df.columns:
        return pd.Series(False, index=df.index)
    s = pd.to_numeric(
        df[feature].replace(SPECIAL_VALUES, np.nan), errors="coerce"
    )
    th = float(threshold)
    op = operator.strip()
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

    combined = pd.Series(False, index=tr.index)
    per_rule: List[Dict[str, Any]] = []
    for r in rules:
        feat = r.get("feature", "")
        op = r.get("operator", ">")
        th = float(r.get("threshold", 0))
        mask = _rule_hit_mask(tr, feat, op, th)
        combined |= mask
        hit_stats = _portfolio_stats(tr[mask])
        bad_n = int(tr.loc[mask, "overdue_flag"].sum()) if hit_stats["count"] else 0
        per_rule.append({
            "feature": feat,
            "operator": op,
            "threshold": th,
            "hit_count": hit_stats["count"],
            "bad_count": bad_n,
            "bad_rate": hit_stats["bad_rate"],
            "money_bad_rate": hit_stats["money_bad_rate"],
            "rule_display": f"{feat}{op}{_format_threshold(th)}",
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
