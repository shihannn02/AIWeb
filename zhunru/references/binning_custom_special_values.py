"""
自定义分箱：特殊值(-999/0/NaN)各单独一箱 + 其余值等频分箱
适用场景：用户要求把缺失填充值、零值、空值分别作为独立分箱
（非标准需求，用户明确说"只有这一次需要"）

特点：
- -999 (缺失填充值) → 单独一箱 '特殊值(-999)'
- 0 (零值) → 单独一箱 '特殊值(0)'
- NaN (空值) → 单独一箱 '空值(NaN)'
- 其余正常值 → pd.qcut 等频分箱 (默认10箱)
- 支持 FIT/APPLY 模式：从 Train 学边界，Test 用同一边界
- 支持条件切分（日期截止点）或 iloc 比例切分

参数修改区在脚本底部 main() 调用处。
"""

import pandas as pd
import numpy as np
from tqdm import tqdm
import warnings
warnings.filterwarnings("ignore")


def fit_bin_frequency(x, y, n=10):
    """学习等频分箱边界（仅正常值），记录特殊值是否存在"""
    nan_mask = x.isna()
    neg999_mask = (x == -999) & ~nan_mask
    zero_mask = (x == 0) & ~nan_mask & ~neg999_mask
    normal_mask = ~nan_mask & ~neg999_mask & ~zero_mask

    x_normal = x[normal_mask]
    bin_edges = None
    if len(x_normal) > 0 and x_normal.nunique() > 1:
        try:
            _, edges = pd.qcut(x_normal, n, retbins=True, duplicates='drop')
            bin_edges = list(edges)
        except Exception:
            bin_edges = None

    return {
        'bin_edges': bin_edges,
        'has_nan': nan_mask.any(),
        'has_neg999': neg999_mask.any(),
        'has_zero': zero_mask.any(),
        'total_all': len(y),
        'bad_all': y.sum(),
        'good_all': len(y) - y.sum(),
    }


