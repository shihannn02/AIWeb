import pandas as pd
import numpy as np
from tqdm import tqdm

import warnings
warnings.filterwarnings("ignore")
import sys, os
_this_dir = os.path.dirname(os.path.abspath(__file__))
_bbbrisk_dir = os.path.join(_this_dir, "bbbrisk")
if os.path.dirname(_bbbrisk_dir) not in sys.path:
    sys.path.insert(0, os.path.dirname(_bbbrisk_dir))
import bbbrisk as br

# -------------------------------------------------------------------
# ----------------------------数据读取--------------------------------
# -------------------------------------------------------------------

file_path = r'C:\Users\6\Desktop\新准入规则\toufang_变量_放款_非白.xlsx'
output_file = r'C:\Users\6\Desktop\新准入规则\分箱结果_卡方.xlsx'

data = pd.read_excel(file_path)
df1 = data.copy()
if 'product' in data.columns:
    df1 = data[data['product'] != 'unKnow'].copy()
print(f'='*50)
print(f'数据大小：{df1.shape}')

# --------------------------------------------------------------------
# ----------------------------修改参数--------------------------------
# --------------------------------------------------------------------
drop_cols = ['id_x', 'id_y', 'client_id', 'apply_id', 'pkid', 'pid', 'product', 'serial_number', 'money', 'fact_money', 'fact_repay_money', 'create_time_y', 'risk_over_days'] # 不需要分箱的变量
label = 'overdue_flag2' # 逾期标签
time_col = 'create_time_x'  # OOT_RATIO=0, 时间列无关紧要  # 用于划分训练集和测试集的列
threshold_missing = 0.95 # 缺失值的threshold，大于这个threshold要删除
threshold_concentration = 0.95 # 集中度的threshold，大于这个threshold要删除
threshold_corr = 0.9 # 相关性的threshold，大于这个threshold要删除
bin_num = 10
init_bin_num = 20

# 是否筛选PSI,IV and correlation
OOT_RATIO = 0.2 # train和test的比例是多少，默认是0.3
LONGTAIL_FILTER = False # 长尾截断
MISSING_FILTER = False # 缺失值
CONCENTRATION_FILTER = False # 集中度
PSI_FILTER = False # PSI
IV_FILTER = False # IV
CORR_FILTER = False # 相关性
INCLUDE_CAT = True  # 是否保留cat类型 ==> True: 保留类别特征并分箱; False: 剔除类别特征

df1 = df1.drop(columns=[c for c in drop_cols if c in df1.columns], errors='ignore')  # create_time_x kept for OOT split

# --------------------------------------------------------------------
# --------------------------------代码--------------------------------
# --------------------------------------------------------------------

def parse_time(x):
    try:
        if str(x).isdigit():
            return pd.Timestamp(int(x), unit='s')
        else:
            return pd.to_datetime(x)
    except:
        return pd.NaT
    
def fit_preprocess(train_df):
    cat_cols = train_df.select_dtypes(include=['object', 'category']).columns
    large_cat_info = {}
    
    for col in cat_cols:
        if train_df[col].nunique() > 50: # 对大于50的数据，找到类别小于10的
            valid_cats = train_df[col].value_counts()
            valid_cats = valid_cats[valid_cats > 10].index.tolist()
            large_cat_info[col] = valid_cats
    
    return large_cat_info

def transform_preprocess(df, large_cat_info):
    df = df.copy()
    
    missing_values = [
        -9999979.0, -999979.0, -999.000, -999990,
        -999999, -999, -999999, -99999, -9999979, -9999.0,
        "-9999979", "-999979", "-999", "-999999",
        "-99999", "-99", "-999.0", "-9999.0", "-9999", "-9999979.0", "-99999.0",
        "", " ", None, np.nan, "NaN", "None", "none", "NaT", pd.NaT
    ]
    num_cols = df.select_dtypes(include=[np.number]).columns
    df[num_cols] = df[num_cols].fillna(-999)
    df[num_cols] = df[num_cols].replace(missing_values, -999)
    
    cat_cols = df.select_dtypes(include=['object', 'category']).columns
    df[cat_cols] = df[cat_cols].fillna('MISSING')
    
    for col, valid_cats in large_cat_info.items():
        if col in df.columns:
            df[col] = df[col].where(df[col].isin(valid_cats), 'Other')
    
    return df

