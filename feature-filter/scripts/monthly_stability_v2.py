# -*- coding: utf-8 -*-
"""
月度稳定性分析（矩阵版 v3）— 列=特征，行=月份
Sheet6: Train月度稳定性
Sheet7: Test月度稳定性
每个筛选变量的逾期率/金额逾期率，带 data bar
"""
import pandas as pd, numpy as np, re, warnings, os, math, shutil
warnings.filterwarnings("ignore")

# ===== 用户修改以下配置 =====
BINNING_PATH = r"C:\Users\6\Desktop\qiongdan数据2\分箱结果_头尾5.xlsx"
RAW_PATH = r"C:\Users\6\Desktop\qiongdan数据2\失联用户及黑名单变量样本.csv"
LABEL = "target3"
CUTOFF = "2026-05-01"  # 匹配原始分箱的切分
OUTPUT_PATH = None      # None=追加到原文件
# ===========================

if OUTPUT_PATH is None:
    OUTPUT_PATH = BINNING_PATH

# ── 读取原始数据 ──
print("读取原始数据...")
df_raw = pd.read_csv(RAW_PATH, encoding='gbk')
df_raw['overdue_flag'] = df_raw[LABEL]
df_raw = df_raw[df_raw['overdue_flag'].isin([0, 1])].copy()
# 确保 money 列存在
if 'money' not in df_raw.columns:
    df_raw['money'] = 1000.0

# 解析月份（从 apply_date）
df_raw['_month'] = df_raw['apply_date'].str[:7]  # "2026-03"

train_df = df_raw[df_raw['apply_date'] < CUTOFF].copy()
test_df = df_raw[df_raw['apply_date'] >= CUTOFF].copy()
t_months = sorted(train_df['_month'].unique())
te_months = sorted(test_df['_month'].unique())
print(f"Train: {len(train_df)} 行, 月份: {t_months}")
print(f"Test:  {len(test_df)} 行, 月份: {te_months}")

# ── 读取分箱结果 ──
print("读取分箱结果...")
candidates = pd.read_excel(BINNING_PATH, sheet_name='候选变量汇总', engine='openpyxl')
train_detail = pd.read_excel(BINNING_PATH, sheet_name='Train分箱明细', engine='openpyxl')
test_detail = pd.read_excel(BINNING_PATH, sheet_name='Test分箱明细', engine='openpyxl')

# 标准化列名
train_detail.rename(columns={'变量英文名': 'feature', '#Obs': 'total', '#Bad': 'bad',
                              '%Bad_Rate': 'bad_rate', 'Bin': 'bin_label'}, inplace=True, errors='ignore')
test_detail.rename(columns={'变量英文名': 'feature', '#Obs': 'total', '#Bad': 'bad',
                             '%Bad_Rate': 'bad_rate', 'Bin': 'bin_label'}, inplace=True, errors='ignore')
train_detail['bad_rate'] = pd.to_numeric(train_detail['bad_rate'], errors='coerce')
test_detail['bad_rate'] = pd.to_numeric(test_detail['bad_rate'], errors='coerce')

vars_list = candidates['变量名字'].unique()
print(f"筛选变量: {len(vars_list)} 个")

# ── 识别每个变量的拒绝方向（头箱/尾箱） ──
def get_rejection_direction(detail, feat):
    """返回 ('head', bin_label) 或 ('tail', bin_label) 或 None"""
    vd = detail[detail['feature'] == feat].copy()
    if len(vd) == 0:
        return None
    # 解析 min_bin 用于排序
    def parse_min(b):
        s = str(b)
        if any(k in s for k in ['缺失', 'nan', 'NA', '空值', '特殊', 'special']):
            return None
        nums = re.findall(r'-?\d+\.?\d*', s)
        if not nums:
            return None
        v = float(nums[0])
        if v == float('-inf') or '(-inf' in s:
            return float(nums[1]) if len(nums) > 1 else None
        return v
    vd['_min'] = vd['bin_label'].apply(parse_min)
    vd = vd.dropna(subset=['_min']).sort_values('_min')
    if len(vd) < 1:
        return None
    head = vd.iloc[0]
    tail = vd.iloc[-1]
    results = []
    if head['bad_rate'] > 0.60 and head['total'] >= 20:
        results.append(('head', head['bin_label']))
    if tail['bad_rate'] > 0.60 and tail['total'] >= 20 and len(vd) > 1:
        results.append(('tail', tail['bin_label']))
    return results if results else None

