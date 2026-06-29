# -*- coding: utf-8 -*-
"""\nserial_strategy.py — 串联策略分析\n流程：\n  1. 读取分箱结果，查找用户选定变量的阈值边界（修正版：支持单值箱 [1.0] -> > 0）\n  2. 形成拒绝规则，按用户选择排序（最高坏率 / 最大人数 / 用户指定顺序）\n  3. 串联分析，丢弃 lift < 1.10 或 <5人的弱规则\n  4. 输出准入分析结果 Excel（单一Sheet）\n"""

import pandas as pd
import numpy as np
import os, sys, re, warnings
warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════
#  核心函数
# ═══════════════════════════════════════════════

def read_binning_sheet(excel_path):
    """读取分箱结果中的 Train 分箱明细"""
    xls = pd.ExcelFile(excel_path)
    train_sheet = test_sheet = None
    for s in xls.sheet_names:
        if 'train' in s.lower() and '分箱' in s:
            train_sheet = s
        if 'test' in s.lower() and '分箱' in s:
            test_sheet = s
    if train_sheet is None:
        for s in xls.sheet_names:
            if 'train' in s.lower():
                train_sheet = s; break
    if train_sheet is None:
        raise ValueError(f"找不到 Train 分箱明细 Sheet: {xls.sheet_names}")
    train_df = pd.read_excel(excel_path, sheet_name=train_sheet)
    test_df = pd.read_excel(excel_path, sheet_name=test_sheet) if test_sheet else None
    train_df = normalize_cols(train_df)
    if test_df is not None:
        test_df = normalize_cols(test_df)
    return train_df, test_df


def normalize_cols(df):
    """标准化列名（头尾5%中英兼容）"""
    d = df.copy()
    d.rename(columns={
        '变量英文名': 'feature', '变量中文名': 'feature_cn',
        '#Obs': 'total', '#Bad': 'bad', '%Bad_Rate': 'bad_rate',
        'Bin': 'bin_label', 'Lift': 'lift', 'bin_ks': 'bin_ks',
        'total_ks': 'total_ks', 'IV(total)': 'iv_total',
    }, inplace=True, errors='ignore')
    d['bad_rate'] = pd.to_numeric(d['bad_rate'], errors='coerce')
    d['total'] = pd.to_numeric(d['total'], errors='coerce').fillna(0).astype(int)
    d['bad'] = pd.to_numeric(d['bad'], errors='coerce').fillna(0).astype(int)
    return d


def is_special_bin(bin_label):
    s = str(bin_label)
    return any(k in s for k in ['特殊', '空值', '缺失', 'nan', 'NA', 'null', 'special'])


def get_relevant_threshold(bin_label, direction):
    """智能提取阈值边界，处理 [1.0] 单值箱 vs (1.0, inf) 区间箱"""
    s = str(bin_label)
    nums = re.findall(r'-?\d+\.?\d*', s)
    floats = [float(n) for n in nums] if nums else []

    if direction == 'tail':
        # 尾箱高: 变量 > 阈值
        if s.startswith('[') and s.endswith(']') and len(floats) == 1:
            val = floats[0]
            if val == 1.0:
                return '> 0'
            return f'> {val - 1}' if val > 1 else '> 0'
        elif len(floats) >= 1 and ('inf' in s or floats[0] != float('-inf')):
            lo = floats[0]
            if lo == float('-inf') or s.startswith('(-inf'):
                return f'> {floats[-1]}' if len(floats) > 1 else None
            return f'> {lo}'
        elif len(floats) >= 1:
            return f'> {floats[0]}'
    else:
        # 头箱高: 变量 <= 阈值
        if s.startswith('(-inf') or ('(-inf' in s):
            if len(floats) >= 1:
                return f'<= {floats[-1]}'
        elif s.startswith('[') and s.endswith(']') and len(floats) == 1:
            return f'<= {floats[0]}'
        elif len(floats) >= 2:
            return f'<= {floats[1]}'
    return None