def fit_cap_outliers(df, low_quantile=0.01, high_quantile=0.99):
    bounds = {}
    num_cols = df.select_dtypes(include=[np.number]).columns
    for col in num_cols:
        bounds[col] = {
            'low': df[col].quantile(low_quantile),
            'high': df[col].quantile(high_quantile)
        }
    return bounds

def transform_cap_outliers(df, bounds):
    df = df.copy()
    for col, bound in bounds.items():
        if col in df.columns:
            df[col] = df[col].clip(lower=bound['low'], upper=bound['high'])
    return df

# 缺失率高转化成布尔特征
def analyze_missing_distribution(train, step=0.05):
    """分析缺失率分布，帮助决定阈值"""
    missing_rate = (train == -999).mean()
    
    bins = np.arange(0, 1 + step, step)
    labels = [f"{int(l*100)}%~{int(r*100)}%" for l, r in zip(bins[:-1], bins[1:])]
    
    distribution = pd.cut(missing_rate, bins=bins, labels=labels, include_lowest=True)
    dist_df = distribution.value_counts().sort_index().reset_index()
    dist_df.columns = ['missing_rate_range', 'feature_count']
    
    print(f'='*50)
    print("Missing Distribution")
    print(dist_df.to_string(index=False))
    
    return dist_df

# 1.删除缺失值>85%的特征
def drop_missing_cols(train, oot, threshold_missing):
    missing_rate = (train == -999).mean()

    drop_cols = missing_rate[missing_rate > threshold_missing].index
    train = train.drop(columns=drop_cols)
    oot = oot.drop(columns=drop_cols)

    print(f'='*50)
    print(f"缺失率>{threshold_missing*100}%删掉了{len(drop_cols)}列，剩余{train.shape[1]}列")
    return train, oot

def fit_drop_concentration(df, threshold_concentration, label):

    feature_cols = [col for col in df.columns if col != label] 
    concentration = df[feature_cols].apply(lambda x: x.value_counts(normalize=True).iloc[0])

    drop_cols = concentration[concentration>threshold_concentration].index
    remain_cols = [col for col in feature_cols if col not in drop_cols]

    return remain_cols

def calculate_num_psi(base_list, test_list, bins=20, min_sample=10):
    try:

        base_df = pd.DataFrame(base_list, columns=['score'])
        test_df = pd.DataFrame(test_list, columns=['score']) 
        
        # 1.去除缺失值后，统计两个分布的样本量
        base_notnull_cnt = len(list(base_df['score'].dropna()))
        test_notnull_cnt = len(list(test_df['score'].dropna()))

        # 空分箱
        base_null_cnt = len(base_df) - base_notnull_cnt
        test_null_cnt = len(test_df) - test_notnull_cnt
        
        # 2.最小分箱数
        q_list = []
        if type(bins) == int:
            bin_num = min(bins, int(base_notnull_cnt / min_sample))
            q_list = [x / bin_num for x in range(1, bin_num)]
            break_list = []
            for q in q_list:
                bk = base_df['score'].quantile(q)
                break_list.append(bk)
            break_list = sorted(list(set(break_list))) # 去重复后排序
            score_bin_list = [-np.inf] + break_list + [np.inf]
        else:
            score_bin_list = bins
        
        # 4.统计各分箱内的样本量
        base_cnt_list = [base_null_cnt]
        test_cnt_list = [test_null_cnt]
        bucket_list = ["MISSING"]
        for i in range(len(score_bin_list)-1):
            left  = round(score_bin_list[i+0], 4)
            right = round(score_bin_list[i+1], 4)
            bucket_list.append("(" + str(left) + ',' + str(right) + ']')
            
            base_cnt = base_df[(base_df.score > left) & (base_df.score <= right)].shape[0]
            base_cnt_list.append(base_cnt)
            
            test_cnt = test_df[(test_df.score > left) & (test_df.score <= right)].shape[0]
            test_cnt_list.append(test_cnt)
        
        # 5.汇总统计结果    
        stat_df = pd.DataFrame({"bucket": bucket_list, "base_cnt": base_cnt_list, "test_cnt": test_cnt_list})
        stat_df['base_dist'] = stat_df['base_cnt'] / len(base_df)
        stat_df['test_dist'] = stat_df['test_cnt'] / len(test_df)
        
        def sub_psi(row):
            # 6.计算PSI
            base_list = row['base_dist']
            test_dist = row['test_dist']
            # 处理某分箱内样本量为0的情况
            if base_list == 0 and test_dist == 0:
                return 0
            elif base_list == 0 and test_dist > 0:
                base_list = 1 / base_notnull_cnt   
            elif base_list > 0 and test_dist == 0:
                test_dist = 1 / test_notnull_cnt
                
            return (test_dist - base_list) * np.log(test_dist / base_list)
        
        stat_df['psi'] = stat_df.apply(lambda row: sub_psi(row), axis=1)
        stat_df = stat_df[['bucket', 'base_cnt', 'base_dist', 'test_cnt', 'test_dist', 'psi']]
        psi = stat_df['psi'].sum()
        
    except:
        print('error!!!')
        psi = np.nan 
        stat_df = None
    return psi, stat_df

