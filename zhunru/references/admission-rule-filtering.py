"""
准入规则变量筛选脚本
用于从分箱结果中筛选可用于准入规则的变量。
筛选条件：
  1. 每箱观测数 >= 总样本 * MIN_OBS_PCT
  2. 正常箱数 >= MIN_BINS
  3. bad_rate 单调递增或递减（可容忍 tolerance 次打破）
  4. 最低风险箱（头箱/尾箱）bad_rate < 大盘坏率
输出：
  - Sheet1 筛选变量：通过条件的变量汇总
  - Sheet2 分箱明细：通过变量的完整分箱表
  - Sheet3 相关变量组：按基名分组的同类变量一览
"""

import pandas as pd
import numpy as np
import re

# ======================== 参数设置 ========================
input_file = "分箱结果.xlsx"           # binning.py 的输出
output_file = "筛选变量.xlsx"           # 本脚本的输出

OVERALL_BAD_RATE = 0.57               # 大盘坏率，运行前先计算 df[label].mean()
MONOTONE_TOLERANCE = 2                # 单调性容忍度（小样本放宽到 2，严格单调设为 0）
MIN_OBS_PCT = 0.05                    # 每箱最少样本占比（5%）
MIN_BINS = 3                          # 最少分箱数
# ==========================================================


def get_base_name(col):
    """提取变量基名（去掉时间窗口和后缀），用于同类变量分组"""
    col = re.sub(r'_(avg|cv|entropy|std|pct|new|old|max|min|acceleration)$', '', col)
    col = re.sub(r'_(avg|cv|entropy|std|pct)_\d+[dhm]$', '', col)
    col = re.sub(r'_\d+[dhm]$', '', col)
    col = re.sub(r'_(new|old|risk)_\d+[dhm]$', '', col)
    return col


def check_monotone(bad_rates, tolerance=2):
    """检查 bad_rate 是否单调。返回 'increasing' / 'decreasing' / None"""
    diffs = pd.Series(bad_rates).diff().dropna()
    up_breaks = (diffs < 0).sum()   # 期望递增却下降
    down_breaks = (diffs > 0).sum() # 期望递减却上升
    if up_breaks <= tolerance:
        return "increasing"
    if down_breaks <= tolerance:
        return "decreasing"
    return None


def filter_bins(input_file, output_file, overall_bad_rate, tolerance, min_obs_pct, min_bins):
    df = pd.read_excel(input_file)
    df_normal = df[~df["bin_label"].astype(str).str.startswith("special")].copy()

    # 建立基名 -> 成员映射
    feature_base_map = {}
    for f in df_normal["feature"].unique():
        base = get_base_name(f)
        feature_base_map.setdefault(base, []).append(f)

    results = []
    passed_features = set()

    for feature, group in df_normal.groupby("feature", sort=False):
        group = group.reset_index(drop=True)
        feature_total = group["total"].sum()
        min_obs = feature_total * min_obs_pct
        bin_totals = group["total"].tolist()
        bad_rates = group["bad_rate"].tolist()
        bin_count = len(bad_rates)

        # 条件1：分箱数不能太少
        if bin_count < min_bins:
            continue
        # 条件2：每箱观测数不能太少
        if any(t < min_obs for t in bin_totals):
            continue
        # 条件3：bad_rate 单调性
        direction = check_monotone(bad_rates, tolerance)
        if direction is None:
            continue
        # 条件4：最低风险箱 bad_rate < 大盘坏率
        min_risk = bad_rates[0] if direction == "increasing" else bad_rates[-1]
        if min_risk >= overall_bad_rate:
            continue

        passed_features.add(feature)
        results.append({
            "feature": feature,
            "base_name": get_base_name(feature),
            "total_ks": round(group["total_ks"].iloc[0], 4),
            "total_iv": round(group["total_iv"].iloc[0], 4),
            "bin_count": bin_count,
            "min_bin_obs": int(min(bin_totals)),
            "min_risk_bad_rate": round(min_risk, 4),
            "direction": direction,
        })

    result_df = pd.DataFrame(results).sort_values("min_risk_bad_rate", ascending=True)

    # 相关变量组：任一变量通过筛选，整组都收集
    related_groups = {}
    for base, members in feature_base_map.items():
        passed_in_group = [m for m in members if m in passed_features]
        if passed_in_group:
            related_groups[base] = {"passed": passed_in_group, "all": members}

    # 写入 Excel
    with pd.ExcelWriter(output_file, engine="xlsxwriter") as writer:
        result_df.to_excel(writer, sheet_name="筛选变量", index=False)
        detail_df = df[df["feature"].isin(passed_features)].copy()
        detail_df.to_excel(writer, sheet_name="分箱明细", index=False)

        group_rows = []
        for base, info in sorted(related_groups.items(), key=lambda x: -len(x[1]["all"])):
            group_rows.append({
                "基名": base,
                "通过数": len(info["passed"]),
                "全部相关变量数": len(info["all"]),
                "通过变量": ", ".join(info["passed"]),
                "全部变量": ", ".join(info["all"]),
            })
        pd.DataFrame(group_rows).to_excel(writer, sheet_name="相关变量组", index=False)

        # 格式化
        workbook = writer.book
        ws = writer.sheets["分箱明细"]
        ci = detail_df.columns.get_loc("bad_rate")
        ws.conditional_format(1, ci, len(detail_df), ci,
                              {"type": "data_bar", "bar_color": "#5DADE2", "bar_solid": True})
        fmt_g, fmt_w = workbook.add_format({"bg_color": "#EBEBEB"}), workbook.add_format({"bg_color": "#FFFFFF"})
        pf, fl = None, 0
        for i, f in enumerate(detail_df["feature"].tolist()):
            if f != pf:
                fl = 1 - fl
                pf = f
            ws.set_row(i + 1, None, fmt_g if fl else fmt_w)

    print(f"大盘坏率: {overall_bad_rate:.2%}")
    print(f"每箱最低 obs: ≥{int(min_obs_pct*100)}% | 最少箱数: {min_bins}")
    print(f"通过筛选: {len(result_df)} 个变量, {len(related_groups)} 个变量组")
    if len(result_df) > 0:
        cols = ["feature", "bin_count", "min_bin_obs", "min_risk_bad_rate", "direction", "total_ks"]
        print(result_df[cols].to_string(index=False))


if __name__ == "__main__":
    filter_bins(input_file, output_file, OVERALL_BAD_RATE, MONOTONE_TOLERANCE, MIN_OBS_PCT, MIN_BINS)