def find_variable_threshold(train_df, feature_name, threshold=0.6):
    """查找变量的拒绝阈值边界（修正版：支持单值箱 [1.0] -> > 0）"""
    ft = train_df[train_df['feature'] == feature_name].copy()
    if len(ft) == 0:
        return None, None, None, None

    normal = ft[~ft['bin_label'].apply(is_special_bin)].copy()
    if len(normal) == 0:
        return None, None, None, None

    def parse_min(b):
        nums = re.findall(r'-?\d+\.?\d*', str(b))
        if not nums:
            return 0
        v = float(nums[0])
        if v == float('-inf') or '(-inf' in str(b):
            return float(nums[-1]) if len(nums) > 1 else 0
        return v

    normal['_min'] = normal['bin_label'].apply(parse_min)
    normal = normal.sort_values('_min').reset_index(drop=True)

    head = normal.iloc[0]
    tail = normal.iloc[-1]
    head_br = head['bad_rate']
    tail_br = tail['bad_rate']

    candidates = []
    if head_br > threshold and head['total'] >= 20:
        candidates.append(('head', head, head_br))
    if tail_br > threshold and tail['total'] >= 20:
        candidates.append(('tail', tail, tail_br))
    if not candidates:
        return None, None, None, None

    best_dir, best_bin, best_br = max(candidates, key=lambda x: x[2])
    rule = get_relevant_threshold(best_bin['bin_label'], best_dir)
    if rule is None:
        return None, None, None, None

    rule_expr = f"{feature_name} {rule}"
    return rule_expr, best_br, best_bin['total'], best_dir


def run_serial_analysis(df, rules_with_info, orig_badrate, lift_min=1.10, min_hit=5):
    current = df.copy()
    gains = []

    for entry in rules_with_info:
        name, expr, br, th_total, direction = entry[:5]
        n_before = len(current)
        before_br = current['overdue_flag'].sum() / n_before if n_before > 0 else 0

        try:
            th = df.query(expr)
        except Exception as e:
            print(f"  [错误] {name}: 规则 '{expr}' {e}")
            continue
        th_n, th_bad = len(th), th['overdue_flag'].sum()
        th_br = th_bad / th_n if th_n > 0 else 0

        try:
            hit = current.query(expr)
        except Exception as e:
            print(f"  [错误] {name}: 串联 '{expr}' {e}")
            continue
        passed = current.drop(hit.index)
        sh_n, sh_bad = len(hit), hit['overdue_flag'].sum()
        sh_br = sh_bad / sh_n if sh_n > 0 else 0

        lift = sh_br / orig_badrate if orig_badrate > 0 else 0
        lift_2 = sh_br / before_br if before_br > 0 else 0

        if lift < lift_min or sh_n < min_hit:
            print(f"  [丢弃] {name:50s}  {th_n:4d}人({th_br:.2%}) -> {sh_n:3d}人({sh_br:.2%}) lift={lift:.3f}")
            continue

        gains.append({
            '上线规则': expr, '总样本': len(df),
            'lift': round(lift, 4), 'badrate': f"{th_br:.2%}",
            '规则命中（全量）': th_n, '串联命中人数': sh_n,
            '拒绝率': f"{sh_n/len(df):.2%}", '串联坏客数': sh_bad,
            '串联坏比率': f"{sh_br:.2%}", 'lift_2': round(lift_2, 4),
            '剩余样本数': len(passed),
        })
        current = passed
        print(f"  [应用] {name:50s}  {th_n:4d}人({th_br:.2%}) -> {sh_n:3d}人({sh_br:.2%}) lift={lift:.3f}")

    return gains, current


def write_output(output_path, summary_df, gains_df):
    with pd.ExcelWriter(output_path, engine='xlsxwriter') as writer:
        summary_df.to_excel(writer, sheet_name='串联分析', index=False, startrow=0)
        if len(gains_df) > 0:
            gains_df.to_excel(writer, sheet_name='串联分析', index=False, startrow=len(summary_df) + 3)
    print(f"\n输出: {output_path}")


