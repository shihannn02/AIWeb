"""IV 支线：两变量等频交叉格子（初筛 C(n,2) + 精筛可调边界）。"""
from __future__ import annotations

import itertools
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from services.binning_runner import get_job
from services.feature_review_service import (
    _bin_sort_key,
    _compute_iv_ks_from_bin_rows,
    _get_job_binning_bundle,
    _load_train_frame,
    assign_bin_with_fallback,
    get_chinese_name,
    parse_bin_boundary,
    SPECIAL_BIN_SENTINELS,
    SPECIAL_VALUES,
    _value_matches_special_bin,
)

SUB_COLS = ["总人数", "逾期数", "坏率", "金额逾期率"]


def _pair_key(f1: str, f2: str) -> str:
    a, b = sorted([f1, f2])
    return f"{a}|{b}"


def _label_col(job_params: Dict[str, Any], df: pd.DataFrame) -> str:
    if "overdue_flag" in df.columns:
        return "overdue_flag"
    label = job_params.get("label") or "target"
    if label in df.columns:
        return label
    for c in ("target", "target3", "y"):
        if c in df.columns:
            return c
    raise ValueError("找不到标签列")


def _edges_and_labels_from_sheet(
    train_norm: pd.DataFrame, feat: str,
) -> Tuple[List[float], List[str]]:
    """仅用于精筛自定义边界时的默认等频切分点（不含缺失/特殊箱）。"""
    ft = train_norm[train_norm["feature"] == feat].copy()
    if ft.empty:
        return [-np.inf, np.inf], ["all"]
    normal = ft[~ft["is_special"]].sort_values("min_bin").reset_index(drop=True)
    if normal.empty:
        return [-np.inf, np.inf], ["all"]
    labels = [str(r["bin_label"]) for _, r in normal.iterrows()]
    edges: List[float] = [-np.inf]
    for _, row in normal.iterrows():
        try:
            edges.append(float(row["max_bin"]))
        except (TypeError, ValueError):
            edges.append(edges[-1])
    edges[-1] = np.inf
    if len(edges) < 2:
        edges = [-np.inf, np.inf]
    return edges, labels


