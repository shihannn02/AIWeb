# -*- coding: utf-8 -*-
"""
特征名→中文释义 映射表
按模式匹配，支持正则表达式。匹配优先级：精确匹配 > 前缀匹配 > 正则匹配
"""

import re

# 精确匹配（优先级最高）
EXACT_MAP = {
    'apply_time': '申请时间',
}

# 前缀匹配（按前缀从长到短匹配）
PREFIX_MAP = [
    # ===== phone 通讯录维度 =====
    ('phone_cnt_bl_hit_1d', '近1天通讯录黑名单命中次数'),
    ('phone_cnt_bl_hit_7d', '近7天通讯录黑名单命中次数'),
    ('phone_cnt_bl_hit_30d', '近30天通讯录黑名单命中次数'),
    ('phone_cnt_call_max_sys_id_30d', '近30天通讯录通话最多平台次数'),
    ('phone_cnt_call_max_sys_id_7d', '近7天通讯录通话最多平台次数'),
    ('phone_cnt_distinct_sys_id_bl_hit_30d', '近30天通讯录黑名单命中不同平台数'),
    ('phone_cnt_distinct_sys_id_bl_hit_90d', '近90天通讯录黑名单命中不同平台数'),
    ('phone_cnt_distinct_sys_id_180d', '近180天通讯录不同平台数'),
    ('phone_cnt_distinct_sys_id_30d', '近30天通讯录不同平台数'),
    ('phone_cnt_distinct_sys_id_7d', '近7天通讯录不同平台数'),
    ('phone_cnt_distinct_sys_id_90d', '近90天通讯录不同平台数'),
    ('phone_cnt_ratio_sys_id_7d_30d', '通讯录不同平台数7天/30天比值'),
    ('phone_days_since_first_sys_id_call', '距离通讯录首次平台通话天数'),
    ('phone_days_since_last_bl_hit', '距离通讯录末次黑名单命中天数'),
    ('phone_days_since_last_call', '距离通讯录末次通话天数'),
    ('phone_days_since_new_sys_id_30d', '近30天新平台距今天数'),
    ('phone_hhi_sys_id_30d', '近30天通讯录平台通话集中度(HHI)'),
    ('phone_hhi_sys_id_90d', '近90天通讯录平台通话集中度(HHI)'),
    ('phone_hours_since_last_call', '距离通讯录末次通话小时数'),
    ('phone_is_bl_hit_ever', '通讯录是否曾命中黑名单'),
    ('phone_is_multi_sys_id_bl_hit_ever', '通讯录是否曾多平台命中黑名单'),
    ('phone_max_cnt_1d', '通讯录单日最大通话次数'),
    ('phone_max_cnt_1h', '通讯录单小时最大通话次数'),
    ('phone_pct_bl_hit_30d', '近30天通讯录黑名单命中率'),
    ('phone_pct_bl_hit_7d', '近7天通讯录黑名单命中率'),
    ('phone_pct_call_night_7d', '近7天通讯录夜间通话占比'),
    ('phone_pct_call_top_sys_id_30d', '近30天通讯录Top1平台通话占比'),
    ('phone_pct_sys_id_bl_hit_30d', '近30天通讯录各平台黑名单命中率'),
    ('phone_pct_sys_id_bl_hit_90d', '近90天通讯录各平台黑名单命中率'),
    
    # phone_cnt_call_XXX (generic)
]

# 构建通用前缀翻译规则
def _build_phone_rules():
    rules = []
    time_suffix = {
        '1h': '1小时', '3h': '3小时', '6h': '6小时', '12h': '12小时',
        '24h': '24小时', '3d': '3天', '7d': '7天', '14d': '14天',
        '30d': '30天', '90d': '90天', '120d': '120天', '180d': '180天', '365d': '365天',
    }
    for suf, cn in time_suffix.items():
        rules.append((f'phone_cnt_call_{suf}', f'近{cn}通讯录通话次数'))
        rules.append((f'id_cnt_call_{suf}', f'近{cn}身份维度通话次数'))
        rules.append((f'phone_cnt_ratio_3d_{suf}', f'通讯录3天与{cn}通话次数比'))
        rules.append((f'id_cnt_ratio_3d_{suf}', f'身份3天与{cn}通话次数比'))
    # phone_cnt_ratio variants
    for a, ac in [('3d','3天'),('7d','7天'),('14d','14天'),('30d','30天'),('90d','90天'),('180d','180天')]:
        for b, bc in [('7d','7天'),('14d','14天'),('30d','30天'),('90d','90天'),('180d','180天'),('365d','365天')]:
            if a != b:
                rules.append((f'phone_cnt_ratio_{a}_{b}', f'通讯录{ac}/{bc}通话次数比'))
                rules.append((f'id_cnt_ratio_{a}_{b}', f'身份维度{ac}/{bc}通话次数比'))
    return rules

