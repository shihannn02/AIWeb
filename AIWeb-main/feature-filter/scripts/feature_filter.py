# -*- coding: utf-8 -*-
"""
feature-filter.py — 分箱后变量筛选
规则：
  0. 头/尾箱 total >= 20
  1. 头/尾箱 bad_rate > 阈值 → 候选（第一箱低于阈值、第二箱高于阈值则不选）
  2. U型分布 → 标记人工判断（无色）
  3. Test 验证 → 好=绿 / 一般=橙 / 不好=红
输出：新建 _完整.xlsx（含原2个Sheet + 新5个Sheet），原始分箱 Excel 永不动
"""

import pandas as pd
import numpy as np
import os, sys, warnings, re, math, copy
warnings.filterwarnings("ignore")
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.formatting.rule import DataBarRule

# ====================== 月度稳定性模块 (from monthly_stability_v3.py) ======================
SUB_COLS = ['总人数', '逾期数', '坏率', '金额逾期率']

def parse_bin_boundary(bin_label):
    """Always return 5-element tuple: (lo, hi, left_closed, right_closed, is_missing)"""
    s = str(bin_label).strip()
    lower = s.lower()
    if lower.startswith('special(') or lower == 'missing':
        return (None, None, False, False, True)
    if any(k in s for k in ['特殊','空值','缺失','nan','NA','null','MISSING']):
        return (None, None, False, False, True)  # is_missing=True
    parts = [p.strip() for p in s.split(',')]
    if len(parts) == 1:
        nums = re.findall(r'-?\d+\.?\d*', parts[0])
        if nums:
            v = float(nums[0])
            return (v, v, True, True, False)  # single-value bin, both closed
        return (None, None, False, False, True)
    lo_str, hi_str = parts[0], parts[-1]
    lo = float('-inf') if 'inf' in lo_str.lower() else float(re.findall(r'-?\d+\.?\d*', lo_str)[0]) if re.findall(r'-?\d+\.?\d*', lo_str) else float('-inf')
    hi = float('inf') if 'inf' in hi_str.lower() else float(re.findall(r'-?\d+\.?\d*', hi_str)[0]) if re.findall(r'-?\d+\.?\d*', hi_str) else float('inf')
    return (lo, hi, s.startswith('['), s.endswith(']'), False)  # is_missing=False

SPECIAL_VALUES = [-999, -9999, -999999, -99999, -9999979, -999979, -999990, -1111]

def assign_bin(value, bin_defs):
    """Assign value to bin using 5-element (lo, hi, cl, cr, is_missing) tuple"""
    is_nan = value is None or (isinstance(value, float) and math.isnan(value))
    is_special = not is_nan and value in SPECIAL_VALUES
    # NaN/special → missing bin
    if is_nan or is_special:
        for bl, meta in bin_defs:
            if meta is None: continue
            if len(meta) >= 5 and meta[4] is True:
                return bl
        return None
    # Normal values → first matching bin
    for bl, meta in bin_defs:
        if meta is None: continue
        if len(meta) < 5: continue
        if meta[4] is True: continue  # skip missing bins
        lo, hi, cl, cr, _ = meta
        # Single-value bin
        if lo == hi and cl and cr:
            if abs(value - lo) < 1e-9:
                return bl
            continue
        # Range bin
        left_ok = (value >= lo) if cl else (value > lo)
        if lo == float('-inf'): left_ok = True
        right_ok = (value <= hi) if cr else (value < hi)
        if hi == float('inf'): right_ok = True
        if left_ok and right_ok:
            return bl
    return None


def assign_bin_with_fallback(value, bin_defs, bin_order=None):
    """在 assign_bin 无匹配时，按 Train 箱顺序回退到头/尾/前一箱（APPLY 常见空隙）。"""
    assigned = assign_bin(value, bin_defs)
    if assigned is not None:
        return assigned
    is_nan = value is None or (isinstance(value, float) and math.isnan(value))
    if is_nan:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None

    order = list(bin_order) if bin_order else [bl for bl, _ in bin_defs]
    numeric = []
    meta_by_label = {bl: meta for bl, meta in bin_defs}
    for bl in order:
        meta = meta_by_label.get(bl)
        if not meta or len(meta) < 5 or meta[4]:
            continue
        lo, hi, cl, cr, _ = meta
        numeric.append((bl, lo, hi, cl, cr))
    if not numeric:
        return None

    first_bl, first_lo, _, first_cl, _ = numeric[0]
    last_bl, _, last_hi, _, last_cr = numeric[-1]

    if first_lo != float('-inf'):
        if v < first_lo or (v == first_lo and not first_cl):
            return first_bl
    if last_hi != float('inf'):
        if v > last_hi or (v == last_hi and not last_cr):
            return last_bl

    for i in range(1, len(numeric)):
        bl, lo, hi, cl, cr = numeric[i]
        if v < lo or (v == lo and not cl):
            return numeric[i - 1][0]
    return last_bl

def build_stability_data(data, detail, months):
    out = []
    for var in vars_list:
        vd = detail[detail['feature']==var]
        if len(vd)==0: continue
        if var not in data.columns: continue
        bdefs = [(r['bin_label'], parse_bin_boundary(r['bin_label'])) for _, r in vd.iterrows()]
        # 处理 -999 等特殊值 → NaN，与分箱预处理保持一致
        ds = data[[var,'apply_month','overdue_flag','money']].copy()
        ds[var] = ds[var].replace(SPECIAL_VALUES, np.nan)
        ds['_bin'] = ds[var].apply(lambda v: assign_bin(v, bdefs))
        miss = ds[var].isna()
        if miss.any():
            sp = [bl for bl,m in bdefs if (len(m)==3 and m[2]==True) or (len(m)==5 and m[4]==True)]
            if sp: ds.loc[miss,'_bin'] = sp[0]
        grp = ds.groupby(['_bin','apply_month']).agg(obs=('overdue_flag','count'),bad=('overdue_flag','sum'),money=('money','sum')).reset_index()
        bdf = ds[ds['overdue_flag']==1].groupby(['_bin','apply_month'])['money'].sum().reset_index().rename(columns={'money':'bad_money'})
        grp = grp.merge(bdf, on=['_bin','apply_month'], how='left')
        grp['bad_money'] = grp['bad_money'].fillna(0.0)
        bo = vd['bin_label'].tolist()
        out.append(('VAR', var))
        out.append(('H1', months))
        out.append(('H2', None))
        cs = {m:{'obs':0,'bad':0,'money':0.0,'bad_money':0.0} for m in months}
        at = {'obs':0,'bad':0,'money':0.0,'bad_money':0.0}
        for bl in bo:
            rd = [str(bl)]
            rt = {'obs':0,'bad':0,'money':0.0,'bad_money':0.0}
            for m in months:
                sub = grp[(grp['_bin']==bl)&(grp['apply_month']==m)]
                obs = int(sub.iloc[0]['obs']) if len(sub) else 0
                bad = int(sub.iloc[0]['bad']) if len(sub) else 0
                money = float(sub.iloc[0]['money']) if len(sub) else 0.0
                bmoney = float(sub.iloc[0]['bad_money']) if len(sub) else 0.0
                rd += [obs, bad, bad/obs if obs else 0, bmoney/money if money else 0]
                rt['obs'] += obs; rt['bad'] += bad; rt['money'] += money; rt['bad_money'] += bmoney
                cs[m]['obs'] += obs; cs[m]['bad'] += bad; cs[m]['money'] += money; cs[m]['bad_money'] += bmoney
            tbr = rt['bad']/rt['obs'] if rt['obs'] else 0
            tmbr = rt['bad_money']/rt['money'] if rt['money'] else 0
            rd += [rt['obs'], rt['bad'], tbr, tmbr]
            for k in at: at[k] += rt[k]
            out.append(('DATA', rd))
        sr = ['合计']
        for m in months:
            br = cs[m]['bad']/cs[m]['obs'] if cs[m]['obs'] else 0
            mbr = cs[m]['bad_money']/cs[m]['money'] if cs[m]['money'] else 0
            sr += [cs[m]['obs'], cs[m]['bad'], br, mbr]
        tbr = at['bad']/at['obs'] if at['obs'] else 0
        tmbr = at['bad_money']/at['money'] if at['money'] else 0
        sr += [at['obs'], at['bad'], tbr, tmbr]
        out.append(('SUM', sr))
        out.append(('BLK', None))
    return out