def parse_bin_boundary(bin_label):
    """解析 bin_label 为 (lo, hi, lo_closed, hi_closed, is_special)"""
    s = str(bin_label).strip()
    if any(k in s for k in ['特殊', '空值', '缺失', 'nan', 'NA', 'null']):
        return (None, None, True)
    parts = [p.strip() for p in s.split(',')]
    if len(parts) == 1:
        nums = re.findall(r'-?\d+\.?\d*', parts[0])
        v = float(nums[0]) if nums else 0
        return (v, v, False)  # 单值箱
    lo_str, hi_str = parts[0], parts[-1]
    lo_closed = lo_str.startswith('[')
    hi_closed = hi_str.endswith(']')
    lo = float('-inf') if 'inf' in lo_str.lower() else (
        float(re.findall(r'-?\d+\.?\d*', lo_str)[0]) if re.findall(r'-?\d+\.?\d*', lo_str) else float('-inf'))
    hi = float('inf') if 'inf' in hi_str.lower() else (
        float(re.findall(r'-?\d+\.?\d*', hi_str)[0]) if re.findall(r'-?\d+\.?\d*', hi_str) else float('inf'))
    return (lo, hi, lo_closed, hi_closed, False)

def value_in_bin(val, meta):
    """检查值是否在箱内"""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return False
    if len(meta) == 3:  # 单值箱
        lo, hi, spec = meta
        return spec or (lo == hi and abs(val - lo) < 1e-9)
    lo, hi, lc, hc, spec = meta
    if spec:
        return False
    if lo == hi and abs(val - lo) < 1e-9:
        return True
    left_ok = (val >= lo) if lc else (val > lo)
    right_ok = (val <= hi) if hc else (val < hi)
    return left_ok and right_ok

feat_info = {}
for feat in vars_list:
    if pd.isna(feat):
        continue
    match = candidates[candidates['变量名字'] == feat]
    if len(match) == 0:
        print(f"  跳过 {feat}: 不在候选变量汇总中")
        continue
    reason = str(match.iloc[0]['选出的原因'])
    dirs = []
    if '头箱' in reason:
        dirs.append(('head', None))
    if '尾箱' in reason:
        dirs.append(('tail', None))
    if not dirs:
        print(f"  跳过 {feat}: 无法解析方向: {reason[:50]}")
        continue
    feat_info[feat] = {'directions': dirs}

