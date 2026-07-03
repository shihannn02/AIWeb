#!/usr/bin/env python3
"""
规则串联评估报告（单一Sheet格式）：
- 按最高坏率排序依次拒绝高风险客群
- 规则命中 ≠ 命中人数（全量 vs 串联）
- badrate ≠ 坏比率(串行)（规则纯度 vs 串联实际）
- lift < 1.10 或 <5人 的规则自动丢弃
- 输出单一Sheet含汇总表 + 增益效果明细表

使用前修改：
  1. rule_defs: 规则列表（expr, name）
  2. data路径
"""
import pandas as pd
import numpy as np

# ═══ 修改参数 ═══
data_path = r"data.xlsx"
OUTPUT_FILE = r"串联分析结果.xlsx"

# 规则按最高坏率排序
rule_defs = [
    ('ios_score <= 463', 'ios_score <= 463'),
    ('ios评分卡250107_ios_score <= 611', 'ios评分卡250107#ios_score <= 611'),
]

LIFT_MIN = 1.10      # 低于此值丢弃
MIN_HIT = 5          # 少于此人丢弃
# ═══════════════

# 读取数据
df = pd.read_excel(data_path)
col_map = {c: c.replace('#', '_') for c in df.columns if '#' in c}
df = df.rename(columns=col_map)
df['overdue_flag'] = df['overdue_flag2']
df = df[df['overdue_flag'].isin([0, 1])]

orig_total = len(df)
orig_bad = df['overdue_flag'].sum()
orig_badrate = orig_bad / orig_total

print(f"大盘坏率: {orig_badrate:.2%} ({orig_bad}/{orig_total})")

current = df.copy()
gains = []

for expr, name in rule_defs:
    n_before = len(current)
    before_br = current['overdue_flag'].sum() / n_before if n_before > 0 else 0

    # 全量命中
    th = df.query(expr)
    th_n, th_bad = len(th), th['overdue_flag'].sum()
    th_br = th_bad / th_n if th_n > 0 else 0

    # 串联命中
    hit = current.query(expr)
    passed = current.drop(hit.index)
    sh_n, sh_bad = len(hit), hit['overdue_flag'].sum()
    sh_br = sh_bad / sh_n if sh_n > 0 else 0

    lift = sh_br / orig_badrate if orig_badrate > 0 else 0
    lift_2 = sh_br / before_br if before_br > 0 else 0

    if lift < LIFT_MIN or sh_n < MIN_HIT:
        print(f"  [丢弃] {name:55s} 全量{th_n:2d}人({th_br:.2%}) → 串联{sh_n:2d}人({sh_br:.2%}) lift={lift:.3f}")
        continue

    gains.append({
        '上线：': name, '总样本': orig_total,
        'lift': round(lift, 4), 'badrate': f"{th_br:.2%}",
        '规则命中': th_n, '命中人数': sh_n,
        '拒绝率': f"{sh_n/orig_total:.2%}", '坏客数(命中)': sh_bad,
        '坏比率(串行)': f"{sh_br:.2%}", 'lift_2': round(lift_2, 4),
        '剩余样本数': len(passed),
    })
    current = passed
    print(f"  [应用] {name:55s} 全量{th_n:2d}人({th_br:.2%}) → 串联{sh_n:2d}人({sh_br:.2%}) lift={lift:.3f}")

final_n, final_bad = len(current), current['overdue_flag'].sum()
final_br = final_bad / final_n if final_n > 0 else 0
pass_rate = final_n / orig_total

# 输出
gains_df = pd.DataFrame(gains)
summary = pd.DataFrame([
    ['总单数', orig_total, final_n],
    ['逾期单数', orig_bad, final_bad],
    ['件数逾期率', f"{orig_badrate:.2%}", f"{final_br:.2%}"],
    ['通过率', "100.00%", f"{pass_rate:.2%}"],
    ['金额逾期率', f"{orig_badrate:.2%}", f"{final_br:.2%}"],
], columns=['指标', '现数据', '调整后'])

with pd.ExcelWriter(OUTPUT_FILE, engine='xlsxwriter') as writer:
    summary.to_excel(writer, sheet_name='串联分析', index=False, startrow=0)
    gains_df.to_excel(writer, sheet_name='串联分析', index=False, startrow=len(summary)+3)

print(f"\n最终: {orig_total} → {final_n}, 坏率 {orig_badrate:.2%} → {final_br:.2%}, 通过率 {pass_rate:.2%}")
print(f"输出: {OUTPUT_FILE}")