def write_stability_sheet(ws, rows, months):
    nm = len(months)
    wt = 1 + nm*4 + 4
    ws.column_dimensions['A'].width = 18
    for ci in range(1, wt):
        from openpyxl.utils import get_column_letter as gcl
        ws.column_dimensions[gcl(ci+1)].width = 10
    FONT_H1 = Font(bold=True, color='FFFFFF', size=10)
    FONT_H2 = Font(bold=True, color='FFFFFF', size=9)
    FONT_V = Font(bold=True, size=11, color='366092')
    FILL_H1 = PatternFill(start_color='366092', end_color='366092', fill_type='solid')
    FILL_H2 = PatternFill(start_color='5B9BD5', end_color='5B9BD5', fill_type='solid')
    FILL_SUM = PatternFill(start_color='D9E2F3', end_color='D9E2F3', fill_type='solid')
    ALIGN_C = Alignment(horizontal='center', vertical='center', wrap_text=True)
    BORDER_THIN = Border(
        left=Side(style='thin', color='D9D9D9'), right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'), bottom=Side(style='thin', color='D9D9D9'))
    ri = 0; data_start = -1; bar_ranges = []
    for item in rows:
        t, d = item
        if t == 'VAR':
            ws.merge_cells(start_row=ri+1, start_column=1, end_row=ri+1, end_column=wt)
            ws.cell(row=ri+1, column=1, value=f"变量: {d}").font = FONT_V; ri += 1
        elif t == 'H1':
            c = ws.cell(row=ri+1, column=1, value='箱')
            c.fill = FILL_H1; c.font = FONT_H1; c.alignment = ALIGN_C; c.border = BORDER_THIN
            for mi, m in enumerate(months):
                cs = 2 + mi * 4
                ws.merge_cells(start_row=ri+1, start_column=cs, end_row=ri+1, end_column=cs+3)
                c = ws.cell(row=ri+1, column=cs, value=m)
                c.fill = FILL_H1; c.font = FONT_H1; c.alignment = ALIGN_C; c.border = BORDER_THIN
            cs = 2 + nm * 4
            ws.merge_cells(start_row=ri+1, start_column=cs, end_row=ri+1, end_column=cs+3)
            c = ws.cell(row=ri+1, column=cs, value='合计')
            c.fill = FILL_H1; c.font = FONT_H1; c.alignment = ALIGN_C; c.border = BORDER_THIN
            ri += 1
            for mi in range(nm + 1):
                for si, s in enumerate(SUB_COLS):
                    col = 2 + mi * 4 + si
                    c = ws.cell(row=ri+1, column=col, value=s)
                    c.fill = FILL_H2; c.font = FONT_H2; c.alignment = ALIGN_C; c.border = BORDER_THIN
            ri += 1; data_start = ri
        elif t == 'DATA':
            for ci, v in enumerate(d):
                col = ci + 1
                c = ws.cell(row=ri+1, column=col, value=v)
                c.border = BORDER_THIN; c.alignment = ALIGN_C
                if ci > 0: c.number_format = '#,##0' if (ci-1)%4 in [0,1] else '0.00%'
            ri += 1
        elif t == 'SUM':
            data_end = ri - 1
            if data_end >= data_start:
                for mi in range(nm + 1):
                    for si in [2, 3]:
                        col = 2 + mi * 4 + si
                        bar_ranges.append((col, data_start, data_end))
            for ci, v in enumerate(d):
                col = ci + 1
                c = ws.cell(row=ri+1, column=col, value=v)
                c.fill = FILL_SUM; c.border = BORDER_THIN; c.alignment = ALIGN_C; c.font = Font(bold=True)
                if ci > 0: c.number_format = '#,##0' if (ci-1)%4 in [0,1] else '0.00%'
            ri += 1; data_start = -1
        elif t == 'BLK': ri += 1
    for col, sr, er in bar_ranges:
        try:
            from openpyxl.utils import get_column_letter as gcl2
            rng = f'{gcl2(col)}{sr+1}:{gcl2(col)}{er+1}'
            ws.conditional_formatting.add(rng, DataBarRule(start_type='min', end_type='max', color='5B9BD5'))
        except: pass
# ====================== 月度稳定性模块结束 ======================

# ====================== xlsxwriter 格式函数（复用原始分箱格式） ======================
# ==================== 原始分箱格式（从 E:\\binning-5percent 原版移植） ====================
biaotou='#366092'
text='#F4F4F4'
title='#44546A'
split_color='#FFFFFF'
xunhuan1='#D1D1D1'
xunhuan2='#E3E3E3'
title_size=12
biaotou_size=10
text_size=8

condition_format_pink_no = {'type': 'data_bar','bar_solid': True,'data_bar_2010': True,'bar_color': '#4472C4'}

title_format = {'bold': True,'font_name': 'Arial','font_size': title_size,'font_color': 'white',
               'top_color': biaotou,'bottom_color': biaotou,'left_color': biaotou,
               'right_color': biaotou,'bg_color': biaotou}
subtitle_format={'border': True,'font_size': biaotou_size,'font_name': 'Arial',
                                'top_color':title,'font_color': 'white',
                               'bottom_color': title,'bold': True,
                                'left_color': split_color,
                                'right_color': split_color,
                                'bg_color': title,
                                'align': 'left',
                               'valign': 'vcenter'}
body_text_format_01={'border': True,'font_size': text_size,'font_name': 'Arial',
                                'top_color':xunhuan1,
                                'bottom_color': xunhuan1,
                                'left_color': split_color,
                                'right_color': split_color,
                                'bg_color': xunhuan1,
                                'align': 'left',
                                'valign': 'vcenter'}
body_text_per_format_01=copy.deepcopy(body_text_format_01)
body_text_per_format_01['num_format']='0.00%'
body_text_format_02={'border': True,'font_size': text_size,'font_name': 'Arial',
                                'top_color':xunhuan2,
                                'bottom_color': xunhuan2,
                                'left_color': split_color,
                                'right_color':split_color,
                                'bg_color': xunhuan2,
                                'align': 'left',
                                'valign': 'vcenter'}
body_text_per_format_02=copy.deepcopy(body_text_format_02)
body_text_per_format_02['num_format']='0.00%'

def get_same_len(x):
    if type(x)!=str:
        x=str(x)
    l=list(x)
    num=0
    for i in l:
        if re.match("[\\u4e00-\\u9fa5]+",i):
            num=num+2
        else:
            num=num+1
    return num

def details_result_output(wb,sheetname,data,suoyin,ana_people):
    nrows, ncols = data.shape
    body_text_xunhuan1 = wb.add_format(body_text_format_01)
    body_text_xunhuan1_per = wb.add_format(body_text_per_format_01)
    body_text_xunhuan2 = wb.add_format(body_text_format_02)
    body_text_xunhuan2_per = wb.add_format(body_text_per_format_02)
    body_text_title = wb.add_format(subtitle_format)
    ws = wb.add_worksheet(sheetname)
    ws.freeze_panes(1, 4)
    ws.autofilter(0,0,nrows,ncols-1)
    ws.hide_gridlines({'option': 1})
    column = data.columns
    for i in range(len(column)):
        x = column[[i]][0]
        ll = get_same_len(x)
        lll = max(8, ll)
        ws.set_column(i , i , lll)
    data = data.replace(np.inf, 'inf')
    data = data.fillna('')
    data = data.replace(-np.inf, '-inf')
    for j in range(ncols):
        ws.write(0, j, column[j], body_text_title)
    for i in range(nrows):
        for j in range(ncols):
            if 'pass1' in column[j]:
                ws.conditional_format(1, j, nrows, j, {'type': 'data_bar','bar_solid': True,'data_bar_2010': True,'bar_color': '#65d97d'})
            elif '%Bad_Rate' in column[j] or 'Lift' in column[j]:
                ws.conditional_format(1, j, nrows, j, condition_format_pink_no)
            elif 'pass2' in column[j]:
                ws.conditional_format(1, j, nrows, j, {'type': 'data_bar','bar_solid': True,'data_bar_2010': True,'bar_color': '#f2572d'})
            elif 'pass3' in column[j]:
                ws.conditional_format(1, j, nrows, j, {'type': 'data_bar','bar_solid': True,'data_bar_2010': True,'bar_color': '#1E90FF'})
            value = data.iloc[i][j]
            key = int(data.iloc[i, suoyin].replace(ana_people, ''))
            if key % 2 == 1:
                if ('%' in column[j] or '率' in column[j] or 'Rate' in column[j]):
                    ws.write(i + 1, j, value, body_text_xunhuan1_per)
                else:
                    ws.write(i + 1, j, value, body_text_xunhuan1)
            else:
                if ('%' in column[j] or '率' in column[j] or 'Rate' in column[j]):
                    ws.write(i + 1, j, value, body_text_xunhuan2_per)
                else:
                    ws.write(i + 1, j, value, body_text_xunhuan2)
# ====================== xlsxwriter 格式函数结束 ======================

from openpyxl.utils import get_column_letter

# ---------- 中文映射 ----------
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'references'))
try:
    from chinese_name_map import get_chinese_name