def calculate_cat_psi(origin,new,feature_name):

    origin_cut = origin[feature_name].value_counts(dropna=False).reset_index()
    origin_cut.columns = ['buckets','origin_cnt']
    origin_cut['feature'] = feature_name
    origin_cut = origin_cut[['feature','buckets','origin_cnt']]

    new_cut = new[feature_name].value_counts(dropna=False).reset_index()
    new_cut.columns = ['buckets','new_cnt']
    new_cut['feature'] = feature_name
    new_cut = new_cut[['feature','buckets','new_cnt']]
    
    psi_df = pd.merge(origin_cut,new_cut,on=['feature','buckets'])
    
    # 计算占比，分子加1，防止计算PSI时分子为0（这里分母不可能为0）
    psi_df['origin_percent'] = (psi_df['origin_cnt'] + 1) / psi_df['origin_cnt'].sum()
    psi_df['new_percent'] = (psi_df['new_cnt'] + 1) / psi_df['new_cnt'].sum()
    psi_df['minus'] = psi_df.apply(lambda x: x['origin_percent']-x['new_percent'],axis=1)
    psi_df['log'] = psi_df.apply(lambda x: np.log(x['origin_percent']/x['new_percent']),axis=1)
    
    psi_df['psi_bucket']  = psi_df.apply(lambda x: x['minus'] * x['log'],axis=1)
    psi_df['psi'] = psi_df['psi_bucket'].sum() 
    
    psi = psi_df['psi_bucket'].sum()
    
    return psi,psi_df

def drop_psi(remain_cols, train, oot):

    psi_result = {}
    for var in tqdm(remain_cols, desc='Calc PSI'):
        if train[var].dtype in ['category', 'object']:
            psi, _ = calculate_cat_psi(train, oot, var)
        else:
            psi, _ = calculate_num_psi(list(train[var]), list(oot[var]))
        psi_result[var] = psi

    psi_series = pd.Series(psi_result).sort_values(ascending=False)

    # 只删除大于0.1的特征
    psi_remain = psi_series[psi_series <= 0.1].index.tolist()

    return psi_remain

def bin_frequency(x, y, n=10, special_values=[-999, -9999, -1111]):
    # Fallback for compatibility
    return fit_chisquare_bins(x, y, bin_num=n, special_values=special_values)[0]


def fit_chisquare_bins(x, y, bin_num=10, init_bin_num=20, special_values=[-999, -9999, -1111]):
    # Learn chi-square bin edges from training data
    epsilon = 1e-6
    special_mask = x.isin(special_values)
    x_special, y_special = x[special_mask], y[special_mask]
    x_normal, y_normal = x[~special_mask], y[~special_mask]
    total = y.count()
    bad = y.sum()
    good = total - bad
    # Chi-square on normal values
    if len(x_normal) > 1:
        try:
            bin_set = br.bins.merge.chi2(x_normal, y_normal, bin_num=bin_num, init_bin_num=init_bin_num)
        except Exception as e:
            print(f"  chi2 failed, fallback to qcut: {e}")
            from pandas import qcut
            qr = qcut(x_normal, min(bin_num, len(x_normal)), duplicates='drop')
            bin_intervals = qr.cat.categories
            breaks = sorted(set(
                [interval.left for interval in bin_intervals] +
                [interval.right for interval in bin_intervals]
            ))
            cuts_list = breaks[1:-1]
            if cuts_list:
                bin_set = [['-', cuts_list[0]]]
                for i in range(len(cuts_list)-1):
                    bin_set.append([cuts_list[i], cuts_list[i+1]])
                bin_set.append([cuts_list[-1], '+'])
            else:
                bin_set = [['-', '+']]
        # Extract cut points from bin_set
        cuts = []
        for b in bin_set:
            if b is None:
                continue
            if b[0] != '-':
                cuts.append(b[0])
        if not cuts:
            bin_edges = [-np.inf, np.inf]
        else:
            bin_edges = [-np.inf] + cuts + [np.inf]
    else:
        bin_edges = [-np.inf, np.inf]
    return bin_edges, special_values