def apply_bin_frequency(x, y, fit_result):
    """用 Train 的边界对数据分箱"""
    epsilon = 1e-6
    total_all = fit_result['total_all']
    bad_all = fit_result['bad_all']
    good_all = fit_result['good_all']

    # 分离特殊值和正常值
    nan_mask = x.isna()
    neg999_mask = (x == -999) & ~nan_mask
    zero_mask = (x == 0) & ~nan_mask & ~neg999_mask
    normal_mask = ~nan_mask & ~neg999_mask & ~zero_mask

    x_normal = x[normal_mask]
    y_normal = y[normal_mask]

    # 正常值分箱
    bin_edges = fit_result['bin_edges']
    if bin_edges is not None and len(x_normal) > 0:
        try:
            bin_labels = pd.cut(x_normal, bins=bin_edges, duplicates='drop', include_lowest=True)
            d1 = pd.DataFrame({'x': x_normal, 'y': y_normal, 'bucket': bin_labels})
            d2 = d1.groupby('bucket', as_index=True, observed=True)
            d3 = pd.DataFrame({
                'min_bin': d2.x.min(),
                'max_bin': d2.x.max(),
                'bad': d2.y.sum(),
                'total': d2.y.count(),
                'bin_label': d2.apply(lambda g: str(g.name)),
            }).reset_index(drop=True)
        except Exception:
            d3 = pd.DataFrame({
                'min_bin': [x_normal.min()], 'max_bin': [x_normal.max()],
                'bad': [y_normal.sum()], 'total': [len(y_normal)],
                'bin_label': [f'[{x_normal.min():.4f}, {x_normal.max():.4f}]'],
            })
    else:
        d3 = pd.DataFrame(columns=['min_bin', 'max_bin', 'bad', 'total', 'bin_label'])

    # 构造特殊值行
    special_rows = []
    if fit_result['has_neg999'] and neg999_mask.any():
        special_rows.append({'min_bin': -999.0, 'max_bin': -999.0,
                             'bad': y[neg999_mask].sum(), 'total': neg999_mask.sum(),
                             'bin_label': '特殊值(-999)'})
    if fit_result['has_zero'] and zero_mask.any():
        special_rows.append({'min_bin': 0.0, 'max_bin': 0.0,
                             'bad': y[zero_mask].sum(), 'total': zero_mask.sum(),
                             'bin_label': '特殊值(0)'})
    if fit_result['has_nan'] and nan_mask.any():
        special_rows.append({'min_bin': np.nan, 'max_bin': np.nan,
                             'bad': y[nan_mask].sum(), 'total': nan_mask.sum(),
                             'bin_label': '空值(NaN)'})

    if special_rows:
        d_special = pd.DataFrame(special_rows)
        d3 = pd.concat([d3, d_special], ignore_index=True)

    # 确保数值类型
    for c in ['bad', 'total']:
        if c in d3.columns:
            d3[c] = pd.to_numeric(d3[c], errors='coerce').fillna(0).astype(int)

    # 计算指标
    d3['bad_rate'] = d3.apply(lambda r: r['bad'] / r['total'] if r['total'] > 0 else 0.0, axis=1)
    d3['badattr'] = d3['bad'] / bad_all if bad_all > 0 else 0.0
    d3['goodattr'] = (d3['total'] - d3['bad']) / good_all if good_all > 0 else 0.0
    d3['woe'] = np.log((d3['badattr'] + epsilon) / (d3['goodattr'] + epsilon))
    d3['cum_bad_rate'] = bad_all / total_all
    d3['lift'] = d3['bad_rate'] / d3['cum_bad_rate']
    d3['bins_iv'] = (d3['badattr'] - d3['goodattr']) * d3['woe']
    d3['total_iv'] = d3['bins_iv'].sum()

    # 排序：正常箱按 min_bin 排序，特殊箱在后
    normal_df = d3[~d3['bin_label'].str.startswith(('特殊值', '空值'), na=False)] \
        .sort_values('min_bin').reset_index(drop=True)
    special_df = d3[d3['bin_label'].str.startswith(('特殊值', '空值'), na=False)] \
        .reset_index(drop=True)
    d4 = pd.concat([normal_df, special_df], ignore_index=True)

    # 累计指标和 KS
    d4['acu_bin_badrate'] = d4['badattr'].cumsum()
    d4['acu_bin_goodrate'] = d4['goodattr'].cumsum()
    d4['bin_ks'] = (d4['acu_bin_badrate'] - d4['acu_bin_goodrate']).abs().round(4)
    d4['total_ks'] = d4['bin_ks'].max()
    d4['acu_badnum'] = d4['bad'].cumsum()
    d4['acu_allnum'] = d4['total'].cumsum()
    d4['acu_badrate'] = d4['acu_badnum'] / d4['acu_allnum']

    out_cols = ['min_bin', 'max_bin', 'bin_label', 'bad', 'acu_badnum', 'total',
                'acu_allnum', 'bad_rate', 'cum_bad_rate', 'acu_badrate', 'badattr',
                'acu_bin_badrate', 'goodattr', 'acu_bin_goodrate', 'bins_iv',
                'total_iv', 'bin_ks', 'total_ks', 'woe', 'lift']
    return d4[out_cols]


