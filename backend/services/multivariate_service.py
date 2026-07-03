"""多变量组合规则挖掘与评估（基于决策树，逻辑来自 多变量.ipynb）。"""
from __future__ import annotations

import itertools
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn import tree
from sklearn.tree import DecisionTreeClassifier, _tree

from services.binning_runner import get_job
from services.data_service import read_dataframe
from services.feature_review_service import (
    SPECIAL_BIN_SENTINELS,
    _format_rule_display,
    _format_threshold,
    _load_train_frame,
    _portfolio_stats,
    _prepare_stability_frames,
    _rule_hit_mask,
    get_chinese_name,
)

# 挖掘结果缓存：job_id -> {cache_key: result}
_MINING_CACHE: Dict[str, Dict[str, Any]] = {}
_MAX_CACHE_PER_JOB = 3

DEFAULT_MINING_PARAMS = {
    "comb": 3,
    "tree_depth": 3,
    "max_leaf_nodes": 4,
    "min_samples_leaf_frac": 0.005,
    "lift_threshold": 1.0,
    "sample_multiple": 3,
    "combo_random": 0.1,
    "na_threshold": 0.95,
    "correlation_threshold": 0.75,
    "need_rm_narow": False,
    "min_bad_samples": 30,
    "max_features": 50,
    "skip_feature_filter": True,
}

_MINING_LOGIC_VERSION = "notebook-classifier-v1"


def _json_safe(value: Any) -> Any:
    """递归清理 inf/nan，避免 FastAPI JSON 序列化 500。"""
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.floating, float)):
        v = float(value)
        if not math.isfinite(v):
            return None
        return v
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    return value


def _trim_mining_cache(job_id: str) -> None:
    bucket = _MINING_CACHE.get(job_id)
    if not bucket:
        return
    if len(bucket) > _MAX_CACHE_PER_JOB:
        for k in list(bucket.keys())[:-_MAX_CACHE_PER_JOB]:
            bucket.pop(k, None)


def _load_full_frame(job_id: str) -> pd.DataFrame:
    """Train+Test 全量（与 notebook full_data 口径一致，用于组合规则坏率评估）。"""
    tr = _load_train_frame(job_id)
    job = get_job(job_id)
    if not job:
        return tr
    params = job.run_params or {}
    file_path = params.get("file_path")
    if not file_path or not Path(file_path).exists():
        return tr
    raw_df = read_dataframe(Path(file_path))
    label = params.get("label", "target3")
    time_col = params.get("time_col") or "apply_date"
    _, te = _prepare_stability_frames(
        raw_df,
        label,
        time_col,
        params.get("split_mode", "ai"),
        float(params.get("oot_ratio", 0.2)),
        params.get("cutoff_date"),
        Path(params["train_file_path"]) if params.get("train_file_path") else None,
        Path(params["test_file_path"]) if params.get("test_file_path") else None,
    )
    if te is None or len(te) == 0:
        return tr
    if "overdue_flag" not in te.columns and label in te.columns:
        te = te.copy()
        te["overdue_flag"] = pd.to_numeric(te[label], errors="coerce").fillna(0).astype(int)
    if "money" not in te.columns:
        te = te.copy()
        te["money"] = 1000.0
    full = pd.concat([tr, te], axis=0)
    return full[~full.index.duplicated(keep="first")]


def _mining_cache_key(features: List[str], params: Dict[str, Any]) -> str:
    fs = ",".join(sorted(features))
    p = "|".join(f"{k}={params.get(k, DEFAULT_MINING_PARAMS.get(k))}" for k in sorted(DEFAULT_MINING_PARAMS))
    return f"{_MINING_LOGIC_VERSION}|{fs}|{p}"


def _notebook_clean_features(df: pd.DataFrame, feature_cols: List[str]) -> pd.DataFrame:
    """哨兵值转为 NaN 并保留缺失（缺失作为独立分箱参与决策树，不删行、不填充）。"""
    work = df[feature_cols].copy()
    for col in list(work.columns):
        if pd.api.types.is_numeric_dtype(work[col]):
            work[col] = work[col].replace(list(SPECIAL_BIN_SENTINELS) + [-999, -9999, -999999], np.nan)
        else:
            work[col] = work[col].astype(str).str.strip()
            bad = work[col].isin(["-999", "-9999", "-999999", "nan", "None", "null", "NULL", ""])
            work.loc[bad, col] = np.nan
            converted = pd.to_numeric(work[col], errors="coerce")
            if converted.notna().sum() > 0:
                work[col] = converted
    return work


def _parse_condition(part: str) -> Optional[Dict[str, Any]]:
    part = part.strip()
    for op in ("<=", ">=", "<", ">"):
        if op in part:
            idx = part.rfind(op)
            feat = part[:idx].strip()
            try:
                th = float(part[idx + len(op):].strip())
            except ValueError:
                return None
            return {"feature": feat, "operator": op, "threshold": th}
    return None


