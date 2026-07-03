"""
规则串联/并联评估脚本
用法：修改顶部的 rules / rule_names / 数据路径 后直接运行
输出：Excel 含并联分析、串联分析、汇总统计三个 Sheet
"""
import pandas as pd
import numpy as np
from collections import Counter

pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)
pd.set_option('display.max_colwidth', None)

# ===== 数据读取（按需修改路径）=====
df1 = pd.read_excel(r'C:\Users\6\Desktop\test_file\toufang_变量_放款_非白.xlsx')

# 列名替换（# 替换为 _，避免pandas query解析报错）
col_map = {c: c.replace('#', '_') for c in df1.columns if '#' in c}
df1 = df1.rename(columns=col_map)

# 标签处理（按需修改列名）
df1['overdue_flag'] = df1['overdue_flag2']
df1['overdue_flag'] = df1['overdue_flag'].replace(-1, 0)
df1 = df1[df1['overdue_flag'].isin([0, 1])]

# 类型转换
for i in df1.columns[df1.dtypes == 'object']:
    df1[i] = df1[i].astype(str).str.strip()
    df1.loc[df1[i].isin(['-999', '-9999', '-999999']), i] = np.nan
    try:
        df1[i] = df1[i].astype('float64')
    except:
        df1.drop(columns=[i], inplace=True)

print(f"数据量: {len(df1)}")

# ===== 在这里修改规则 =====
# 使用替换后的列名（# → _）
rules = [
    '(ios_new_f_lgbm_fpd7_wsh_v1 <= 0.65)',
    '(ios评分卡0916_ios_score_0916 > 376)',
    '(ios评分卡250107_ios_score > 651)',
]

rule_names = [
    'ios_new_f_lgbm_fpd7_wsh_v1 <= 0.65',
    'ios评分卡0916#ios_score_0916 > 376',
    'ios评分卡250107#ios_score > 651',
]

# ===== 函数 =====
def analyze_parallel(rules, names, df):
    total = len(df)
    total_money = df['money'].sum()
    results = []
    for expr, name in zip(rules, names):
        try:
            hit = df.query(expr)
            counts = hit['overdue_flag'].value_counts().reindex([1, 0], fill_value=0)
            valid = counts[1] + counts[0]
            bad_money = hit[hit['overdue_flag'] == 1]['money'].sum()
            hit_money = hit['money'].sum()
            results.append({
                "规则名称": name, "总样本量": total,
                "命中人数": len(hit), "命中坏客数": counts[1], "命中好客数": counts[0],
                "坏占比": f"{counts[1] / valid:.2%}" if valid else "0%",
                "金额逾期率": f"{bad_money / hit_money:.2%}" if hit_money else "0%",
                "命中率": f"{len(hit) / total:.2%}",
            })
        except Exception as e:
            results.append({"规则名称": name, "错误": str(e)})
    return pd.DataFrame(results)


def analyze_series(rules, names, df, order=None):
    # 准入规则：命中的保留，未命中的拒绝
    order = order or list(range(len(rules)))
    current = df.copy()
    total = len(df)
    results = []
    for seq, idx in enumerate(order, 1):
        expr, name = rules[idx], names[idx]
        curr_count = len(current)
        try:
            passed = current.query(expr)
            hit = current.drop(passed.index)
            counts = hit['overdue_flag'].value_counts().reindex([1, 0], fill_value=0)
            valid = counts[1] + counts[0]
            bad_money = hit[hit['overdue_flag'] == 1]['money'].sum()
            hit_money = hit['money'].sum()
            results.append({
                "当前顺序": seq, "规则名称": name,
                "本环节样本量": curr_count, "拒绝人数": len(hit),
                "拒绝坏客数": counts[1], "拒绝好客数": counts[0],
                "坏占比": f"{counts[1] / valid:.2%}" if valid else "0%",
                "金额逾期率": f"{bad_money / hit_money:.2%}" if hit_money else "0%",
                "当前拒绝率": f"{len(hit) / curr_count:.2%}", "累计剩余": len(passed),
            })
            current = passed
        except Exception as e:
            results.append({"当前顺序": seq, "规则名称": name, "错误": str(e)})
    return pd.DataFrame(results), current


def calc_summary(df_original, df_remaining):
    total = len(df_original)
    total_bad_orig = (df_original['overdue_flag'] == 1).sum()
    total_remain = len(df_remaining)
    total_bad_remain = (df_remaining['overdue_flag'] == 1).sum()
    return pd.DataFrame({
        '指标': ['总单数', '逾期单数', '件数逾期率', '通过率'],
        '原始数据': [total, total_bad_orig,
            f"{total_bad_orig / total:.2%}" if total else "0%", "100.00%"],
        '规则后': [total_remain, total_bad_remain,
            f"{total_bad_remain / total_remain:.2%}" if total_remain else "0%",
            f"{total_remain / total:.2%}" if total else "0%"]
    })


# ===== 执行 =====
print("\n=== 并联分析 ===")
parallel_result = analyze_parallel(rules, rule_names, df1)
print(parallel_result.to_string(index=False))

print("\n=== 串联分析 ===")
series_result, remaining_data = analyze_series(rules, rule_names, df1)
print(series_result.to_string(index=False))

summary_result = calc_summary(df1, remaining_data)
print("\n=== 汇总统计 ===")
print(summary_result.to_string(index=False))

output_path = r'串联分析结果.xlsx'
with pd.ExcelWriter(output_path) as writer:
    parallel_result.to_excel(writer, sheet_name='并联分析', index=False)
    series_result.to_excel(writer, sheet_name='串联分析', index=False)
    summary_result.to_excel(writer, sheet_name='汇总统计', index=False)

print(f"\n结果已保存: {output_path}")
print(f"原始数据: {len(df1)}")
print(f"通过规则: {len(remaining_data)}")
print(f"累计拒绝: {len(df1) - len(remaining_data)}")
