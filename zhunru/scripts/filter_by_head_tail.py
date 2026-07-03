#!/usr/bin/env python3
"""
准入规则筛选：从分箱结果中提取头/尾箱bad_rate明显低于大盘的变量。
输出两个 Sheet：
  - 筛选变量：符合条件变量汇总，按头箱bad_rate升序
  - 分箱明细：这些变量的完整分箱表（21列全字段）

使用前修改顶部参数：
  input_file, output_file, OVERALL_BAD_RATE, BAD_RATE_THRESHOLD
"""
import pandas as pd
import numpy as np

# ═══ 修改参数 ═══
input_file = r"C:\Users\6\Desktop\test_file\分箱结果.xlsx"
output_file = r"C:\Users\6\Desktop\筛选变量.xlsx"
OVERALL_BAD_RATE = 0.5707   # 从 df[label].mean() 计算
BAD_RATE_THRESHOLD = 0.08   # 头/尾箱低于大盘至少N个百分点
# ═══════════════

MIN_BINS = 3
MIN_OBS = 10

def check_monotone(br, tol=2):
    d = pd.Series(br).diff().dropna()
    if (d < 0).sum() <= tol: return 'increasing'
    if (d > 0).sum() <= tol: return 'decreasing'
    return None

def main():
    df = pd.read_excel(input_file)
    df = df[~df['feature'].isin(['overdue_flag2', 'product'])].copy()
    df_normal = df[~df['bin_label'].astype(str).str.startswith('special')].copy()
    results = []; detail_features = []

    for feature, group in df_normal.groupby('feature', sort=False):
        group = group.reset_index(drop=True)
        bad_rates = group['bad_rate'].tolist()
        totals = group['total'].tolist()
        if len(bad_rates) < MIN_BINS: continue
        head_bad = bad_rates[0]; tail_bad = bad_rates[-1]
        direction = check_monotone(bad_rates)
        head_good = (OVERALL_BAD_RATE - head_bad) >= BAD_RATE_THRESHOLD
        tail_good = (OVERALL_BAD_RATE - tail_bad) >= BAD_RATE_THRESHOLD

        reason = ""
        if direction == 'increasing' and head_good and totals[0] >= MIN_OBS:
            reason = f"头箱={head_bad:.2%}<大盘(递增), {totals[0]}条"
        elif direction == 'decreasing' and tail_good and totals[-1] >= MIN_OBS:
            reason = f"尾箱={tail_bad:.2%}<大盘(递减), {totals[-1]}条"
        elif head_good and totals[0] >= MIN_OBS:
            reason = f"头箱={head_bad:.2%}<大盘, {totals[0]}条"
        elif tail_good and totals[-1] >= MIN_OBS:
            reason = f"尾箱={tail_bad:.2%}<大盘, {totals[-1]}条"
        else:
            continue

        results.append({
            'feature': feature, 'bins': len(bad_rates),
            'direction': str(direction) if direction else 'non-monotone',
            'head_bad_rate': round(head_bad, 4),
            'tail_bad_rate': round(tail_bad, 4),
            'min_bad_rate': round(min(bad_rates), 4),
            'select_reason': reason,
        })
        detail_features.append(feature)

    result_df = pd.DataFrame(results).sort_values('head_bad_rate', ascending=True)
    with pd.ExcelWriter(output_file, engine='xlsxwriter') as writer:
        result_df.to_excel(writer, sheet_name='筛选变量', index=False)
        detail_df = df[df['feature'].isin(detail_features)].copy()
        detail_df.to_excel(writer, sheet_name='分箱明细', index=False)
        workbook = writer.book; ws = writer.sheets['分箱明细']
        col_idx = detail_df.columns.get_loc('bad_rate')
        ws.conditional_format(1, col_idx, len(detail_df), col_idx,
            {'type': 'data_bar', 'bar_color': '#5DADE2', 'bar_solid': True})
        fmt_gray = workbook.add_format({'bg_color': '#EBEBEB'})
        fmt_white = workbook.add_format({'bg_color': '#FFFFFF'})
        prev, cf = None, 0
        for i, f in enumerate(detail_df['feature'].tolist()):
            if f != prev: cf = 1 - cf; prev = f
            ws.set_row(i + 1, None, fmt_gray if cf else fmt_white)

    print(f"大盘坏率: {OVERALL_BAD_RATE:.2%}")
    print(f"阈值: 头/尾 < {(OVERALL_BAD_RATE-BAD_RATE_THRESHOLD):.2%}, MIN_BINS={MIN_BINS}, MIN_OBS={MIN_OBS}")
    print(f"通过: {len(detail_features)} / {df_normal['feature'].nunique()}")
    for _, r in result_df.iterrows():
        print(f"  {r['feature']:55s} head={r['head_bad_rate']:.2%} tail={r['tail_bad_rate']:.2%} | {r['select_reason']}")
    print(f"\n输出: {output_file}")

if __name__ == '__main__':
    main()