def main():
    print('=' * 60)
    print('   serial-strategy — 串联策略分析')
    print('=' * 60)

    input_file = input("\n请输入分箱结果 Excel 路径: ").strip().strip("'\"")
    if not os.path.exists(input_file):
        print(f"[错误] 文件不存在: {input_file}"); return

    print("\n正在读取分箱结果...")
    train_df, test_df = read_binning_sheet(input_file)

    raw_path = input("\n请输入原始数据路径（含标签列）: ").strip().strip("'\"")
    if not os.path.exists(raw_path):
        print(f"[错误] 不存在: {raw_path}"); return

    ext = os.path.splitext(raw_path)[1].lower()
    if ext == '.csv':
        df = pd.read_csv(raw_path, encoding='gbk')
    else:
        df = pd.read_excel(raw_path)

    label_candidates = [c for c in df.columns if any(k in c.lower() for k in ['overdue', 'fpd', 'flag', 'label', 'target'])]
    print(f"\n检测到的可能标签列: {label_candidates}")
    label = input("请输入标签列名: ").strip()
    if label not in df.columns:
        print(f"[错误] 标签列不存在"); return

    df['overdue_flag'] = df[label]
    df = df[df['overdue_flag'].isin([0, 1])].copy()
    col_map = {c: c.replace('#', '_') for c in df.columns if '#' in c}
    df = df.rename(columns=col_map)

    n_total = len(df)
    total_bad = df['overdue_flag'].sum()
    orig_badrate = total_bad / n_total
    print(f"\n数据量: {n_total}, 大盘坏率: {orig_badrate:.2%}")

    # 排序方式
    sort_choice = input("\n串联排序方式? (1=按人数 / 2=按坏率 / 3=按指定顺序) [默认1]: ").strip()
    sort_by_population = (sort_choice == '1' or sort_choice == '')
    sort_by_badrate = (sort_choice == '2')
    sort_by_order = (sort_choice == '3')
        sort_by_population = None  # 保留用户指定顺序
        print("  按用户指定顺序（不重排）")
    else:
        sort_by_population = (sort_choice != '2')

    # 变量选择
    print("\n--- 变量选择 ---")
    selected_raw = input("请输入选定的变量列表（逗号分隔）: ").strip()
    selected_features = [s.strip() for s in selected_raw.replace('，', ',').split(',') if s.strip()]
    if not selected_features:
        print("[错误] 未选择变量"); return

    threshold_input = input("请输入原拒绝阈值 (如 0.6 = 60%): ").strip()
    threshold = float(threshold_input)

    # 查阈值
    print("\n--- 查找阈值边界 ---")
    rules_info = []
    for feat in selected_features:
        feat_clean = feat.replace('#', '_')
        rule_expr, br, total_hit, direction = find_variable_threshold(train_df, feat_clean, threshold)
        if rule_expr is None:
            print(f"  [跳过] {feat:45s}  未找到高于阈值的边界")
            continue
        dir_label = '尾箱高' if direction == 'tail' else '头箱高'
        print(f"  [找到] {feat:45s}  {dir_label}  br={br:.2%}  规则: {rule_expr}  训练命中={total_hit}人")
        rules_info.append((feat_clean, rule_expr, br, total_hit, direction))

    if not rules_info:
        print("[错误] 无可用规则"); return

    # 计算全量命中人数
    hit_info = []
    for name, expr, br, th_total, direction in rules_info:
        try:
            hit = df.query(expr)
            pop = len(hit)
            pop_br = hit['overdue_flag'].mean() if pop > 0 else 0
        except:
            pop, pop_br = 0, 0
        hit_info.append((name, expr, br, th_total, direction, pop, pop_br))

    # 排序（sort_by_population=None 时保留原始顺序，不重排）
    if sort_by_population is None:
    # 排序
    if sort_by_order:
        # 按用户指定顺序，不重排
        print(f"\n排序（按指定顺序）:")
        pass
    elif sort_by_population:
        hit_info.sort(key=lambda x: -x[5])
        print(f"\n排序（按人数降序）:")
    else:
        hit_info.sort(key=lambda x: -x[2])
        print(f"\n排序（按坏率降序）:")

    for i, (name, expr, br, th_total, direction, pop, pop_br) in enumerate(hit_info, 1):
        print(f"  {i:2d}. {name:45s}  全量{pop:>5d}人  br={pop_br:.2%}  {expr}")

    # 串联
    print(f"\n--- 串联分析 ---")
    gains, remaining = run_serial_analysis(df, hit_info, orig_badrate)

    if not gains:
        print("\n[结果] 所有规则被丢弃")
        return

    final_n, final_bad = len(remaining), remaining['overdue_flag'].sum()
    final_br = final_bad / final_n if final_n > 0 else 0
    pass_rate = final_n / n_total

    summary_df = pd.DataFrame([
        ['总单数', n_total, final_n],
        ['逾期单数', int(total_bad), int(final_bad)],
        ['件数逾期率', f"{orig_badrate:.2%}", f"{final_br:.2%}"],
        ['通过率', "100.00%", f"{pass_rate:.2%}"],
        ['金额逾期率', f"{orig_badrate:.2%}", f"{final_br:.2%}"],
    ], columns=['指标', '现数据', '调整后'])

    gains_df = pd.DataFrame(gains)

    output_path = input("\n输出路径 (默认: 串联分析结果.xlsx): ").strip().strip("'\"")
    if not output_path:
        output_path = os.path.join(os.path.dirname(input_file), '串联分析结果.xlsx')

    write_output(output_path, summary_df, gains_df)

    print(f"\n{'='*60}")
    print(f"最终: {n_total} -> {final_n}, 坏率 {orig_badrate:.2%} -> {final_br:.2%}, 通过率 {pass_rate:.2%}")
    print(f"\n{summary_df.to_string(index=False)}")
    if len(gains_df) > 0:
        print(f"\n{gains_df.to_string(index=False).replace('上线：', '上线:')}")
    print("\n完成!")


if __name__ == '__main__':
    main()
