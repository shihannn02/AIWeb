#!/usr/bin/env python3
"""
头尾5%分箱 — 带 OOT 切分 + FIT/APPLY 模式
=============================================
FIT：从 train 学习分箱边界（get_bin_lift 的 knot 索引）
APPLY：用 train 同一边界对 test 分箱（important_bin_calculate）

输出格式与原始 binning_v2 完全相同（%Bad_Rate 粉色数据条，交替灰白背景）。
输出 2 个 Sheet：Train分箱明细 + Test分箱明细（OOT_RATIO>0 或 cutoff_date 设置时）。

使用前修改顶部参数区 (file_path, output_file, label, time_col, cutoff_date, OOT_RATIO, drop_cols)。
切分方式二选一：
  - cutoff_date: 条件切分，如 apply_date < "2026-05-01" 为 Train
  - OOT_RATIO: 比例切分，如 0.2 = 80% train / 20% test
"""

import numpy as np
import pandas as pd
import copy
from datetime import datetime
import re
import os
import xlsxwriter
import warnings
warnings.filterwarnings("ignore")

pd.set_option('display.max_columns', 30)
pd.set_option('display.max_rows', 30)

# ==================== PARAMETERS (MODIFY HERE) ====================
file_path = r'C:\Users\6\Desktop\新准入规则\toufang_变量_放款_非白.xlsx'
output_file = r'C:\Users\6\Desktop\新准入规则\分箱结果_头尾5.xlsx'
label = 'overdue_flag2'
time_col = 'create_time_x'

# 切分方式（二选一）：
# 选项1: 条件切分 — 指定日期截止点（如 apply_date < "2026-05-01" 为 Train）
#   时间列必须为 ISO 格式 (YYYY-MM-DD)，字符串比较即等价日期排序
#   设置后 OOT_RATIO 自动失效
cutoff_date = None  # 如 "2026-05-01"，设为 None 则用 OOT_RATIO

# 选项2: 比例切分 — 按时间 quantile 切分（仅 cutoff_date=None 时生效）
OOT_RATIO = 0.2  # 0 = no split, 0.2 = 80% train, 20% test

drop_cols = ['id_x','id_y','client_id','apply_id','pkid','pid',
             'serial_number','is_old','create_time_y',
             'risk_over_days','fact_money','fact_repay_money','expire']

# ==================== READ DATA ====================
if file_path.endswith('.csv'):
    data = pd.read_csv(file_path)
else:
    data = pd.read_excel(file_path)

print(f'数据大小: {data.shape}')
print(f'标签列 {label} 存在: {label in data.columns}')

if label not in data.columns:
    raise KeyError(f'标签列 {label} 不存在于数据中')

# ==================== SPLIT TRAIN/TEST ====================
use_cutoff = (cutoff_date is not None and time_col in data.columns)
use_oot_ratio = (not use_cutoff and OOT_RATIO > 0 and OOT_RATIO < 1 and time_col in data.columns)

if use_cutoff:
    # 条件切分：按指定日期截止点（ISO 格式字符串比较，等价于日期排序）
    data = data.sort_values(by=time_col)
    train_data = data[data[time_col] < cutoff_date].copy()
    test_data = data[data[time_col] >= cutoff_date].copy()
    print(f'条件切分: {time_col} < {cutoff_date}')
    print(f'Train: {len(train_data)}, Test: {len(test_data)}')
    actual_oot = True
elif use_oot_ratio:
    # 比例切分：按时间 quantile
    data[time_col] = pd.to_datetime(data[time_col])
    data = data.sort_values(by=time_col)
    oot_date = data[time_col].quantile(1 - OOT_RATIO)
    train_data = data[data[time_col] < oot_date].copy()
    test_data = data[data[time_col] >= oot_date].copy()
    print(f'Train: {len(train_data)}, Test: {len(test_data)}')
    actual_oot = True
else:
    train_data = data.copy()
    test_data = data.copy()
    actual_oot = False
    OOT_RATIO = 0

# ==================== PREPROCESS ====================
def preprocess(df, drop_cols_actual, label_name):
    my_data = df.copy()
    my_data['dpd_7'] = my_data[label_name]
    my_data['dpd_7'] = my_data['dpd_7'].replace(-1, 0)
    my_data = my_data[my_data['dpd_7'].isin([0, 1])]
    my_data = my_data.replace(-999, np.nan)
    cols_to_drop = [c for c in drop_cols_actual if c in my_data.columns]
    my_data.drop(labels=cols_to_drop, axis=1, inplace=True, errors='ignore')
    # Handle money column
    if 'money' not in my_data.columns:
        my_data['money'] = 1000
    money_col = my_data['money'].copy()
    my_data.drop(columns=['money'], inplace=True)
    return my_data, money_col