print(f"有确认拒绝方向的特征: {len(feat_info)} 个")
# ── 构建矩阵 ──
def build_matrix(data_df, detail_df, months, feat_info):
    """输出 (header, rows) 矩阵"""
    # header: 月份 | feat1_逾期率 | feat1_金额逾期率 | feat2_逾期率 | feat2_金额逾期率 | ...
    feat_names = list(feat_info.keys())
    headers = ['月份']
    for f in feat_names:
        headers.append(f'{f}_逾期率')
        headers.append(f'{f}_金额逾期率')
    headers.append('大盘_逾期率')
    headers.append('大盘_金额逾期率')

    rows = []
    # 大盘每月的坏率
    month_overall = {}
    for m in months:
        midx = data_df['_month'] == m
        total = midx.sum()
        bad = data_df.loc[midx, 'overdue_flag'].sum()
        money = data_df.loc[midx, 'money'].sum()
        bad_money = data_df.loc[midx & (data_df['overdue_flag'] == 1), 'money'].sum()
        month_overall[m] = (bad/total if total else 0, bad_money/money if money else 0)

    for m in months:
        row = [m]
        midx = data_df['_month'] == m
        mdata = data_df[midx]
        for feat in feat_names:
            dirs = feat_info[feat]['directions']
            # 获取该变量在这批数据上的值
            if feat not in mdata.columns:
                row += [0.0, 0.0]
                continue
            # 获取该变量的bin定义
            vd = detail_df[detail_df['feature'] == feat]
            bin_defs = [(r['bin_label'], parse_bin_boundary(r['bin_label'])) for _, r in vd.iterrows()]

            # 匹配拒绝箱
            reject_vals = mdata[feat].apply(
                lambda v: any(
                    value_in_bin(v, meta)
                    for _, meta in bin_defs  # 这里简化为所有bin
                )
            )
            # 更精确：只匹配拒绝方向对应的bin
            reject_labels = [d[1] for d in dirs]
            def is_in_reject_bin(val):
                for bl, meta in bin_defs:
                    if bl in reject_labels and value_in_bin(val, meta):
                        return True
                return False

            in_bin = mdata[feat].apply(is_in_reject_bin)
            bin_total = in_bin.sum()
            if bin_total < 5:
                row += [0.0, 0.0]
                continue
            bin_bad = mdata.loc[in_bin, 'overdue_flag'].sum()
            bin_money = mdata.loc[in_bin, 'money'].sum()
            bin_bad_money = mdata.loc[in_bin & (mdata['overdue_flag'] == 1), 'money'].sum()
            row += [bin_bad/bin_total, bin_bad_money/bin_money if bin_money else 0]

        # 大盘
        overall = month_overall[m]
        row += [overall[0], overall[1]]
        rows.append(row)

    # 合计行
    total_row = ['合计']
    for feat in feat_names:
        dirs = feat_info[feat]['directions']
        reject_labels = [d[1] for d in dirs]
        vd = detail_df[detail_df['feature'] == feat]
        bin_defs = [(r['bin_label'], parse_bin_boundary(r['bin_label'])) for _, r in vd.iterrows()]

        def is_in_reject_bin(val):
            for bl, meta in bin_defs:
                if bl in reject_labels and value_in_bin(val, meta):
                    return True
            return False

        if feat not in data_df.columns:
            total_row += [0.0, 0.0]
            continue
        in_bin = data_df[feat].apply(is_in_reject_bin)
        bin_total = in_bin.sum()
        if bin_total < 5:
            total_row += [0.0, 0.0]
            continue
        bin_bad = data_df.loc[in_bin, 'overdue_flag'].sum()
        bin_money = data_df.loc[in_bin, 'money'].sum()
        bin_bad_money = data_df.loc[in_bin & (data_df['overdue_flag'] == 1), 'money'].sum()
        total_row += [bin_bad/bin_total, bin_bad_money/bin_money if bin_money else 0]

    total_all_bad = data_df['overdue_flag'].sum()
    total_all = len(data_df)
    total_all_money = data_df['money'].sum()
    total_all_bad_money = data_df.loc[data_df['overdue_flag'] == 1, 'money'].sum()
    total_row += [total_all_bad/total_all, total_all_bad_money/total_all_money if total_all_money else 0]
    rows.append(total_row)

    return headers, rows

print("\n构建 Train 月度稳定性矩阵...")
t_headers, t_rows = build_matrix(train_df, train_detail, t_months, feat_info)
print(f"  {len(t_rows)} 行 x {len(t_headers)} 列")

print("构建 Test 月度稳定性矩阵...")
te_headers, te_rows = build_matrix(test_df, test_detail, te_months, feat_info)
print(f"  {len(te_rows)} 行 x {len(te_headers)} 列")

# ── 写入 Excel（保留原5个Sheet + 追加2个矩阵Sheet） ──
print("写入 Excel...")
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

wb = openpyxl.load_workbook(BINNING_PATH)

