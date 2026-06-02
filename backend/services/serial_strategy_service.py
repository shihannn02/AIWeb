"""规则串联分析：Train / Test / 全量 分别计算。"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from services.binning_runner import get_job
from services.data_service import read_dataframe
from services.feature_review_service import (
    _format_threshold,
    _load_train_frame,
    _portfolio_stats,
    _prepare_stability_frames,
    _rule_hit_mask,
    get_chinese_name,
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


def _rule_display(rule: Dict[str, Any]) -> str:
    feat = rule.get("feature", "")
    op = rule.get("operator", ">")
    th = rule.get("threshold", 0)
    return f"{feat}{op}{_format_threshold(th)}"


def _enrich_rules(full_df: pd.DataFrame, rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for r in rules:
        mask = _rule_hit_mask(
            full_df, r["feature"], r.get("operator", ">"), float(r.get("threshold", 0))
        )
        stats = _portfolio_stats(full_df[mask])
        enriched.append({
            **r,
            "rule_display": _rule_display(r),
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
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
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

    for rule in ordered_rules:
        hit_all = _rule_hit_mask(
            df, rule["feature"], rule.get("operator", ">"), float(rule.get("threshold", 0))
        )
        hit_serial = hit_all & remaining

        th_stats = _portfolio_stats(df[hit_all])
        sh_stats = _portfolio_stats(df[hit_serial])

        before_mask = remaining
        before_stats = _portfolio_stats(df[before_mask])
        before_br = before_stats["bad_rate"]

        lift = sh_stats["bad_rate"] / orig_badrate if orig_badrate else 0.0
        lift_2 = sh_stats["bad_rate"] / before_br if before_br else 0.0
        dropped = lift < lift_min or sh_stats["count"] < min_hit
        if sh_stats["count"] < min_hit:
            drop_reason = f"串联命中 {sh_stats['count']} < {min_hit}"
        elif lift < lift_min:
            drop_reason = f"Lift {lift:.2f} < {lift_min}"
        else:
            drop_reason = ""

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
            "remaining": int((remaining & ~hit_serial).sum()) if not dropped else int(remaining.sum()),
            "dropped": dropped,
            "drop_reason": drop_reason,
        })

        if not dropped:
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
    enriched = _enrich_rules(full, rules)
    ordered = _sort_rules(enriched, sort_mode)

    train_orig_br = _portfolio_stats(tr)["bad_rate"]
    train_steps, train_sum = _serial_on_df(tr, ordered, train_orig_br, lift_min, min_hit)
    test_steps, test_sum = _serial_on_df(
        te if len(te) else None,
        ordered,
        _portfolio_stats(te)["bad_rate"] if len(te) else 0.0,
        lift_min,
        min_hit,
    )
    full_steps, full_sum = _serial_on_df(
        full, ordered, _portfolio_stats(full)["bad_rate"], lift_min, min_hit
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
            "status": status,
            "drop_reason": ts.get("drop_reason", ""),
            "train": {k: ts[k] for k in ts if k not in ("dropped", "drop_reason")},
            "test": {k: tes[k] for k in tes if k not in ("dropped", "drop_reason")},
            "full": {k: fs[k] for k in fs if k not in ("dropped", "drop_reason")},
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
            "规则名称": r["rule_display"],
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


def export_serial_excel(analysis: Dict[str, Any]) -> bytes:
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
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="汇总", index=False)

        for sheet_name, key in (
            ("Train串联", "train"),
            ("Test串联", "test"),
            ("全量串联", "full"),
        ):
            detail_df = pd.DataFrame(_segment_detail_rows(rules, key))
            detail_df.to_excel(writer, sheet_name=sheet_name, index=False)

    buf.seek(0)
    return buf.getvalue()