# ===== id 身份维度 =====
def _build_id_rules():
    return [
        ('id_cnt_bl_hit_1d', '近1天身份维度黑名单命中次数'),
        ('id_cnt_bl_hit_7d', '近7天身份维度黑名单命中次数'),
        ('id_cnt_bl_hit_30d', '近30天身份维度黑名单命中次数'),
        ('id_cnt_call_max_sys_id_30d', '近30天身份维度通话最多平台次数'),
        ('id_cnt_call_max_sys_id_7d', '近7天身份维度通话最多平台次数'),
        ('id_cnt_distinct_sys_id_bl_hit_30d', '近30天身份黑名单命中不同平台数'),
        ('id_cnt_distinct_sys_id_bl_hit_90d', '近90天身份黑名单命中不同平台数'),
        ('id_cnt_distinct_sys_id_180d', '近180天身份维度不同平台数'),
        ('id_cnt_distinct_sys_id_30d', '近30天身份维度不同平台数'),
        ('id_cnt_distinct_sys_id_7d', '近7天身份维度不同平台数'),
        ('id_cnt_distinct_sys_id_90d', '近90天身份维度不同平台数'),
        ('id_cnt_ratio_sys_id_7d_30d', '身份不同平台数7天/30天比值'),
        ('id_days_since_first_sys_id_call', '距离身份首次平台通话天数'),
        ('id_days_since_last_bl_hit', '距离身份末次黑名单命中天数'),
        ('id_days_since_last_call', '距离身份末次通话天数'),
        ('id_days_since_new_sys_id_30d', '身份近30天新平台距今天数'),
        ('id_hhi_sys_id_30d', '近30天身份平台通话集中度(HHI)'),
        ('id_hhi_sys_id_90d', '近90天身份平台通话集中度(HHI)'),
        ('id_hours_since_last_call', '距离身份末次通话小时数'),
        ('id_is_bl_hit_ever', '身份是否曾命中黑名单'),
        ('id_is_multi_sys_id_bl_hit_ever', '身份是否曾多平台命中黑名单'),
        ('id_max_cnt_1d', '身份单日最大通话次数'),
        ('id_max_cnt_1h', '身份单小时最大通话次数'),
        ('id_pct_bl_hit_30d', '近30天身份黑名单命中率'),
        ('id_pct_bl_hit_7d', '近7天身份黑名单命中率'),
        ('id_pct_call_night_7d', '近7天身份夜间通话占比'),
        ('id_pct_call_top_sys_id_30d', '近30天身份Top1平台通话占比'),
        ('id_pct_sys_id_bl_hit_30d', '近30天身份各平台黑名单命中率'),
        ('id_pct_sys_id_bl_hit_90d', '近90天身份各平台黑名单命中率'),
    ]