except ImportError:
    def get_chinese_name(feature_name):
        """特征名翻译为中文"""
        fn = feature_name.lower()
        
        # ===== 手机号通话次数类 =====
        if fn.startswith('phone_cnt_call_'):
            period = fn.replace('phone_cnt_call_', '')
            return f"手机号通话次数({period})"
        if fn.startswith('phone_cnt_ratio_'):
            parts = fn.replace('phone_cnt_ratio_', '').split('_')
            if len(parts) >= 2:
                return f"手机号通话次数比({parts[0]}d/{parts[1]}d)"
            return f"手机号通话次数比({fn})"
        if fn.startswith('phone_cnt_bl_hit_'):
            period = fn.replace('phone_cnt_bl_hit_', '')
            return f"手机号黑名单通话次数({period})"
        if fn.startswith('phone_pct_bl_hit_'):
            period = fn.replace('phone_pct_bl_hit_', '')
            return f"手机号黑名单通话占比({period})"
        if 'phone_is_bl_hit_ever' in fn:
            return "手机号是否曾命中黑名单"
        if 'phone_hours_since_last_call' in fn:
            return "手机号上次通话距现在(小时)"
        if 'phone_days_since_last_call' in fn:
            return "手机号上次通话距现在(天)"
        if 'phone_days_since_last_bl_hit' in fn:
            return "手机号上次黑名单通话距现在(天)"
        if 'phone_max_cnt_1h' in fn:
            return "手机号1小时内最大通话次数"
        if 'phone_max_cnt_1d' in fn:
            return "手机号1天内最大通话次数"
        if 'phone_pct_call_night_7d' in fn:
            return "手机号7天夜间通话占比"
        
        # ===== 手机号系统ID类 =====
        if fn.startswith('phone_cnt_distinct_sys_id_'):
            period = fn.replace('phone_cnt_distinct_sys_id_', '')
            return f"手机号不同系统ID数({period})"
        if fn.startswith('phone_cnt_call_max_sys_id_'):
            period = fn.replace('phone_cnt_call_max_sys_id_', '')
            return f"手机号最常联系系统ID通话次数({period})"
        if fn.startswith('phone_cnt_distinct_sys_id_bl_hit_'):
            period = fn.replace('phone_cnt_distinct_sys_id_bl_hit_', '')
            return f"手机号黑名单系统ID数({period})"
        if 'phone_is_multi_sys_id_bl_hit_ever' in fn:
            return "手机号是否曾多个系统ID命中黑名单"
        if fn.startswith('phone_pct_sys_id_bl_hit_'):
            period = fn.replace('phone_pct_sys_id_bl_hit_', '')
            return f"手机号系统ID黑名单命中率({period})"
        if fn.startswith('phone_pct_call_top_sys_id_'):
            period = fn.replace('phone_pct_call_top_sys_id_', '')
            return f"手机号最常联系系统ID通话占比({period})"
        if fn.startswith('phone_cnt_ratio_sys_id_'):
            parts = fn.replace('phone_cnt_ratio_sys_id_', '').split('_')
            if len(parts) >= 2:
                return f"手机号系统ID通话数比({parts[0]}/{parts[1]})"
            return f"手机号系统ID通话数比({fn})"
        if 'phone_days_since_first_sys_id_call' in fn:
            return "手机号首次系统ID通话距今天数"
        if fn.startswith('phone_days_since_new_sys_id_'):
            period = fn.replace('phone_days_since_new_sys_id_', '')
            return f"手机号新系统ID出现距今天数({period})"
        if fn.startswith('phone_hhi_sys_id_'):
            period = fn.replace('phone_hhi_sys_id_', '')
            return f"手机号系统ID HHI集中度({period})"
        
        # ===== 身份证号类（同手机号逻辑，前缀为id_） =====
        if fn.startswith('id_cnt_call_'):
            period = fn.replace('id_cnt_call_', '')
            return f"身份证通话次数({period})"
        if fn.startswith('id_cnt_ratio_'):
            parts = fn.replace('id_cnt_ratio_', '').split('_')
            if len(parts) >= 2:
                return f"身份证通话次数比({parts[0]}d/{parts[1]}d)"
            return f"身份证通话次数比({fn})"
        if fn.startswith('id_cnt_bl_hit_'):
            period = fn.replace('id_cnt_bl_hit_', '')
            return f"身份证黑名单通话次数({period})"
        if fn.startswith('id_pct_bl_hit_'):
            period = fn.replace('id_pct_bl_hit_', '')
            return f"身份证黑名单通话占比({period})"
        if 'id_is_bl_hit_ever' in fn:
            return "身份证是否曾命中黑名单"
        if 'id_hours_since_last_call' in fn:
            return "身份证上次通话距现在(小时)"
        if 'id_days_since_last_call' in fn:
            return "身份证上次通话距现在(天)"
        if 'id_days_since_last_bl_hit' in fn:
            return "身份证上次黑名单通话距现在(天)"
        if 'id_max_cnt_1h' in fn:
            return "身份证1小时内最大通话次数"
        if 'id_max_cnt_1d' in fn:
            return "身份证1天内最大通话次数"
        if 'id_pct_call_night_7d' in fn:
            return "身份证7天夜间通话占比"
        if fn.startswith('id_cnt_distinct_sys_id_'):
            period = fn.replace('id_cnt_distinct_sys_id_', '')
            return f"身份证不同系统ID数({period})"
        if fn.startswith('id_cnt_call_max_sys_id_'):
            period = fn.replace('id_cnt_call_max_sys_id_', '')
            return f"身份证最常联系系统ID通话次数({period})"
        if fn.startswith('id_cnt_distinct_sys_id_bl_hit_'):
            period = fn.replace('id_cnt_distinct_sys_id_bl_hit_', '')
            return f"身份证黑名单系统ID数({period})"
        if 'id_is_multi_sys_id_bl_hit_ever' in fn:
            return "身份证是否曾多个系统ID命中黑名单"
        if fn.startswith('id_pct_sys_id_bl_hit_'):
            period = fn.replace('id_pct_sys_id_bl_hit_', '')
            return f"身份证系统ID黑名单命中率({period})"
        if fn.startswith('id_pct_call_top_sys_id_'):
            period = fn.replace('id_pct_call_top_sys_id_', '')
            return f"身份证最常联系系统ID通话占比({period})"
        if fn.startswith('id_cnt_ratio_sys_id_'):
            parts = fn.replace('id_cnt_ratio_sys_id_', '').split('_')
            if len(parts) >= 2:
                return f"身份证系统ID通话数比({parts[0]}/{parts[1]})"
            return f"身份证系统ID通话数比({fn})"
        if 'id_days_since_first_sys_id_call' in fn:
            return "身份证首次系统ID通话距今天数"
        if fn.startswith('id_days_since_new_sys_id_'):
            period = fn.replace('id_days_since_new_sys_id_', '')
            return f"身份证新系统ID出现距今天数({period})"
        if fn.startswith('id_hhi_sys_id_'):
            period = fn.replace('id_hhi_sys_id_', '')
            return f"身份证系统ID HHI集中度({period})"
        
        # ===== OLC查询类 =====
        # 查询次数
        if fn.startswith('olc_qry_cnt_7d_') or fn == 'olc_qry_cnt_7d_phone':
            return "近7天OLC查询次数"
        if fn.startswith('olc_qry_cnt_30d_') or fn == 'olc_qry_cnt_30d_phone':
            return "近30天OLC查询次数"
        if fn.startswith('olc_qry_cnt_90d_') or fn == 'olc_qry_cnt_90d_phone':
            return "近90天OLC查询次数"
        if fn.startswith('olc_qry_cnt_180d_') or fn == 'olc_qry_cnt_180d_phone':
            return "近180天OLC查询次数"
        if fn == 'olc_qry_cnt_phone':
            return "OLC总查询次数"
        
        # 查询比率
        if 'olc_qry_7d_ratio' in fn:
            return "近7天OLC查询占比"
        if 'olc_qry_30d_ratio' in fn:
            return "近30天OLC查询占比"
        if 'olc_qry_90d_ratio' in fn:
            return "近90天OLC查询占比"
        if 'olc_qry_180d_ratio' in fn:
            return "近180天OLC查询占比"
        
        # 查询频次
        if 'olc_qry_freq_30d' in fn:
            return "近30天OLC查询频次"
        
        # 查询时间
        if 'olc_qry_first_days' in fn:
            return "OLC首次查询距今天数"
        if 'olc_qry_last_days' in fn:
            return "OLC末次查询距今天数"
        if 'olc_qry_span_days' in fn:
            return "OLC查询跨度(天)"
        if 'olc_qry_gap_days' in fn:
            return "OLC查询间隔(天)"
        
        # 查询命中
        if 'olc_qry_hit_flag' in fn or 'olc_qry_hit_7d_flag' in fn:
            return "OLC查询命中标识"
        if 'olc_qry_hit_30d_flag' in fn:
            return "近30天OLC查询命中标识"
        if 'olc_qry_hit_90d_flag' in fn:
            return "近90天OLC查询命中标识"
        if 'olc_qry_hit_180d_flag' in fn:
            return "近180天OLC查询命中标识"
        
        # 高频查询
        if 'olc_qry_hi_freq_flag' in fn:
            return "OLC高频查询标识"
        
        # 系统数
        if 'olc_qry_sys_cnt' in fn and '30d' in fn:
            return "近30天OLC查询系统数"
        if 'olc_qry_sys_cnt' in fn and '90d' in fn:
            return "近90天OLC查询系统数"
        if fn == 'olc_qry_sys_cnt_phone':
            return "OLC查询系统总数"
        
        # 多系统标识
        if 'olc_multi_sys_flag' in fn and '30d' in fn:
            return "近30天多系统OLC查询标识"
        if fn == 'olc_multi_sys_flag_phone':
            return "多系统OLC查询标识"
        
        # 有效/空查询
        if 'olc_qry_valid_cnt' in fn:
            return "OLC有效查询次数"
        if 'olc_qry_empty_cnt' in fn:
            return "OLC空查询次数"
        if 'olc_qry_empty_rate' in fn:
            return "OLC空查询率"
        if 'olc_qry_last_empty_flag' in fn:
            return "OLC末次空查询标识"
        if 'olc_qry_cont_empty' in fn:
            return "OLC连续空查询次数"
        
        # 错误标识
        if 'olc_error_flag' in fn:
            return "OLC查询错误标识"
        
        # 自名单
        if 'olc_self_' in fn:
            return f"OLC自名单({fn})"
        
        # 黑名单AB套餐相关
        if 'olc_b_pkg_cnt' in fn:
            return "OLC B套餐查询次数"
        if 'olc_b_pkg_overlap_flag' in fn:
            return "OLC B套餐重叠标识"
        if 'olc_b_both_pkg_flag' in fn:
            return "OLC B套两个包都命中标识"
        if 'olc_b_only_pkg1_flag' in fn:
            return "OLC B套仅包1命中标识"
        if 'olc_b_only_pkgx_flag' in fn:
            return "OLC B套仅包X命中标识"
        if 'olc_dual_hit_flag' in fn:
            return "OLC AB双命中标识"
        if 'olc_only_a_flag' in fn:
            return "OLC 仅A命中标识"
        if 'olc_only_b_flag' in fn:
            return "OLC 仅B命中标识"
        if 'olc_no_hit_flag' in fn:
            return "OLC 无命中标识"
        if 'olc_any_hit_flag' in fn:
            return "OLC 任何命中标识"
        if 'olc_total_exposure_cnt' in fn:
            return "OLC 总曝光次数"
        if 'olc_ab_pkg1_dual_flag' in fn:
            return "OLC 包1AB双命中标识"
        if 'olc_ab_pkgx_dual_flag' in fn:
            return "OLC 包XAB双命中标识"
        if 'olc_both_pkg_ab_flag' in fn:
            return "OLC 两个包AB都命中标识"
        
        # 包含pkg1/pkgx的通用处理
        if fn.startswith('olc_') and 'pkg1' in fn:
            # 推断是pkg1相关
            base = fn.replace('pkg1_', '').replace('_pkg1', '').replace('_phone', '')
            return f"OLC Pkg1-({base})"
        if fn.startswith('olc_') and 'pkgx' in fn:
            base = fn.replace('pkgx_', '').replace('_pkgx', '').replace('_phone', '')
            return f"OLC PkgX-({base})"
        
        if fn == 'target3':
            return "目标变量(target3)"
        
        # Fallback: return original name
        return feature_name


