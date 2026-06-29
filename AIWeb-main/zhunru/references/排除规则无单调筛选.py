#!/usr/bin/env python3
"""
排除规则候选特征筛选（无单调性要求）：
- 头箱或尾箱 bad_rate 高于阈值 → 高风险客群识别
- 不检查单调性
- 业务解释性（中文翻译）
- 输出 3-sheet Excel（全部分箱 / 候选特征+test效果 / 分箱明细）

使用前修改顶部参数：
  input_file, output_file, TRAIN_BAD_RATE, HEAD_TAIL_THRESHOLD
"""
import pandas as pd
import numpy as np

# ═══ 修改参数 ═══
input_file        = r"分箱结果.xlsx"
output_file       = r"筛选变量.xlsx"
TRAIN_BAD_RATE    = 0.5658        # train 大盘坏率
HEAD_TAIL_THRESHOLD = 0.7         # 头/尾箱坏率绝对值 > 此值
MIN_BINS          = 3             # 最少 3 箱
MIN_SAMPLE        = 10            # 高风险箱至少 10 条
# ═══════════════

TRANSLATION_MAP = {
    'ios_new_f_lgbm_fpd7_wsh_v1': 'iOS新申请LGBM模型分(首次逾期7天)',
    'ios_score': 'iOS评分卡分数',
    'risk_over_days': '风险逾期天数',
    'is_old': '是否老客',
    'expire': '是否到期',
    # 扩展更多翻译...
}

def translate_feature(name):
    if name in TRANSLATION_MAP:
        return TRANSLATION_MAP[name]
    clean = name.replace('#', '_')
    if 'one_id__oneid_v1_all' in clean:
        return f"全量-{clean.split('one_id__oneid_v1_all')[-1].lstrip('_')}"
    if 'one_id__oneid_v1_only' in clean:
        return f"当前-{clean.split('one_id__oneid_v1_only')[-1].lstrip('_')}"
    return name

def get_biz_reason(feature, head_br, tail_br, max_br, head_high, tail_high):
    parts = ["模型评分" if 'score' in feature.lower() or '评分' in feature else
             "逾期指标" if 'overdue' in feature.lower() else
             "在贷指标" if 'onloan' in feature.lower() else
             "借款指标" if 'loan' in feature.lower() else
             "还款指标" if 'repay' in feature.lower() or 'pay' in feature.lower() else
             "用户画像特征"]
    if head_high and tail_high:
        parts.append("，头尾两端均为高风险客群")
    elif head_high:
        parts.append("，低分段(头箱)风险显著偏高")
    elif tail_high:
        parts.append("，高分段(尾箱)风险显著偏高")
    margin = (max_br - TRAIN_BAD_RATE) * 100
    if margin > 20:     parts.append(f"，极高区分度(大盘{margin:.0f}pp)")
    elif margin > 12:   parts.append(f"，强区分度(大盘{margin:.0f}pp)")
    else:               parts.append(f"，有区分度(大盘{margin:.0f}pp)")
    return ''.join(parts)

def main():
    df = pd.read_excel(input_file)
    df_normal = df[~df['bin_label'].astype(str).str.startswith('special')].copy()
    results, detail_features = [], []

    for feature, group in df_normal.groupby('feature', sort=False):
        group = group.sort_values('min_bin').reset_index(drop=True)
        br, tot = group['bad_rate'].tolist(), group['total'].tolist()
        if len(br) < MIN_BINS: continue
        head_high = br[0] > HEAD_TAIL_THRESHOLD and tot[0] >= MIN_SAMPLE
        tail_high = br[-1] > HEAD_TAIL_THRESHOLD and tot[-1] >= MIN_SAMPLE
        if not (head_high or tail_high): continue

        reasons = []
        if head_high: reasons.append(f"头箱坏率{br[0]:.1%}(>{HEAD_TAIL_THRESHOLD:.0%}, {tot[0]}条)")
        if tail_high: reasons.append(f"尾箱坏率{br[-1]:.1%}(>{HEAD_TAIL_THRESHOLD:.0%}, {tot[-1]}条)")

        results.append({
            '特征名': feature, '中文释义': translate_feature(feature),
            '选择原因': f"{get_biz_reason(feature, br[0], br[-1], max(br), head_high, tail_high)}。{' | '.join(reasons)}",
            'max_bad_rate': round(max(br), 4),
        })
        detail_features.append(feature)

    result_df = pd.DataFrame(results).sort_values('max_bad_rate', ascending=False).reset_index(drop=True)

    # ==== 输出 ====
    with pd.ExcelWriter(output_file, engine='xlsxwriter') as writer:
        wb = writer.book; fg = wb.add_format({'bg_color':'#EBEBEB'}); fw = wb.add_format({'bg_color':'#FFFFFF'})
        # Sheet1: 全部分箱
        df.to_excel(writer, sheet_name='全部分箱', index=False)
        ws = writer.sheets['全部分箱']
        ci = df.columns.get_loc('bad_rate')
        ws.conditional_format(1, ci, len(df), ci, {'type':'data_bar','bar_color':'#5DADE2','bar_solid':True})
        p,c=None,0
        for i,f in enumerate(df['feature'].tolist()):
            if f!=p: c=1-c; p=f
            ws.set_row(i+1, None, fg if c else fw)
        # Sheet2: 候选特征
        cand = result_df[['特征名','中文释义','选择原因','max_bad_rate']].copy()
        cand.columns = ['特征名','中文释义','选择原因','最高坏率']
        cand['最高坏率'] = cand['最高坏率'].apply(lambda x: f"{x:.1%}")
        cand.to_excel(writer, sheet_name='候选特征', index=False)
        ws = writer.sheets['候选特征']
        ws.set_column('A:A',50); ws.set_column('B:B',35); ws.set_column('C:C',65); ws.set_column('D:D',12)
        # Sheet3: 分箱明细
        detail = df[df['feature'].isin(detail_features)]
        detail.to_excel(writer, sheet_name='分箱明细', index=False)
        ws = writer.sheets['分箱明细']
        ci = detail.columns.get_loc('bad_rate')
        ws.conditional_format(1, ci, len(detail), ci, {'type':'data_bar','bar_color':'#E74C3C','bar_solid':True})
        p,c=None,0
        for i,f in enumerate(detail['feature'].tolist()):
            if f!=p: c=1-c; p=f
            ws.set_row(i+1, None, fg if c else fw)

    print(f"候选变量: {len(detail_features)} / {df_normal['feature'].nunique()}")
    print(f"输出: {output_file}")

if __name__ == '__main__':
    main()