train_raw, money_train = preprocess(train_data, drop_cols, label)
if actual_oot:
    test_raw, money_test = preprocess(test_data, drop_cols, label)

# ==================== FORMATTING HELPERS ====================
biaotou='#366092'
text='#F4F4F4'
title='#44546A'
split_color='#FFFFFF'
xunhuan1='#D1D1D1'
xunhuan2='#E3E3E3'
title_size=12
biaotou_size=10
text_size=8

condition_format_pink_no = {'type': 'data_bar','bar_solid': True,'data_bar_2010': True,'bar_color': '#FF69B4'}

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

# ==================== CORE BINNING FUNCTIONS ====================
def group_by_var_value(data, flag_name, factor_name, bad_name, good_name, discrete_list=[]):
    if len(data) == 0:
        return pd.DataFrame()
    regroup1 = data.groupby([factor_name])[flag_name].count()
    regroup2 = data.groupby([factor_name])[flag_name].sum()
    data1 = pd.DataFrame({good_name: regroup1 - regroup2, bad_name: regroup2}).reset_index()
    good = float(sum(data1[good_name]))
    bad = float(sum(data1[bad_name]))
    total = good + bad
    data1['%Bad_Rate'] = data1[bad_name] / (data1[bad_name] + data1[good_name])
    data1['#Obs'] = (data1[good_name] + data1[bad_name])
    data1['%Obs'] = (data1[good_name] + data1[bad_name]) / total
    data1['%Cum_Obs'] = np.cumsum(data1['%Obs'])
    data1['%Opps_Cum_Obs'] = (1 - np.cumsum(data1['%Obs'])) + data1['%Obs']
    if factor_name not in discrete_list:
        data1 = data1.sort_values(by=[factor_name], ascending=True)
        data1['Char_Type'] = 'numeric'
    else:
        data1 = data1.sort_values(by=['%Bad_Rate'], ascending=True)
        data1['Char_Type'] = 'non-numeric'
    data1 = data1.reset_index(drop=True)
    return data1

def get_na_bin(data_total, flag_name, factor_name, good_name, bad_name):
    data = data_total[(data_total[factor_name].isnull())]
    good_cnt = data[flag_name].sum()
    tn = len(data[data[flag_name].notnull()])
    na_df = pd.DataFrame([["缺失值", good_cnt, tn - good_cnt]],columns=[factor_name, good_name, bad_name])
    return na_df