def read_binning_sheets(excel_path):
    xls = pd.ExcelFile(excel_path)
    train_sheet = test_sheet = None
    for s in xls.sheet_names:
        if 'test' in s.lower() and '分箱' in s:
            test_sheet = s
    for s in xls.sheet_names:
        if s == 'train分箱结果':
            train_sheet = s; break
        if 'train' in s.lower() and '分箱' in s:
            train_sheet = s; break
    if train_sheet is None:
        for s in xls.sheet_names:
            if s != test_sheet and '分箱' in s:
                train_sheet = s; break
    if train_sheet is None:
        for s in xls.sheet_names:
            if 'train' in s.lower():
                train_sheet = s; break
    if train_sheet is None:
        raise ValueError(f"找不到 Train/2.变量分箱 Sheet: {xls.sheet_names}")
    train_df = pd.read_excel(excel_path, sheet_name=train_sheet)
    print(f"  Train: '{train_sheet}' -> {len(train_df)} 行")
    test_df = pd.read_excel(excel_path, sheet_name=test_sheet) if test_sheet else None
    if test_sheet:
        print(f"  Test:  '{test_sheet}' -> {len(test_df)} 行")
    return train_df, test_df


def normalize_cols(df):
    """标准化列名 (头尾5%中英兼容)"""
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
    def parse_min(b):
        s = str(b)
        lower = s.lower()
        if any(k in s for k in ['缺失', 'nan', 'NA', '空值']):
            return 999999
        if any(k in s for k in ['特殊', 'special']):
            return -999999
        if '(-inf' in lower:
            return float('-inf')
        nums = re.findall(r'-?\d+\.?\d*', s)
        if nums:
            return float(nums[0])
        return 0
    def is_special_label(bl):
        s = str(bl)
        lower = s.lower()
        if any(k in s for k in ['缺失', 'nan', 'NA', '空值', '特殊']):
            return True
        return lower.startswith('special(') or lower == 'missing'

    d['min_bin'] = d['bin_label'].apply(parse_min)
    d['is_special'] = d['bin_label'].apply(is_special_label)
    return d


