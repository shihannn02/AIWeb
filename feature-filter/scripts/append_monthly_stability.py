# -*- coding: utf-8 -*-
"""追加月度稳定性到现有分箱 Excel（不重写任何已有 Sheet）
用法：修顶部参数 → python append_monthly_stability.py
输入：分箱结果.xlsx（含 Train分箱明细 + Test分箱明细 + 训练筛选变量）
输出：追加 Sheet 6(Train月度稳定性) + Sheet 7(Test月度稳定性) 到原文件

陷阱：
- 坏率/金额逾期率是小数（0.5376 ≠ 53.76%），必须在 DATA 和 SUM 行设置 number_format='0.00%'
- 子表头从第2列开始（列1=箱标签），列偏移计算：2+mi*4+si
- 颜色标记必须先于分类空行插入（先标色再插空行，颜色随单元格移动）
"""
import pandas as pd, numpy as np, re, warnings, os, math
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.formatting.rule import DataBarRule
warnings.filterwarnings("ignore")

BINNING_PATH = r"C:\Users\6\Desktop\qiongdan数据2\分箱结果_头尾5.xlsx"
RAW_PATH = r"C:\Users\6\Desktop\qiongdan数据2\失联用户及黑名单变量样本.csv"
LABEL = "target"
CUTOFF = "2026-05-01"
SUB_COLS = ['总人数', '逾期数', '坏率', '金额逾期率']

def parse_bin_boundary(bin_label):
    """解析分箱标签为 (lo, hi, left_closed, right_closed, is_special)"""
    s = str(bin_label).strip()
    if any(k in s for k in ['特殊','空值','缺失','nan','NA','null']):
        return (None, None, True)
    parts = [p.strip() for p in s.split(',')]
    if len(parts) == 1:
        nums = re.findall(r'-?\d+\.?\d*', parts[0])
        return (float(nums[0]), float(nums[0]), False) if nums else (None, None, True)
    lo_str, hi_str = parts[0], parts[-1]
    lo = float('-inf') if 'inf' in lo_str.lower() else float(re.findall(r'-?\d+\.?\d*', lo_str)[0]) if re.findall(r'-?\d+\.?\d*', lo_str) else float('-inf')
    hi = float('inf') if 'inf' in hi_str.lower() else float(re.findall(r'-?\d+\.?\d*', hi_str)[0]) if re.findall(r'-?\d+\.?\d*', hi_str) else float('inf')
    return (lo, hi, s.startswith('['), s.endswith(']'), False)

def assign_bin(value, bin_defs):
    """将原始值分配到对应分箱"""
    is_nan = value is None or (isinstance(value, float) and math.isnan(value))
    if is_nan:
        for bl, meta in bin_defs:
            if (len(meta)==3 and meta[2]==True) or (len(meta)==5 and meta[4]==True): return bl
        return None
    for bl, meta in bin_defs:
        if (len(meta)==3 and meta[2]==True) or (len(meta)==5 and meta[4]==True): continue
        if len(meta) == 3:
            lo, hi, spec = meta
            if lo == hi and abs(value-lo)<1e-9: return bl
            continue
        lo, hi, cl, cr, spec = meta
        if lo == hi and abs(value-lo)<1e-9: return bl
        left_ok = (value>=lo) if cl else (value>lo)
        if lo==float('-inf'): left_ok=True
        right_ok = (value<=hi) if cr else (value<hi)
        if hi==float('inf'): right_ok=True
        if left_ok and right_ok: return bl
    return None

def build(data, detail, months):
    """构建月度稳定性数据行"""
    out = []
    for var in vars_list:
        vd = detail[detail['feature']==var]
        if len(vd)==0: continue
        if var not in data.columns: continue
        bdefs = [(r['bin_label'], parse_bin_boundary(r['bin_label'])) for _, r in vd.iterrows()]
        ds = data[[var,'apply_month','overdue_flag','money']].copy()
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
