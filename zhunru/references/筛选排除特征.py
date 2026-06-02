#!/usr/bin/env python3
"""
筛选排除规则候选特征。
条件（三个同时满足）：
1. 大致单调（tolerance=2）
2. 某箱 bad_rate > 大盘 + 8pp（≥10条）
3. 最低箱 bad_rate < 大盘 - 5pp

输出 3-sheet Excel：
  - 全部分箱：原样
  - 候选特征：特征, 选择原因, 最高bad_rate
  - 分箱明细：完整数据，交替灰白，红色数据条

使用前修改：
  - input_file -> 分箱结果.xlsx 路径
  - output_file -> 输出路径
  - OVERALL -> 大盘坏率（用 df[label].mean() 计算）
"""
import pandas as pd
import numpy as np

input_file = r"C:\Users\6\Desktop\test_file\分箱结果.xlsx"
output_file = r"C:\Users\6\Desktop\test_file\筛选变量.xlsx"
OVERALL = 0.5707  # 大盘坏率
MIN_BINS = 3
MIN_OBS = 10
HIGH_TH = 0.08   # 最高箱 > 大盘 + 8pp
LOW_TH = 0.05    # 最低箱 < 大盘 - 5pp

# 排除的无关变量
EXCLUDE = ['overdue_flag2', 'product', 'risk_over_days']

def is_approx_monotone(br, tolerance=2):
    d = pd.Series(br).diff().dropna()
    up = (d < 0).sum()
    down = (d > 0).sum()
    if up <= tolerance: return '递增', up
    if down <= tolerance: return '递减', down
    return None, min(up, down)

# 读取
df = pd.read_excel(input_file)
df = df[~df['feature'].isin(EXCLUDE)].copy()
df_normal = df[~df['bin_label'].astype(str).str.startswith('special')].copy()

results = []
for feature, group in df_normal.groupby('feature', sort=False):
    group = group.reset_index(drop=True)
    br = group['bad_rate'].tolist()
    tt = group['total'].tolist()
    if len(br) < MIN_BINS: continue
    
    direction, _ = is_approx_monotone(br)
    if not direction: continue
    
    max_br = max(br)
    max_idx = br.index(max_br)
    max_tt = tt[max_idx]
    min_br = min(br)
    high_diff = max_br - OVERALL
    low_diff = OVERALL - min_br
    
    if high_diff < HIGH_TH or max_tt < MIN_OBS or low_diff < LOW_TH:
        continue
    
    pos = "头" if max_idx == 0 else ("尾" if max_idx == len(br)-1 else f"第{max_idx+1}")
    results.append({
        'feature': feature, 'direction': direction, 'bins': len(br),
        'max_bad_rate': max_br, 'min_bad_rate': min_br,
        'why': f"单调{direction}, {pos}箱={max_br:.2%}>大盘+{high_diff:.1%}pp, 极差={max_br-min_br:.1%}pp",
    })

result_df = pd.DataFrame(results).sort_values('max_bad_rate', ascending=False)
detail_features = result_df['feature'].tolist()

# 输出
sheet2 = result_df[['feature', 'why', 'max_bad_rate']].copy()
sheet2.columns = ['特征', '选择原因', '最高bad_rate']
sheet2['最高bad_rate'] = sheet2['最高bad_rate'].apply(lambda x: f"{x:.2%}")

with pd.ExcelWriter(output_file, engine='xlsxwriter') as writer:
    # Sheet1: 全部分箱
    df.to_excel(writer, sheet_name='全部分箱', index=False)
    ws1 = writer.sheets['全部分箱']
    ws1.conditional_format(1, df.columns.get_loc('bad_rate'), len(df), df.columns.get_loc('bad_rate'), {
        'type': 'data_bar', 'bar_color': '#5DADE2', 'bar_solid': True
    })
    # Sheet2: 候选特征
    sheet2.to_excel(writer, sheet_name='候选特征', index=False)
    ws2 = writer.sheets['候选特征']
    ws2.set_column('A:A', 55); ws2.set_column('B:B', 85); ws2.set_column('C:C', 15)
    # Sheet3: 分箱明细
    detail_df = df[df['feature'].isin(detail_features)].copy()
    detail_df.to_excel(writer, sheet_name='分箱明细', index=False)
    ws3 = writer.sheets['分箱明细']
    ws3.conditional_format(1, detail_df.columns.get_loc('bad_rate'), len(detail_df),
        detail_df.columns.get_loc('bad_rate'), {
            'type': 'data_bar', 'bar_color': '#E74C3C', 'bar_solid': True
        })
    fmt_gray = writer.book.add_format({'bg_color': '#F2F2F2'})
    fmt_white = writer.book.add_format({'bg_color': '#FFFFFF'})
    prev_f, cf = None, 0
    for i, f in enumerate(detail_df['feature'].tolist()):
        if f != prev_f: cf = 1 - cf; prev_f = f
        ws3.set_row(i + 1, None, fmt_gray if cf else fmt_white)

print(f"大盘坏率: {OVERALL:.2%}")
print(f"候选特征: {len(result_df)} 个")
for _, r in result_df.iterrows():
    print(f"  {r['feature']:55s} 最高={r['max_bad_rate']:.2%} {r['why']}")
print(f"\n输出: {output_file}")