def calc_overall_bad_rate(df):
    if 'bad' not in df.columns or 'total' not in df.columns: return None
    total_bad = df['bad'].sum()
    total_all = df['total'].sum()
    return total_bad / total_all if total_all > 0 else 0


def check_head_tail(bins_info, threshold, min_samples=20):
    """检查头/尾箱高坏率, 返回 (type, bin_label, br, total, reason) 或 None"""
    if bins_info is None or bins_info['n_normal'] < 1:
        return None
    normal = bins_info['normal']
    n = len(normal)
    # 头箱
    head = normal.iloc[0]
    if head['total'] >= min_samples and head['bad_rate'] > threshold:
        return ('head', head['bin_label'], head['bad_rate'], head['total'],
                f"头箱坏率{head['bad_rate']:.2%} > {threshold:.0%}，{int(head['total'])}人")
    # 尾箱
    if n >= 1:
        tail = normal.iloc[n-1]
        if tail['total'] >= min_samples and tail['bad_rate'] > threshold:
            return ('tail', tail['bin_label'], tail['bad_rate'], tail['total'],
                    f"尾箱坏率{tail['bad_rate']:.2%} > {threshold:.0%}，{int(tail['total'])}人")
    return None


def check_u_shape(bins_info, overall_bad_rate, min_samples=20):
    """检查U型分布, 返回 (br, total, reason) 或 None"""
    if bins_info is None or bins_info['n_normal'] < 5:
        return None
    normal = bins_info['normal']
    n = len(normal)
    head, tail = normal.iloc[0], normal.iloc[n-1]
    if head['total'] < min_samples or tail['total'] < min_samples:
        return None
    if not (head['bad_rate'] > overall_bad_rate * 1.05 and
            tail['bad_rate'] > overall_bad_rate * 1.05):
        return None
    middle = normal.iloc[1:n-1]
    if not any(m < overall_bad_rate * 0.95 for m in middle['bad_rate'].values):
        return None
    mid_avg = middle['bad_rate'].mean()
    head_avg = (head['bad_rate'] + normal.iloc[1]['bad_rate']) / 2 if n > 2 else head['bad_rate']
    tail_avg = (tail['bad_rate'] + normal.iloc[n-2]['bad_rate']) / 2 if n > 2 else tail['bad_rate']
    if mid_avg == 0: return None
    if head_avg > mid_avg * 1.15 and tail_avg > mid_avg * 1.15:
        return (max(head['bad_rate'], tail['bad_rate']), head['total'] + tail['total'],
                f"U型: 头{head['bad_rate']:.2%} 尾{tail['bad_rate']:.2%} 中{mid_avg:.2%}")
    return None


def evaluate_test_on_train(rule_br, threshold):
    """基于Train坏率大小分颜色：>=65%好，>=阈值一般，<阈值不好"""
    if rule_br >= 0.65:
        return '好', 'good'
    elif rule_br >= threshold:
        return '一般', 'fair'
    else:
        return '不好', 'bad' 