def apply_bin_frequency(x, y, bin_edges=None, special_values=[-999, -9999, -1111]):
    # Apply pre-learned bin edges (same stats computation as quantile)
    epsilon = 1e-6
    special_mask = x.isin(special_values)
    x_special, y_special = x[special_mask], y[special_mask]
    x_normal, y_normal = x[~special_mask], y[~special_mask]
    total = y.count()
    bad = y.sum()
    good = total - bad
    if bin_edges is None or len(x_normal) == 0 or len(bin_edges) < 2:
        buckets = pd.cut(x_normal, bins=2) if len(x_normal) > 0 else pd.Series(dtype='category')
    else:
        buckets = pd.cut(x_normal, bins=bin_edges, right=True, include_lowest=True, duplicates='drop')
    d1 = pd.DataFrame({'x': x_normal, 'y': y_normal, 'bucket': buckets})
    d2 = d1.groupby('bucket', as_index=True, observed=True)
    d3 = pd.DataFrame({'min_bin': d2.x.min(), 'max_bin': d2.x.max(),
                        'bad': d2.y.sum(), 'total': d2.y.count()})
    d3['bin_label'] = d3.index.astype(str)
    d3 = d3.reset_index(drop=True)
    # Special value bins
    special_rows = []
    for sv in special_values:
        mask_sv = x_special == sv
        if mask_sv.sum() == 0:
            continue
        y_sv = y_special[mask_sv]
        special_rows.append({
            'min_bin': sv, 'max_bin': sv,
            'bad': y_sv.sum(), 'total': y_sv.count(),
            'bin_label': f'special({sv})'
        })
    if special_rows:
        d_special = pd.DataFrame(special_rows)
        d3 = pd.concat([d3, d_special], ignore_index=True)
    # Derived metrics
    d3['bad_rate'] = d3['bad'] / d3['total']
    d3['badattr'] = d3['bad'] / bad if bad > 0 else 0
    d3['goodattr'] = (d3['total'] - d3['bad']) / good if good > 0 else 0
    d3['woe'] = np.log((d3['badattr'] + epsilon) / (d3['goodattr'] + epsilon))
    d3['cum_bad_rate'] = bad / total if total > 0 else 0
    d3['lift'] = np.where(d3['cum_bad_rate'] > 0, d3['bad_rate'] / d3['cum_bad_rate'], 0)
    d3['bins_iv'] = (d3['badattr'] - d3['goodattr']) * d3['woe']
    d3['total_iv'] = d3['bins_iv'].sum()
    # Sort: normal bins first, special bins last
    has_normal = (~d3['bin_label'].str.startswith('special')).any()
    has_special = d3['bin_label'].str.startswith('special').any()
    parts = []
    if has_normal:
        parts.append(d3[~d3['bin_label'].str.startswith('special')].sort_values('min_bin').reset_index(drop=True))
    if has_special:
        parts.append(d3[d3['bin_label'].str.startswith('special')].reset_index(drop=True))
    d4 = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if len(d4) > 0:
        d4['acu_bin_badrate'] = d4['badattr'].cumsum()
        d4['acu_bin_goodrate'] = d4['goodattr'].cumsum()
        d4['bin_ks'] = (d4['acu_bin_badrate'] - d4['acu_bin_goodrate']).abs().round(4)
        d4['total_ks'] = d4['bin_ks'].max()
        d4['acu_badnum'] = d4['bad'].cumsum()
        d4['acu_allnum'] = d4['total'].cumsum()
        d4['acu_badrate'] = d4['acu_badnum'] / d4['acu_allnum']
    else:
        for col in ['acu_bin_badrate', 'acu_bin_goodrate', 'bin_ks', 'total_ks', 'acu_badnum', 'acu_allnum', 'acu_badrate']:
            d4[col] = np.nan
    cols = ['min_bin', 'max_bin', 'bin_label', 'bad', 'acu_badnum', 'total', 'acu_allnum',
           'bad_rate', 'cum_bad_rate', 'acu_badrate', 'badattr', 'acu_bin_badrate',
           'goodattr', 'acu_bin_goodrate', 'bins_iv', 'total_iv', 'bin_ks', 'total_ks', 'woe', 'lift']
    return d4[cols]