def parse_compound_rule_string(rule_str: str) -> Tuple[List[Dict[str, Any]], Optional[float]]:
    """解析 'a<=1.0 and b>2.0 Badrate:0.75' 或纯条件串。"""
    bad_rate = None
    body = rule_str.strip()
    if " Badrate:" in body:
        body, br = body.rsplit(" Badrate:", 1)
        try:
            bad_rate = float(br.strip())
        except ValueError:
            bad_rate = None
    conditions: List[Dict[str, Any]] = []
    for part in re.split(r"\s+and\s+|\s*&\s*", body, flags=re.IGNORECASE):
        c = _parse_condition(part)
        if c:
            conditions.append(c)
    return conditions, bad_rate


def format_compound_rule_display(conditions: List[Dict[str, Any]]) -> str:
    """展示用：同一变量上的 > 与 <= 合并为区间，避免 K=2 时看起来像 3 个变量。"""
    grouped: Dict[str, Dict[str, Optional[float]]] = {}
    bin_parts: List[str] = []
    for c in conditions:
        feat = c.get("feature", "")
        op = c.get("operator", ">")
        if op == "in":
            bins = c.get("values") or c.get("source_bins") or []
            if bins:
                bin_parts.append(f"{feat} ∈ {{{', '.join(str(b) for b in bins)}}}")
            continue
        th = float(c.get("threshold", 0))
        g = grouped.setdefault(feat, {"lo": None, "lo_op": ">", "hi": None, "hi_op": "<="})
        if op in (">", ">="):
            if g["lo"] is None or th >= g["lo"]:
                g["lo"] = th
                g["lo_op"] = ">" if op == ">" else ">="
        elif op in ("<", "<="):
            if g["hi"] is None or th <= g["hi"]:
                g["hi"] = th
                g["hi_op"] = "<" if op == "<" else "<="

    parts: List[str] = []
    for feat, bounds in grouped.items():
        lo, hi = bounds["lo"], bounds["hi"]
        if lo is not None and hi is not None:
            parts.append(
                f"{_format_threshold(lo)} <= {feat} < {_format_threshold(hi)}"
            )
        elif lo is not None:
            parts.append(f"{_format_threshold(lo)} <= {feat}")
        elif hi is not None:
            parts.append(f"{feat} < {_format_threshold(hi)}")
    return " and ".join([*parts, *bin_parts])