def classify_category(chinese_name, feature_name):
    """根据中文名和特征名分类变量类别（同类放一起，不同类空行）"""
    cn = str(chinese_name)
    fn = str(feature_name).lower()
    
    # 1. 目标变量
    if fn == 'target3' or 'target' in fn:
        return 'A_目标变量'
    
    # 2. 手机号通话次数类
    if fn.startswith('phone_cnt_call_'):
        return 'B_手机号通话次数'
    if fn.startswith('phone_cnt_ratio_') and not 'sys_id' in fn:
        return 'C_手机号通话次数比'
    
    # 3. 手机号黑名单类
    if 'phone_bl_hit' in fn or 'phone_is_bl' in fn:
        return 'D_手机号黑名单通话'
    if 'phone_pct_bl_hit' in fn:
        return 'D_手机号黑名单通话'
    if 'phone_days_since_last_bl' in fn:
        return 'D_手机号黑名单通话'
    
    # 4. 手机号通话时间/频次类
    if 'phone_hours_since' in fn or 'phone_days_since' in fn:
        return 'E_手机号通话时间特征'
    if 'phone_max_cnt' in fn:
        return 'F_手机号最大通话频次'
    if 'phone_pct_call_night' in fn:
        return 'G_手机号夜间通话占比'
    
    # 5. 手机号系统ID类
    if fn.startswith('phone_cnt_distinct_sys_id_'):
        return 'H_手机号系统ID分布'
    if fn.startswith('phone_cnt_call_max_sys_id_'):
        return 'H_手机号系统ID分布'
    if fn.startswith('phone_cnt_ratio_sys_id_'):
        return 'H_手机号系统ID分布'
    if 'phone_sys_id' in fn and 'bl_hit' in fn:
        return 'I_手机号系统ID黑名单'
    if 'phone_pct_sys_id_bl' in fn:
        return 'I_手机号系统ID黑名单'
    if 'phone_is_multi_sys_id' in fn:
        return 'I_手机号系统ID黑名单'
    if 'phone_pct_call_top_sys_id' in fn:
        return 'H_手机号系统ID分布'
    if 'phone_days_since_first_sys' in fn or 'phone_days_since_new_sys' in fn:
        return 'H_手机号系统ID分布'
    if 'phone_hhi_sys_id' in fn:
        return 'H_手机号系统ID分布'
    
    # 6. 身份证维度（同手机号逻辑）
    if fn.startswith('id_cnt_call_'):
        return 'J_身份证通话次数'
    if fn.startswith('id_cnt_ratio_') and not 'sys_id' in fn:
        return 'K_身份证通话次数比'
    if 'id_bl_hit' in fn or 'id_is_bl' in fn:
        return 'L_身份证黑名单通话'
    if 'id_pct_bl_hit' in fn:
        return 'L_身份证黑名单通话'
    if 'id_days_since_last_bl' in fn:
        return 'L_身份证黑名单通话'
    if 'id_hours_since' in fn or 'id_days_since' in fn:
        return 'M_身份证通话时间特征'
    if 'id_max_cnt' in fn:
        return 'N_身份证最大通话频次'
    if 'id_pct_call_night' in fn:
        return 'O_身份证夜间通话占比'
    if fn.startswith('id_cnt_distinct_sys_id_'):
        return 'P_身份证系统ID分布'
    if fn.startswith('id_cnt_call_max_sys_id_'):
        return 'P_身份证系统ID分布'
    if fn.startswith('id_cnt_ratio_sys_id_'):
        return 'P_身份证系统ID分布'
    if 'id_sys_id' in fn and 'bl_hit' in fn:
        return 'Q_身份证系统ID黑名单'
    if 'id_pct_sys_id_bl' in fn:
        return 'Q_身份证系统ID黑名单'
    if 'id_is_multi_sys_id' in fn:
        return 'Q_身份证系统ID黑名单'
    if 'id_pct_call_top_sys_id' in fn:
        return 'P_身份证系统ID分布'
    if 'id_days_since_first_sys' in fn or 'id_days_since_new_sys' in fn:
        return 'P_身份证系统ID分布'
    if 'id_hhi_sys_id' in fn:
        return 'P_身份证系统ID分布'
    
    # 7. OLC查询类
    if fn.startswith('olc_qry_cnt_') and 'pkg' not in fn:
        return 'R_OLC查询次数'
    if 'olc_qry_7d_ratio' in fn or 'olc_qry_30d_ratio' in fn or 'olc_qry_90d_ratio' in fn or 'olc_qry_180d_ratio' in fn:
        return 'S_OLC查询比率'
    if 'olc_qry_freq' in fn:
        return 'T_OLC查询频次'
    if 'olc_qry_first_days' in fn or 'olc_qry_last_days' in fn or 'olc_qry_span' in fn or 'olc_qry_gap' in fn:
        return 'U_OLC查询时间'
    if 'olc_qry_hit_flag' in fn or 'olc_qry_hit_' in fn:
        return 'V_OLC查询命中'
    if 'olc_qry_hi_freq' in fn:
        return 'W_OLC高频查询'
    if 'olc_qry_sys_cnt' in fn:
        return 'X_OLC查询系统数'
    if 'olc_multi_sys_flag' in fn:
        return 'Y_OLC多系统标识'
    if 'olc_qry_valid_cnt' in fn or 'olc_qry_empty_cnt' in fn or 'olc_qry_empty_rate' in fn:
        return 'Z_OLC查询有效性'
    if 'olc_qry_last_empty' in fn or 'olc_qry_cont_empty' in fn:
        return 'ZA_OLC空查询特征'
    if 'olc_error_flag' in fn:
        return 'ZB_OLC错误标识'
    if 'olc_self_' in fn:
        return 'ZC_OLC自名单'
    if 'olc_id_bind' in fn or 'olc_phn_bind' in fn or 'olc_id_type_cnt' in fn:
        return 'ZD_OLC身份关联'
    if 'olc_pkg_cnt' in fn or 'olc_pkg_max' in fn or 'olc_pkg_last' in fn or 'olc_pkg1_qry_rate' in fn:
        return 'ZE_OLC套餐查询量'
    if 'olc_pkg_multi_flag' in fn or 'olc_pkg_single_flag' in fn:
        return 'ZF_OLC套餐类型'
    if 'olc_pkg1_hit' in fn or 'olc_pkgx_hit' in fn or 'olc_both_pkg_a_hit' in fn:
        return 'ZG_OLC套餐命中'
    if 'olc_only_pkg1' in fn or 'olc_only_pkgx' in fn:
        return 'ZG_OLC套餐命中'
    if 'olc_in_' in fn:
        return 'ZH_OLC关联人特征'
    if 'olc_b_pkg' in fn or 'olc_b_' in fn:
        return 'ZI_OLC B套餐'
    if 'olc_dual_hit' in fn or 'olc_only_a' in fn or 'olc_only_b' in fn or 'olc_no_hit' in fn or 'olc_any_hit' in fn:
        return 'ZJ_OLC AB综合'
    if 'olc_total_exposure' in fn:
        return 'ZK_OLC总曝光量'
    if 'olc_ab_pkg1_dual' in fn or 'olc_ab_pkgx_dual' in fn or 'olc_both_pkg_ab' in fn:
        return 'ZL_OLC AB套餐双命中'
    
    return 'ZZ_其他'