def fit_bin_frequency(x, y, n=10, special_values=[-999, -9999, -1111]):
    # Alias for compatibility
    return fit_chisquare_bins(x, y, bin_num=n, init_bin_num=n*2, special_values=special_values)


def calculate_iv_cate(col, df, label, special_values=['MISSING', 'nan', 'NaN', 'None', 'none']):
    epsilon = 1e-6
    
    # 1. 预处理：确保缺失值有统一的标签
    # 将真实的空值和在 special_values 列表中的值统一替换为 'MISSING'
    temp_df = df[[col, label]].copy()
    temp_df[col] = temp_df[col].astype(str).str.strip() # 转字符串并去空格
    
    mask_missing = (
        temp_df[col].isna() | 
        temp_df[col].isin(special_values) | 
        (temp_df[col] == '')
    )
    temp_df.loc[mask_missing, col] = 'MISSING'

    # 2. 分组计算 (使用 dropna=False 确保 MISSING 被统计)
    grouped = temp_df.groupby(col, dropna=False)[label].agg(['sum', 'count'])
    grouped.columns = ['bad', 'total']
    grouped['good'] = grouped['total'] - grouped['bad']

    total_bad  = grouped['bad'].sum()
    total_good = grouped['good'].sum()
    total_all  = grouped['total'].sum()

    # 3. 计算衍生指标
    grouped['bad_rate']     = grouped['bad'] / grouped['total']
    grouped['badattr']      = grouped['bad']  / (total_bad + epsilon)
    grouped['goodattr']     = grouped['good'] / (total_good + epsilon)
    grouped['woe']          = np.log((grouped['badattr'] + epsilon) / (grouped['goodattr'] + epsilon))
    grouped['cum_bad_rate'] = total_bad / (total_all + epsilon)
    grouped['lift']         = grouped['bad_rate'] / grouped['cum_bad_rate']
    grouped['bins_iv']      = (grouped['badattr'] - grouped['goodattr']) * grouped['woe']
    grouped['total_iv']     = grouped['bins_iv'].sum()

    # 4. 排序逻辑：将 MISSING 放在最后，正常类别按 bad_rate 升序排列（或者按字母）
    grouped = grouped.reset_index().rename(columns={col: 'min_bin'})
    
    is_missing = grouped['min_bin'] == 'MISSING'
    df_normal = grouped[~is_missing].sort_values('min_bin')
    df_missing = grouped[is_missing]
    grouped = pd.concat([df_normal, df_missing], ignore_index=True)

    # 5. 计算累计指标与 KS
    grouped['acu_bin_badrate']  = grouped['badattr'].cumsum()
    grouped['acu_bin_goodrate'] = grouped['goodattr'].cumsum()
    grouped['bin_ks']           = (grouped['acu_bin_badrate'] - grouped['acu_bin_goodrate']).abs().round(4)
    grouped['total_ks']         = grouped['bin_ks'].max()
    grouped['acu_badnum']       = grouped['bad'].cumsum()
    grouped['acu_allnum']       = grouped['total'].cumsum()
    grouped['acu_badrate']      = grouped['acu_badnum'] / grouped['acu_allnum']

    # 6. 格式化输出
    grouped['max_bin'] = grouped['min_bin']
    # 增加一个标签列，方便辨认
    grouped['bin_label'] = grouped['min_bin'].apply(lambda x: 'special(MISSING)' if x == 'MISSING' else str(x))

    d5 = grouped[['min_bin', 'max_bin', 'bin_label', 'bad', 'acu_badnum', 'total', 'acu_allnum', 'bad_rate',
                  'cum_bad_rate', 'acu_badrate', 'badattr', 'acu_bin_badrate', 'goodattr',
                  'acu_bin_goodrate', 'bins_iv', 'total_iv', 'bin_ks', 'total_ks', 'woe', 'lift']]
    return d5