def _simplify_conditions(conditions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一变量多次切分合并为更紧的阈值（> 取最大，<= 取最小）。"""
    grouped: Dict[str, Dict[str, List[float]]] = {}
    for c in conditions:
        feat = c["feature"]
        op = c.get("operator", ">")
        th = float(c.get("threshold", 0))
        grouped.setdefault(feat, {"gt": [], "le": []})
        if op in (">", ">="):
            grouped[feat]["gt"].append(th)
        elif op in ("<", "<="):
            grouped[feat]["le"].append(th)
    out: List[Dict[str, Any]] = []
    for feat, bounds in grouped.items():
        if bounds["gt"]:
            out.append({"feature": feat, "operator": ">", "threshold": max(bounds["gt"])})
        if bounds["le"]:
            out.append({"feature": feat, "operator": "<=", "threshold": min(bounds["le"])})
    return out


def compound_rule_hit_mask(
    df: pd.DataFrame,
    conditions: List[Dict[str, Any]],
    *,
    train_detail_df: Optional[pd.DataFrame] = None,
    train_norm: Optional[pd.DataFrame] = None,
) -> pd.Series:
    if not conditions:
        return pd.Series(False, index=df.index)
    mask = pd.Series(True, index=df.index)
    for c in conditions:
        m = _rule_hit_mask(
            df,
            c["feature"],
            c.get("operator", ">"),
            float(c.get("threshold", 0)),
            values=c.get("values"),
            train_detail_df=train_detail_df,
            train_norm=train_norm,
            source_bins=c.get("source_bins"),
            force_raw_numeric=True,
        )
        mask &= m
    return mask


def unified_rule_hit_mask(
    df: pd.DataFrame,
    rule: Dict[str, Any],
    *,
    train_detail_df: Optional[pd.DataFrame] = None,
    train_norm: Optional[pd.DataFrame] = None,
) -> pd.Series:
    if rule.get("rule_type") == "compound" and rule.get("conditions"):
        return compound_rule_hit_mask(
            df, rule["conditions"], train_detail_df=train_detail_df, train_norm=train_norm
        )
    return _rule_hit_mask(
        df,
        rule.get("feature", ""),
        rule.get("operator", ">"),
        float(rule.get("threshold", 0)),
        values=rule.get("values"),
        train_detail_df=train_detail_df,
        train_norm=train_norm,
        source_bins=rule.get("source_bins"),
    )


def _tree_parent_side_maps(
    left: np.ndarray, right: np.ndarray
) -> Tuple[Dict[int, int], Dict[int, str]]:
    """构建 child -> parent 与 child -> split side 映射（跳过 -1 叶节点占位）。"""
    parent_map: Dict[int, int] = {}
    side_map: Dict[int, str] = {}
    for node_idx in range(len(left)):
        lc = int(left[node_idx])
        rc = int(right[node_idx])
        if lc >= 0:
            parent_map[lc] = node_idx
            side_map[lc] = "le"
        if rc >= 0:
            parent_map[rc] = node_idx
            side_map[rc] = "rg"
    return parent_map, side_map


def _leaf_node_values(dtree: tree.DecisionTreeRegressor) -> List[float]:
    tree_ = dtree.tree_
    values: List[float] = []
    for node in range(tree_.node_count):
        if tree_.feature[node] == _tree.TREE_UNDEFINED:
            val = tree_.value[node]
            values.append(float(np.ravel(val)[0]))
    return values


def _combo_has_variance(df: pd.DataFrame, features: List[str]) -> bool:
    """与 notebook 一致：组合内各变量 dropna 后至少 2 个不同取值。"""
    for feat in features:
        s = pd.to_numeric(df[feat], errors="coerce").dropna()
        if s.nunique() <= 1:
            return False
    return True


def _class_counts_at_node(clf: DecisionTreeClassifier, node: int) -> Tuple[float, float, float]:
    """返回叶/节点上的 (good, bad, total)，target=1 视为 bad。"""
    counts = clf.tree_.value[node][0]
    classes = list(clf.classes_)
    good = bad = 0.0
    for i, cls in enumerate(classes):
        if int(cls) == 1:
            bad = float(counts[i])
        else:
            good += float(counts[i])
    total = good + bad
    return good, bad, total


def _extract_classifier_leaf_rules(
    clf: DecisionTreeClassifier,
    feature_names: List[str],
    *,
    lift_threshold: float = 1.0,
    base_bad_rate: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    与 notebook extract_all_leaf_rules 一致：递归提取所有叶节点规则。
    K 表示参与训树的变量个数；叶路径上的条件数可以 ≤ K。
    """
    tree_ = clf.tree_
    fname = [
        feature_names[i] if i != _tree.TREE_UNDEFINED else ""
        for i in tree_.feature
    ]
    rules: List[Dict[str, Any]] = []
    leaf_samples: List[int] = []

    def recurse(node: int, path: List[Tuple[str, str, float]]) -> None:
        if tree_.feature[node] != _tree.TREE_UNDEFINED:
            name = fname[node]
            th = float(tree_.threshold[node])
            recurse(tree_.children_left[node], path + [(name, "<=", th)])
            recurse(tree_.children_right[node], path + [(name, ">", th)])
            return
        good, bad, total = _class_counts_at_node(clf, node)
        if total <= 0:
            return
        leaf_samples.append(int(total))
        bad_rate = bad / total
        lift = bad_rate / base_bad_rate if base_bad_rate > 0 else 0.0
        if lift < lift_threshold:
            return
        parts = []
        for feat, op, th in path:
            sign = "<=" if op == "<=" else ">"
            parts.append(f"{feat}{sign}{th}")
        rule_str = " and ".join(parts) + f" Badrate:{round(bad_rate, 4)}"
        rules.append({
            "rule_str": rule_str,
            "bad_rate": bad_rate,
            "lift": lift,
            "samples": int(total),
            "bad": int(bad),
        })

    recurse(0, [])
    meta = {
        "leaves": len(leaf_samples),
        "paths_all": len(rules),
        "leaf_samples": leaf_samples,
    }
    return rules, meta


def _train_notebook_combo_tree(
    df: pd.DataFrame,
    x_cols: List[str],
    target_col: str,
    params: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """DecisionTreeClassifier + 全量样本，参数对齐 notebook model_tree。"""
    tree_depth = int(params.get("tree_depth", 3))
    max_leaf_nodes = int(params.get("max_leaf_nodes", 4))
    min_samples_leaf = float(params.get("min_samples_leaf_frac", 0.005))
    lift_threshold = float(params.get("lift_threshold", 1.0))

    y = pd.to_numeric(df[target_col], errors="coerce").fillna(0).astype(int)
    x_mat = df[x_cols].apply(pd.to_numeric, errors="coerce")
    base_bad_rate = float(y.mean()) if len(y) else 0.0

    clf = DecisionTreeClassifier(
        max_depth=tree_depth,
        max_leaf_nodes=max_leaf_nodes,
        min_samples_leaf=min_samples_leaf,
        random_state=42,
    )
    clf.fit(x_mat, y)
    rules, meta = _extract_classifier_leaf_rules(
        clf, x_cols, lift_threshold=lift_threshold, base_bad_rate=base_bad_rate,
    )
    meta["tree_depth"] = tree_depth
    meta["max_leaf_nodes"] = max_leaf_nodes
    return rules, meta


def _resolve_feature_columns(df: pd.DataFrame, features: List[str]) -> Tuple[List[str], Dict[str, str]]:
    """将用户挑选的变量名映射到 DataFrame 实际列名（大小写不敏感）。"""
    col_map = {str(c).lower(): c for c in df.columns}
    resolved: List[str] = []
    missing: List[str] = []
    for f in features:
        key = str(f).lower()
        if f in df.columns:
            resolved.append(f)
        elif key in col_map:
            resolved.append(col_map[key])
        else:
            missing.append(f)
    return list(dict.fromkeys(resolved)), missing


def _preprocess_features(
    df: pd.DataFrame, features: List[str], params: Dict[str, Any]
) -> Tuple[pd.DataFrame, List[str], List[str], Dict[str, str]]:
    """返回 (数据, 保留列, 被剔除列, 剔除原因)。"""
    work = df.copy()
    skip_filter = bool(params.get("skip_feature_filter", True))
    na_threshold = float(params.get("na_threshold", 0.95))
    corr_threshold = float(params.get("correlation_threshold", 0.75))
    removal_reasons: Dict[str, str] = {}

    for col in work.select_dtypes(include=[np.number]).columns:
        work[col] = work[col].replace(list(SPECIAL_BIN_SENTINELS) + [-999, -9999, -999999], np.nan)

    keep = [f for f in features if f in work.columns]
    missing = [f for f in features if f not in work.columns]
    for f in missing:
        removal_reasons[f] = "原始全量数据中找不到该列（请检查变量名是否与上传文件一致）"
    work = work[keep].copy()
    removed: List[str] = list(missing)

    del_empty: List[str] = []
    for col in list(work.columns):
        non_null = int(work[col].notna().sum())
        if non_null == 0:
            del_empty.append(col)
            removal_reasons[col] = "清洗后整列均为缺失值（可能全是 -999 等哨兵值）"
    if del_empty:
        work = work.drop(columns=del_empty)
        removed.extend(del_empty)

    if skip_filter:
        final_cols = [c for c in work.columns]
        return work[final_cols], final_cols, removed, removal_reasons

    del_mode: List[str] = []
    for col in list(work.columns):
        non_null = work[col].dropna()
        if len(non_null) == 0:
            continue
        top_rate = float(non_null.value_counts(normalize=True).iloc[0])
        if top_rate >= na_threshold:
            del_mode.append(col)
            removal_reasons[col] = f"众数占比 {top_rate:.1%} ≥ {na_threshold:.0%}（notebook 式剔除）"

    if del_mode:
        work = work.drop(columns=[c for c in del_mode if c in work.columns])
        removed.extend(del_mode)

    numeric_cols = work.select_dtypes(include=[np.number]).columns.tolist()
    filled = work[numeric_cols].copy()
    for col in numeric_cols:
        if filled[col].isnull().any():
            filled[col] = filled[col].fillna(filled[col].mean())

    del_corr: List[str] = []
    cols = [c for c in numeric_cols if c in filled.columns]
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            ci, cj = cols[i], cols[j]
            if ci in del_corr or cj in del_corr:
                continue
            try:
                roh = float(np.corrcoef(filled[ci].values, filled[cj].values)[0, 1])
                if abs(roh) >= corr_threshold:
                    drop = cj if np.random.rand() >= 0.5 else ci
                    if drop not in del_corr:
                        del_corr.append(drop)
                        removal_reasons[drop] = f"与 {ci if drop == cj else cj} 相关系数 {roh:.2f} ≥ {corr_threshold}"
            except Exception:
                continue

    if del_corr:
        removed.extend(del_corr)
    final_cols = [c for c in work.columns if c not in del_corr]
    return work[final_cols], final_cols, removed, removal_reasons


def _build_feature_report(
    user_features: List[str],
    final_cols: List[str],
    removal_reasons: Dict[str, str],
    cleaned: pd.DataFrame,
) -> List[Dict[str, Any]]:
    """每个用户所选变量的预处理/参与状态说明。"""
    final_set = {str(c).lower(): c for c in final_cols}
    report: List[Dict[str, Any]] = []
    for feat in user_features:
        key = str(feat).lower()
        reason = removal_reasons.get(feat)
        if not reason:
            for k, v in removal_reasons.items():
                if str(k).lower() == key:
                    reason = v
                    break
        canonical = final_set.get(key)
        if canonical and canonical in cleaned.columns:
            col = cleaned[canonical]
            non_null = int(col.notna().sum())
            n = len(col)
            top_rate = None
            if non_null > 0:
                top_rate = float(col.dropna().value_counts(normalize=True).iloc[0])
            report.append({
                "feature": feat,
                "chinese_name": get_chinese_name(feat) or get_chinese_name(canonical) or feat,
                "status": "参与挖掘",
                "reason": "通过预处理，进入 K 组合挖树",
                "non_null_count": non_null,
                "non_null_rate": non_null / n if n else 0.0,
                "top_mode_rate": top_rate,
            })
        else:
            report.append({
                "feature": feat,
                "chinese_name": get_chinese_name(feat) or feat,
                "status": "预处理剔除",
                "reason": reason or "未能进入挖掘（请检查原始列是否存在、是否全为缺失/哨兵值）",
                "non_null_count": 0,
                "non_null_rate": 0.0,
                "top_mode_rate": None,
            })
    return report


def _mine_trees_on_data(
    model_data: pd.DataFrame,
    target_col: str,
    feature_cols: List[str],
    params: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    与 notebook model_tree + extract_all_leaf_rules 对齐：
    - K = 每次参与训树的变量个数（C(n,K) 组合）
    - DecisionTreeClassifier(max_depth=3, max_leaf_nodes=4, min_samples_leaf=0.005)
    - 全量样本训树，提取所有 lift≥阈值的叶规则
    """
    comb = int(params.get("comb", 3))
    combo_random = float(params.get("combo_random", 0.3))
    tree_depth = int(params.get("tree_depth", 3))
    need_rm_narow = bool(params.get("need_rm_narow", False))

    if target_col not in model_data.columns:
        return [], {"error": "找不到目标列"}

    bad_n = int((model_data[target_col] == 1).sum())
    if bad_n <= int(params.get("min_bad_samples", 30)):
        return [], {
            "error": f"全量坏样本 {bad_n} 个过少，不满足挖树最低坏样本要求",
            "bad_count": bad_n,
        }

    rules_out: List[Dict[str, Any]] = []
    n_vars = len(feature_cols)
    if n_vars < comb:
        return [], {"error": f"可用变量 {n_vars} 个不足 K={comb}"}

    var_indices = list(range(n_vars))
    combos = list(itertools.combinations(var_indices, comb))
    total_combos = len(combos)
    if comb == 2 or len(combos) <= 45:
        max_combos = len(combos)
    else:
        np.random.shuffle(combos)
        max_combos = max(1, int(len(combos) * combo_random))
        combos = combos[:max_combos]

    stats: Dict[str, Any] = {
        "total_combos": total_combos,
        "combos_planned": len(combos),
        "combos_trained": 0,
        "skip_no_variance": 0,
        "skip_no_bad_or_good": 0,
        "skip_fit_error": 0,
        "raw_paths_extracted": 0,
        "keep_missing_rows": not need_rm_narow,
        "tree_depth": tree_depth,
        "mine_all_pairs": comb == 2,
        "mining_mode": "classifier_leaf_rules",
        "combo_samples": [],
    }

    for combo in combos:
        cols = [feature_cols[i] for i in combo] + [target_col]
        combo_names = [feature_cols[i] for i in combo]
        sub = model_data[cols].copy()
        x_cols = [c for c in cols if c != target_col]
        rows_total = len(sub)
        if need_rm_narow:
            sub = sub.dropna()

        if len(sub) < 2:
            stats["skip_no_bad_or_good"] += 1
            continue

        bad_df = sub[sub[target_col] == 1]
        good_df = sub[sub[target_col] == 0]
        if len(bad_df) == 0 or len(good_df) == 0:
            stats["skip_no_bad_or_good"] += 1
            continue

        if not _combo_has_variance(sub, x_cols):
            stats["skip_no_variance"] += 1
            if len(stats["combo_samples"]) < 12:
                stats["combo_samples"].append({
                    "combo": combo_names,
                    "rows_used": len(sub),
                    "result": "跳过：组合内某变量去缺失后仅 1 个取值（与 notebook 一致）",
                })
            continue

        try:
            extracted, path_meta = _train_notebook_combo_tree(sub, x_cols, target_col, params)
            stats["combos_trained"] += 1
            n_rules = len(extracted)
            for item in extracted:
                rules_out.append({
                    **item,
                    "feature_combo": combo_names,
                })
            stats["raw_paths_extracted"] += n_rules
            if len(stats["combo_samples"]) < 12:
                stats["combo_samples"].append({
                    "combo": combo_names,
                    "rows_total": rows_total,
                    "rows_used": len(sub),
                    "bad": len(bad_df),
                    "good": len(good_df),
                    "tree_depth": path_meta.get("tree_depth", tree_depth),
                    "leaves": path_meta.get("leaves", 0),
                    "paths": n_rules,
                    "result": (
                        f"Classifier 深度{path_meta.get('tree_depth')} "
                        f"max_leaf={path_meta.get('max_leaf_nodes', 4)}："
                        f"提取 {n_rules} 条叶规则（lift≥{params.get('lift_threshold', 1.0)}）"
                    ),
                })
        except Exception as exc:
            stats["skip_fit_error"] += 1
            if len(stats["combo_samples"]) < 12:
                stats["combo_samples"].append({
                    "combo": combo_names,
                    "rows_used": len(sub),
                    "result": f"训树失败：{exc}",
                })

    if not rules_out and not stats.get("error"):
        hints: List[str] = []
        if stats["combos_trained"] > 0:
            hints.append(
                f"已训 {stats['combos_trained']} 组 K={comb} 组合树，"
                f"但无叶规则满足 lift≥{params.get('lift_threshold', 1.0)}"
            )
        elif stats["combos_trained"] == 0:
            hints.append("所有组合均未成功训树（可能无方差、无好/坏样本等）")
        stats["zero_rules_hint"] = "；".join(hints) if hints else "未知原因，请换变量或降低 lift 阈值后重试"

    return rules_out, stats


def _evaluate_compound_on_frame(
    df: pd.DataFrame,
    conditions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    mask = compound_rule_hit_mask(df, conditions)
    stats = _portfolio_stats(df[mask])
    bad_n = int(df.loc[mask, "overdue_flag"].sum()) if stats["count"] else 0
    return {
        "hit_count": stats["count"],
        "bad_count": bad_n,
        "bad_rate": stats["bad_rate"],
        "money_bad_rate": stats["money_bad_rate"],
    }


def _dedupe_conditions(conditions: List[Dict[str, Any]]) -> str:
    parts = sorted(
        f"{c['feature']}{c['operator']}{float(c['threshold']):.6f}" for c in conditions
    )
    return "|".join(parts)


def mine_multivariate_rules(
    job_id: str,
    features: List[str],
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not features:
        raise ValueError("请至少选择一个特征用于多变量挖掘")

    p = {**DEFAULT_MINING_PARAMS, **(params or {})}
    features = list(dict.fromkeys(f.strip() for f in features if f.strip()))
    max_feat = int(p.get("max_features", 50))
    if len(features) > max_feat:
        raise ValueError(f"多变量挖掘最多支持 {max_feat} 个特征，当前 {len(features)} 个，请减少选择")

    cache_key = _mining_cache_key(features, p)
    bucket = _MINING_CACHE.setdefault(job_id, {})
    if cache_key in bucket:
        return bucket[cache_key]

    tr = _load_train_frame(job_id)
    full_df = _load_full_frame(job_id)
    job = get_job(job_id)
    if not job:
        raise ValueError("分箱任务不存在")
    run_params = job.run_params or {}
    label = run_params.get("label", "target3")

    model_df = full_df.copy()
    label_series = model_df.get(label, model_df.get("overdue_flag"))
    if label_series is None:
        raise ValueError("找不到逾期标签列")

    model_df["_target"] = pd.to_numeric(label_series, errors="coerce").fillna(0).astype(int)

    resolved, missing_names = _resolve_feature_columns(model_df, features)
    if missing_names:
        hint = "、".join(missing_names[:5])
        extra = f" 等 {len(missing_names)} 个" if len(missing_names) > 5 else ""
        raise ValueError(
            f"以下变量在原始全量数据中找不到对应列：{hint}{extra}。"
            "请确认第 5 步所选变量名与上传 CSV 列名一致（大小写会自动匹配）。"
        )

    if len(resolved) < int(p.get("comb", 3)):
        raise ValueError(f"可用特征不足 {p.get('comb')} 个，无法进行组合挖掘")

    cleaned, final_cols, removed, removal_reasons = _preprocess_features(
        _notebook_clean_features(model_df, resolved), resolved, p
    )
    comb = int(p.get("comb", 3))
    if len(final_cols) < comb:
        def _reason_for(feat: str) -> str:
            if feat in removal_reasons:
                return removal_reasons[feat]
            for k, v in removal_reasons.items():
                if str(k).lower() == str(feat).lower():
                    return v
            return "预处理时被剔除"

        detail_lines = [f"· {feat}：{_reason_for(feat)}" for feat in features]
        detail = "\n".join(detail_lines[:8])
        raise ValueError(
            f"您选了 {len(features)} 个变量，预处理后可用 {len(final_cols)} 个，不足 K={comb}。\n"
            f"原因说明：\n{detail}\n"
            "建议：返回第 5 步换一批变量；若此前因众数/相关剔除导致全被删，"
            "现已对「您已挑选的变量池」跳过该过滤，请重新点击「开始多变量挖掘」。"
        )

    work = pd.concat([cleaned, model_df[["_target"]]], axis=1)

    feature_report = _build_feature_report(features, final_cols, removal_reasons, cleaned)
    raw_rules, mining_stats = _mine_trees_on_data(work, "_target", final_cols, p)

    seen: set[str] = set()
    rules: List[Dict[str, Any]] = []
    rule_idx = 0
    comb_k = int(p.get("comb", 3))
    deduped_count = 0
    skipped_short = 0
    for item in raw_rules:
        rs = item.get("rule_str", "") if isinstance(item, dict) else str(item)
        combo_feats = item.get("feature_combo", []) if isinstance(item, dict) else []
        tree_lift = item.get("lift") if isinstance(item, dict) else None
        raw_conditions, _tree_br = parse_compound_rule_string(rs)
        if not raw_conditions:
            skipped_short += 1
            continue
        conditions = _simplify_conditions(raw_conditions)
        if not conditions:
            skipped_short += 1
            continue
        feat_names = list(dict.fromkeys(c["feature"] for c in conditions))
        key = _dedupe_conditions(conditions)
        if key in seen:
            deduped_count += 1
            continue
        seen.add(key)
        rule_idx += 1
        display = format_compound_rule_display(conditions)
        rules.append({
            "rule_id": f"mv_{rule_idx}",
            "rule_type": "compound",
            "conditions": conditions,
            "features": feat_names,
            "feature_combo": combo_feats,
            "feature_labels": [get_chinese_name(f) or f for f in feat_names],
            "rule_display": display,
            "tree_bad_rate": _tree_br,
            "tree_lift": tree_lift,
            "n_conditions": len(conditions),
            "distinct_feature_count": len(feat_names),
        })

    def _eval_frame_with_features(base: pd.DataFrame) -> pd.DataFrame:
        out = base.copy()
        cleaned_feats = _notebook_clean_features(base, final_cols)
        for c in final_cols:
            if c in cleaned_feats.columns:
                out[c] = cleaned_feats[c]
        if "overdue_flag" not in out.columns:
            src = label if label in out.columns else None
            if src:
                out["overdue_flag"] = pd.to_numeric(out[src], errors="coerce").fillna(0).astype(int)
        return out

    full_eval = _eval_frame_with_features(full_df)
    tr_eval = _eval_frame_with_features(tr)

    for r in rules:
        full_ev = _evaluate_compound_on_frame(full_eval, r["conditions"])
        train_ev = _evaluate_compound_on_frame(tr_eval, r["conditions"])
        r.update(full_ev)
        r["train_hit_count"] = train_ev["hit_count"]
        r["train_bad_rate"] = train_ev["bad_rate"]
        r["train_bad_count"] = train_ev["bad_count"]

    result = {
        "job_id": job_id,
        "input_features": features,
        "mined_features": final_cols,
        "removed_features": removed,
        "removal_reasons": removal_reasons,
        "feature_report": feature_report,
        "mining_stats": mining_stats,
        "skip_feature_filter": bool(p.get("skip_feature_filter", True)),
        "raw_rule_count": len(raw_rules),
        "candidate_count": len(rules),
        "deduped_count": deduped_count,
        "skipped_short_count": skipped_short,
        "comb_k": comb_k,
        "rules": rules,
        "params": p,
        "portfolio_bad_rate": _portfolio_stats(full_eval)["bad_rate"],
        "train_portfolio_bad_rate": _portfolio_stats(tr_eval)["bad_rate"],
        "eval_scope": "full",
    }
    bucket[cache_key] = result
    _trim_mining_cache(job_id)
    return _json_safe(result)


def filter_multivariate_rules(
    mined: Dict[str, Any],
    bad_rate_threshold: float,
    min_hit_count: int,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for r in mined.get("rules", []):
        if r.get("bad_rate", 0) >= bad_rate_threshold and r.get("hit_count", 0) >= min_hit_count:
            out.append(r)
    out.sort(key=lambda x: (-float(x.get("bad_rate") or 0), -int(x.get("hit_count") or 0)))
    return out


def build_mv_threshold_overview(
    mined: Dict[str, Any],
    min_hit_count: int = 10,
) -> Dict[str, Any]:
    """组合规则在不同坏率阈值下的满足条数（全量口径，与 notebook Cell 6 一致）。"""
    rules = mined.get("rules") or []
    thresholds = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
    rows: List[Dict[str, Any]] = []
    for th in thresholds:
        matched = [
            r for r in rules
            if float(r.get("bad_rate") or 0) >= th and int(r.get("hit_count") or 0) >= min_hit_count
        ]
        avg_hit = int(round(sum(int(r.get("hit_count") or 0) for r in matched) / len(matched))) if matched else 0
        rows.append({
            "threshold": th,
            "threshold_pct": f"{int(round(th * 100))}%",
            "rule_count": len(matched),
            "avg_hit": avg_hit if matched else None,
            "hint": "全量命中样本坏率 ≥ 阈值，且命中人数 ≥ 最少命中",
        })
    return {
        "candidate_count": len(rules),
        "min_hit_count": min_hit_count,
        "portfolio_bad_rate": mined.get("portfolio_bad_rate"),
        "train_portfolio_bad_rate": mined.get("train_portfolio_bad_rate"),
        "eval_scope": mined.get("eval_scope", "full"),
        "threshold_overview": rows,
    }


def evaluate_mixed_reject_preview(
    job_id: str,
    compound_rules: List[Dict[str, Any]],
    single_rules: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    tr = _load_train_frame(job_id)
    baseline = _portfolio_stats(tr)
    all_rules = list(single_rules or []) + list(compound_rules or [])

    if not all_rules:
        return {
            "baseline": baseline,
            "after": baseline,
            "rejected": {"count": 0, "bad_rate": 0.0, "money_bad_rate": 0.0},
            "per_rule": [],
        }

    try:
        from services.feature_review_service import _get_job_binning_bundle
        bundle = _get_job_binning_bundle(job_id)
        train_raw = bundle["train_raw"]
        train_norm = bundle["train_norm"]
    except ValueError:
        train_raw = None
        train_norm = None

    combined = pd.Series(False, index=tr.index)
    per_rule: List[Dict[str, Any]] = []
    for r in all_rules:
        mask = unified_rule_hit_mask(tr, r, train_detail_df=train_raw, train_norm=train_norm)
        combined |= mask
        hit_stats = _portfolio_stats(tr[mask])
        bad_n = int(tr.loc[mask, "overdue_flag"].sum()) if hit_stats["count"] else 0
        display = r.get("rule_display") or (
            format_compound_rule_display(r["conditions"])
            if r.get("rule_type") == "compound"
            else _format_rule_display(r)
        )
        per_rule.append({
            "rule_id": r.get("rule_id") or r.get("feature", ""),
            "rule_type": r.get("rule_type", "single"),
            "feature": r.get("feature", r.get("rule_id", "")),
            "rule_display": display,
            "hit_count": hit_stats["count"],
            "bad_count": bad_n,
            "bad_rate": hit_stats["bad_rate"],
            "money_bad_rate": hit_stats["money_bad_rate"],
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


def get_compound_rule_stability(
    job_id: str,
    conditions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    job = get_job(job_id)
    if not job:
        raise ValueError("分箱任务不存在")
    params = job.run_params or {}
    file_path = params.get("file_path")
    empty_side = {"months": [], "rows": [], "total": {}}
    if not file_path:
        return {
            "train": empty_side,
            "test": empty_side,
            "note": "缺少原始数据，无法计算月度稳定性",
        }

    raw_df = read_dataframe(Path(file_path))
    label = params.get("label", "target3")
    time_col = params.get("time_col") or "apply_date"
    tr, te = _prepare_stability_frames(
        raw_df,
        label,
        time_col,
        params.get("split_mode", "ai"),
        float(params.get("oot_ratio", 0.2)),
        params.get("cutoff_date"),
        Path(params["train_file_path"]) if params.get("train_file_path") else None,
        Path(params["test_file_path"]) if params.get("test_file_path") else None,
    )

    try:
        from services.feature_review_service import _get_job_binning_bundle
        bundle = _get_job_binning_bundle(job_id)
        train_raw = bundle["train_raw"]
        train_norm = bundle["train_norm"]
    except ValueError:
        train_raw = None
        train_norm = None

    def _monthly_table(data: pd.DataFrame) -> Dict[str, Any]:
        if data.empty or "apply_month" not in data.columns:
            return {"months": [], "rows": [], "total": {}}
        mask = compound_rule_hit_mask(
            data, conditions, train_detail_df=train_raw, train_norm=train_norm,
        )
        months = sorted({
            str(m).strip()
            for m in data["apply_month"].dropna().unique()
            if str(m).strip().lower() not in ("nan", "none", "nat", "")
        })
        if not months:
            return {"months": [], "rows": [], "total": {}}
        hit_df = data[mask]
        rows = []
        for m in months:
            sub = hit_df[hit_df["apply_month"].astype(str) == m]
            obs = len(sub)
            bad = int(sub["overdue_flag"].sum()) if obs else 0
            money = float(sub["money"].sum()) if obs and "money" in sub.columns else 0.0
            bad_money = float(sub.loc[sub["overdue_flag"] == 1, "money"].sum()) if bad else 0.0
            rows.append({
                "month": m,
                "obs": obs,
                "bad": bad,
                "bad_rate": bad / obs if obs else 0.0,
                "money_bad_rate": bad_money / money if money else 0.0,
            })
        total_obs = len(hit_df)
        total_bad = int(hit_df["overdue_flag"].sum()) if total_obs else 0
        return {
            "months": months,
            "rows": rows,
            "total": {
                "obs": total_obs,
                "bad": total_bad,
                "bad_rate": total_bad / total_obs if total_obs else 0.0,
            },
        }

    train_stab = _monthly_table(tr)
    no_test_split = len(te) == 0
    test_stab = _monthly_table(te) if not no_test_split else {"months": [], "rows": [], "total": {}}

    notes: List[str] = []
    if not train_stab.get("months"):
        notes.append(
            f"未能从时间列「{time_col}」解析出有效月份，请回到第 1 步确认时间列（如 apply_date）选择正确。"
        )
    if no_test_split:
        notes.append("当前为 Train 全量分箱（OOT=0 或未切 Test），Test 月度稳定性为空属正常现象。")

    return {
        "train": train_stab,
        "test": test_stab,
        "rule_display": format_compound_rule_display(conditions),
        "time_col": time_col,
        "no_test_split": no_test_split,
        "note": " ".join(notes) if notes else None,
    }