def write_xlsxwriter_output(output_path, analysis_rows, train_df, test_df, train_raw, test_raw, candidate_features,
                           raw_input_path, label_col):
    """用 xlsxwriter 输出全部 7 个 Sheet。Sheet1-2 用 build_output_worksheet（粉色数据条，与原始格式一致）"""
    # Build sheet 1 data
    sheet1_data = []
    for r in analysis_rows:
        sheet1_data.append({
            '变量名字': r['feature'], '最高坏率': r['max_bad_rate'],
            '变量效果': r['effect_label'], '选出的原因': r['reason'],
            '中文名字': r['chinese_name'], '样本占比': r['sample_pct'],
        })
    sheet1_df = pd.DataFrame(sheet1_data)
    
    # Build slim data for sheets 4-5
    feat_mask = train_df['feature'].isin(candidate_features)
    slim_cols = ['feature', 'bin_label', 'total', 'bad_rate', 'iv_total', 'total_ks']
    # Train slim
    if feat_mask.any():
        train_slim = train_df[feat_mask].copy()
        train_slim = train_slim.replace([np.inf, -np.inf], np.nan).fillna('')
        rename_map = {'feature': '变量名称', 'bin_label': 'Bin', 'bad_rate': '%Bad_Rate',
                      'total': '#Obs', 'iv_total': 'IV(total)', 'total_ks': 'total_ks'}
        train_slim = train_slim[[c for c in slim_cols if c in train_slim.columns]]
        train_slim = train_slim.rename(columns={c: rename_map.get(c, c) for c in train_slim.columns})
        train_slim = train_slim.reset_index(drop=True)
    else:
        train_slim = pd.DataFrame()
    
    # Test slim
    feat_mask_test = test_df['feature'].isin(candidate_features) if test_df is not None else pd.Series([False]*len(test_df))
    if test_df is not None and len(test_df) > 0 and feat_mask_test.any():
        test_slim = test_df[feat_mask_test].copy()
        test_slim = test_slim.replace([np.inf, -np.inf], np.nan).fillna('')
        test_slim = test_slim[[c for c in slim_cols if c in test_slim.columns]]
        test_slim = test_slim.rename(columns={c: rename_map.get(c, c) for c in test_slim.columns})
        test_slim = test_slim.reset_index(drop=True)
    else:
        test_slim = pd.DataFrame()
    
    # Monthly stability data
    print("\n计算月度稳定性...")
    try:
        df_stab = pd.read_csv(raw_input_path, encoding='gbk')
        df_stab['overdue_flag'] = df_stab[label_col]
        df_stab = df_stab[df_stab['overdue_flag'].isin([0,1])].copy()
        col_map = {c:c.replace('#','_') for c in df_stab.columns if '#' in c}
        df_stab = df_stab.rename(columns=col_map)
        trn = df_stab[df_stab['apply_date'] < '2026-05-01'].copy()
        tst = df_stab[df_stab['apply_date'] >= '2026-05-01'].copy()
        
        td = train_df.copy().rename(columns={'变量英文名':'feature','Bin':'bin_label'}, errors='ignore')
        ted = test_df.copy().rename(columns={'变量英文名':'feature','Bin':'bin_label'}, errors='ignore')
        
        global vars_list
        vars_list = [r['feature'] for r in analysis_rows]
        
        if not trn.empty and 'apply_month' in trn.columns:
            tm = sorted(trn['apply_month'].unique())
            if 'money' not in trn.columns: trn['money'] = 1000
            if 'money' not in tst.columns: tst['money'] = 1000
            tr_rows = build_stability_data(trn, td, tm)
            if not tst.empty:
                tem = sorted(tst['apply_month'].unique())
                ter_rows = build_stability_data(tst, ted, tem)
            else:
                ter_rows = []
            has_stability = True
        else:
            tr_rows = []; ter_rows = []; tm = []; tem = []; has_stability = False
    except Exception as e:
        print(f"  月度稳定性跳过: {e}")
        tr_rows = []; ter_rows = []; tm = []; tem = []; has_stability = False
    
    # Create xlsxwriter workbook
    print("输出 Excel...")
    writer = pd.ExcelWriter(output_path, engine='xlsxwriter')
    wb = writer.book
    
    # Sheet 1: 2.变量分箱 — 用原始未标准化数据，动态列（只包含实际存在的列）
    train_out = train_raw.copy() if train_raw is not None else train_df.copy()
    # 只保留实际存在的列，不填充不存在的32列格式
    train_out['序号'] = train_out['序号'].fillna('wtt0').astype(str)
    details_result_output(wb, 'train分箱结果', train_out, suoyin=0, ana_people='wtt')
    
    # Sheet 2: Test分箱明细 — 同方式处理
    test_out = test_raw.copy() if test_raw is not None else test_df.copy()
    test_out['序号'] = test_out['序号'].fillna('wtt0').astype(str)
    details_result_output(wb, 'Test分箱明细', test_out, suoyin=0, ana_people='wtt')
    
    # Sheet 3: 候选变量汇总 (xlsxwriter)
    sheet3_ws = wb.add_worksheet('候选变量汇总')
    _write_candidate_sheet(sheet3_ws, wb, sheet1_df, analysis_rows)
    
    # Sheet 4: 训练筛选变量 (xlsxwriter)
    if len(train_slim) > 0:
        _write_slim_sheet(wb, '训练筛选变量', train_slim)
    
    # Sheet 5: 测试筛选变量 (xlsxwriter)
    if len(test_slim) > 0:
        _write_slim_sheet(wb, '测试筛选变量', test_slim)
    
    # Sheet 6: Train月度稳定性 (xlsxwriter)
    if has_stability and tr_rows:
        _write_stability_xlsx(wb, 'Train月度稳定性', tr_rows, tm)
        print(f"  Sheet: Train月度稳定性")
    
    # Sheet 7: Test月度稳定性 (xlsxwriter)
    if has_stability and ter_rows:
        _write_stability_xlsx(wb, 'Test月度稳定性', ter_rows, tem)
        print(f"  Sheet: Test月度稳定性")
    
    writer.close()
    print(f"\n输出: {output_path}")
    print(f"  Sheet1: train分箱结果 ({len(train_out)} 行)")
    print(f"  Sheet2: Test分箱明细 ({len(test_out)} 行)")
    print(f"  Sheet3: 候选变量汇总 ({len(sheet1_df)} 行)")
    print(f"  Sheet4: 训练筛选变量 ({len(train_slim)} 行)")
    print(f"  Sheet5: 测试筛选变量 ({len(test_slim)} 行)")



def _write_candidate_sheet(ws, wb, df, analysis_rows):
    """Write 候选变量汇总 with xlsxwriter"""
    if len(df) == 0: return
    cols = list(df.columns)
    header_fmt = wb.add_format({'bold': True, 'bg_color': '#4472C4', 'font_color': 'white',
                                'border': 1, 'align': 'center', 'valign': 'vcenter', 'font_size': 11})
    data_fmt = wb.add_format({'border': 1, 'align': 'center', 'valign': 'vcenter'})
    pct_fmt = wb.add_format({'border': 1, 'align': 'center', 'valign': 'vcenter', 'num_format': '0.00%'})
    orange_fmt = wb.add_format({'border': 1, 'align': 'center', 'valign': 'vcenter', 'bg_color': '#FFD966'})
    orange_pct = wb.add_format({'border': 1, 'align': 'center', 'valign': 'vcenter', 'bg_color': '#FFD966', 'num_format': '0.00%'})
    
    # Headers
    for ci, cn in enumerate(cols):
        ws.write(0, ci, cn, header_fmt)
        ws.set_column(ci, ci, max(12, len(str(cn)) + 2))
    
    # Categorize and sort
    prev_cat = None
    blank_offset = 0
    for ri in range(len(df)):
        cat = str(analysis_rows[ri].get('category', ''))
        if prev_cat is not None and cat != prev_cat:
            blank_offset += 1
        prev_cat = cat
        r = ri + 1 + blank_offset
        eff = df.iloc[ri]['变量效果']
        is_good = str(eff).startswith('好')
        for ci in range(len(cols)):
            val = df.iloc[ri, ci]
            if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
                val = ''
            fm = orange_fmt if is_good else data_fmt
            if isinstance(val, float) and not isinstance(val, bool) and ('坏率' in cols[ci] or '占比' in cols[ci]):
                fm = orange_pct if is_good else pct_fmt
            ws.write(r, ci, val, fm)
        if prev_cat is not None and cat != prev_cat:
            pass  # blank row handled above


def _write_slim_sheet(wb, name, df):
    """Write 训练/测试筛选变量 with xlsxwriter (blue data bars on %%Bad_Rate)"""
    if len(df) == 0: return
    ws = wb.add_worksheet(name)
    header_fmt = wb.add_format({'bold': True, 'bg_color': '#4472C4', 'font_color': 'white',
                                'border': 1, 'align': 'center', 'valign': 'vcenter', 'font_size': 11})
    data_fmt = wb.add_format({'border': 1, 'align': 'center', 'valign': 'vcenter'})
    pct_fmt = wb.add_format({'border': 1, 'align': 'center', 'valign': 'vcenter', 'num_format': '0.00%'})
    
    cols = list(df.columns)
    for ci, cn in enumerate(cols):
        ws.write(0, ci, cn, header_fmt)
        ws.set_column(ci, ci, max(12, len(str(cn)) + 2))
    
    br_col = None
    for ci, cn in enumerate(cols):
        if cn == '%Bad_Rate':
            br_col = ci
        for ri in range(len(df)):
            val = df.iloc[ri, ci]
            if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
                val = ''  # xlsxwriter can't write NaN/INF
            fm = pct_fmt if isinstance(val, float) and not isinstance(val, bool) and (cn in ('%Bad_Rate',)) else data_fmt
            ws.write(ri + 1, ci, val, fm)
    
    # Blue data bar on %%Bad_Rate
    if br_col is not None:
        ws.conditional_format(1, br_col, len(df), br_col, {
            'type': 'data_bar', 'bar_color': '#5DADE2', 'bar_solid': True
        })


