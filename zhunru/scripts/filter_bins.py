import pandas as pd
import numpy as np

# -------------------------------------------------------------------
# 修改参数
# -------------------------------------------------------------------
input_file          = '分箱结果.xlsx'   # 分箱结果文件路径（binning.py 的输出）
output_file         = '筛选变量.xlsx'   # 输出文件路径
KS_THRESHOLD        = 0.2               # total_ks 阈值，保留 >= 此值的变量
BAD_RATE_MAX        = 0.3               # 最低箱 bad_rate 需 < 此值
MONOTONE_TOLERANCE  = 1                 # 允许几个箱打破单调性（0 = 严格单调）
# -------------------------------------------------------------------

def check_monotone(bad_rates, tolerance=1):
    """
    检查 bad_rate 序列是否单调（递增或递减）。
    tolerance: 允许打破单调性的箱数。
    返回: 'increasing' / 'decreasing' / None
    """
    diffs = pd.Series(bad_rates).diff().dropna()
    up_breaks   = (diffs < 0).sum()
    down_breaks = (diffs > 0).sum()

    if up_breaks <= tolerance:
        return 'increasing'
    if down_breaks <= tolerance:
        return 'decreasing'
    return None


def filter_bins(input_file, output_file, ks_threshold, bad_rate_max, tolerance):
    df = pd.read_excel(input_file)

    # 只用正常箱判断排序性，排除 special/MISSING 箱
    df_normal = df[~df['bin_label'].astype(str).str.startswith('special')].copy()

    results = []

    for feature, group in df_normal.groupby('feature', sort=False):
        group     = group.reset_index(drop=True)
        total_ks  = group['total_ks'].iloc[0]
        total_iv  = group['total_iv'].iloc[0]
        bad_rates = group['bad_rate'].tolist()

        # 条件1：KS 达标
        if total_ks < ks_threshold:
            continue

        # 条件2：最低箱 bad_rate < 阈值
        min_bad_rate = min(bad_rates)
        if min_bad_rate >= bad_rate_max:
            continue

        # 条件3：bad_rate 有排序性
        direction = check_monotone(bad_rates, tolerance)
        if direction is None:
            continue

        results.append({
            'feature':      feature,
            'total_ks':     round(total_ks, 4),
            'total_iv':     round(total_iv, 4),
            'bin_count':    len(bad_rates),
            'min_bad_rate': round(min_bad_rate, 4),
            'max_bad_rate': round(max(bad_rates), 4),
            'direction':    direction,
        })

    result_df = pd.DataFrame(results).sort_values('total_ks', ascending=False)

    # 输出两个 Sheet
    with pd.ExcelWriter(output_file, engine='xlsxwriter') as writer:

        # Sheet1：筛选变量汇总
        result_df.to_excel(writer, sheet_name='筛选变量', index=False)

        # Sheet2：筛选变量的完整分箱明细
        selected_features = result_df['feature'].tolist()
        detail_df = df[df['feature'].isin(selected_features)].copy()
        detail_df.to_excel(writer, sheet_name='分箱明细', index=False)

        # bad_rate 列加数据条
        workbook  = writer.book
        worksheet = writer.sheets['分箱明细']
        col_idx   = detail_df.columns.get_loc('bad_rate')
        worksheet.conditional_format(1, col_idx, len(detail_df), col_idx, {
            'type': 'data_bar', 'bar_color': '#5DADE2', 'bar_solid': True
        })

        # 按 feature 交替灰白背景
        fmt_gray  = workbook.add_format({'bg_color': '#EBEBEB'})
        fmt_white = workbook.add_format({'bg_color': '#FFFFFF'})
        prev_feature, color_flag = None, 0
        for i, f in enumerate(detail_df['feature'].tolist()):
            if f != prev_feature:
                color_flag = 1 - color_flag
                prev_feature = f
            worksheet.set_row(i + 1, None, fmt_gray if color_flag else fmt_white)

    print('=' * 50)
    print(f'共筛选出 {len(result_df)} 个变量（总变量数：{df_normal["feature"].nunique()}）')
    print(f'结果已保存至：{output_file}')
    print(result_df.to_string(index=False))


if __name__ == '__main__':
    filter_bins(input_file, output_file, KS_THRESHOLD, BAD_RATE_MAX, MONOTONE_TOLERANCE)