def important_bin_calculate(data_df,na_df, good_name, bad_name, factor_name, knots_list,if_sort=False):
    flag = data_df['Char_Type'].max()
    temp_df_list = []
    bin_list = []
    for i in range(1, len(knots_list)):
        if i == 1:
            temp_df_list.append(data_df.loc[knots_list[i - 1]:knots_list[i]])
            if flag == 'numeric':
                bin_list.append('(-inf, ' + get_str(data_df[factor_name][knots_list[i]]) + ']')
            else:
                bin_list.append(list(data_df[factor_name])[knots_list[i - 1]:knots_list[i] + 1])
        else:
            temp_df_list.append(data_df.loc[knots_list[i - 1] + 1:knots_list[i]])
            if flag == 'numeric':
                if knots_list[i - 1] + 1 == knots_list[i]:
                    bin_list.append('[' + get_str(data_df[factor_name][knots_list[i]]) + ']')
                elif i == len(knots_list) - 1:
                    bin_list.append('(' + get_str(data_df[factor_name][knots_list[i - 1]]) + ', inf)')
                else:
                    bin_list.append(
                        '(' + get_str(data_df[factor_name][knots_list[i - 1]]) + ', ' + get_str(
                            data_df[factor_name][knots_list[i]]) + ']')
            else:
                bin_list.append(list(data_df[factor_name])[knots_list[i - 1] + 1:knots_list[i] + 1])
    if len(knots_list) == 2:
        bin_list = ['(-inf, inf)']
    if len(na_df) != 0:
        na_good = sum(na_df[good_name])
        na_bad = sum(na_df[bad_name])
        total_good = sum(data_df[good_name]) + na_good
        total_bad = sum(data_df[bad_name]) + na_bad
        temp_df_list.append(na_df)
        bin_list.append("缺失值")
    else:
        na_good = 0
        na_bad = 0
        total_good = sum(data_df[good_name])
        total_bad = sum(data_df[bad_name])
    good_list = list(map(lambda x: sum(x[good_name]), temp_df_list))
    bad_list = list(map(lambda x: sum(x[bad_name]), temp_df_list))
    good_percent_series = pd.Series(list(map(lambda x: float(sum(x[good_name])) / total_good, temp_df_list)))
    bad_percent_series = pd.Series(list(map(lambda x: float(sum(x[bad_name])) / total_bad, temp_df_list)))
    woe_list = list(np.log(good_percent_series / bad_percent_series))
    IV_list = list((good_percent_series - bad_percent_series) * np.log(good_percent_series / bad_percent_series))
    total_list = list(map(lambda x: sum(x[good_name]) + sum(x[bad_name]), temp_df_list))
    bin_rate_list = list(
        map(lambda x: float(sum(x[good_name]) + sum(x[bad_name])) / (total_good + total_bad), temp_df_list))
    non_na_indicator = pd.DataFrame({'Bin': bin_list, '#Obs': total_list, '#Good': good_list, '#Bad': bad_list,
                                     'IV(bin)': IV_list, 'WOE': woe_list, '%Obs': bin_rate_list})
    l = ['Bin', '#Obs', '%Obs', '#Cum_Obs', '%Cum_Obs', '#Good', '%Good', '#Cum_Good', '%Cum_Good',
         '#Bad', '%Bad', '#Cum_Bad', '%Cum_Bad', '%Bad_Rate', 'WOE', 'IV(bin)', 'IV(total)', 'Odds1', 'Odds2', 'Lift']
    result_indicator = non_na_indicator.reset_index(drop=True)
    result_indicator = result_indicator[result_indicator['Bin'] != 'NA']
    if if_sort:
        result_indicator = result_indicator.sort_index(ascending=False).reset_index()
    result_indicator['%Cumulative_Bad_Rate'] = np.cumsum(result_indicator['#Bad']) / np.cumsum(result_indicator['#Obs'])
    result_indicator['WOE'] = result_indicator['WOE'].map(lambda x: 0 if x in [np.inf, -np.inf] else x)
    result_indicator['IV(bin)'] = result_indicator['IV(bin)'].map(lambda x: 0 if x in [np.inf, -np.inf] else x)
    result_indicator['#Cum_Obs'] = np.cumsum(result_indicator['#Obs'])
    result_indicator['%Cum_Obs'] = np.cumsum(result_indicator['%Obs'])
    result_indicator['%Good'] = result_indicator['#Good'] / sum(result_indicator['#Good'])
    result_indicator['#Cum_Good'] = np.cumsum(result_indicator['#Good'])
    result_indicator['%Cum_Good'] = result_indicator['#Cum_Good'] / sum(result_indicator['#Good'])
    result_indicator['%Bad'] = result_indicator['#Bad'] / sum(result_indicator['#Bad'])
    result_indicator['#Cum_Bad'] = np.cumsum(result_indicator['#Bad'])
    result_indicator['%Cum_Bad'] = result_indicator['#Cum_Bad'] / sum(result_indicator['#Bad'])
    result_indicator['%Bad_Rate'] = result_indicator['#Bad'] / result_indicator['#Obs']
    result_indicator['IV(total)'] = np.cumsum(result_indicator['IV(bin)'])
    if len(na_df) != 0:
        result_indicator_1=result_indicator[result_indicator['Bin']!='缺失值']
        result_indicator['Odds1']=((result_indicator_1['#Cum_Bad']/result_indicator_1['#Cum_Good'])/(
            (total_bad -na_bad- result_indicator_1['#Cum_Bad'])/(total_good -na_good- result_indicator_1['#Cum_Good']))).tolist()+[np.nan]
        result_indicator['Odds2']=[np.nan]+list(1/result_indicator['Odds1'])[0:-2]+[np.nan]
    else:
        result_indicator['Odds1'] = (result_indicator['#Cum_Bad']/result_indicator['#Cum_Good'])/(
            (total_bad-result_indicator['#Cum_Bad'])/(total_good-result_indicator['#Cum_Good']))
        result_indicator['Odds2'] = [np.nan] + list(1/result_indicator['Odds1'])[0:-1]
    result_indicator['Lift'] = result_indicator['%Bad_Rate']/(sum(result_indicator['#Bad'])/(sum(result_indicator['#Obs'])))
    result_indicator = result_indicator.replace(np.inf, 0)
    return result_indicator[l]

def get_str(x):
    if type(x) in [float, np.float64, np.float16, np.float32]:
        return ('{0:.17}'.format(x))
    elif type(x) in [int, np.int8, np.int16, np.int32, np.int64]:
        return str(x)
    else:
        try:
            return str(x)
        except:
            return x