def calculate_iv(psi_remain, df, label, bin_num):
    iv_results = {}

    for col in tqdm(psi_remain, desc='Calc IV'):
        if df[col].dtype in ['category', 'object']:
            d5 = calculate_iv_cate(col, df, label)
        else:
            d5 = bin_frequency(df[col], df[label], bin_num)
        iv_results[col] = d5['total_iv'].iloc[0]

    iv_series = pd.Series(iv_results).sort_values(ascending=False)
    iv_remain = iv_series[iv_series > 0.02].index.tolist()

    return iv_remain, iv_series

def drop_corr(iv_remain_cols, train, iv_series, threshold, label):

    _ = train.select_dtypes(include=[np.number]).columns
    # 排除 label
    num_cols = [num for num in iv_remain_cols if num in _ and num != label]
    cat_cols = [i for i in iv_remain_cols if i not in num_cols and i != label]

    corr_df = train[num_cols]
    corr_matrix = corr_df.corr().abs()
    upper = corr_matrix.where(np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))

    corr_removed_cols = []
    for col in upper.columns:
        high_corr_cols = upper.index[upper[col] > threshold].tolist()
        for corr_col in high_corr_cols:
            if iv_series[col] >= iv_series[corr_col]:
                if corr_col not in corr_removed_cols:
                    corr_removed_cols.append(corr_col)
            else:
                if col not in corr_removed_cols:
                    corr_removed_cols.append(col)

    corr_remain_num_cols = [col for col in num_cols if col not in corr_removed_cols]
    
    # 合并 num 和 cat，cat 直接全部保留
    corr_remain_cols = corr_remain_num_cols + cat_cols
    
    return corr_remain_cols