def run_binning(file_path, output_file, label, time_col, bin_num=10,
                drop_cols=None, train_cutoff=None, encoding='gbk'):
    """
    主流程：读取 → 过滤标签 → 切分 → 预处理 → 分箱 → 输出 Excel

    Parameters
    ----------
    file_path : str
        输入数据路径 (.csv 或 .xlsx)
    output_file : str
        输出 Excel 路径
    label : str
        标签列名
    time_col : str
        时间列名（用于切分）
    bin_num : int
        等频箱数
    drop_cols : list
        切分后删除的无关列
    train_cutoff : str or None
        条件切分日期截止点，如 "2026-05-01"。
        None = 不切分全量用（OOT_RATIO=0 模式）
    encoding : str
        CSV 文件编码
    """
    if drop_cols is None:
        drop_cols = ['client_id', 'apply_id', 'pkid', 'pid']

    # 1. 读取
    print(f"读取: {file_path}")
    if file_path.endswith('.csv'):
        data = pd.read_csv(file_path, encoding=encoding)
    else:
        data = pd.read_excel(file_path)
    print(f"原始大小: {data.shape}")

    # 2. 过滤标签（仅 0/1）
    data = data[data[label].isin([0, 1])].copy()
    data[label] = data[label].astype(int)
    print(f"大盘坏率: {data[label].mean():.4%}")

    # 3. 切分
    if train_cutoff is not None:
        train = data[data[time_col] < train_cutoff].copy()
        test = data[data[time_col] >= train_cutoff].copy()
    else:
        train = data.copy()
        test = data.copy()
    print(f"Train: {len(train)} 行 ({train[label].mean():.2%}), "
          f"Test: {len(test)} 行 ({test[label].mean():.2%})")

    # 4. 删除无关列（切分后，确保时间列已用完）
    drop_target = [c for c in drop_cols if c in train.columns] + [time_col]
    train = train.drop(columns=drop_target, errors='ignore')
    test = test.drop(columns=drop_target, errors='ignore')

    # 5. 预处理：将缺失标记替换为 -999，但保留 NaN
    missing_sentinels = [
        -9999979.0, -999979.0, -999990, -999999, -99999,
        -9999.0, -1111, "", " ", None, "NaN", "None", "none", pd.NaT
    ]

    def _preprocess(df):
        df = df.copy()
        for col in df.select_dtypes(include=[np.number]).columns:
            for sv in missing_sentinels:
                if sv is not np.nan and sv is not pd.NaT and sv is not None:
                    df[col] = df[col].replace(sv, -999)
        return df

    train = _preprocess(train)
    test = _preprocess(test)

    # 6. 数值特征列
    feature_cols = [c for c in train.columns
                    if c != label and np.issubdtype(train[c].dtype, np.number)]
    print(f"数值特征: {len(feature_cols)}")

    # 7. 分箱
    all_train, all_test = [], []
    for col in tqdm(feature_cols, desc='Binning'):
        fit = fit_bin_frequency(train[col], train[label], n=bin_num)
        tr = apply_bin_frequency(train[col], train[label], fit)
        tr.insert(0, 'feature', col)
        all_train.append(tr)
        te = apply_bin_frequency(test[col], test[label], fit)
        te.insert(0, 'feature', col)
        all_test.append(te)

    train_final = pd.concat(all_train, ignore_index=True)
    test_final = pd.concat(all_test, ignore_index=True)

    # 8. 输出 Excel
    writer = pd.ExcelWriter(output_file, engine='xlsxwriter')
    train_final.to_excel(writer, sheet_name='Train分箱明细', index=False)
    test_final.to_excel(writer, sheet_name='Test分箱明细', index=False)

    workbook = writer.book
    fmt_gray = workbook.add_format({'bg_color': '#EBEBEB'})
    fmt_white = workbook.add_format({'bg_color': '#FFFFFF'})

    for sheet_name, df_data in [('Train分箱明细', train_final), ('Test分箱明细', test_final)]:
        ws = writer.sheets[sheet_name]
        col_br = df_data.columns.get_loc('bad_rate')
        bar_color = '#5DADE2' if sheet_name == 'Train分箱明细' else '#9B59B6'
        ws.conditional_format(1, col_br, len(df_data), col_br,
                              {'type': 'data_bar', 'bar_color': bar_color, 'bar_solid': True})
        feats = df_data['feature'].tolist()
        flag, prev = 0, None
        for i, f in enumerate(feats):
            if f != prev:
                flag = 1 - flag
                prev = f
            ws.set_row(i + 1, None, fmt_gray if flag == 1 else fmt_white)

    writer.close()
    print(f"输出: {output_file}")


# ============================================================
# 使用示例（修改此处参数后运行）
# ============================================================
if __name__ == '__main__':
    run_binning(
        file_path=r'C:\Users\6\Desktop\测试数据qiongdan\安卓高息贷后0301以后行为埋点.csv',
        output_file=r'C:\Users\6\Desktop\测试数据qiongdan\分箱结果_等频.xlsx',
        label='target7',
        time_col='apply_date',
        bin_num=10,
        drop_cols=['client_id', 'apply_id', 'pkid', 'pid'],
        train_cutoff='2026-05-01',  # None 表示全量不分
        encoding='gbk',
    )