# ===== OLC 在线征信维度 =====
def _build_olc_rules():
    rules = []
    # 查询次数类
    for suf in ['', '_7d', '_30d', '_90d', '_180d']:
        sc = {'': '', '_7d':'近7天', '_30d':'近30天', '_90d':'近90天', '_180d':'近180天'}[suf]
        rules.append((f'olc_qry_cnt{suf}_phone', f'{sc}手机号查询次数'))
        rules.append((f'olc_qry_cnt{suf}_pkg1_phone', f'{sc}Pkg1查询次数'))
        rules.append((f'olc_qry_cnt{suf}_pkgx_phone', f'{sc}PkgX查询次数'))
    # 查询比率类
    for suf in ['_7d', '_30d', '_90d', '_180d']:
        sc = {'_7d':'7天', '_30d':'30天', '_90d':'90天', '_180d':'180天'}[suf]
        rules.append((f'olc_qry_{suf}_ratio_phone', f'近{sc}查询次数占比(手机号)'))
        rules.append((f'olc_qry_{suf}_ratio_pkgx_phone', f'近{sc}查询次数占比(PkgX)'))
    # 查询命中标识类
    for suf in ['', '_7d', '_30d', '_90d', '_180d']:
        sc = {'': '历史', '_7d':'近7天', '_30d':'近30天', '_90d':'近90天', '_180d':'近180天'}[suf]
        rules.append((f'olc_qry_hit{suf}_flag_phone', f'{sc}查询命中标识(手机号)'))
        rules.append((f'olc_qry_hit{suf}_flag_pkg1_phone', f'{sc}查询命中标识(Pkg1)'))
        rules.append((f'olc_qry_hit{suf}_flag_pkgx_phone', f'{sc}查询命中标识(PkgX)'))
    # 高频查询标识
    for suf in ['', '_pkg1', '_pkgx']:
        ss = {'': '', '_pkg1':'(Pkg1)', '_pkgx':'(PkgX)'}[suf]
        rules.append((f'olc_qry_hi_freq_flag_phone{ss}', f'高频查询标识{ss}'))
    # 查询系统数
    for suf in ['', '_30d', '_90d']:
        sc = {'': '历史', '_30d':'近30天', '_90d':'近90天'}[suf]
        for pkg in ['', '_pkg1', '_pkgx']:
            ps = {'': '(手机号)', '_pkg1':'(Pkg1)', '_pkgx':'(PkgX)'}[pkg]
            rules.append((f'olc_qry_sys_cnt{suf}{pkg}_phone', f'{sc}查询平台数{ps}'))
    # 多系统标识
    for suf in ['', '_30d']:
        sc = {'': '历史', '_30d':'近30天'}[suf]
        for pkg in ['', '_pkg1', '_pkgx']:
            ps = {'': '(手机号)', '_pkg1':'(Pkg1)', '_pkgx':'(PkgX)'}[pkg]
            rules.append((f'olc_multi_sys{suf}_flag{pkg}_phone', f'{sc}多平台查询标识{ps}'))
    
    # 空号/无效查询
    rules.extend([
        ('olc_qry_empty_cnt_phone', '空号查询次数(手机号)'),
        ('olc_qry_empty_cnt_pkg1_phone', '空号查询次数(Pkg1)'),
        ('olc_qry_empty_cnt_pkgx_phone', '空号查询次数(PkgX)'),
        ('olc_qry_empty_rate_phone', '空号查询率(手机号)'),
        ('olc_qry_empty_rate_pkgx_phone', '空号查询率(PkgX)'),
        ('olc_qry_last_empty_flag_phone', '末次查询是否空号(手机号)'),
        ('olc_qry_last_empty_flag_pkg1_phone', '末次查询是否空号(Pkg1)'),
        ('olc_qry_last_empty_flag_pkgx_phone', '末次查询是否空号(PkgX)'),
        ('olc_qry_cont_empty_phone', '连续空号查询次数(手机号)'),
        ('olc_qry_cont_empty_pkg1_phone', '连续空号查询次数(Pkg1)'),
        ('olc_qry_cont_empty_pkgx_phone', '连续空号查询次数(PkgX)'),
        ('olc_qry_valid_cnt_phone', '有效查询次数(手机号)'),
        ('olc_qry_valid_cnt_pkg1_phone', '有效查询次数(Pkg1)'),
        ('olc_qry_valid_cnt_pkgx_phone', '有效查询次数(PkgX)'),
    ])
    
    # 首末次/间隔查询
    rules.extend([
        ('olc_qry_first_days_phone', '距离首次查询天数(手机号)'),
        ('olc_qry_first_days_pkg1_phone', '距离首次查询天数(Pkg1)'),
        ('olc_qry_first_days_pkgx_phone', '距离首次查询天数(PkgX)'),
        ('olc_qry_last_days_phone', '距离末次查询天数(手机号)'),
        ('olc_qry_last_days_pkgx_phone', '距离末次查询天数(PkgX)'),
        ('olc_qry_gap_days_phone', '查询间隔天数(手机号)'),
        ('olc_qry_gap_days_pkgx_phone', '查询间隔天数(PkgX)'),
        ('olc_qry_span_days_phone', '查询跨度天数(手机号)'),
        ('olc_qry_span_days_pkgx_phone', '查询跨度天数(PkgX)'),
        ('olc_qry_freq_30d_phone', '近30天查询频率(手机号)'),
        ('olc_qry_freq_30d_pkg1_phone', '近30天查询频率(Pkg1)'),
        ('olc_qry_freq_30d_pkgx_phone', '近30天查询频率(PkgX)'),
    ])
    
    # 收入关联(olc_in_*)
    rules.extend([
        ('olc_in_any_cnt_phone', '收入关联总次数(手机号)'),
        ('olc_in_any_cnt_pkg1_phone', '收入关联总次数(Pkg1)'),
        ('olc_in_any_cnt_pkgx_phone', '收入关联总次数(PkgX)'),
        ('olc_in_any_flag_phone', '是否有收入关联(手机号)'),
        ('olc_in_any_flag_pkg1_phone', '是否有收入关联(Pkg1)'),
        ('olc_in_any_flag_pkgx_phone', '是否有收入关联(PkgX)'),
        ('olc_in_any_user_cnt_phone', '收入关联人数(手机号)'),
        ('olc_in_any_user_cnt_pkg1_phone', '收入关联人数(Pkg1)'),
        ('olc_in_any_user_cnt_pkgx_phone', '收入关联人数(PkgX)'),
        ('olc_in_type_cnt_phone', '收入关联类型数(手机号)'),
        ('olc_in_type_cnt_pkg1_phone', '收入关联类型数(Pkg1)'),
        ('olc_in_type_cnt_pkgx_phone', '收入关联类型数(PkgX)'),
        ('olc_in_multi_type_flag_phone', '多类型收入关联标识'),
        ('olc_assoc_sys_cnt_phone', '关联平台数(手机号)'),
        ('olc_assoc_sys_cnt_pkg1_phone', '关联平台数(Pkg1)'),
        ('olc_assoc_sys_cnt_pkgx_phone', '关联平台数(PkgX)'),
    ])
    
    # 紧急联系人(eme)
    for suf in ['', '_30d', '_90d']:
        sc = {'': '历史', '_30d':'近30天', '_90d':'近90天'}[suf]
        for pkg in ['', '_pkg1', '_pkgx']:
            ps = {'': '(手机号)', '_pkg1':'(Pkg1)', '_pkgx':'(PkgX)'}[pkg]
            rules.append((f'olc_in_eme_cnt{suf}{pkg}_phone', f'{sc}紧急联系人次数{ps}'))
    for pkg in ['', '_pkg1', '_pkgx']:
        ps = {'': '(手机号)', '_pkg1':'(Pkg1)', '_pkgx':'(PkgX)'}[pkg]
        rules.extend([
            (f'olc_in_eme_first_days_phone{pkg}', f'距离首个紧急联系人天数{ps}'),
            (f'olc_in_eme_last_days_phone{pkg}', f'距离末个紧急联系人天数{ps}'),
            (f'olc_in_eme_flag_phone{pkg}', f'是否有紧急联系人{ps}'),
            (f'olc_in_eme_user_cnt_phone{pkg}', f'紧急联系人数{ps}'),
            (f'olc_in_eme_multi_user_phone', '多紧急联系人标识'),
            (f'olc_in_eme_30d_flag_phone{pkg}', f'近30天是否有紧急联系人{ps}'),
        ])
    
    # 短信(sms)
    for suf in ['', '_30d', '_90d']:
        sc = {'': '历史', '_30d':'近30天', '_90d':'近90天'}[suf]
        for pkg in ['', '_pkg1', '_pkgx']:
            ps = {'': '(手机号)', '_pkg1':'(Pkg1)', '_pkgx':'(PkgX)'}[pkg]
            rules.append((f'olc_in_sms_cnt{suf}{pkg}_phone', f'{sc}短信关联次数{ps}'))
            rules.append((f'olc_in_sms_user_cnt{suf}{pkg}_phone', f'{sc}短信关联人数{ps}'))
    rules.extend([
        ('olc_in_sms_first_days_phone', '距离首条关联短信天数'),
        ('olc_in_sms_last_days_phone', '距离末条关联短信天数'),
        ('olc_in_sms_flag_phone', '是否有短信关联(手机号)'),
        ('olc_in_sms_flag_pkg1_phone', '是否有短信关联(Pkg1)'),
        ('olc_in_sms_flag_pkgx_phone', '是否有短信关联(PkgX)'),
    ])
    
    # 钱包(wlt)
    for suf in ['', '_30d', '_90d']:
        sc = {'': '历史', '_30d':'近30天', '_90d':'近90天'}[suf]
        for pkg in ['', '_pkg1', '_pkgx']:
            ps = {'': '(手机号)', '_pkg1':'(Pkg1)', '_pkgx':'(PkgX)'}[pkg]
            rules.append((f'olc_in_wlt_cnt{suf}{pkg}_phone', f'{sc}钱包关联次数{ps}'))
            rules.append((f'olc_in_wlt_user_cnt{suf}{pkg}_phone', f'{sc}钱包关联人数{ps}'))
    for pkg in ['', '_pkg1', '_pkgx']:
        ps = {'': '(手机号)', '_pkg1':'(Pkg1)', '_pkgx':'(PkgX)'}[pkg]
        rules.append((f'olc_in_wlt_flag{pkg}_phone', f'是否有钱包关联{ps}'))
    
    # 电话(phn)
    for suf in ['', '_30d', '_90d']:
        sc = {'': '历史', '_30d':'近30天', '_90d':'近90天'}[suf]
        for pkg in ['', '_pkg1', '_pkgx']:
            ps = {'': '(手机号)', '_pkg1':'(Pkg1)', '_pkgx':'(PkgX)'}[pkg]
            rules.append((f'olc_in_phn_cnt{suf}{pkg}_phone', f'{sc}电话关联次数{ps}'))
            rules.append((f'olc_in_phn_user_cnt{suf}{pkg}_phone', f'{sc}电话关联人数{ps}'))
    for pkg in ['', '_pkg1', '_pkgx']:
        ps = {'': '(手机号)', '_pkg1':'(Pkg1)', '_pkgx':'(PkgX)'}[pkg]
        rules.append((f'olc_in_phn_flag{pkg}_phone', f'是否有电话关联{ps}'))
    
    # 本人信息(olc_self_*)
    for pkg in ['', '_pkg1', '_pkgx']:
        ps = {'': '(手机号)', '_pkg1':'(Pkg1)', '_pkgx':'(PkgX)'}[pkg]
        rules.extend([
            (f'olc_self_phn_cnt{pkg}_phone', f'本人手机数{ps}'),
            (f'olc_self_wlt_cnt{pkg}_phone', f'本人钱包数{ps}'),
            (f'olc_self_sms_cnt{pkg}_phone', f'本人短信数{ps}'),
            (f'olc_self_eme_cnt{pkg}_phone', f'本人紧急联系人数{ps}'),
            (f'olc_self_total_cnt{pkg}_phone', f'本人信息总数{ps}'),
            (f'olc_self_apply_days{pkg}_phone', f'本人信息距今天数{ps}'),
            (f'olc_self_has_phn_flag{pkg}_phone', f'是否有本人手机{ps}'),
            (f'olc_self_has_wlt_flag{pkg}_phone', f'是否有本人钱包{ps}'),
            (f'olc_self_has_sms_flag{pkg}_phone', f'是否有本人短信{ps}'),
            (f'olc_self_has_eme_flag{pkg}_phone', f'是否有本人紧急联系人{ps}'),
            (f'olc_self_no_eme_flag{pkg}_phone', f'是否有紧急联系人缺失{ps}'),
            (f'olc_self_decay_flag{pkg}_phone', f'本人信息衰减标识{ps}'),
        ])
    rules.extend([
        ('olc_self_phn_chg_phone', '本人手机变更次数'),
        ('olc_self_phn_chg_pkgx_phone', '本人手机变更次数(PkgX)'),
        ('olc_self_sms_chg_phone', '本人短信变更次数'),
        ('olc_self_sms_chg_pkgx_phone', '本人短信变更次数(PkgX)'),
        ('olc_self_eme_chg_phone', '紧急联系人变更次数'),
        ('olc_self_eme_chg_pkgx_phone', '紧急联系人变更次数(PkgX)'),
        ('olc_self_total_chg_phone', '本人信息变更总次数'),
        ('olc_self_total_chg_pkgx_phone', '本人信息变更总次数(PkgX)'),
    ])
    
    # 数据包相关
    rules.extend([
        ('olc_pkg_cnt_phone', '数据包总数(手机号)'),
        ('olc_pkg_cnt_30d_phone', '近30天数据包数'),
        ('olc_pkg_cnt_90d_phone', '近90天数据包数'),
        ('olc_pkg_max_qry_phone', '单包最大查询数'),
        ('olc_pkg_max_qry_30d_phone', '近30天单包最大查询数'),
        ('olc_pkg_last_phone', '末次数据包距今天数'),
        ('olc_pkg1_qry_rate_phone', 'Pkg1查询占比'),
        ('olc_pkgx_qry_rate_phone', 'PkgX查询占比'),
        ('olc_pkg1_vs_pkgx_cnt_phone', 'Pkg1与PkgX查询次数比'),
        ('olc_pkg_multi_flag_phone', '多数据包标识'),
        ('olc_pkg_single_flag_phone', '单数据包标识'),
        ('olc_pkg1_hit_flag_phone', 'Pkg1命中标识'),
        ('olc_pkgx_hit_flag_phone', 'PkgX命中标识'),
        ('olc_both_pkg_a_hit_flag_phone', '双包A类均命中标识'),
        ('olc_only_pkg1_a_flag_phone', '仅Pkg1 A类命中'),
        ('olc_only_pkgx_a_flag_phone', '仅PkgX A类命中'),
        ('olc_b_pkg_cnt_phone', 'B包数量'),
        ('olc_b_pkg_overlap_flag_phone', 'B包重叠标识'),
        ('olc_b_both_pkg_flag_phone', '双包B类标识'),
        ('olc_b_only_pkg1_flag_phone', '仅Pkg1 B类标识'),
        ('olc_b_only_pkgx_flag_phone', '仅PkgX B类标识'),
        ('olc_dual_hit_flag_phone', 'AB双类命中标识'),
        ('olc_only_a_flag_phone', '仅A类命中'),
        ('olc_only_b_flag_phone', '仅B类命中'),
        ('olc_no_hit_flag_phone', '无命中标识'),
        ('olc_any_hit_flag_phone', '任意命中标识'),
        ('olc_total_exposure_cnt_phone', '总曝光次数'),
        ('olc_ab_pkg1_dual_flag_phone', 'Pkg1 AB双类标识'),
        ('olc_ab_pkgx_dual_flag_phone', 'PkgX AB双类标识'),
        ('olc_both_pkg_ab_flag_phone', '双包AB类标识'),
    ])
    
    # 其他OLC
    rules.extend([
        ('olc_error_flag', 'OLC查询错误标识'),
        ('olc_id_bind_phn_cnt', '身份绑定手机号数'),
        ('olc_id_multi_phn_flag', '身份多手机号标识'),
        ('olc_id_type_cnt_phone', '身份类型数(手机号)'),
        ('olc_phn_bind_id_cnt_phone', '手机号绑定身份数'),
        ('olc_phn_multi_id_flag_phone', '手机号多身份标识'),
        ('olc_qry_cnt_id', '身份查询次数'),
    ])
    
    return rules