def main(df1, time_col, threshold_missing, threshold_concentration, threshold_corr, bin_num, label, output_file):
    
    # ------------------------ Step 1: 划分训练集和测试集 ------------------------------

    # 核心开关判断
    if OOT_RATIO <= 0 or OOT_RATIO >= 1:
        train_ori = df1.copy()
        oot_ori = df1.copy()
        # 强制关闭 PSI 过滤，因为同一个数据集计算 PSI 永远为 0
        global PSI_FILTER
        PSI_FILTER = False
    else:
        # 正常划分 OOT
        df1[time_col] = df1[time_col].apply(parse_time)
        df1 = df1.sort_values(by=time_col)

        oot_date = df1[time_col].quantile(1-OOT_RATIO)
        train_ori = df1[df1[time_col] < oot_date]
        oot_ori = df1[df1[time_col] >= oot_date] 
        print(f'='*50)
        print(f"Train: {len(train_ori)}, OOT: {len(oot_ori)}")

    # ----------------------- Step 2: preprocessing -----------------------------------
    # ---------------------------- a. 填补缺失值 -------------------------------------------
    cat_bounds = fit_preprocess(train_ori)
    train = transform_preprocess(train_ori, cat_bounds)
    oot   = transform_preprocess(oot_ori, cat_bounds)
        # Drop time column after OOT split (no longer needed)
    if 'create_time_x' in train.columns:
        train = train.drop(columns=['create_time_x'])
        oot   = oot.drop(columns=['create_time_x'])
    remain_cols = train.columns.to_list()

    # --------------------------- b. 长尾截断 --------------------------------------------
    if LONGTAIL_FILTER:
        num_bounds = fit_cap_outliers(train)
        train = transform_cap_outliers(train, num_bounds)
        oot   = transform_cap_outliers(oot, num_bounds)

    # --------------------------- c. 缺失率 ----------------------------------------
    if MISSING_FILTER:
        distribution_missing = analyze_missing_distribution(train)
        train, oot = drop_missing_cols(train, oot, threshold_missing)
        remain_cols = train.columns.to_list()

    # -------------------------- d. 集中度 ------------------------------------------
    if CONCENTRATION_FILTER:
        remain_cols = fit_drop_concentration(train, threshold_concentration, label)
        print(f'='*50)
        print(f'保留的集中度小于95%特征数量是{len(remain_cols)}')

    # --------------------------- e. PSI -------------------------------------------
    if PSI_FILTER:
        remain_cols = drop_psi(remain_cols, train, oot)
        print(f'='*50)
        print(f'保留的psi特征数量是{len(remain_cols)}')
    
    # -------------------------- f. IV --------------------------------------------
    if IV_FILTER:
        remain_cols, iv_series = calculate_iv(remain_cols, train, label, bin_num)
        print(f'='*50)
        print(f"保留的IV特征数量是{len(remain_cols)}")

    if CORR_FILTER:
        remain_cols = drop_corr(remain_cols, train, iv_series, threshold_corr, label)
        print(f'='*50)
        print(f'保留的相关性特征数量是{len(remain_cols)}')

    # -------------------------- g. 类别特征过滤开关 --------------------------------
    # 在顶部定义开关：INCLUDE_CAT = True 或 False
    
    if not INCLUDE_CAT:
        # 识别出 remain_cols 中的类别型特征
        cols_to_remove = train[remain_cols].select_dtypes(include=['object', 'category']).columns.tolist()
        # 更新 remain_cols，仅保留非类别型特征
        remain_cols = [col for col in remain_cols if col not in cols_to_remove]
        print(f"已剔除 {len(cols_to_remove)} 个类别特征，当前剩余特征: {len(remain_cols)}")
    else:
        print(f"当前保留类别特征进入分箱阶段， 当前剩余特征：{len(remain_cols)}")

    # -------------------------- h. 最终分箱输出（Train + Test）----------------------------------
    all_train_d5 = []
    all_test_d5 = []
    all_bin_edges = {}  # 记录每个变量的分箱边界

    for col in tqdm(remain_cols, desc='Chi2 FIT on Train'):
        if train[col].dtype in ['category', 'object']:
            d5 = calculate_iv_cate(col, train, label)
            all_train_d5.append(d5)
            # 类别变量不做等频 transform，但全量 test 分箱里可以复用 calculate_iv_cate
        else:
            edges, _ = fit_chisquare_bins(train[col], train[label], bin_num, init_bin_num)
            d5 = apply_bin_frequency(train[col], train[label], bin_edges=edges)
            all_bin_edges[col] = edges
            d5.insert(0, 'feature', col)
            all_train_d5.append(d5)

    if OOT_RATIO > 0 and OOT_RATIO < 1:
        # 有真实的 OOT 集，对 oot 用同一批边界做 transform 分箱
        for col in tqdm(remain_cols, desc='Chi2 APPLY on Test'):
            if oot[col].dtype in ['category', 'object']:
                d5_test = calculate_iv_cate(col, oot, label)
            else:
                edges = all_bin_edges.get(col)
                if edges is not None:
                    d5_test = apply_bin_frequency(oot[col], oot[label], bin_edges=edges)
                else:
                    d5_test = bin_frequency(oot[col], oot[label], bin_num)
            d5_test.insert(0, 'feature', col)
            all_test_d5.append(d5_test)

    train_df = pd.concat(all_train_d5, ignore_index=True)
    test_df = pd.concat(all_test_d5, ignore_index=True) if len(all_test_d5) > 0 else pd.DataFrame()

    # ---------- 写入 Excel（2 个 Sheet） ----------
    writer = pd.ExcelWriter(output_file, engine='xlsxwriter')
    workbook = writer.book

    def write_sheet(worksheet, df, sheet_name):
        """写入 sheet + bad_rate 数据条 + 交替灰白背景"""
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        ws = writer.sheets[sheet_name]
        
        if len(df) > 0:
            col_idx = df.columns.get_loc('bad_rate')
            row_count = len(df)
            ws.conditional_format(1, col_idx, row_count, col_idx, {
                'type': 'data_bar',
                'bar_color': '#5DADE2',
                'bar_solid': True
            })
            # 交替灰白背景
            fmt_gray  = workbook.add_format({'bg_color': '#EBEBEB'})
            fmt_white = workbook.add_format({'bg_color': '#FFFFFF'})
            feature_vals = df['feature'].tolist()
            color_flag = 0
            prev_feature = None
            for i, f in enumerate(feature_vals):
                if f != prev_feature:
                    color_flag = 1 - color_flag
                    prev_feature = f
                fmt = fmt_gray if color_flag == 1 else fmt_white
                ws.set_row(i + 1, None, fmt)

    write_sheet(workbook, train_df, 'Train分箱明细')
    if len(test_df) > 0:
        write_sheet(workbook, test_df, 'Test分箱明细')

    writer.close()
    print(f"输出完成：{output_file}")
    print(f"  Sheet1: Train分箱明细 ({len(train_df)} 行)")
    if len(test_df) > 0:
        print(f"  Sheet2: Test分箱明细 ({len(test_df)} 行)")


if __name__ == '__main__':
    main(df1, time_col, threshold_missing, threshold_concentration,
         threshold_corr, bin_num, label, output_file)
