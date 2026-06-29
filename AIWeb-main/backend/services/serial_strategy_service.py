"""规则串联分析：Lift/最少命中仅用于 Train 准入；Test/全量跟随 Train 已应用规则。"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from services.binning_runner import get_job
from services.binning_excel_format import write_formatted_binning_sheet
from services.data_service import read_dataframe
from services.feature_review_service import (
    _bin_sort_key,
    _bins_for_feature_from_train_defs,
    _feature_col,
    _format_rule_display,
    _get_job_binning_bundle,
    _job_has_test_split,
    _load_train_frame,
    _portfolio_stats,
    _prepare_stability_frames,
    _rule_hit_mask,
    ensure_test_binning_aligned_for_job,
    get_chinese_name,
    read_binning_sheets,
)


def _load_train_test_full(job_id: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    tr = _load_train_frame(job_id)
    job = get_job(job_id)
    if not job:
        raise ValueError("分箱任务不存在")
    params = job.run_params or {}
    te = pd.DataFrame()
    file_path = params.get("file_path")
    if file_path and Path(file_path).exists():
        raw_df = read_dataframe(Path(file_path))
        label = params.get("label", "target3")
        tr2, te2 = _prepare_stability_frames(
            raw_df,
            label,
            params.get("time_col", "apply_time"),
            params.get("split_mode", "ai"),
            float(params.get("oot_ratio", 0.2)),
            params.get("cutoff_date"),
            Path(params["train_file_path"]) if params.get("train_file_path") else None,
            Path(params["test_file_path"]) if params.get("test_file_path") else None,
        )
        if len(tr2):
            tr = tr2
        if len(te2):
            te = te2
    if len(te):
        full = pd.concat([tr, te], axis=0)
    else:
        full = tr.copy()
    return tr, te, full


def _enrich_rules(
    full_df: pd.DataFrame,
    rules: List[Dict[str, Any]],
    *,
    train_raw: Optional[pd.DataFrame] = None,
    train_norm: Optional[pd.DataFrame] = None,
) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for r in rules:
        op = r.get("operator", ">")
        if op == "in" and not r.get("values"):
            continue
        mask = _rule_hit_mask(
            full_df,
            r["feature"],
            op,
            float(r.get("threshold", 0)),
            values=r.get("values"),
            train_detail_df=train_raw,
            train_norm=train_norm,
            source_bins=r.get("source_bins"),
        )
        stats = _portfolio_stats(full_df[mask])
        enriched.append({
            **r,
            "rule_display": _format_rule_display(r),
            "chinese_name": get_chinese_name(r.get("feature", "")),
            "full_hit": stats["count"],
            "full_bad_rate": stats["bad_rate"],
        })
    return enriched


def _sort_rules(rules: List[Dict[str, Any]], sort_mode: str) -> List[Dict[str, Any]]:
    if sort_mode == "bad_rate":
        return sorted(rules, key=lambda x: -float(x.get("full_bad_rate") or 0))
    if sort_mode == "hit_count":
        return sorted(rules, key=lambda x: -int(x.get("full_hit") or 0))
    return rules


def _serial_on_df(
    df: Optional[pd.DataFrame],
    ordered_rules: List[Dict[str, Any]],
    orig_badrate: float,
    lift_min: float,
    min_hit: int,
    *,
    train_raw: Optional[pd.DataFrame] = None,
    train_norm: Optional[pd.DataFrame] = None,
    apply_decisions: Optional[List[bool]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """串联模拟。

    apply_decisions=None：在 df 上按 Lift/最少命中决定是否应用（Train 准入）。
    apply_decisions 给定：Test/全量跟随 Train 决策，不再重复校验 Lift/最少命中。
    """
    empty_step = {
        "pool_size": 0,
        "rule_hit": 0,
        "serial_hit": 0,
        "serial_bad_count": 0,
        "rule_bad_rate": 0.0,
        "serial_bad_rate": 0.0,
        "money_bad_rate": 0.0,
        "lift": 0.0,
        "lift_2": 0.0,
        "reject_rate": 0.0,
        "remaining": 0,
    }
    if df is None or df.empty:
        return [{**empty_step} for _ in ordered_rules], {
            "total": 0,
            "final_count": 0,
            "orig_bad_rate": 0.0,
            "final_bad_rate": 0.0,
            "orig_money_bad_rate": 0.0,
            "final_money_bad_rate": 0.0,
            "pass_rate": 0.0,
            "total_bad": 0,
            "final_bad": 0,
        }

    orig = _portfolio_stats(df)
    remaining = pd.Series(True, index=df.index)
    per_rule: List[Dict[str, Any]] = []

    for rule_idx, rule in enumerate(ordered_rules):
        hit_all = _rule_hit_mask(
            df,
            rule["feature"],
            rule.get("operator", ">"),
            float(rule.get("threshold", 0)),
            values=rule.get("values"),
            train_detail_df=train_raw,
            train_norm=train_norm,
            source_bins=rule.get("source_bins"),
        )
        hit_serial = hit_all & remaining

        th_stats = _portfolio_stats(df[hit_all])
        sh_stats = _portfolio_stats(df[hit_serial])

        before_mask = remaining
        before_stats = _portfolio_stats(df[before_mask])
        before_br = before_stats["bad_rate"]

        lift = sh_stats["bad_rate"] / orig_badrate if orig_badrate else 0.0
        lift_2 = sh_stats["bad_rate"] / before_br if before_br else 0.0

        if apply_decisions is None:
            dropped = lift < lift_min or sh_stats["count"] < min_hit
            if sh_stats["count"] < min_hit:
                drop_reason = f"Train 串联命中 {sh_stats['count']} < {min_hit}"
            elif lift < lift_min:
                drop_reason = f"Train Lift {lift:.2f} < {lift_min}"
            else:
                drop_reason = ""
            apply_rule = not dropped
        else:
            apply_rule = bool(apply_decisions[rule_idx])
            dropped = not apply_rule
            drop_reason = "Train 未达 Lift/最少命中" if dropped else ""

        pool_size = int(remaining.sum())

        per_rule.append({
            "pool_size": pool_size,
            "rule_hit": th_stats["count"],
            "serial_hit": sh_stats["count"],
            "serial_bad_count": int(df.loc[hit_serial, "overdue_flag"].sum()) if sh_stats["count"] else 0,
            "rule_bad_rate": th_stats["bad_rate"],
            "serial_bad_rate": sh_stats["bad_rate"],
            "money_bad_rate": sh_stats["money_bad_rate"],
            "lift": round(lift, 4),
            "lift_2": round(lift_2, 4),
            "reject_rate": sh_stats["count"] / orig["count"] if orig["count"] else 0.0,
            "remaining": int((remaining & ~hit_serial).sum()) if apply_rule else int(remaining.sum()),
            "dropped": dropped,
            "drop_reason": drop_reason,
            "applied": apply_rule,
        })

        if apply_rule:
            remaining &= ~hit_serial

    final_stats = _portfolio_stats(df[remaining])
    summary = {
        "total": orig["count"],
        "final_count": final_stats["count"],
        "orig_bad_rate": orig["bad_rate"],
        "final_bad_rate": final_stats["bad_rate"],
        "orig_money_bad_rate": orig["money_bad_rate"],
        "final_money_bad_rate": final_stats["money_bad_rate"],
        "pass_rate": final_stats["count"] / orig["count"] if orig["count"] else 0.0,
        "total_bad": int(df["overdue_flag"].sum()),
        "final_bad": int(df.loc[remaining, "overdue_flag"].sum()),
    }
    return per_rule, summary


def build_serial_analysis(
    job_id: str,
    rules: List[Dict[str, Any]],
    sort_mode: str = "selection",
    lift_min: float = 1.10,
    min_hit: int = 5,
) -> Dict[str, Any]:
    if not rules:
        raise ValueError("请至少选择一个特征规则")

    tr, te, full = _load_train_test_full(job_id)
    try:
        bundle = _get_job_binning_bundle(job_id)
        train_raw = bundle["train_raw"]
        train_norm = bundle["train_norm"]
    except ValueError:
        train_raw = None
        train_norm = None

    enriched = _enrich_rules(full, rules, train_raw=train_raw, train_norm=train_norm)
    ordered = _sort_rules(enriched, sort_mode)

    train_orig_br = _portfolio_stats(tr)["bad_rate"]
    ctx = {"train_raw": train_raw, "train_norm": train_norm}
    train_steps, train_sum = _serial_on_df(tr, ordered, train_orig_br, lift_min, min_hit, **ctx)
    train_apply = [bool(s.get("applied")) for s in train_steps]
    follow = {"apply_decisions": train_apply, **ctx}
    test_steps, test_sum = _serial_on_df(
        te if len(te) else None,
        ordered,
        _portfolio_stats(te)["bad_rate"] if len(te) else 0.0,
        lift_min,
        min_hit,
        **follow,
    )
    full_steps, full_sum = _serial_on_df(
        full, ordered, _portfolio_stats(full)["bad_rate"], lift_min, min_hit, **follow
    )

    rows: List[Dict[str, Any]] = []
    for i, rule in enumerate(ordered):
        ts, tes, fs = train_steps[i], test_steps[i], full_steps[i]
        status = "dropped" if ts.get("dropped") else "applied"
        rows.append({
            "order": i + 1,
            "feature": rule["feature"],
            "chinese_name": rule.get("chinese_name", ""),
            "rule_display": rule["rule_display"],
            "operator": rule.get("operator", ">"),
            "threshold": float(rule.get("threshold", 0)),
            "values": rule.get("values"),
            "status": status,
            "drop_reason": ts.get("drop_reason", ""),
            "train": {k: ts[k] for k in ts if k not in ("dropped", "drop_reason", "applied")},
            "test": {k: tes[k] for k in tes if k not in ("dropped", "drop_reason", "applied")},
            "full": {k: fs[k] for k in fs if k not in ("dropped", "drop_reason", "applied")},
        })

    applied = [r for r in rows if r["status"] == "applied"]
    return {
        "sort_mode": sort_mode,
        "lift_min": lift_min,
        "min_hit": min_hit,
        "rule_count_input": len(rules),
        "rule_count_applied": len(applied),
        "summary": {
            "train": train_sum,
            "test": test_sum,
            "full": full_sum,
        },
        "rules": rows,
    }


def _segment_detail_rows(rules: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    """按 Train / Test / 全量 生成串联明细行（与手动脚本列名对齐）。"""
    rows: List[Dict[str, Any]] = []
    for r in rules:
        seg = r[key]
        pool = seg.get("pool_size") or 0
        serial_hit = seg.get("serial_hit") or 0
        rows.append({
            "序号": r["order"],
            "规则名称": _format_rule_display({
                "feature": r["feature"],
                "operator": r.get("operator", ">"),
                "threshold": r.get("threshold", 0),
                "values": r.get("values"),
            }, full=True),
            "中文名": r.get("chinese_name", ""),
            "状态": "丢弃" if r["status"] == "dropped" else "应用",
            "丢弃原因": r.get("drop_reason", "") if r["status"] == "dropped" else "",
            "样本量_池子": pool,
            "规则命中": seg.get("rule_hit", 0),
            "串联命中": serial_hit,
            "命中率": serial_hit / pool if pool else 0.0,
            "规则坏率": seg.get("rule_bad_rate", 0.0),
            "串联坏率": seg.get("serial_bad_rate", 0.0),
            "串联坏客数": seg.get("serial_bad_count", 0),
            "lift": seg.get("lift", 0.0),
            "lift_2": seg.get("lift_2", 0.0),
            "拒绝率": seg.get("reject_rate", 0.0),
            "剩余样本": seg.get("remaining", 0),
        })
    return rows


def _bin_col_name(df: pd.DataFrame) -> str:
    if "Bin" in df.columns:
        return "Bin"
    if "bin_label" in df.columns:
        return "bin_label"
    raise ValueError("分箱结果缺少 Bin / bin_label 列")


def _sort_binning_rows(df: pd.DataFrame, feature_order: List[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    col = _feature_col(df)
    bin_col = _bin_col_name(df)
    out = df[df[col].isin(feature_order)].copy()
    if out.empty:
        return out
    out["_feat_order"] = pd.Categorical(out[col], categories=feature_order, ordered=True)
    out["_bin_order"] = out[bin_col].astype(str).apply(_bin_sort_key)
    out = out.sort_values(["_feat_order", "_bin_order"]).drop(columns=["_feat_order", "_bin_order"])
    return out.reset_index(drop=True)


def _apply_computed_bin_stats(row: Dict[str, Any], computed: Dict[str, Any]) -> Dict[str, Any]:
    obs = int(computed.get("obs", 0) or 0)
    bad = int(computed.get("bad", 0) or 0)
    br = float(computed.get("bad_rate", 0) or 0)
    if "#Obs" in row:
        row["#Obs"] = obs
        row["#Bad"] = bad
        row["%Bad_Rate"] = br
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


def _build_full_binning_sheet(
    job_id: str,
    feature_order: List[str],
    train_raw: pd.DataFrame,
) -> pd.DataFrame:
    """用 Train 分箱边界在全量样本上重算，输出与 Train 表相同列结构（含全部类别/分箱）。"""
    job = get_job(job_id)
    if not job:
        return pd.DataFrame()
    if not _job_has_test_split(job):
        return _sort_binning_rows(train_raw, feature_order)

    _, _, full = _load_train_test_full(job_id)
    if full.empty:
        return pd.DataFrame()

    col = _feature_col(train_raw)
    bin_col = _bin_col_name(train_raw)
    chunks: List[pd.DataFrame] = []
    for feat in feature_order:
        feat_rows = train_raw[train_raw[col] == feat].copy()
        if feat_rows.empty:
            continue
        feat_rows = feat_rows.copy()
        feat_rows["_sort"] = feat_rows[bin_col].astype(str).apply(_bin_sort_key)
        feat_rows = feat_rows.sort_values("_sort").drop(columns="_sort")
        computed = _bins_for_feature_from_train_defs(full, train_raw, feat)
        by_bin = {str(c["bin"]): c for c in computed}
        rebuilt = []
        for _, r in feat_rows.iterrows():
            bl = str(r[bin_col])
            rebuilt.append(_apply_computed_bin_stats(r.to_dict(), by_bin.get(bl, {})))
        chunks.append(pd.DataFrame(rebuilt))
    if not chunks:
        return pd.DataFrame()
    return pd.concat(chunks, ignore_index=True)


def _load_selected_binning_sheets(
    job_id: str,
    feature_order: List[str],
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    job = get_job(job_id)
    if not job or not job.output_path or not job.output_path.exists():
        raise ValueError("分箱任务不存在或分箱结果文件已丢失，请重新执行分箱")
    ensure_test_binning_aligned_for_job(job_id)
    train_raw, test_raw = read_binning_sheets(str(job.output_path))
    train_sel = _sort_binning_rows(train_raw, feature_order)
    test_sel = _sort_binning_rows(test_raw, feature_order) if test_raw is not None else pd.DataFrame()
    if test_sel.empty and not train_sel.empty:
        test_sel = pd.DataFrame(columns=train_sel.columns)
    full_sel = _build_full_binning_sheet(job_id, feature_order, train_raw)
    return train_sel, test_sel, full_sel


def _collect_selected_features(
    rules: List[Dict[str, Any]],
    analysis: Dict[str, Any],
) -> List[str]:
    seen: set = set()
    ordered: List[str] = []
    for src in (rules, analysis.get("rules") or []):
        for r in src:
            feat = r.get("feature") if isinstance(r, dict) else None
            if feat and feat not in seen:
                seen.add(feat)
                ordered.append(feat)
    return ordered


def export_serial_excel(
    analysis: Dict[str, Any],
    job_id: str,
    selected_features: List[str],
) -> bytes:
    summary = analysis["summary"]
    rules = analysis["rules"]

    summary_rows = [
        ["指标", "Train", "Test", "全量"],
        ["总样本", summary["train"]["total"], summary["test"]["total"], summary["full"]["total"]],
        ["剩余样本", summary["train"]["final_count"], summary["test"]["final_count"], summary["full"]["final_count"]],
        ["逾期人数(前)", summary["train"]["total_bad"], summary["test"]["total_bad"], summary["full"]["total_bad"]],
        ["逾期人数(后)", summary["train"]["final_bad"], summary["test"]["final_bad"], summary["full"]["final_bad"]],
        ["件数逾期率(前)", summary["train"]["orig_bad_rate"], summary["test"]["orig_bad_rate"], summary["full"]["orig_bad_rate"]],
        ["件数逾期率(后)", summary["train"]["final_bad_rate"], summary["test"]["final_bad_rate"], summary["full"]["final_bad_rate"]],
        ["金额逾期率(前)", summary["train"]["orig_money_bad_rate"], summary["test"]["orig_money_bad_rate"], summary["full"]["orig_money_bad_rate"]],
        ["金额逾期率(后)", summary["train"]["final_money_bad_rate"], summary["test"]["final_money_bad_rate"], summary["full"]["final_money_bad_rate"]],
        ["通过率", summary["train"]["pass_rate"], summary["test"]["pass_rate"], summary["full"]["pass_rate"]],
    ]
    summary_df = pd.DataFrame(summary_rows[1:], columns=summary_rows[0])

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        workbook = writer.book
        summary_df.to_excel(writer, sheet_name="汇总", index=False)

        for sheet_name, key in (
            ("Train串联", "train"),
            ("Test串联", "test"),
            ("全量串联", "full"),
        ):
            detail_df = pd.DataFrame(_segment_detail_rows(rules, key))
            detail_df.to_excel(writer, sheet_name=sheet_name, index=False)

        if selected_features:
            train_bins, test_bins, full_bins = _load_selected_binning_sheets(
                job_id, selected_features
            )
            if train_bins.empty:
                raise ValueError(
                    "未能从第3步分箱结果中匹配到所选特征的分箱明细，"
                    "请确认分箱文件未删除，或重新执行分箱后再导出"
                )
            write_formatted_binning_sheet(writer, workbook, train_bins, "Train分箱明细")
            write_formatted_binning_sheet(writer, workbook, test_bins, "Test分箱明细")
            write_formatted_binning_sheet(writer, workbook, full_bins, "全量分箱明细")
        else:
            raise ValueError("导出分箱明细需要至少一条已选特征规则")

    buf.seek(0)
    return buf.getvalue()