def write_matrix_sheet(ws, headers, rows):
    """写入矩阵格式：列=特征，行=月份"""
    ncols = len(headers)
    nrows = len(rows)

    # 样式
    hdr_fill = PatternFill(start_color='366092', end_color='366092', fill_type='solid')
    hdr_font = Font(bold=True, color='FFFFFF', size=10)
    hdr_align = Alignment(horizontal='center', vertical='center')
    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin')
    )
    sum_fill = PatternFill(start_color='D9E2F3', end_color='D9E2F3', fill_type='solid')
    pct_fmt = '#,##0.00%'
    alt_fill1 = PatternFill(start_color='F2F2F2', end_color='F2F2F2', fill_type='solid')
    alt_fill2 = PatternFill(start_color='FFFFFF', end_color='FFFFFF', fill_type='solid')

    ws.cell(row=1, column=1, value='月份')
    ws.cell(row=1, column=1).fill = hdr_fill
    ws.cell(row=1, column=1).font = hdr_font
    ws.cell(row=1, column=1).alignment = hdr_align
    ws.cell(row=1, column=1).border = thin_border

    feat_names = [h.replace('_逾期率', '').replace('_金额逾期率', '') for h in headers[1:-1:2]]
    # 写列头
    for ci in range(1, ncols):
        cell = ws.cell(row=1, column=ci+1, value=headers[ci])
        cell.fill = hdr_fill
        cell.font = hdr_font
        cell.alignment = hdr_align
        cell.border = thin_border

    # 写数据
    for ri, row_data in enumerate(rows):
        is_sum = (row_data[0] == '合计')
        r = ri + 2  # Excel 行号（1-indexed）
        fill = sum_fill if is_sum else (alt_fill1 if ri % 2 == 0 else alt_fill2)

        c1 = ws.cell(row=r, column=1, value=row_data[0])
        c1.fill = fill
        c1.border = thin_border
        c1.font = Font(bold=is_sum)
        c1.alignment = Alignment(horizontal='center')

        for ci in range(1, ncols):
            val = row_data[ci]
            cell = ws.cell(row=r, column=ci+1)
            if isinstance(val, float):
                cell.value = val
                cell.number_format = pct_fmt
            else:
                cell.value = val
            cell.fill = fill
            cell.border = thin_border
            cell.alignment = Alignment(horizontal='center')

    # 列宽
    ws.column_dimensions['A'].width = 12
    for ci in range(1, ncols):
        ws.column_dimensions[get_column_letter(ci+1)].width = 16

    # 冻结首行
    ws.freeze_panes = 'B2'

    # 逾期率列（第2、4、6...列）加 data bar
    # 逾期率列是奇数索引 (1, 3, 5, ...) 即 columns index 为 2, 4, 6, ... (0-indexed)
    for ci in range(1, ncols):
        col_letter = get_column_letter(ci + 1)
        col_name = headers[ci]
        if '_逾期率' in col_name:
            # Data bar on 逾期率 columns
            last_row = nrows + 1
            for row_idx in range(2, last_row + 1):
                cell_ref = f'{col_letter}{row_idx}'
                ws.conditional_formatting.add(cell_ref, openpyxl.formatting.rule.CellIsRule(
                    operator='greaterThan', formula=['0'],
                    fill=PatternFill(start_color='5B9BD5', end_color='5B9BD5', fill_type='solid')
                ))
            # 使用 data bar
            from openpyxl.formatting.rule import DataBarRule
            rule = DataBarRule(
                start_type='min', end_type='max',
                color='5B9BD5', showValue=True,
                minLength=None, maxLength=None
            )
            ws.conditional_formatting.add(
                f'{col_letter}2:{col_letter}{nrows + 1}',
                rule
            )

# 写 Train
if 'Train月度稳定性' in wb.sheetnames:
    del wb['Train月度稳定性']
ws_tr = wb.create_sheet('Train月度稳定性')
write_matrix_sheet(ws_tr, t_headers, t_rows)

# 写 Test
if 'Test月度稳定性' in wb.sheetnames:
    del wb['Test月度稳定性']
ws_te = wb.create_sheet('Test月度稳定性')
write_matrix_sheet(ws_te, te_headers, te_rows)

wb.save(BINNING_PATH)
wb.close()

print(f"\n完成!")
print(f"  Sheet6: Train月度稳定性 ({len(t_rows)}行 x {len(t_headers)}列)")
print(f"  Sheet7: Test月度稳定性 ({len(te_rows)}行 x {len(te_headers)}列)")
print(f"  特征数: {len(feat_info)}")