def _split_norm_bins(ft: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """数值箱在前、缺失/特殊箱在后（与精筛自定义边界一致）。"""
    special_mask = ft["is_special"].fillna(False).astype(bool)
    if not special_mask.any():
        bl = ft["bin_label"].astype(str)
        special_mask = bl.str.contains(
            r"缺失|missing|MISSING|nan|NA|special\(",
            case=False,
            na=False,
            regex=True,
        )
    special = ft[special_mask].copy()
    normal = ft[~special_mask].copy()
    return normal, special


def _feature_bin_schema_from_norm(
    train_norm: pd.DataFrame,
    feat: str,
) -> Tuple[List[str], List[Tuple[str, Any]], List[Dict[str, Any]]]:
    """从分箱明细取完整箱序：数值箱按 min_bin 升序，special/缺失固定排在末尾。"""
    ft = train_norm[train_norm["feature"] == feat].copy()
    if ft.empty:
        return ["all"], [("all", None)], [{"index": 0, "label": "all", "is_special": False}]
    normal, special = _split_norm_bins(ft)
    if normal.empty and special.empty:
        return ["all"], [("all", None)], [{"index": 0, "label": "all", "is_special": False}]
    normal = normal.sort_values("min_bin").reset_index(drop=True)
    if not special.empty:
        special = special.copy()
        special["_sp_sort"] = special["bin_label"].astype(str).apply(_bin_sort_key)
        special = special.sort_values("_sp_sort").reset_index(drop=True)

    bin_order: List[str] = []
    bdefs: List[Tuple[str, Any]] = []
    defs: List[Dict[str, Any]] = []
    for _, row in normal.iterrows():
        bl = str(row["bin_label"])
        bin_order.append(bl)
        bdefs.append((bl, parse_bin_boundary(bl)))
        defs.append({
            "index": len(defs),
            "label": bl,
            "is_special": False,
            "lo": None,
            "hi": None,
        })
    for _, row in special.iterrows():
        bl = str(row["bin_label"])
        if bl in bin_order:
            continue
        bin_order.append(bl)
        bdefs.append((bl, parse_bin_boundary(bl)))
        defs.append({
            "index": len(defs),
            "label": bl,
            "is_special": True,
            "lo": None,
            "hi": None,
        })
    return bin_order, bdefs, defs


def _format_interval_label(lo: float, hi: float) -> str:
    if math.isinf(hi):
        hi_str = "inf"
    else:
        hi_str = f"{float(hi):g}"
    if math.isinf(lo) and lo < 0:
        if hi_str == "inf":
            return "(-inf, inf)"
        return f"(-inf, {hi_str}]"
    lo_str = f"{float(lo):g}"
    if hi_str == "inf":
        return f"({lo_str}, inf)"
    return f"({lo_str}, {hi_str}]"


def _schema_from_numeric_edges(
    edges: List[float],
) -> Tuple[List[str], List[Tuple[str, Any]], List[Dict[str, Any]]]:
    """由用户调整的数值边界生成区间标签与分箱元数据。"""
    labels: List[str] = []
    defs: List[Dict[str, Any]] = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        label = _format_interval_label(lo, hi)
        labels.append(label)
        defs.append({
            "index": i,
            "label": label,
            "is_special": False,
            "lo": None if (isinstance(lo, float) and math.isinf(lo) and lo < 0) else float(lo),
            "hi": None if (isinstance(hi, float) and math.isinf(hi)) else float(hi),
        })
    bdefs = [(lbl, parse_bin_boundary(lbl)) for lbl in labels]
    return labels, bdefs, defs


def _is_special_raw_value(raw: Any) -> bool:
    if pd.isna(raw):
        return True
    if raw in SPECIAL_VALUES:
        return True
    try:
        fv = float(raw)
        if not math.isfinite(fv):
            return True
        for sentinel in SPECIAL_BIN_SENTINELS:
            if abs(fv - float(sentinel)) < 1e-9:
                return True
    except (TypeError, ValueError):
        return True
    return False


def _meta_by_label(bdefs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    return {bl: meta for bl, meta in bdefs}


def _assign_special_only(
    raw: Any,
    bdefs: List[Tuple[str, Any]],
    bin_order: List[str],
    idx_map: Dict[str, int],
) -> Optional[int]:
    """NaN / -999 等只落 special 箱，禁止匹配 (-inf, x] 数值箱。"""
    val = np.nan if pd.isna(raw) else raw
    meta_map = _meta_by_label(bdefs)
    for bl in bin_order:
        meta = meta_map.get(bl)
        if meta is None or len(meta) < 6 or not meta[4]:
            continue
        if _value_matches_special_bin(val, meta[5]):
            ix = idx_map.get(str(bl))
            if ix is not None:
                return ix
    for bl in reversed(bin_order):
        meta = meta_map.get(bl)
        if meta is not None and len(meta) >= 5 and meta[4]:
            ix = idx_map.get(str(bl))
            if ix is not None:
                return ix
    return None


def _assign_bins_custom_edges(
    values: pd.Series,
    edges: List[float],
    bin_order: List[str],
    bdefs: List[Tuple[str, Any]],
) -> pd.Series:
    """正常值按 edges 做 pd.cut；NaN / -999 等只落缺失/特殊箱，绝不进入 (-inf, x]。"""
    idx_map = {bl: i for i, bl in enumerate(bin_order)}
    out = pd.Series(index=values.index, dtype="Int64")

    spec_idx = values.index[values.map(_is_special_raw_value)]
    for ix in spec_idx:
        code = _assign_special_only(values.loc[ix], bdefs, bin_order, idx_map)
        if code is not None:
            out.loc[ix] = code

    normal_idx = values.index.difference(spec_idx)
    if len(normal_idx):
        raw_normal = values.loc[normal_idx]
        num = pd.to_numeric(raw_normal, errors="coerce")
        # 二次拦截：coerce 后仍可能是 -999 或 NaN
        leak_spec = normal_idx[num.isna() | num.map(_is_special_raw_value)]
        for ix in leak_spec:
            code = _assign_special_only(values.loc[ix], bdefs, bin_order, idx_map)
            if code is not None:
                out.loc[ix] = code
        cut_idx = normal_idx.difference(leak_spec)
        if len(cut_idx):
            cut_num = num.loc[cut_idx]
            try:
                cats = pd.cut(cut_num, bins=edges, include_lowest=True, duplicates="drop")
                codes = pd.Series(cats.cat.codes, index=cut_idx).where(cats.notna(), pd.NA)
                for ix, code in codes.items():
                    if pd.isna(code):
                        sp = _assign_special_only(values.loc[ix], bdefs, bin_order, idx_map)
                        if sp is not None:
                            out.loc[ix] = sp
                    else:
                        out.loc[ix] = int(code)
            except Exception:
                for ix in cut_idx:
                    v = values.loc[ix]
                    if _is_special_raw_value(v):
                        code = _assign_special_only(v, bdefs, bin_order, idx_map)
                    else:
                        bl = assign_bin_with_fallback(float(v), bdefs, bin_order)
                        meta = _meta_by_label(bdefs).get(str(bl) if bl else "")
                        if bl and meta and len(meta) >= 5 and meta[4]:
                            code = _assign_special_only(v, bdefs, bin_order, idx_map)
                        else:
                            code = idx_map.get(str(bl)) if bl else None
                    if code is not None:
                        out.loc[ix] = code
    return out


def _feature_bin_schema_from_custom_edges(
    train_norm: pd.DataFrame,
    feat: str,
    edges: List[float],
    normal_labels: List[str],
) -> Tuple[List[str], List[Tuple[str, Any]], List[Dict[str, Any]]]:
    """精筛调边界：按新数值边界生成正常箱 + 保留原明细缺失/特殊箱。"""
    ft = train_norm[train_norm["feature"] == feat].copy()
    special = ft[ft["is_special"]].copy()
    if special.empty:
        special = ft[ft["bin_label"].astype(str).str.contains("缺失|missing|MISSING|nan|NA|-999|-1111", case=False, na=False)]

    _, normal_bdefs, normal_defs = _schema_from_numeric_edges(edges)
    bin_order = [d["label"] for d in normal_defs]
    bdefs = list(normal_bdefs)

    if special.empty:
        special = ft[ft["bin_label"].astype(str).str.contains(
            "缺失|missing|MISSING|nan|NA|-999|-1111|special\\(",
            case=False,
            na=False,
            regex=True,
        )].copy()
    if not special.empty:
        special = special.copy()
        special["_sp_sort"] = special["bin_label"].astype(str).apply(_bin_sort_key)
        special = special.sort_values("_sp_sort").reset_index(drop=True)

    for _, row in special.iterrows():
        bl = str(row["bin_label"])
        if bl in bin_order:
            continue
        bin_order.append(bl)
        bdefs.append((bl, parse_bin_boundary(bl)))
        normal_defs.append({
            "index": len(normal_defs),
            "label": bl,
            "is_special": True,
            "lo": None,
            "hi": None,
        })

    if not any(d.get("is_special") for d in normal_defs):
        missing_label = "缺失/特殊"
        if missing_label not in bin_order:
            bin_order.append(missing_label)
            bdefs.append((missing_label, None))
            normal_defs.append({
                "index": len(normal_defs),
                "label": missing_label,
                "is_special": True,
                "lo": None,
                "hi": None,
            })
    return bin_order, bdefs, normal_defs


def _assign_bins_from_schema(
    values: pd.Series,
    bin_order: List[str],
    bdefs: List[Tuple[str, Any]],
) -> pd.Series:
    """按分箱表分配箱标签索引；NaN / -999 等走 assign_bin_with_fallback 落入缺失/特殊箱。"""
    idx_map = {bl: i for i, bl in enumerate(bin_order)}
    fallback = bin_order[-1] if bin_order else None

    def _to_idx(raw: Any) -> Optional[int]:
        v = np.nan if pd.isna(raw) else raw
        bl = assign_bin_with_fallback(v, bdefs, bin_order)
        if bl is None:
            bl = fallback
        if bl is None:
            return None
        return idx_map.get(str(bl))

    return values.apply(_to_idx).astype("Int64")


def _finite_inner_cuts(values: Optional[List[float]]) -> List[float]:
    if not values:
        return []
    out: List[float] = []
    for v in values:
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fv):
            out.append(fv)
    return sorted(out)


def _full_edges_from_inner_cuts(
    inner: Optional[List[float]],
    default_edges: List[float],
) -> List[float]:
    """API 仅传内部切分点，在此补全 ±inf 边界。"""
    cuts = _finite_inner_cuts(inner)
    if not cuts:
        return default_edges
    return [-np.inf, *cuts, np.inf]


def _bin_defs(edges: List[float], labels: List[str]) -> List[Dict[str, Any]]:
    defs: List[Dict[str, Any]] = []
    n = min(len(labels), max(len(edges) - 1, 1))
    for i in range(n):
        lo = edges[i] if i < len(edges) else -np.inf
        hi = edges[i + 1] if i + 1 < len(edges) else np.inf
        defs.append({
            "index": i,
            "label": labels[i] if i < len(labels) else f"bin_{i}",
            "lo": None if math.isinf(lo) and lo < 0 else float(lo),
            "hi": None if math.isinf(hi) else float(hi),
        })
    return defs


def _pair_grid_monthly_stability(
    sub: pd.DataFrame,
    label: str,
    row_defs: List[Dict[str, Any]],
    col_defs: List[Dict[str, Any]],
    max_cell: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    empty: Dict[str, Any] = {
        "months": [],
        "bins": [],
        "summary": None,
        "columns": SUB_COLS,
        "note": "",
    }
    if sub.empty or "apply_month" not in sub.columns:
        empty["note"] = "未能解析申请月份，请回到第 1 步确认时间列（如 apply_date）。"
        return empty

    months = sorted({
        str(m).strip()
        for m in sub["apply_month"].dropna().unique()
        if str(m).strip().lower() not in ("nan", "none", "nat", "")
    })
    if not months:
        empty["note"] = "无有效月份数据"
        return empty

    work = sub
    if "money" not in work.columns:
        work = work.copy()
        work["money"] = 1000.0

    row_labels = {d["index"]: d["label"] for d in row_defs}
    col_labels = {d["index"]: d["label"] for d in col_defs}
    max_i = max_cell.get("i") if max_cell else None
    max_j = max_cell.get("j") if max_cell else None

    bins_out: List[Dict[str, Any]] = []
    for rd in row_defs:
        for cd in col_defs:
            i, j = rd["index"], cd["index"]
            part = work[(work["_b1"] == i) & (work["_b2"] == j)]
            bl = f"{row_labels.get(i, i)} × {col_labels.get(j, j)}"
            row: Dict[str, Any] = {
                "bin": bl,
                "i": int(i),
                "j": int(j),
                "is_max_cell": max_i is not None and int(i) == int(max_i) and int(j) == int(max_j),
                "months": {},
                "total": {},
            }
            rt = {"obs": 0, "bad": 0, "money": 0.0, "bad_money": 0.0}
            for m in months:
                sm = part[part["apply_month"].astype(str) == m]
                obs = int(len(sm))
                bad = int(sm[label].sum()) if obs else 0
                money = float(sm["money"].sum()) if obs else 0.0
                bm = float(sm.loc[sm[label] == 1, "money"].sum()) if bad else 0.0
                row["months"][m] = {
                    "obs": obs,
                    "bad": bad,
                    "bad_rate": bad / obs if obs else 0.0,
                    "money_bad_rate": bm / money if money else 0.0,
                }
                rt["obs"] += obs
                rt["bad"] += bad
                rt["money"] += money
                rt["bad_money"] += bm
            row["total"] = {
                "obs": rt["obs"],
                "bad": rt["bad"],
                "bad_rate": rt["bad"] / rt["obs"] if rt["obs"] else 0.0,
                "money_bad_rate": rt["bad_money"] / rt["money"] if rt["money"] else 0.0,
            }
            bins_out.append(row)

    bins_out.sort(key=lambda x: (-x["total"]["bad_rate"], -x["total"]["obs"]))

    summary: Dict[str, Any] = {"bin": "合计", "months": {}, "total": {}}
    at = {"obs": 0, "bad": 0, "money": 0.0, "bad_money": 0.0}
    for m in months:
        sm = work[work["apply_month"].astype(str) == m]
        obs = int(len(sm))
        bad = int(sm[label].sum()) if obs else 0
        money = float(sm["money"].sum()) if obs else 0.0
        bm = float(sm.loc[sm[label] == 1, "money"].sum()) if bad else 0.0
        summary["months"][m] = {
            "obs": obs,
            "bad": bad,
            "bad_rate": bad / obs if obs else 0.0,
            "money_bad_rate": bm / money if money else 0.0,
        }
        at["obs"] += obs
        at["bad"] += bad
        at["money"] += money
        at["bad_money"] += bm
    summary["total"] = {
        "obs": at["obs"],
        "bad": at["bad"],
        "bad_rate": at["bad"] / at["obs"] if at["obs"] else 0.0,
        "money_bad_rate": at["bad_money"] / at["money"] if at["money"] else 0.0,
    }

    return {
        "months": months,
        "bins": bins_out,
        "summary": summary,
        "columns": SUB_COLS,
        "note": "",
    }


def compute_pair_grid(
    job_id: str,
    f1: str,
    f2: str,
    bin_edges_f1: Optional[List[float]] = None,
    bin_edges_f2: Optional[List[float]] = None,
) -> Dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise ValueError("分箱任务不存在")
    params = job.run_params or {}
    bin_num = int(params.get("bin_num") or 10)

    train = _load_train_frame(job_id)
    bundle = _get_job_binning_bundle(job_id)
    train_norm = bundle["train_norm"]
    label = _label_col(params, train)

    for feat in (f1, f2):
        if feat not in train.columns:
            raise ValueError(f"变量 {feat} 不在训练数据中")

    default_e1, labels1 = _edges_and_labels_from_sheet(train_norm, f1)
    default_e2, labels2 = _edges_and_labels_from_sheet(train_norm, f2)
    has_custom1 = bool(_finite_inner_cuts(bin_edges_f1))
    has_custom2 = bool(_finite_inner_cuts(bin_edges_f2))
    edges1 = _full_edges_from_inner_cuts(bin_edges_f1, default_e1) if has_custom1 else default_e1
    edges2 = _full_edges_from_inner_cuts(bin_edges_f2, default_e2) if has_custom2 else default_e2

    if has_custom1:
        order1, bdefs1, defs1 = _feature_bin_schema_from_custom_edges(train_norm, f1, edges1, labels1)
    else:
        order1, bdefs1, defs1 = _feature_bin_schema_from_norm(train_norm, f1)
    if has_custom2:
        order2, bdefs2, defs2 = _feature_bin_schema_from_custom_edges(train_norm, f2, edges2, labels2)
    else:
        order2, bdefs2, defs2 = _feature_bin_schema_from_norm(train_norm, f2)

    sub = train[[f1, f2, label]].copy()
    if "apply_month" in train.columns:
        sub["apply_month"] = train["apply_month"]
    if "money" in train.columns:
        sub["money"] = train["money"]
    sub[label] = pd.to_numeric(sub[label], errors="coerce").fillna(0).astype(int)

    sub["_b1"] = (
        _assign_bins_custom_edges(train[f1].loc[sub.index], edges1, order1, bdefs1)
        if has_custom1
        else _assign_bins_from_schema(train[f1].loc[sub.index], order1, bdefs1)
    )
    sub["_b2"] = (
        _assign_bins_custom_edges(train[f2].loc[sub.index], edges2, order2, bdefs2)
        if has_custom2
        else _assign_bins_from_schema(train[f2].loc[sub.index], order2, bdefs2)
    )
    sub = sub[sub["_b1"].notna() & sub["_b2"].notna()]
    if sub.empty:
        raise ValueError(f"组合 {f1} × {f2} 无有效样本")

    overall_bad = float(sub[label].mean()) if len(sub) else 0.0
    cells: List[Dict[str, Any]] = []
    max_bad = 0.0
    max_cell = None
    for (i, j), grp in sub.groupby(["_b1", "_b2"], dropna=True):
        obs = int(len(grp))
        bad = int(grp[label].sum())
        br = bad / obs if obs else 0.0
        cell = {
            "i": int(i),
            "j": int(j),
            "obs": obs,
            "bad": bad,
            "bad_rate": round(br, 6),
            "lift": round(br / overall_bad, 4) if overall_bad > 0 else 0.0,
        }
        cells.append(cell)
        if br >= max_bad:
            max_bad = br
            max_cell = cell

    row_stats = []
    for d in defs1:
        idx = d["index"]
        part = sub[sub["_b1"] == idx]
        obs = len(part)
        bad = int(part[label].sum()) if obs else 0
        row_stats.append({**d, "obs": obs, "bad": bad, "bad_rate": bad / obs if obs else 0.0})
    col_stats = []
    for d in defs2:
        idx = d["index"]
        part = sub[sub["_b2"] == idx]
        obs = len(part)
        bad = int(part[label].sum()) if obs else 0
        col_stats.append({**d, "obs": obs, "bad": bad, "bad_rate": bad / obs if obs else 0.0})

    monthly = _pair_grid_monthly_stability(sub, label, defs1, defs2, max_cell)
    grid_iv, grid_ks = _compute_iv_ks_from_bin_rows(cells)

    return {
        "pair_key": _pair_key(f1, f2),
        "feature_a": f1,
        "feature_b": f2,
        "chinese_a": get_chinese_name(f1),
        "chinese_b": get_chinese_name(f2),
        "rows": row_stats,
        "cols": col_stats,
        "cells": cells,
        "overall_bad_rate": overall_bad,
        "max_bad_rate": max_bad,
        "max_cell": max_cell,
        "grid_iv_total": grid_iv,
        "grid_ks_total": grid_ks,
        "sample_count": int(len(sub)),
        "bin_num": bin_num,
        "edges_a": [None if (isinstance(v, float) and math.isinf(v)) else v for v in edges1],
        "edges_b": [None if (isinstance(v, float) and math.isinf(v)) else v for v in edges2],
        "monthly_stability": monthly,
    }


def build_pair_grids_overview(
    job_id: str,
    features: List[str],
) -> Dict[str, Any]:
    feats = list(dict.fromkeys([str(f).strip() for f in features if str(f).strip()]))
    if len(feats) < 2:
        raise ValueError("至少选择 2 个变量才能生成两两组合格子")
    if len(feats) > 6:
        raise ValueError("IV 等频格子最多支持 6 个变量")

    pairs = list(itertools.combinations(feats, 2))
    combos: List[Dict[str, Any]] = []
    errors: List[str] = []
    for f1, f2 in pairs:
        try:
            grid = compute_pair_grid(job_id, f1, f2)
            combos.append({
                "pair_key": grid["pair_key"],
                "feature_a": f1,
                "feature_b": f2,
                "chinese_a": grid["chinese_a"],
                "chinese_b": grid["chinese_b"],
                "sample_count": grid["sample_count"],
                "grid_iv_total": grid.get("grid_iv_total"),
                "grid_ks_total": grid.get("grid_ks_total"),
                "max_bad_rate": grid["max_bad_rate"],
                "overall_bad_rate": grid["overall_bad_rate"],
                "row_bins": len(grid["rows"]),
                "col_bins": len(grid["cols"]),
                "max_cell": grid["max_cell"],
                "grid": grid,
            })
        except Exception as exc:
            errors.append(f"{f1}×{f2}: {exc}")

    combos.sort(key=lambda x: (-(x.get("grid_iv_total") or 0), -(x.get("max_bad_rate") or 0)))
    return {
        "features": feats,
        "feature_count": len(feats),
        "pair_count": len(pairs),
        "combos": combos,
        "errors": errors,
    }


def build_pair_grid_detail(
    job_id: str,
    feature_a: str,
    feature_b: str,
    bin_edges_a: Optional[List[float]] = None,
    bin_edges_b: Optional[List[float]] = None,
) -> Dict[str, Any]:
    return compute_pair_grid(
        job_id, feature_a, feature_b,
        bin_edges_f1=bin_edges_a,
        bin_edges_f2=bin_edges_b,
    )