def get_bin_lift_with_edges(data, flag_name, factor_name, min_rate=0.001, sub_div_bin=0.1, min_num=5, method='best', max_bins=50, numOfSplit=30):
    """get_bin_lift 的增强版，额外返回 k 表和 knots 列表供 APPLY 使用"""
    k = group_by_var_value(data, flag_name, factor_name, '#Bad', '#Good')
    k1 = get_na_bin(data, flag_name, factor_name, '#Bad', '#Good')
    if len(k) == 0:
        return pd.DataFrame(), None, None
    if method == 'best':
        total = len(data[data[factor_name].notnull()])
        min_rate_act = max(min_num / total, min_rate)
        knot_start = []; obs_start = []; knot_end = []; obs_end = []
        if sub_div_bin >= min_rate_act:
            end_cnt = int(sub_div_bin / min_rate_act)
        else:
            end_cnt = int(min_rate_act / sub_div_bin)
        for i in range(end_cnt):
            if len(knot_start) == 0:
                tmp = k[k['%Cum_Obs'] >= min_rate_act].index.tolist()
            else:
                tmp = k[k['%Cum_Obs'] >= min_rate_act + obs_start[-1]].index.tolist()
            if len(tmp) > 0:
                knot_start.append(tmp[0]); obs_start.append(k['%Cum_Obs'][tmp[0]])
        for i in range(end_cnt):
            if len(knot_end) == 0:
                tmp = k[k['%Opps_Cum_Obs'] >= min_rate_act].index.tolist()
            else:
                tmp = k[k['%Opps_Cum_Obs'] >= min_rate_act + obs_end[-1]].index.tolist()
            if len(tmp) > 0:
                if tmp[-1] > 0:
                    knot_end.append(tmp[-1] - 1); obs_end.append(k['%Opps_Cum_Obs'][tmp[-1]-1])
        knot = sorted(list(set(knot_start + knot_end)))
        if len(k) - 1 in knot:
            knot.remove(len(k) - 1)
        if len(knot) > max_bins:
            knot = knot[:max_bins // 2] + knot[-max_bins // 2:]
    else:
        knot = []
    res1 = important_bin_calculate(k, k1, '#Good', '#Bad', factor_name, [0] + knot + [len(k) - 1])
    return res1, k, knot

def calculate_ks(good, bad):
    cum_good = np.cumsum(good) / np.sum(good) if np.sum(good) > 0 else 0
    cum_bad = np.cumsum(bad) / np.sum(bad) if np.sum(bad) > 0 else 0
    ks = np.abs(cum_good - cum_bad)
    return ks


def fit_cate_bounds(train_df, cat_cols):
    """高基数类别：仅保留样本量>10的档位，其余合并为 Other（与等频/卡方一致）。"""
    info = {}
    for col in cat_cols:
        if col not in train_df.columns:
            continue
        if train_df[col].nunique() > 50:
            vc = train_df[col].value_counts()
            info[col] = vc[vc > 10].index.tolist()
    return info


def transform_cate_series(series, valid_cats=None):
    s = series.astype(str).str.strip()
    s = s.replace(['-999', '-9999', '-999999', 'nan', 'None', '', 'NaT', 'nan'], np.nan)
    s = s.fillna('MISSING')
    if valid_cats is not None:
        s = s.where(s.isin(valid_cats), 'Other')
    return s


def get_cate_bin_detail(data, flag_name, factor_name):
    """类别变量：每个类别一箱，输出与头尾5%数值分箱相同列结构。"""
    k = group_by_var_value(data, flag_name, factor_name, '#Bad', '#Good', discrete_list=[factor_name])
    if len(k) == 0:
        return pd.DataFrame(), None
    good = k['#Good'].values.astype(float)
    bad = k['#Bad'].values.astype(float)
    total_good = good.sum()
    total_bad = bad.sum()
    total = total_good + total_bad
    good_pct = good / total_good if total_good > 0 else np.zeros_like(good, dtype=float)
    bad_pct = bad / total_bad if total_bad > 0 else np.zeros_like(bad, dtype=float)
    woe = np.log(np.where((good_pct > 0) & (bad_pct > 0), good_pct / bad_pct, 1.0))
    woe = np.where(np.isfinite(woe), woe, 0.0)
    iv = (good_pct - bad_pct) * woe
    result = pd.DataFrame({
        'Bin': k[factor_name].astype(str).values,
        '#Obs': k['#Obs'].values,
        '#Good': good,
        '#Bad': bad,
        '%Obs': k['#Obs'].values / total if total > 0 else 0,
        '%Bad_Rate': k['%Bad_Rate'].values,
        'WOE': woe,
        'IV(bin)': iv,
    })
    result['#Cum_Obs'] = np.cumsum(result['#Obs'])
    result['%Cum_Obs'] = np.cumsum(result['%Obs'])
    result['%Good'] = result['#Good'] / total_good if total_good > 0 else 0
    result['#Cum_Good'] = np.cumsum(result['#Good'])
    result['%Cum_Good'] = result['#Cum_Good'] / total_good if total_good > 0 else 0
    result['%Bad'] = result['#Bad'] / total_bad if total_bad > 0 else 0
    result['#Cum_Bad'] = np.cumsum(result['#Bad'])
    result['%Cum_Bad'] = result['#Cum_Bad'] / total_bad if total_bad > 0 else 0
    result['IV(total)'] = np.cumsum(result['IV(bin)'])
    portfolio_br = total_bad / total if total > 0 else 0
    result['Lift'] = result['%Bad_Rate'] / portfolio_br if portfolio_br > 0 else 0
    result['Odds1'] = np.nan
    result['Odds2'] = np.nan
    return result, k


def _split_numeric_and_cate_columns(df):
    """object/category 能转数值的保留为数值，否则作为类别变量参与分箱。"""
    cate_cols = []
    for col in df.select_dtypes(include=['object', 'category']).columns:
        s = df[col].astype(str).str.strip()
        s = s.replace(['-999', '-9999', '-999999', 'nan', 'None', '', 'NaT'], np.nan)
        try:
            df[col] = pd.to_numeric(s, errors='raise')
        except (ValueError, TypeError):
            cate_cols.append(col)
    return df, cate_cols


def _apply_cate_preprocess(df, cate_cols, cate_bounds):
    for col in cate_cols:
        if col in df.columns:
            df[col] = transform_cate_series(df[col], cate_bounds.get(col))
    return df


# ==================== RUN BINNING ON TRAIN ====================
print("="*50)
print("正在对 Train 数据做头尾5%分箱 (FIT)...")

# Clean object/category columns (train): 可转数值的转数值，否则保留为类别变量
train_raw, cate_cols = _split_numeric_and_cate_columns(train_raw)
cate_bounds = fit_cate_bounds(train_raw, cate_cols)
train_raw = _apply_cate_preprocess(train_raw, cate_cols, cate_bounds)
if cate_cols:
    print(f'类别变量 {len(cate_cols)} 个，将按类别分箱')

train_raw['agr_label'] = 1
my_data = train_raw

sample_range = '头尾5%分箱'
seq = 1
sample_type = ['Total']
sample_type_col = {}
sample_type_target = {'Total': ['dpd_7']}
target_ripe = {'dpd_7': ['agr_label']}
target_del_col = {'dpd_7': ['agr_label']}
sub_div_bin = 0.1
target_min_rate = {'dpd_7': [0.01]}
min_num = 20
hit_num = 10
sample_type_lift = {'Total': {'dpd_7': 2.5}}

# Minimal describe_stat_ana to generate var_select_01 metadata
var_select_01 = pd.DataFrame()
col_seq = ["序号", "分析时间", "样本类型", "坏客户定义", "变量英文名", "变量中文名", "样本区间",
           "%Bad_Rate(不含缺失值)", "%Bad_Rate(包含缺失值)", "总样本量","坏样本量", "缺失量", "缺失率",
           "变量取值数（包含缺失值）", "变量取值数（不含缺失值）"] + [f"d{i}" for i in range(24)] + ["标签1"]
for sample_type_sub in sample_type:
    if 'Total' in sample_type_sub:
        describedata = my_data
    else:
        describedata = my_data
    target = sample_type_target[sample_type_sub]
    for target_sub in target:
        mydata1 = describedata[describedata[target_ripe[target_sub][0]] == 1]
        mydata1 = mydata1.drop(labels=target_del_col[target_sub], axis=1)
        for var in mydata1.columns[:-1]:
            data_nona = mydata1[[var, target_sub]].dropna()
            bad_rate_nona = data_nona[target_sub].mean() if len(data_nona) > 0 else 0
            bad_rate_withna = mydata1[target_sub].mean()
            na_rate = mydata1[var].isnull().mean()
            unique_withna = mydata1[var].nunique(dropna=False)
            row_data = ['wtt'+str(seq), datetime.now().strftime('%Y-%m-%d'), sample_type_sub, target_sub,
                       var, var, sample_range, bad_rate_nona, bad_rate_withna, len(mydata1),
                       sum(mydata1[target_sub]==1), mydata1[var].isnull().sum(), na_rate,
                       unique_withna] + [np.nan]*25 + ['Y']
            seq += 1
            row_df = pd.DataFrame([row_data], columns=col_seq)
            var_select_01 = pd.concat([var_select_01, row_df], ignore_index=True)

var_select_01['标签2'] = 'N'
var_select_01['标签3'] = 'N'

# Build train detail with stored edges
train_bins_detail_rows = []
train_stored_edges = {}

for sample_type_sub in sample_type:
    describedata = my_data if 'Total' in sample_type_sub else my_data
    target = sample_type_target[sample_type_sub]
    for target_sub in target:
        mydata1 = describedata[describedata[target_ripe[target_sub][0]] == 1]
        mydata1 = mydata1.drop(labels=target_del_col[target_sub], axis=1)
        min_rate = target_min_rate[target_sub][0]
        print(sample_type_sub, target_sub, '每箱最小占比：', min_rate, '数据量：', len(mydata1))
        for var in mydata1.columns[:-1]:
            print('正在分析变量:', var)
            try:
                if var in cate_cols:
                    sample_bin, k = get_cate_bin_detail(mydata1, target_sub, var)
                    if len(sample_bin) == 0:
                        continue
                    train_stored_edges[var] = {'type': 'cate', 'k': k}
                else:
                    sample_bin, k, knot = get_bin_lift_with_edges(
                        data=mydata1, flag_name=target_sub, factor_name=var,
                        min_rate=min_rate, sub_div_bin=sub_div_bin, min_num=min_num,
                        method='best', numOfSplit=25
                    )
                    if len(sample_bin) == 0:
                        continue
                    train_stored_edges[var] = {'k': k, 'knots': knot}
                good = sample_bin['#Good'].values
                bad = sample_bin['#Bad'].values
                ks_values = calculate_ks(good, bad)
                sample_bin['bin_ks'] = ks_values
                sample_bin['total_ks'] = max(ks_values)
                iv_total = sample_bin['IV(bin)'].sum()
                sample_bin['IV(total)'] = iv_total
                total_obs = sample_bin['#Obs'].sum()
                sample_bin['WOE(total)'] = (sample_bin['WOE'] * sample_bin['#Obs']).sum() / total_obs if total_obs > 0 else np.nan
            except Exception as e:
                print(f"  分箱出错: {e}")
                continue
            sample_bin['变量英文名'] = var
            sample_bin['样本类型'] = sample_type_sub
            sample_bin['坏客户定义'] = target_sub
            var_msg = var_select_01.loc[
                (var_select_01.样本类型 == sample_type_sub) &
                (var_select_01.变量英文名 == var) &
                (var_select_01.坏客户定义 == target_sub),
                ['序号', '分析时间', '样本类型', '坏客户定义', '变量英文名', '变量中文名', '样本区间', '标签1', '标签2', '标签3']
            ]
            if len(var_msg) > 0:
                merge = pd.merge(var_msg, sample_bin, on=['变量英文名', '样本类型', '坏客户定义'], how='left')
                train_bins_detail_rows.append(merge)

train_bins_detail = pd.concat(train_bins_detail_rows, ignore_index=True) if train_bins_detail_rows else pd.DataFrame()

# Add money rate for train
if len(train_bins_detail) > 0 and money_train is not None:
    bindata_reset = my_data.reset_index(drop=True)
    bindata_with_money = bindata_reset.copy()
    money_vals = money_train.reset_index(drop=True).values if hasattr(money_train, 'reset_index') else money_train
    bindata_with_money['money'] = money_vals
    money_rate_list = []
    for _, row in train_bins_detail.iterrows():
        var = row['变量英文名']; bin_label = row['Bin']; target_sub = row['坏客户定义']
        if var not in bindata_with_money.columns:
            money_rate_list.append(np.nan); continue
        try:
            if bin_label == '缺失值':
                mask = bindata_with_money[var].isnull()
            elif bin_label.startswith('(-inf,'):
                mask = bindata_with_money[var] <= float(bin_label.split(',')[1].strip().rstrip(']'))
            elif bin_label.endswith('inf)'):
                mask = bindata_with_money[var] > float(bin_label.split('(')[1].split(',')[0].strip())
            elif bin_label.startswith('(') and ',' in bin_label:
                lower = float(bin_label.split('(')[1].split(',')[0].strip())
                upper = float(bin_label.split(',')[1].strip().rstrip(']'))
                mask = (bindata_with_money[var] > lower) & (bindata_with_money[var] <= upper)
            elif bin_label.startswith('[') and bin_label.endswith(']') and ',' not in bin_label:
                mask = bindata_with_money[var] == float(bin_label.strip('[]'))
            elif var in cate_cols:
                mask = bindata_with_money[var].astype(str) == str(bin_label)
            else:
                money_rate_list.append(np.nan); continue
            sub = bindata_with_money[mask]
            rate = sub.loc[sub[target_sub]==1,'money'].sum() / sub['money'].sum() if sub['money'].sum() > 0 else np.nan
            money_rate_list.append(rate)
        except:
            money_rate_list.append(np.nan)
    train_bins_detail['金额逾期率'] = money_rate_list

print(f'Train 分箱完成: {len(train_bins_detail)} 行, {len(train_stored_edges)} 个变量')

# ==================== APPLY ON TEST ====================
if actual_oot:
    print("="*50)
    print("正在对 Test 数据做头尾5%分箱 (APPLY)...")

    test_raw, _test_cate = _split_numeric_and_cate_columns(test_raw)
    for col in cate_cols:
        if col not in test_raw.columns:
            continue
        test_raw[col] = transform_cate_series(test_raw[col], cate_bounds.get(col))

    test_raw['agr_label'] = 1
    test_bins_detail_rows = []

    for sample_type_sub in sample_type:
        describedata = test_raw if 'Total' in sample_type_sub else test_raw
        target = sample_type_target[sample_type_sub]
        for target_sub in target:
            mydata1_test = describedata[describedata[target_ripe[target_sub][0]] == 1]
            mydata1_test = mydata1_test.drop(labels=target_del_col[target_sub], axis=1)
            print(sample_type_sub, target_sub, 'Test 数据量：', len(mydata1_test))
            test_processed = 0
            for var in mydata1_test.columns[:-1]:
                if var not in train_stored_edges:
                    continue
                test_processed += 1
                print('  正在 APPLY 变量:', var)
                try:
                    train_info = train_stored_edges[var]
                    if train_info.get('type') == 'cate':
                        sample_bin_test, _ = get_cate_bin_detail(mydata1_test, target_sub, var)
                        if len(sample_bin_test) == 0:
                            continue
                    else:
                        k_test = group_by_var_value(mydata1_test, target_sub, var, '#Bad', '#Good')
                        k1_test = get_na_bin(mydata1_test, target_sub, var, '#Bad', '#Good')
                        if len(k_test) == 0:
                            continue
                        knot = train_info['knots']
                        max_idx = len(k_test) - 1
                        clamped_knot = [k for k in knot if k < max_idx]
                        if len(clamped_knot) == 0 and max_idx >= 0:
                            clamped_knot = [max_idx // 2]
                        sample_bin_test = important_bin_calculate(
                            k_test, k1_test, '#Good', '#Bad', var,
                            [0] + clamped_knot + [max_idx]
                        )
                        if len(sample_bin_test) == 0:
                            continue
                    good = sample_bin_test['#Good'].values
                    bad = sample_bin_test['#Bad'].values
                    ks_values = calculate_ks(good, bad)
                    sample_bin_test['bin_ks'] = ks_values
                    sample_bin_test['total_ks'] = max(ks_values)
                    iv_total = sample_bin_test['IV(bin)'].sum()
                    sample_bin_test['IV(total)'] = iv_total
                    total_obs = sample_bin_test['#Obs'].sum()
                    sample_bin_test['WOE(total)'] = (sample_bin_test['WOE'] * sample_bin_test['#Obs']).sum() / total_obs if total_obs > 0 else np.nan
                except Exception as e:
                    print(f"  APPLY 出错 ({var}): {e}")
                    continue
                sample_bin_test['变量英文名'] = var
                sample_bin_test['样本类型'] = sample_type_sub
                sample_bin_test['坏客户定义'] = target_sub
                var_msg = var_select_01.loc[
                    (var_select_01.样本类型 == sample_type_sub) &
                    (var_select_01.变量英文名 == var) &
                    (var_select_01.坏客户定义 == target_sub),
                    ['序号', '分析时间', '样本类型', '坏客户定义', '变量英文名', '变量中文名', '样本区间', '标签1', '标签2', '标签3']
                ]
                if len(var_msg) > 0:
                    merge = pd.merge(var_msg, sample_bin_test, on=['变量英文名', '样本类型', '坏客户定义'], how='left')
                    test_bins_detail_rows.append(merge)

    test_bins_detail = pd.concat(test_bins_detail_rows, ignore_index=True) if test_bins_detail_rows else pd.DataFrame()
    print(f"Test APPLY: processed {test_processed} variables, results: {len(test_bins_detail)} rows")

    # Add money rate for test
    if len(test_bins_detail) > 0 and money_test is not None:
        bindata_reset_test = test_raw.reset_index(drop=True)
        bindata_with_money_test = bindata_reset_test.copy()
        money_vals_test = money_test.reset_index(drop=True).values if hasattr(money_test, 'reset_index') else money_test
        bindata_with_money_test['money'] = money_vals_test
        money_rate_list_test = []
        for _, row in test_bins_detail.iterrows():
            var = row['变量英文名']; bin_label = row['Bin']; target_sub = row['坏客户定义']
            if var not in bindata_with_money_test.columns:
                money_rate_list_test.append(np.nan); continue
            try:
                if bin_label == '缺失值':
                    mask = bindata_with_money_test[var].isnull()
                elif bin_label.startswith('(-inf,'):
                    mask = bindata_with_money_test[var] <= float(bin_label.split(',')[1].strip().rstrip(']'))
                elif bin_label.endswith('inf)'):
                    mask = bindata_with_money_test[var] > float(bin_label.split('(')[1].split(',')[0].strip())
                elif bin_label.startswith('(') and ',' in bin_label:
                    lower = float(bin_label.split('(')[1].split(',')[0].strip())
                    upper = float(bin_label.split(',')[1].strip().rstrip(']'))
                    mask = (bindata_with_money_test[var] > lower) & (bindata_with_money_test[var] <= upper)
                elif bin_label.startswith('[') and bin_label.endswith(']') and ',' not in bin_label:
                    mask = bindata_with_money_test[var] == float(bin_label.strip('[]'))
                elif var in cate_cols:
                    mask = bindata_with_money_test[var].astype(str) == str(bin_label)
                else:
                    money_rate_list_test.append(np.nan); continue
                sub = bindata_with_money_test[mask]
                rate = sub.loc[sub[target_sub]==1,'money'].sum() / sub['money'].sum() if sub['money'].sum() > 0 else np.nan
                money_rate_list_test.append(rate)
            except:
                money_rate_list_test.append(np.nan)
        test_bins_detail['金额逾期率'] = money_rate_list_test

    print(f'Test 分箱完成: {len(test_bins_detail)} 行')

# ==================== OUTPUT ====================
print("="*50)
print("正在输出 Excel...")

required_cols = ['序号', '分析时间', '样本类型', '坏客户定义', '变量英文名', '变量中文名', '样本区间', '标签1', '标签2', '标签3',
                 'Bin', '#Obs', '%Obs', '#Cum_Obs', '%Cum_Obs', '#Good', '%Good', '#Cum_Good', '%Cum_Good',
                 '#Bad', '%Bad', '#Cum_Bad', '%Cum_Bad', '%Bad_Rate', 'WOE', 'IV(bin)', 'IV(total)', 'Odds1', 'Odds2',
                 'Lift', 'bin_ks', 'total_ks', '金额逾期率']

for col in required_cols:
    if col not in train_bins_detail.columns:
        train_bins_detail[col] = np.nan
if actual_oot and len(test_bins_detail) > 0:
    for col in required_cols:
        if col not in test_bins_detail.columns:
            test_bins_detail[col] = np.nan

train_bins_detail = train_bins_detail[required_cols]
if actual_oot and len(test_bins_detail) > 0:
    test_bins_detail = test_bins_detail[required_cols]

if '序号' in train_bins_detail.columns:
    train_bins_detail['序号'] = train_bins_detail['序号'].fillna('wtt0').astype(str)

writer = pd.ExcelWriter(output_file, engine='xlsxwriter')
wb = writer.book

details_result_output(wb=wb, sheetname='Train分箱明细', data=train_bins_detail, suoyin=0, ana_people='wtt')

if actual_oot and len(test_bins_detail) > 0:
    if '序号' in test_bins_detail.columns:
        test_bins_detail['序号'] = test_bins_detail['序号'].fillna('wtt0').astype(str)
    details_result_output(wb=wb, sheetname='Test分箱明细', data=test_bins_detail, suoyin=0, ana_people='wtt')

writer.close()
print(f'输出完成: {output_file}')
if actual_oot:
    print(f'  Sheet1: Train分箱明细 ({len(train_bins_detail)} 行, {len(train_stored_edges)} 变量)')
    print(f'  Sheet2: Test分箱明细 ({len(test_bins_detail)} 行)')
else:
    print(f'  Sheet1: Train分箱明细 ({len(train_bins_detail)} 行, 全量无切分)')
print("="*50)