def _write_stability_xlsx(wb, name, rows, months):
    """Write monthly stability sheet with xlsxwriter"""
    if not rows: return
    ws = wb.add_worksheet(name)
    nm = len(months)
    wt = 1 + nm * 4 + 4
    
    fmt_h1 = wb.add_format({'bold': True, 'bg_color': '#366092', 'font_color': 'white',
                            'border': 1, 'align': 'center', 'valign': 'vcenter', 'font_size': 10})
    fmt_h2 = wb.add_format({'bold': True, 'bg_color': '#5B9BD5', 'font_color': 'white',
                            'border': 1, 'align': 'center', 'valign': 'vcenter', 'font_size': 9})
    fmt_var = wb.add_format({'bold': True, 'font_size': 11, 'font_color': '#366092', 'bottom': 1})
    fmt_int = wb.add_format({'border': 1, 'align': 'center', 'num_format': '#,##0'})
    fmt_pct = wb.add_format({'border': 1, 'align': 'center', 'num_format': '0.00%'})
    fmt_sum = wb.add_format({'bold': True, 'border': 1, 'bg_color': '#D9E2F3', 'align': 'center'})
    fmt_sum_int = wb.add_format({'bold': True, 'border': 1, 'bg_color': '#D9E2F3', 'align': 'center', 'num_format': '#,##0'})
    fmt_sum_pct = wb.add_format({'bold': True, 'border': 1, 'bg_color': '#D9E2F3', 'align': 'center', 'num_format': '0.00%'})
    
    ws.set_column(0, 0, 18)
    for ci in range(1, wt):
        ws.set_column(ci, ci, 10)
    
    ri = 0; data_start = -1; bar_cols = []
    for item in rows:
        t, d = item
        if t == 'VAR':
            ws.merge_range(ri, 0, ri, wt - 1, f"变量: {d}", fmt_var)
            ri += 1
        elif t == 'H1':
            # Month headers
            ws.write(ri, 0, '箱', fmt_h1)
            for mi, m in enumerate(months):
                cs = 1 + mi * 4
                ws.merge_range(ri, cs, ri, cs + 3, m, fmt_h1)
            cs = 1 + nm * 4
            ws.merge_range(ri, cs, ri, cs + 3, '合计', fmt_h1)
            ri += 1
            # Sub-headers
            ws.write(ri, 0, '', fmt_h2)
            for mi in range(nm + 1):
                for si, s in enumerate(SUB_COLS):
                    ws.write(ri, 1 + mi * 4 + si, s, fmt_h2)
            ri += 1; data_start = ri
        elif t == 'DATA':
            for ci, v in enumerate(d):
                if ci == 0:
                    ws.write(ri, ci, v, fmt_int)
                else:
                    rel = (ci - 1) % 4
                    ws.write(ri, ci, v, fmt_pct if rel in [2, 3] else fmt_int)
            ri += 1
        elif t == 'SUM':
            data_end = ri - 1
            if data_end >= data_start:
                for mi in range(nm + 1):
                    for si in [2, 3]:
                        bar_cols.append((1 + mi * 4 + si, data_start, data_end))
            for ci, v in enumerate(d):
                if ci == 0:
                    ws.write(ri, ci, v, fmt_sum)
                else:
                    rel = (ci - 1) % 4
                    ws.write(ri, ci, v, fmt_sum_pct if rel in [2, 3] else fmt_sum_int)
            ri += 1; data_start = -1
        elif t == 'BLK':
            ri += 1
    
    for col, sr, er in bar_cols:
        ws.conditional_format(sr, col, er, col, {
            'type': 'data_bar', 'bar_color': '#5B9BD5', 'bar_solid': True
        })




def main():
    print('='*60)
    print('   feature-filter — 分箱后变量筛选')
    print('='*60)

    input_file = input("请输入分箱结果 Excel 路径: ").strip().strip("'\"")
    threshold_str = input("请输入坏率阈值 (如 0.6 = 60%): ").strip()
    threshold = float(threshold_str)

    print(f"\n输入: {input_file}")
    print(f"阈值: {threshold:.0%}\n")

    if not os.path.exists(input_file):
        print(f"[错误] 文件不存在: {input_file}"); return

    # 直接输出到原文件（xlsxwriter 重建全部7个Sheet，Sheet1-2数据格式不变）
    output_path = input_file

    # Read
    train_raw, test_raw = read_binning_sheets(input_file)
    train = normalize_cols(train_raw)
    test = normalize_cols(test_raw) if test_raw is not None else None

    train_overall = calc_overall_bad_rate(train)
    test_overall = calc_overall_bad_rate(test) if test is not None else None
    print(f"\nTrain大盘坏率: {train_overall:.2%}")
    if test_overall: print(f"Test 大盘坏率: {test_overall:.2%}")
    print()

    all_features = train['feature'].unique()
    print(f"共 {len(all_features)} 个变量\n")

    first_feat = train['feature'].iloc[0] if len(train) > 0 else None
    train_total = train[train['feature'] == first_feat]['total'].sum() if first_feat else 0
    results = []
    candidate_features = set()

    for feat in all_features:
        ft = train[train['feature'] == feat]
        normal = ft[~ft['is_special']].sort_values('min_bin').reset_index(drop=True)
        if len(normal) < 1: continue
        max_br = normal['bad_rate'].max()
        cn_name = get_chinese_name(feat)

        bins_info = {'normal': normal, 'n_normal': len(normal), 'all': ft}
        rule1 = check_head_tail(bins_info, threshold)
        if not rule1:
            continue

        rtype, bin_label, r_br, r_total, reason = rule1
        pct = r_total / train_total if train_total > 0 else 0
        u_shape = check_u_shape(bins_info, train_overall)

        if u_shape:
            _, _, u_reason = u_shape
            results.append({
                'feature': feat, 'max_bad_rate': max_br,
                'effect_label': 'U型人工判断',
                'reason': f"{reason}；{u_reason}",
                'chinese_name': cn_name, 'sample_pct': pct,
            })
            candidate_features.add(feat)
            print(f"  [U型+头尾] {feat}")

        else:
            test_eff, test_code = evaluate_test_on_train(r_br, threshold)
            if test_code == 'good':
                eff_label = '好'
            elif test_code == 'fair':
                eff_label = '一般'
            elif test_code == 'bad':
                eff_label = '不好'
            else:
                eff_label = '样本太少'
            results.append({
                'feature': feat, 'max_bad_rate': max_br,
                'effect_label': eff_label, 'reason': reason,
                'chinese_name': cn_name, 'sample_pct': pct,
            })
            candidate_features.add(feat)
            d = 'head' if rtype == 'head' else 'tail'
            print(f"  [{d}] {feat} -> {eff_label}")

    print(f"\n筛选完成: {len(results)} 个候选变量")
    ht = sum(1 for r in results if r['effect_label'] not in ('U型人工判断',))
    u = sum(1 for r in results if r['effect_label'] == 'U型人工判断')
    print(f"  头/尾高坏率: {ht}, U型: {u}")

    if not results:
        print("无候选变量"); return

    for r in results:
        r['category'] = classify_category(r.get('chinese_name', ''), r['feature'])
    results.sort(key=lambda r: (r['category'], -r['max_bad_rate']))

    # 一次性输出全部7个Sheet到新文件（xlsxwriter格式，Sheet1-2粉色数据条，永不动原始文件）
    raw_dir = os.path.dirname(input_file)
    raw_path = r'C:\Users\6\Desktop\qiongdan数据2\失联用户及黑名单变量样本.csv'
    write_xlsxwriter_output(output_path, results, train, test, train_raw, test_raw, candidate_features,
                            raw_path, 'target3')
    print("\n完成!")


if __name__ == '__main__':
    main()