# 组合所有规则
ALL_RULES = []
ALL_RULES.extend(PREFIX_MAP)
ALL_RULES.extend(_build_phone_rules())

# 补充缺失的特定映射
EXTRA_RULES = [
    ('olc_qry_7d_ratio_phone', '近7天查询次数比(手机号)'),
    ('olc_qry_30d_ratio_phone', '近30天查询次数比(手机号)'),
    ('olc_qry_90d_ratio_phone', '近90天查询次数比(手机号)'),
    ('olc_qry_180d_ratio_phone', '近180天查询次数比(手机号)'),
    ('olc_qry_7d_ratio_pkgx_phone', '近7天查询次数比(PkgX)'),
    ('olc_qry_30d_ratio_pkgx_phone', '近30天查询次数比(PkgX)'),
    ('olc_qry_90d_ratio_pkgx_phone', '近90天查询次数比(PkgX)'),
    ('olc_qry_180d_ratio_pkgx_phone', '近180天查询次数比(PkgX)'),
    ('olc_qry_90d_ratio_phone', '近90天查询次数比(手机号)'),
]
ALL_RULES.extend(EXTRA_RULES)
ALL_RULES.extend(_build_id_rules())
ALL_RULES.extend(_build_olc_rules())


def get_chinese_name(feature_name):
    """根据特征名返回中文释义"""
    # 1. 精确匹配
    if feature_name in EXACT_MAP:
        return EXACT_MAP[feature_name]
    
    # 2. 前缀匹配（按字符串长度降序，优先匹配更长的前缀）
    sorted_rules = sorted(ALL_RULES, key=lambda r: -len(r[0]))
    for prefix, cn in sorted_rules:
        if feature_name.startswith(prefix):
            return cn
    
    # 3. 正则匹配
    # phone_* 通用
    if feature_name.startswith('phone_'):
        return f'通讯录-{feature_name[6:]}'
    if feature_name.startswith('id_'):
        return f'身份维度-{feature_name[3:]}'
    if feature_name.startswith('olc_'):
        return f'征信查询-{feature_name[4:]}'
    
    return feature_name
