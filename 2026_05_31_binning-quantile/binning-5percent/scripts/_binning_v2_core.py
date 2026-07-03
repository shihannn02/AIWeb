import numpy as np
import pandas as pd
import copy
from datetime import datetime
import gc
import json
import pymysql
from sklearn.model_selection import train_test_split
import numpy as np
import pymysql
import pandas as pd
import datetime
import re
import os
import xlsxwriter

pd.set_option('display.max_columns', 30)  # 结果展示最大列数
pd.set_option('display.max_rows', 30)  # 结果展示最大行数

# def get_risk_level(row):
#     if pd.isna(row['risk_over_days']):  # 处理空值情况
#         return np.nan
#     elif row['risk_over_days'] <= 7:
#         return 0
#     # elif 1 <= row['risk_over_days'] <= 6:
#     #     return -1
#     else:  # risk_over_days >= 7
#         return 1
# my_data['overdue_flag'] = my_data.apply(get_risk_level, axis=1)


# my_data=my_data[my_data['extend_term']==2]
# my_data=my_data[my_data['source_type']==2]
# my_data=my_data[my_data['tt_score1']<=523]
#my_data=my_data[my_data['create_time_y'] <'2026-01-25']
# my_data['create_time'] = pd.to_datetime(my_data['create_time_x'])
# my_data['month'] = my_data['create_time'].dt.month
#my_data = my_data[my_data['month'].between(4, 9)]
# my_data=my_data[my_data['white_type']==1]
# my_data['overdue_flag']= my_data['overdue_flag2'].replace(-1,0)
# my_data=my_data[my_data['apply_time']<'2026-05-24']

# my_data=my_data[my_data['客户类型']=='老客']

# my_data = my_data[my_data['额度#quota'] > 12000]
# my_data = my_data[(my_data['额度#quota'] > 5000) & (my_data['额度#quota'] <= 12000)]
#y_data = my_data[my_data['overdue_flag'].isin([0, 1])]
# 筛选1-6月的数据
#my_data = my_data[my_data['create_time'].dt.month.between(1, 6)]
#my_data = my_data[my_data['create_time'].dt.month == 7]
# 😕✅1.对一个 Excel 表格中的“变量名”这一列进行清洗和标准化处理。
# var_dict =pd.read_excel(r'待分析变量的数据字典.xlsx')
# var_dict.变量名 = var_dict.变量名.map(lambda x: str(x).lower().replace('\t', ''))

# 😕✅2.删除不需要分析的字段

# 在 drop 之后、object 列清洗之前加这两行

#😕✅3.清洗数据表 my_data 中所有类型为字符串（object）的列，并尝试将它们转换为数值型（float64），如果失败则删除该列。


# 😕✅设置测试参数
# 4.在构建一套自动化变量策略挖掘或风险规则筛选系统时，配置好测算函数所需要的一系列参数，
# 以便在后续分析中可以根据这些参数执行相应的逻辑，比如数据切分、分箱分析、规则筛选等。
# 参数值设置
# sample_type取值只有Total，表示测算全量样本，不需要配置参数sample_type_col；若还有其他样本类型，也支持同时测算
# 举个例子理解规则筛选：
# 假设你在分析变量 年龄，系统可能会发现：
# 年龄 < 22 的客户中，逾期率为 15%
# 全体逾期率为 5%
# 那么这个规则的 Lift = 15% / 5% = 3.0
# 因为这个 Lift > 2.5 且样本数也满足条件，那么这个规则就会被“筛选”为一个“风险规则”。

#😕✅5.用于输出变量筛选结果的一个模块，主要是用于将筛选后的变量写入 Excel 文件，并美化格式，属于报告自动化的一部分



import copy
# 配色
# 通用搭配
# 条件格式: 蓝色数据条，不指定最大、最小值，绿色
# 条件格式: 黄色数据条，不指定最大、最小值，橙色
# 条件格式: 黄色数据条，不指定最大、最小值，橙色
# 总标题格式
# 副标题格式
# 表格正文
# 正文为百分比
# 表格正文
# 正文为百分比
# 表格正文


# 自动获取单元格内容的length，如果包含汉字，则一个汉字计数为2，如果是英文，则计数为1
def get_same_len(x):
    """
    :param x:       输入内容
    :return:        字符串长度
    """
    import re
    if type(x)!=str:
        x=str(x)
    l=list(x) #把字符串转换成字符列表。比如："abc中" 会变成 ['a', 'b', 'c', '中']
    num=0  #初始化计数器 num，用于记录总长度。
    for i in l:
        if re.match("[\u4e00-\u9fa5]+",i):
            num=num+2
        else:
            num=num+1
    return num  #最后返回“视觉长度”


def  details_result_output(wb,sheetname,data,suoyin,ana_people):
    '''
    :param wb:         excel 文件
    :param sheetname:  sheetname
    :param data:       待输出数据
    :param suoyin:     序号所在的列
    :param ana_people: 分析人
    :return:
    '''
    # :param wb:         excel 文件对象（xlsxwriter Workbook）
    # :param sheetname:  要写入的 sheet 名称
    # :param data:       需要输出的 pandas DataFrame 数据
    # :param suoyin:     序号所在的列索引（整数）
    # :param ana_people: 分析人（字符串，作为序号前缀）
    # :return:           无返回，直接写Excel
    nrows, ncols = data.shape
    body_text_xunhuan1 = wb.add_format(body_text_format_01)
    body_text_xunhuan1_per = wb.add_format(body_text_per_format_01)
    body_text_xunhuan2 = wb.add_format(body_text_format_02)
    body_text_xunhuan2_per = wb.add_format(body_text_per_format_02)
    body_text_title = wb.add_format(subtitle_format)
    ws = wb.add_worksheet(sheetname)
    ws.freeze_panes(1, 4)  ## 冻结单元格
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
                ws.conditional_format(1, j, nrows, j, condition_format_green_no)
            elif '%Bad_Rate' in column[j] or 'Lift' in column[j]:
                ws.conditional_format(1, j, nrows, j, condition_format_pink_no)
            elif 'pass2' in column[j]:
                ws.conditional_format(1, j, nrows, j, condition_format_red_no)
            elif 'pass3' in column[j]:
                ws.conditional_format(1, j, nrows, j, condition_format_blue_no)
            value = data.iloc[i][j]
            key = int(data.iloc[i, suoyin].replace(ana_people, ''))
            if key % 2 == 1:
                if ('%' in column[j] or '率' in column[j] or 'Rate' in column[j] or column[j] in ['单一值最大占比', '单一值第二大占比',
                                                                                                 '单一值第三大占比',
                                                                                                 '单一值前二大占比总和',
                                                                                                 '单一值前三大占比总和']):
                    ws.write(i + 1, j, value, body_text_xunhuan1_per)
                else:
                    ws.write(i + 1, j, value, body_text_xunhuan1)
            else:
                if ('%' in column[j] or '率' in column[j] or 'Rate' in column[j] or column[j] in ['单一值最大占比', '单一值第二大占比',
                                                                                                 '单一值第三大占比',
                                                                                                 '单一值前二大占比总和',
                                                                                                 '单一值前三大占比总和']):
                    ws.write(i + 1, j, value, body_text_xunhuan2_per)
                else:
                    ws.write(i + 1, j, value, body_text_xunhuan2)

def summary_result_output(wb, sheetname, data):
    '''
    :param wb:         excel 文件
    :param sheetname:  sheetname
    :param data:       待输出数据
    :return:
    '''
    nrows, ncols = data.shape
    body_text_xunhuan1 = wb.add_format(body_text_format_01)
    body_text_xunhuan1_per = wb.add_format(body_text_per_format_01)
    body_text_xunhuan2 = wb.add_format(body_text_format_02)
    body_text_xunhuan2_per = wb.add_format(body_text_per_format_02)
    body_text_title = wb.add_format(subtitle_format)
    ws = wb.add_worksheet(sheetname)
    ws.freeze_panes(1, 4)  ## 冻结单元格
    ws.autofilter(0, 0, nrows, ncols - 1)
    ws.hide_gridlines({'option': 1})
    column = data.columns
    for i in range(len(column)):
        x = column[[i]][0]
        ll = get_same_len(x)
        lll = max(8, ll)
        ws.set_column(i, i, lll)
    data = data.replace(np.nan, '')
    data = data.replace(np.inf, 'Inf')
    for j in range(ncols):
        ws.write(0, j, column[j], body_text_title)
    for i in range(nrows):
        for j in range(ncols):
            value = data.iloc[i][j]
            if i % 2 == 1:
                if ('%' in column[j] or '率' in column[j] or column[j] in ['单一值最大占比', '单一值第二大占比',
                                                                          '单一值第三大占比',
                                                                          '单一值前二大占比总和', '单一值前三大占比总和']):
                    ws.write(i + 1, j, value, body_text_xunhuan1_per)
                else:
                    ws.write(i + 1, j, value, body_text_xunhuan1)
            else:
                if ('%' in column[j] or '率' in column[j] or column[j] in ['单一值最大占比', '单一值第二大占比',
                                                                          '单一值第三大占比',
                                                                          '单一值前二大占比总和', '单一值前三大占比总和']):
                    ws.write(i + 1, j, value, body_text_xunhuan2_per)
                else:
                    ws.write(i + 1, j, value, body_text_xunhuan2)


def  var_summary_result_output(wb,sheetname,data,start=0):
    '''
    :param wb:         excel 文件
    :param sheetname:  sheetname
    :param data:       待输出数据
    :param start:      开始进行输出的表格行
    :return:
    '''
    ws = wb.add_worksheet(sheetname)
    column = data.columns
    nrows, ncols = data.shape
    for i in range(ncols):
        ws.set_column(i+1, i+1, 10)
    body_text_title = wb.add_format(subtitle_format)
    # sheet第四行，写入总标题
    body_title = wb.add_format(title_format)
    # 表格正文: 边框白色，字体12，背景浅灰色、居中
    body_text_xunhuan2 = wb.add_format(body_text_format_02)
    body_text_xunhuan2_per = wb.add_format(body_text_per_format_02)
    body_text_red = wb.add_format(subtitle_format)
    body_text_red.set_font_color('red')
    body_text_pink = wb.add_format(body_text_format_03)
    body_text_pink.set_bg_color('#F2DCE5')
    body_text_pink.set_text_wrap('True')
    body_text_pink.set_align('vcenter')
    body_text_blue = wb.add_format(body_text_format_03)
    body_text_blue.set_bg_color('#DAEEF3')
    body_text_red_xifen = wb.add_format(subtitle_format)
    body_text_red_xifen.set_font_color('red')
    ws.hide_gridlines({'option': 1})
    ws.merge_range(1 + start, 1, 1 + start, ncols, '一、' + sheetname, body_title)
    ws.set_column(0, 0, 2)
    ws.set_row(start + 3, 130)
    ws.set_row(start + 5, 100)
    ws.set_row(start + 7, 67)
    ws.set_row(start + 9, 26)
    ws.set_row(start + 10, 7)
    remark1 = '1.变量基础分析和筛选'
    remark2 = '''
           (1) 分析维度
           样本量、缺失量、缺失率、Badrate、单一值最大占比的变量值、单一值最大占比的样本量、单一值最大占比、单一值第二大占比的变量值、
           单一值第二大占比的样本量、单一值第二大占比、单一值第三大占比的变量值、单一值第三大占比的样本量、单一值第三大占比、
           单一值前二大占比的总样本量、单一值前二大占比总和、单一值前三大占比的总样本量、单一值前三大占比总和、变量取值数（包含缺失值）、
           变量取值数（不含缺失值）、最小值、最大值、平均值、分位数、标准差、离散系数
           (2) 筛选标准
           a.单一值最大占比 < 99%
           b.变量取值数（不含缺失值） >=2
           筛选结果详见标签1
           '''
    remark3 = '2.变量效果分析和筛选'
    remark4 = '''
           (1) 对变量进行分箱，计算不同分箱的触碰量、触碰率、Odds、Lift等指标
           (2) 基于头部和尾部分箱结果对变量进行筛选
           a.最小触碰量 >= 30 (备注：大于等于某一阈值，默认30)
           b.触碰率 <= 5% (备注：小于等于某一阈值，默认5%)
           c.Lift >= 3  (备注：大于等于某一阈值，默认3)
           筛选结果详见标签2
           '''
    remark5 = '3.变量相关性分析和筛选'
    remark6 = '''
           筛选标准
           a.对标签2筛选的变量进行两两线性相关分析，若相关性较强，选取Lift值大的变量
           b.选取有明确业务含义的变量
           筛选结果详见标签3
           '''
    remark7 = '4.变量分析结果汇总'
    sk0 = data['变量总数'][nrows - 1]
    sk1 = data['标签1筛选变量数'][nrows - 1]
    sk2 = data['标签2筛选变量数'][nrows - 1]
    sk3 = data['标签3筛选变量数'][nrows - 1]
    sk5 = float(data['剩余变量占比'][nrows - 1])
    remark8 = '''        分析的变量数总计为%s个，标签1筛选剩余%s个，标签2筛选剩余%s个，标签3筛选剩余%s个，最终筛选剩余变量占比为%.2f''' % (
        sk0, sk1, sk2, sk3, sk5 * 100) + '%'
    ws.merge_range(start + 2, 1, start + 2, ncols, remark1, body_text_blue)
    ws.merge_range(start + 3, 1, start + 3, ncols, remark2, body_text_pink)
    ws.merge_range(start + 4, 1, start + 4, ncols, remark3, body_text_blue)
    ws.merge_range(start + 5, 1, start + 5, ncols, remark4, body_text_pink)
    ws.merge_range(start + 6, 1, start + 6, ncols, remark5, body_text_blue)
    ws.merge_range(start + 7, 1, start + 7, ncols, remark6, body_text_pink)
    ws.merge_range(start + 8, 1, start + 8, ncols, remark7, body_text_blue)
    ws.merge_range(start + 9, 1, start + 9, ncols, remark8, body_text_pink)
    ws.merge_range(start + 10, 1, start + 10, ncols, '', body_text_pink)
    add = 8
    data = data.replace(np.inf, 'inf')
    data = data.fillna('')
    data = data.replace(-np.inf, '-inf')
    body_text_title.set_text_wrap('True')
    for j in range(ncols):
        ws.write(start + 3 + add, j + 1, column[j], body_text_title)
    ws.autofilter(start + 3 + add, 1, start + 3 + add, ncols)
    for i in range(nrows):
        for j in range(ncols):
            value = data.iloc[i][j]
            if ('%' in column[j] or '占比' in column[j] or 'rate' in column[j]):
                ws.write(i + start + 4 + add, j + 1, value, body_text_xunhuan2_per)
            else:
                ws.write(i + start + 4 + add, j + 1, value, body_text_xunhuan2)

def  var_summary_result_output_01(wb,sheetname,data):
    '''
    :param wb:         excel 文件
    :param sheetname:  sheetname
    :param data:       待输出数据
    :return:
    '''
    nrows, ncols = data.shape
    body_text_xunhuan2 = wb.add_format(body_text_format_02)
    body_text_xunhuan2_per = wb.add_format(body_text_per_format_02)
    body_text_title = wb.add_format(subtitle_format)
    ws = wb.add_worksheet(sheetname)
    ws.freeze_panes(1, 4)  ## 冻结单元格
    ws.autofilter(0,0,nrows,ncols-1)
    ws.hide_gridlines({'option': 1})
    column = data.columns
    for i in range(len(column)):
        x = column[[i]][0]
        ll = get_same_len(x)
        lll = max(8, ll)
        ws.set_column(i , i , lll)
    data = data.replace(np.nan, '')
    data = data.replace(np.inf, 'Inf')
    for j in range(ncols):
        ws.write(0 , j , column[j], body_text_title)
    for i in range(nrows):
        for j in range(ncols):
            value = data.iloc[i][j]
            if ('%' in column[j]  or '占比' in column[j]  or 'rate' in column[j] ):
                ws.write( i+1, j , value, body_text_xunhuan2_per)
            else:
                ws.write(i + 1, j, value, body_text_xunhuan2)


#😕✅6.



def describe_stat_ana(describe_data,sample_range,seq,sample_type,ana_people):
    """
    :param describe_data:      需要分析的数据框   #	数据源 DataFrame（如你的 my_data）
    :param sample_range:       样本区间   #样本范围描述，如“20250527以前”
    :param seq:                分析变量从seq开始计数
    :param sample_type:        分析的样本类型，列表格式  #	要分析的样本类型列表，如 ['Total']
    :param ana_people:         分析人  #	分析人标识，如 'zmc'，方便审计
    :return:                   变量的描述性统计分析结果
    """
    #定义输出表的列名
    col_seq = ["序号", "分析时间", "样本类型", "坏客户定义", "变量英文名", "变量中文名", "样本区间","%Bad_Rate(不含缺失值)", "%Bad_Rate(包含缺失值)",
               "总样本量","坏样本量", "缺失量", "缺失率","变量取值数（包含缺失值）", "变量取值数（不含缺失值）", "单一值最大占比的变量值", "单一值最大占比的样本量",
               "单一值最大占比", "单一值第二大占比的变量值", "单一值第二大占比的样本量","单一值第二大占比", "单一值第三大占比的变量值", "单一值第三大占比的样本量",
               "单一值第三大占比","单一值前二大占比的总样本量", "单一值前二大占比总和", "单一值前三大占比的总样本量", "单一值前三大占比总和",
                "最大值", "最大值数量", "最大值占比", "最小值", "最小值数量", "最小值占比","平均值", "下四分位数", "中位数", "上四分位数",
               "标准差", "离散系数", "标签1"]
    var_detail = pd.DataFrame(columns=col_seq)
# 每一个变量做描述性统计，对数据中的每一个变量做描述性统计，分析其取值分布、缺失情况、Bad Rate、变量集中度等，目的是判断这个变量值的“质量好不好”，是否适合做后续建模或策略规则筛选。
    for sample_type_sub in sample_type:                 #按样本类型循环，传入的是：sample_type = ['Total']
        print('正在分析的样本类型为：', sample_type_sub)
        if '其他' in sample_type_sub:
            describedata = describe_data[describe_data[sample_type_col[sample_type_sub][0]].map(
                lambda x: str(x) not in sample_type_col[sample_type_sub][1])]
        elif 'Total' in sample_type_sub:                #你现在只处理了 'Total'，所以直接用全部数据 describe_data。
            describedata = describe_data
        else:#按目标变量（坏客户定义）循环
            describedata = describe_data[describe_data[sample_type_col[sample_type_sub][0]].map(
                lambda x: str(x) in sample_type_col[sample_type_sub][1])]
        target = sample_type_target[sample_type_sub]
        for target_sub in target:                            #按目标变量（坏客户定义）循环
            print("分析的目标字段为：", target_sub)
            mydata1 = describedata[describedata[target_ripe[target_sub][0]]==1]             ##获取成熟样本
            mydata1 = mydata1.drop(labels=target_del_col[target_sub], axis=1)
            for var in mydata1.columns[:-1]:                    #对每一个变量做描述性统计：最后一列是 dpd_7，是目标字段，不分析它本身
                print('正在分析的变量为:', var)

                # 变量名、时间、序号设置
                seq1 = ana_people + str(seq)
                ana_time = datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d')
                sample_des = sample_type_sub
                var_english_name = var
                var_chinese_name = var
                sample_range = sample_range
                bad_define = target_sub

# 计算badrate,包含和不包含缺失值，坏样本率（bad rate）计算
                data_nona = mydata1[[var, target_sub]].dropna()
                data_withna = mydata1[[var, target_sub]]
                bad_rate_nona = data_nona[target_sub].value_counts(normalize=True)[1] if \
                    len(data_nona[target_sub].value_counts(normalize=True)) > 1 else 0 #只看非缺失的记录中 bad rate
                bad_rate_withna = data_withna[target_sub].value_counts(normalize=True)[1] #所有记录中 bad rate

#缺失和唯一值统计
                total_cnt = len(data_withna)
                bad_cnt =sum(mydata1[target_sub]==1)
                na_cnt = sum(mydata1[var].isnull())  #缺失数
                na_rate = na_cnt * 1.0 / total_cnt  #缺失率
                unique_cnt_withna = len(data_withna[var].unique()) #包括缺失时，取值个数
                unique_cnt_nona = len(data_nona[var].unique())  #不包括缺失时，取值个数
#前3大占比值分析，#最大 / 最小值及占比，#基本统计量（均值、四分位、标准差等）
                first_info_rate = data_withna[var].value_counts(dropna=False, normalize=True).sort_values(
                    ascending=False) #各取值的占比，从高到低
                first_info_cnt = data_withna[var].value_counts(dropna=False).sort_values(ascending=False) #各取值的数量，从高到低
                max_cnt_value = first_info_rate.index[0]
                max_cnt_value_num = first_info_cnt.tolist()[0]
                max_cnt_value_rate = first_info_rate.max()
                second_cnt_value = first_info_rate.index[1] if len(first_info_rate) > 1 else np.nan
                second_cnt_value_num = first_info_cnt.tolist()[1] if len(first_info_cnt) > 1 else np.nan
                second_cnt_value_rate = first_info_rate.tolist()[1] if len(first_info_rate) > 1 else np.nan
                second_cnt_value_rate_01 = first_info_rate.tolist()[1] if len(first_info_rate) > 1 else np.nan
                third_cnt_value = first_info_rate.index[2] if len(first_info_rate) > 2 else np.nan
                third_cnt_value_num = first_info_cnt.tolist()[2] if len(first_info_cnt) > 2 else np.nan
                third_cnt_value_rate = first_info_rate.tolist()[2] if len(first_info_rate) > 2 else np.nan
                third_cnt_value_rate_01 = first_info_rate.tolist()[2] if len(first_info_rate) > 2 else np.nan
                var_max_cnt_value_12num = np.nansum([max_cnt_value_num, second_cnt_value_num])
                var_max_cnt_value_12rate = np.nansum([first_info_rate.tolist()[0], second_cnt_value_rate_01])
                var_max_cnt_value_123num = np.nansum([max_cnt_value_num, second_cnt_value_num, third_cnt_value_num])
                var_max_cnt_value_123rate = np.nansum(
                    [first_info_rate.tolist()[0], second_cnt_value_rate_01, third_cnt_value_rate_01])
                max_value = data_nona[var].max()
                max_value_num = sum(data_nona[var] == max_value)
                max_value_rate = sum(data_nona[var] == max_value) / total_cnt
                min_value = data_nona[var].min()
                min_value_num = sum(data_nona[var] == min_value)
                min_value_rate = sum(data_nona[var] == min_value) / total_cnt
                mean_value = data_nona[var].mean() #均值
                q1_value = np.percentile(data_nona[var], 25) if len(data_nona) > 0 else np.nan #25%分位数
                median_value = data_nona[var].median() #中位数
                q3_value = np.percentile(data_nona[var], 75) if len(data_nona) > 0 else np.nan#75%分位数
                std_value = np.std(data_nona[var])#标准差
                cv = std_value / mean_value if mean_value != 0 else 0#离散系数 = std / mean
                # 计算变量中前两个最常见取值的占比总和，这是为了判断这个变量是不是 值太集中，如果变量值都集中在前两个取值里，比如 99%，说明这个变量信息量不足，对建模/规则的贡献不大。
                top_1_2_rate = np.nansum([first_info_rate.tolist()[0], second_cnt_value_rate_01])
                if (top_1_2_rate < 0.999) & (unique_cnt_withna >= 2):
                    if_trigger_choose = 'Y' #量值比较分散，有信息
                else:
                    if_trigger_choose = 'N' ## 变量值过于集中，如只有一个值，无法建模
#拼成一行结果，加入最终结果表
                sum_info = [seq1, ana_time, sample_des, bad_define, var_english_name, var_chinese_name, sample_range,
                            bad_rate_nona, bad_rate_withna, total_cnt, bad_cnt, na_cnt, na_rate, unique_cnt_withna,
                           unique_cnt_nona,max_cnt_value, max_cnt_value_num,max_cnt_value_rate, second_cnt_value,
                            second_cnt_value_num, second_cnt_value_rate,third_cnt_value, third_cnt_value_num,
                            third_cnt_value_rate, var_max_cnt_value_12num,var_max_cnt_value_12rate,var_max_cnt_value_123num,
                            var_max_cnt_value_123rate, max_value, max_value_num, max_value_rate, min_value,
                           min_value_num, min_value_rate, mean_value, q1_value, median_value, q3_value,
                           std_value, cv, if_trigger_choose]
                sum_info01 = pd.DataFrame(sum_info).T
                sum_info01.columns = col_seq
                var_detail = pd.concat([var_detail, sum_info01], ignore_index=True)
                seq += 1
    return (var_detail)
# 序号	分析时间	变量英文名	中文名	总样本	缺失率	Bad Rate	最大值	最小值	触发标签
# zmc1	2025-06-06	age	年龄	12000	0.02	0.08	88	18	Y
# zmc2	2025-06-06	gender	性别	12000	0.00	0.07	男	女	N
# ...	...	...	...	...	...	...


#变量分箱与Lift分析框架，用于对变量进行离散化处理（即分箱），
# 并计算出各分箱的指标表现，包括 Bad Rate、WOE、IV、Lift 等指标，从而评估变量在风控建模中的有效性。
# 2.1 变量分箱函数
def get_bin_lift(data,flag_name,factor_name,min_rate=0.001,sub_div_bin=0.01,min_num=5,method='best',max_bins=50,numOfSplit=30):
    """
    :param data:            需要分析的数据框
    :param flag_name:       要分析的目标字段   dpd_7
    :param factor_name:     要分析的变量      factor_name
    :param min_rate:        最小分箱占比      每个箱最小占总样本比例
    :param sub_div_bin:     头部和尾部分箱占比 首尾精细分箱的比例（提升极端人群识别）
    :param min_num:         最小分箱样本量                 每箱最小样本数，防止过小样本导致误判
    :param method:          分箱方法：最优分箱、等频分箱   分箱方式，支持 best（最优）和 equalfreq（等频）
    :param numOfSplit:      等频分箱对应的分箱数量         等频分箱的箱数
    :return:                分箱结果
    """
    k=group_by_var_value(data,flag_name,factor_name,'#Bad','#Good')
    k1 = get_na_bin(data, flag_name, factor_name, '#Bad', '#Good')
    # k：统计每个值下的好/坏样本数量（不含缺失）；
    # k1：将缺失值单独作为一个分箱，并计算好坏样本数。
    if len(k)==0:
        print(flag_name,factor_name,' have no value')
        return pd.DataFrame()
    # 分箱点计算（分为两类方法）
#B）.最优分箱法（Best）
    if method == 'best':
        total = len(data[data[factor_name].notnull()])
        min_rate_act=max(min_num/total,min_rate)
        knot_start=[]
        obs_start=[]
        knot_end = []
        obs_end=[]
        if sub_div_bin>=min_rate_act:
            end_cnt=int(sub_div_bin/min_rate_act)
        else:
            end_cnt=int(min_rate_act/sub_div_bin)
        for i in range(end_cnt):
            if len(knot_start) == 0:
                tmp=k[k['%Cum_Obs']>=min_rate_act].index.tolist()
            else:
                tmp=k[k['%Cum_Obs']>=min_rate_act+obs_start[-1]].index.tolist()
            if len(tmp) > 0:
                knot_start.append(tmp[0])
                obs_start.append(k['%Cum_Obs'][tmp[0]])
        for i in range(end_cnt):
            if len(knot_end) == 0:
                tmp=k[k['%Opps_Cum_Obs']>=min_rate_act].index.tolist()
            else:
                tmp=k[k['%Opps_Cum_Obs']>=min_rate_act+obs_end[-1]].index.tolist()
            if len(tmp) > 0:
                if tmp[-1]>0:
                    knot_end.append(tmp[-1]-1)
                    obs_end.append(k['%Opps_Cum_Obs'][tmp[-1]-1])
        knot = sorted(list(set(knot_start + knot_end)))
        if len(k) - 1 in knot:
            knot.remove(len(k) - 1)
        if len(knot) > max_bins:
            knot = knot[:max_bins//2] + knot[-max_bins//2:]
#B）等频分箱法（EqualFrequency）
    elif method == 'equalfreq':
        knot_value = unsupervise_splitbin(data,factor_name,numOfSplit,method)
        knot=[k[k[factor_name]==i].index[0] for i in knot_value ]
#分箱计算及指标统计
    res1 = important_bin_calculate(k, k1, '#Good', '#Bad', factor_name, [0] + knot + [len(k) - 1])
    return res1

#以下后续分箱、计算 WOE、IV、Lift 的基础步骤。
# 计算变量每一种取值、每一种指标取值下好样本的个数、坏样本的个数
def group_by_var_value(data, flag_name, factor_name, bad_name, good_name, discrete_list=[]):
    """
    :param data:            需要分析的数据框
    :param flag_name:       要分析的目标字段
    :param factor_name:     要分析的变量
    :param bad_name:        坏样本个数列名
    :param good_name:       好样本个数列名
    :param discrete_list:   分类变量列表
    :return:                变量每一种取值、每一种指标取值下好样本的个数、坏样本的个数
    """
    if len(data) == 0:
        return pd.DataFrame()  #如果数据为空，直接返回空表，避免后续出错。
    regroup1 = data.groupby([factor_name])[flag_name].count() # 总样本数（好+坏）
    regroup2 = data.groupby([factor_name])[flag_name].sum()   # 坏样本数（flag=1）
    data1 = pd.DataFrame({good_name: regroup1 - regroup2, bad_name: regroup2}).reset_index() # 构建统计 DataFrame
    good = float(sum(data1[good_name]))
    bad = float(sum(data1[bad_name]))
    total = good + bad
    data1['%Bad_Rate'] = data1[bad_name] / (data1[bad_name] + data1[good_name])
    data1['#Obs'] = (data1[good_name] + data1[bad_name])  #每组样本数
    data1['%Obs'] = (data1[good_name] + data1[bad_name]) / total  #样本占比
    data1['%Cum_Obs'] = np.cumsum(data1['%Obs'])
    data1['%Opps_Cum_Obs'] = (1 - np.cumsum(data1['%Obs'])) + data1['%Obs']
# 如果不是离散变量（默认是数值型），按变量值升序排列；
# 如果是离散变量（如城市、职业等），按坏账率升序排序；
# 并标注变量类型为numeric或non - numeric。
    if factor_name not in discrete_list:
        data1 = data1.sort_values(by=[factor_name], ascending=True)
        data1['Char_Type'] = 'numeric'
    else:
        data1 = data1.sort_values(by=['%Bad_Rate'], ascending=True)
        data1['Char_Type'] = 'non-numeric'
    data1 = data1.reset_index(drop=True)
    return data1

#将数字（浮动、整数）转换为字符串格式，确保数值的准确展示，特别是浮动数值的精度控制
def get_str(x):
    """
    将数字转为字符串
    """
    if type(x) in [float, np.float64, np.float16, np.float32]:
        return ('{0:.17}'.format(x))
    elif type(x) in [int, np.int8, np.int16, np.int32, np.int64]:
        return str(x)
    else:
        try:
            return str(x)
        except:
            return x

# 根据切分点获得指标的分组结果，计算和展示按照分箱点（knots_list）划分后的指标（如WOE、IV、Lift等）。
def important_bin_calculate(data_df,na_df, good_name, bad_name, factor_name, knots_list,if_sort=False):
    """
    :param data_df:         转换后的数据框
    :param na_df:           要分析的目标字段
    :param good_name:       好样本个数列名
    :param bad_name:        坏样本个数列名
    :param factor_name:     指标列名
    :param knots_list:      最佳分组点集合
    :param if_sort:         是否需要对指标的分组结果进行排序
    :return:                根据切分点获得指标的分组结果
    """
#初始化部分：
    flag = data_df['Char_Type'].max()
    temp_df_list = []
    bin_list = []
#根据给定的分箱点将数据划分为不同的区间，并将区间信息保存在 bin_list 中。
# 对于数值型变量，使用 (-inf, ...] 或 [...inf) 等区间表示；对于非数值型变量（如分类变量），则将对应的分类值作为分箱区间。
    for i in range(1, len(knots_list)):  # 遍历分箱点
        if i == 1:  # 第一段从第一个分箱点开始
            temp_df_list.append(data_df.loc[knots_list[i - 1]:knots_list[i]])  # 取第1段数据
            if flag == 'numeric':  # 如果是数值型变量
                bin_list.append('(-inf, ' + get_str(data_df[factor_name][knots_list[i]]) + ']')
            else:
                bin_list.append(list(data_df[factor_name])[knots_list[i - 1]:knots_list[i] + 1])
        else:  # 后续段
            temp_df_list.append(data_df.loc[knots_list[i - 1] + 1:knots_list[i]])  # 取后续段数据
            if flag == 'numeric':  # 如果是数值型变量
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
    # 计算每个区间的统计指标
    # 以下部分计算每个分箱区间的指标：
    # 好样本数 (#Good)
    # 坏样本数 (#Bad)
    # WOE（Weight of Evidence）
    # IV（Information Value）
    # 提升度 (Lift)
    good_list = list(map(lambda x: sum(x[good_name]), temp_df_list))
    bad_list = list(map(lambda x: sum(x[bad_name]), temp_df_list))
    good_percent_series = pd.Series(list(map(lambda x: float(sum(x[good_name])) / total_good, temp_df_list)))
    bad_percent_series = pd.Series(list(map(lambda x: float(sum(x[bad_name])) / total_bad, temp_df_list)))
    woe_list = list(np.log(good_percent_series / bad_percent_series))
    IV_list = list((good_percent_series - bad_percent_series) * np.log(good_percent_series / bad_percent_series))
    total_list = list(map(lambda x: sum(x[good_name]) + sum(x[bad_name]), temp_df_list))
    bin_rate_list = list(
        map(lambda x: float(sum(x[good_name]) + sum(x[bad_name])) / (total_good + total_bad), temp_df_list))
    non_na_indicator = pd.DataFrame({'Bin': bin_list,
                                         '#Obs': total_list,
                                         '#Good': good_list,
                                         '#Bad': bad_list,
                                         'IV(bin)': IV_list,
                                         'WOE': woe_list,
                                         '%Obs': bin_rate_list})
    l = ['Bin', '#Obs', '%Obs', '#Cum_Obs', '%Cum_Obs', '#Good', '%Good', '#Cum_Good', '%Cum_Good',
         '#Bad', '%Bad', '#Cum_Bad', '%Cum_Bad', '%Bad_Rate', 'WOE', 'IV(bin)', 'IV(total)', 'Odds1', 'Odds2',
         'Lift']
    result_indicator = non_na_indicator.reset_index(drop=True)
    result_indicator = result_indicator[result_indicator['Bin'] != 'NA']
    if if_sort:
        result_indicator = result_indicator.sort_index(ascending=False).reset_index()
    result_indicator['%Cumulative_Bad_Rate'] = np.cumsum(result_indicator['#Bad']) / np.cumsum(
        result_indicator['#Obs'])
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
        result_indicator['Odds1']=((result_indicator_1['#Cum_Bad'] / result_indicator_1['#Cum_Good']) / (
        (total_bad -na_bad- result_indicator_1['#Cum_Bad']) / (total_good -na_good- result_indicator_1['#Cum_Good']))).tolist()+[np.nan]
        result_indicator['Odds2']=[np.nan] +list(1/result_indicator['Odds1'])[0:-2]+[np.nan]
    else:
        result_indicator['Odds1'] = (result_indicator['#Cum_Bad'] / result_indicator['#Cum_Good']) / (
            (total_bad - result_indicator['#Cum_Bad']) / (total_good - result_indicator['#Cum_Good']))
        result_indicator['Odds2'] = [np.nan] + list(1 / result_indicator['Odds1'])[0:-1]
    result_indicator['Lift'] = result_indicator['%Bad_Rate'] / (sum(result_indicator['#Bad']) / (sum(result_indicator['#Obs'])))
    result_indicator = result_indicator.replace(np.inf, 0)
    return result_indicator[l]

# 获取缺失值分箱
#目的是：处理缺失值样本，将缺失值视为一个单独的分箱，计算该分箱中的好样本和坏样本数量
def get_na_bin(data_total, flag_name, factor_name, good_name, bad_name):
    """
    :param data_total:         全量数据框
    :param flag_name:          要分析的目标字段
    :param factor_name:        要分析的变量列名
    :param good_name:          好样本个数列名
    :param bad_name:           坏样本个数指标列名
    :return:                   缺失值分箱
    """
    data = data_total[(data_total[factor_name].isnull())]
    good_cnt = data[flag_name].sum()
    tn = len(data[data[flag_name].notnull()])
    na_df = pd.DataFrame([["缺失值", good_cnt, tn - good_cnt]],columns=[factor_name, good_name, bad_name])
    return na_df

# 无监督分箱
def unsupervise_splitbin(df, var, numOfSplit=30, method='equalfreq'):
    '''
    :param df:            要分箱数据框
    :param var:           需要分箱的变量。仅限数值型。
    :param numOfSplit:    需要分箱个数
    :param method:        分箱方法，'equalfreq'：等频，否则是等距
    :return:              分箱索引或分箱临界点
    '''
    df = df[~df[var].isnull()]  # 去除 NaN 值
    if method == 'equalfreq':
        N = df.shape[0]  # 获取总样本数
        n = max(5, N // numOfSplit)  # 每个分箱样本数（除法取整）

        splitPointIndex = [i * n for i in range(1, numOfSplit)]  # 计算分箱点的索引
        rawValues = sorted(list(df[var]))  # 将变量的所有值排序

        splitPoint = [rawValues[i] for i in splitPointIndex]  # 根据索引选取分箱点
        splitPoint = sorted(list(set(splitPoint)))  # 去重并排序分箱点
        return splitPoint  # 返回分箱点的列表

    if method == 'equallen':
        var_max, var_min = max(df[var]), min(df[var])  # 获取变量的最大值和最小值
        interval_len = (var_max - var_min) * 1.0 / 20  # 每个分箱的宽度

        splitPoint = [var_min + i * interval_len for i in range(1, numOfSplit)]  # 计算等宽分箱点
        return splitPoint  # 返回等宽分箱点

# 变量最佳切分点确定&变量效果分析和筛选  根据指标分组结果筛选出最优的odds和对应阈值
def select_best_lift(data,flag_name,factor_name,min_rate=0.001,sub_div_bin=0.01,min_num=10,hit_num=30,
                     min_lift=1.5,method='best',numOfSplit=30):
    """
    :param data:                要分析的数据框
    :param flag_name:           要分析的目标字段
    :param factor_name:         指标列名
    :param min_rate:            头部和尾部最小分箱占比
    :param sub_div_bin:         头部和尾部分箱占比
    :param min_num:             最小分箱样本量
    :param hit_num:             最小分箱触碰样本量
    :param min_lift:            最小lift值
    :param method:              分箱方法
    :param numOfSplit:          等频分箱对应的分箱数量
    :return:                    策略阈值及对应的触碰量、风险倍数、是否筛选等指标
    """
    bin_odd = get_bin_lift(data, flag_name, factor_name, min_rate, sub_div_bin, min_num,method,numOfSplit)
    odd1 = bin_odd[bin_odd['%Cum_Obs'] < sub_div_bin]
    sp = list(bin_odd[bin_odd['Bin'] == '缺失值']['%Obs'].to_dict().values())[0]
    odd2 = bin_odd.loc[bin_odd[bin_odd['%Cum_Obs'] >= 1 - sp - sub_div_bin].index[1:]]
    dic={'var_name':factor_name}
    max1 = odd1['Odds1'].max()
    max2 = odd2['Odds2'].max()
    if (np.isnan(max1)) and (np.isnan(max2)):
        print(factor_name+' 未分箱成功或者非缺失值分箱中无坏样本，请关注')
        return dic
    elif (np.isnan(max1)) or (max1<=max2):
        max2=odd2['Odds2'].max()
        max_index=odd2[odd2['Odds2'] == max2].index[0]
        dic['Odds'] = max2
        bin_detail=bin_odd['Bin'][max_index]
        if ',' not in bin_detail:
            max_index2 = max_index-1
            bin_detail = bin_odd['Bin'][max_index2]
            print(bin_detail)
            if ',' not in bin_detail:
                dic['Threshold'] = float(bin_detail.split(' ')[-1][1:-1])
            else:
                dic['Threshold']=float(bin_detail.split(' ')[-1][0:-1])
            print(dic['Threshold'])
            if len(str(dic['Threshold']))==0:
                dic['Threshold'] = float(bin_odd['Bin'][max_index2].split(' ')[-1][1:-1])
        else:
            dic['Threshold'] = float(bin_detail.split(',')[0][1:])
        print(dic)
        dic['type'] = '>'
        bin_odd_1=bin_odd[bin_odd['Bin'] != '缺失值']
        dic['#Obs'] = bin_odd_1['#Obs'][max_index:].sum()
        dic['%Obs'] = bin_odd_1['%Obs'][max_index:].sum()
        dic['#Bad'] = bin_odd_1['#Bad'][max_index:].sum()
        dic['%Bad_Rate'] = dic['#Bad'] / dic['#Obs']
        dic['Lift'] = dic['%Bad_Rate']/(bin_odd['#Bad'].sum()/bin_odd['#Obs'].sum()) if (bin_odd['#Bad'].sum()/bin_odd['#Obs'].sum())>0 else 0
        dic['Selected'] = 'Y' if (dic['Lift'] >= min_lift and dic['#Obs']>=hit_num) else 'N'
        #如果 Lift 大于等于设定的最小值 min_lift 且样本数量大于等于 hit_num，则认为该分箱有效，标记为 'Y'，否则标记为 'N'。
    else:
        max1 = odd1['Odds1'].max()
        max_index = odd1[odd1['Odds1'] == max1].index[0]
        dic['Odds'] = max1
        bin_detail = bin_odd['Bin'][max_index]
        if ',' not in bin_detail:
            dic['Threshold'] = float(bin_detail.split(' ')[-1][1:-1])
        else:
            dic['Threshold'] = float(bin_detail.split(',')[1][:-1])
        dic['type'] = '<='
        dic['#Obs'] = bin_odd['#Obs'][0:(max_index + 1)].sum()
        dic['%Obs'] = bin_odd['%Obs'][0:(max_index + 1)].sum()
        dic['#Bad'] = bin_odd['#Bad'][0:(max_index + 1)].sum()
        dic['%Bad_Rate'] = dic['#Bad'] / dic['#Obs']
        dic['Lift'] = dic['%Bad_Rate']/(bin_odd['#Bad'].sum()/bin_odd['#Obs'].sum()) if (bin_odd['#Bad'].sum()/bin_odd['#Obs'].sum())>0 else 0
        dic['Selected'] = 'Y' if (dic['Lift'] >= min_lift and dic['#Obs']>=hit_num) else 'N'
    return dic
# 例如;{
#     'var_name': 'factor_name',        # 指标列名
#     'Odds': 3.5,                      # 风险倍数
#     'Threshold': 150,                 # 最优分箱阈值
#     'type': '>',                      # 分箱条件（大于等于阈值）
#     '#Obs': 500,                      # 分箱样本数量
#     '%Obs': 0.3,                      # 分箱样本占比
#     '#Bad': 100,                      # 坏样本数量
#     '%Bad_Rate': 0.2,                 # 坏样本率
#     'Lift': 2.0,                      # Lift 值
#     'Selected': 'Y'                   # 是否筛选该分箱
# }

def calculate_ks(good, bad):
    """
    计算 KS 统计量
    :param good: 好样本数列表
    :param bad: 坏样本数列表
    :return: KS 值
    """
    cum_good = np.cumsum(good) / np.sum(good) if np.sum(good) > 0 else 0
    cum_bad = np.cumsum(bad) / np.sum(bad) if np.sum(bad) > 0 else 0
    ks = np.abs(cum_good - cum_bad)
    return ks
# 规则效果分析和统计
def bin_result_summary(bindata,bin_summary,sample_type,sub_div_bin,min_num,hit_num,method,numOfSplit):
    '''
    :param bindata:     传入需要分箱的数据
    :param bin_summary: 详见函数  bin_result_summary_final
    :param sample_type: 样本类型  可同时分析不同样本类型的数据
    :param sub_div_bin: 头部和尾部样本占比
    :param min_num:     最小分箱数量
    :param method:      分箱方法
    :param numOfSplit:  分箱数量
    :return:
    '''
    for sample_type_sub in sample_type:  #迭代每个样本类型（例如 Total 或 其他）。
        print('正在分析的样本类型为：', sample_type_sub)
        if '其他' in sample_type_sub:
            describedata = bindata[bindata[sample_type_col[sample_type_sub][0]].map(
                lambda x: str(x) not in sample_type_col[sample_type_sub][1])]
        elif 'Total' in sample_type_sub:#如果样本类型是 Total，则直接使用全部数据。
            describedata = bindata
        else:
            describedata = bindata[bindata[sample_type_col[sample_type_sub][0]].map(
                lambda x: str(x) in sample_type_col[sample_type_sub][1])]
        target = sample_type_target[sample_type_sub]
        for target_sub in target:
            print("分析的目标字段为：", target_sub)
            mydata1 = describedata[describedata[target_ripe[target_sub][0]] == 1]  ##获取成熟样本
            mydata1 = mydata1.drop(labels=target_del_col[target_sub], axis=1)
            min_rate=target_min_rate[target_sub][0]
            print(sample_type_sub, target_sub, '每箱最小占比：', min_rate, '数据量：', len(mydata1))
            for var in mydata1.columns[:-1]:
                print('正在分析变量:', var)
                try:
                    sample_bin = select_best_lift(data=mydata1, flag_name=target_sub, factor_name=var,
                                                  min_rate=min_rate,  sub_div_bin=sub_div_bin, min_num=min_num,hit_num=hit_num,
                                                  min_lift=sample_type_lift[sample_type_sub][target_sub],method=method,numOfSplit=numOfSplit)
                    if len(sample_bin) <= 2:
                        cnt = mydata1[var].count()
                        data1 = mydata1[[var, target_sub]].dropna()
                        bad = sum(data1[target_sub] == 1)
                        badrate = bad / cnt
                        sample_bin = {'#Bad': bad, '#Obs': cnt, '%Bad_Rate': badrate, '%Obs': 1, 'Odds': 1,
                                      'Lift': 1, 'Threshold': 'NaN', 'type': 'NaN', 'var_name': var,'Selected':'N'}
                except:
                    cnt = mydata1[var].count()
                    data1 = mydata1[[var, target_sub]].dropna()
                    bad = sum(data1[target_sub] == 1)
                    badrate = bad / cnt
                    sample_bin = {'#Bad': bad, '#Obs': cnt, '%Bad_Rate': badrate, '%Obs': 1, 'Odds': 1,
                                  'Lift': 1, 'Threshold': 'NaN', 'type': 'NaN', 'var_name': var,'Selected':'N'}
                sample_bin = pd.DataFrame.from_dict(sample_bin, orient='index').T
                sample_bin['样本类型'] = sample_type_sub
                sample_bin['坏客户定义'] = target_sub
                sample_bin = sample_bin[
                    ['var_name', '样本类型', '坏客户定义', 'Threshold', 'type', '#Obs', '%Obs', '#Bad', '%Bad_Rate', 'Odds',
                     'Lift','Selected']]
                sample_bin.rename(columns={'%Bad_Rate': '%Bin_Bad_Rate', 'var_name': '变量英文名','Selected':'标签2'},  inplace=True)
                bin_summary = pd.concat([bin_summary, sample_bin], ignore_index=True)
    return bin_summary


# 规则效果分析和统计最终结果
def bin_result_summary_final(hit_num, bindata, var_select01, sub_div_bin, min_num, sample_type, method, numOfSplit):
    # 之前的代码保持不变
    bin_summary = pd.DataFrame(columns=['变量英文名', '样本类型', '坏客户定义', 'Threshold', 'type', '#Obs', '%Obs',
                                        '#Bad', '%Bin_Bad_Rate', 'Odds', 'Lift', '标签2'])
    bin_summary_01 = bin_result_summary(bindata=bindata, bin_summary=bin_summary, sample_type=sample_type,
                                        sub_div_bin=sub_div_bin, min_num=min_num, hit_num=hit_num, method=method,
                                        numOfSplit=numOfSplit)

    bin_summary_select_var = ['序号', '分析时间', '样本类型', '坏客户定义', '变量英文名', '变量中文名',
                              '样本区间', '%Bad_Rate(不含缺失值)', '%Bad_Rate(包含缺失值)', '标签1']
    bin_summary_02 = pd.merge(var_select01[bin_summary_select_var], bin_summary_01,
                              on=['变量英文名', '样本类型', '坏客户定义'], how='left')
    filter2 = bin_summary_02
    filter2['标签3'] = 'N'
    pattern = re.compile(r'\d{1,2}')

    for sampletype in filter2.样本类型.unique():
        for target_ls in filter2.坏客户定义.unique():
            filter2_ls = filter2[(filter2.样本类型 == sampletype) & (filter2.坏客户定义 == target_ls)]
            for varname in set(filter2_ls.变量中文名[(filter2_ls.标签1 == 'Y') & (filter2_ls.标签2 == 'Y')]):
                data1 = filter2_ls[
                    (filter2_ls.标签1 == 'Y') & (filter2_ls.标签2 == 'Y') & (filter2_ls.变量中文名 == varname)]
                if len(data1) == 1:
                    var = data1.变量英文名[data1.Odds == data1.Odds.max()].values[0]
                    filter2.loc[(filter2.标签1 == 'Y') & (filter2.标签2 == 'Y') & (filter2.变量英文名 == var) & (
                                filter2.坏客户定义 == target_ls), '标签3'] = 'Y'
                elif len(data1) > 1:
                    var1 = data1.变量英文名[data1.Odds == data1.Odds.max()].values[0]
                    filter2.loc[(filter2.标签1 == 'Y') & (filter2.标签2 == 'Y') & (filter2.变量英文名 == var1) & (
                                filter2.坏客户定义 == target_ls), '标签3'] = 'Y'

    # 计算分箱细节，得到每个分箱详细指标（含 IV(total) 和 total_ks）
    bins_detail = bin_result_detail(bindata=bindata, var_select02=filter2, sample_type=sample_type,
                                    sub_div_bin=sub_div_bin, min_num=min_num, method=method, numOfSplit=numOfSplit)

    # 取 IV(total) 和 total_ks 最大值，合并到 filter2
    agg_stats = bins_detail.groupby(['变量英文名', '样本类型', '坏客户定义']).agg({
        'IV(total)': 'max',
        'total_ks': 'max',
        'WOE(total)': 'first'
    }).reset_index()

    filter2 = pd.merge(filter2, agg_stats, on=['变量英文名', '样本类型', '坏客户定义'], how='left')

    return filter2, bins_detail



# 分箱结果明细
# #该函数生成每个分箱的详细统计信息，包括每个分箱的观测数、坏客户比率、WOE、IV 值等。
def bin_result_detail(bindata, var_select02, sample_type, sub_div_bin, min_num, method, numOfSplit):
    '''
    :param my_data:       需要分析的数据
    :param var_select02:  filter2
    :param sample_type:   样本类型
    :param sub_div_bin:   头部和尾部分箱占比
    :param min_num:       每个分箱的最小数量
    :param method:        best 根据业务逻辑分箱；equalfre 等频率分箱；equallen等宽分箱
    :param numOfSplit:    等宽或者等频分箱数
    :param return:        分箱结果明细（新增 bin_ks 和 total_ks）
    '''
    # 新增 bin_ks 和 total_ks 列
    bin_summary_details = pd.DataFrame(columns=[
        '序号', '分析时间', '样本类型', '坏客户定义', '变量英文名', '变量中文名', '样本区间', '标签1', '标签2', '标签3',
        'Bin', '#Obs', '%Obs', '#Cum_Obs', '%Cum_Obs', '#Good', '%Good', '#Cum_Good', '%Cum_Good',
        '#Bad', '%Bad', '#Cum_Bad', '%Cum_Bad', '%Bad_Rate', 'WOE', 'IV(bin)', 'IV(total)', 'Odds1', 'Odds2',
        'Lift', 'bin_ks', 'total_ks'  # 新增 KS 列
    ])

    for sample_type_sub in sample_type:
        print('正在分析的样本类型为：', sample_type_sub)
        if '其他' in sample_type_sub:
            describedata = bindata[bindata[sample_type_col[sample_type_sub][0]].map(
                lambda x: str(x) not in sample_type_col[sample_type_sub][1])]

        elif 'Total' in sample_type_sub:
            describedata = bindata
        else:
            describedata = bindata[bindata[sample_type_col[sample_type_sub][0]].map(
                lambda x: str(x) in sample_type_col[sample_type_sub][1])]

        target = sample_type_target[sample_type_sub]
        for target_sub in target:
            mydata1 = describedata[describedata[target_ripe[target_sub][0]] == 1]  # 获取成熟样本
            mydata1 = mydata1.drop(labels=target_del_col[target_sub], axis=1)
            min_rate = target_min_rate[target_sub][0]
            print(sample_type_sub, target_sub, '每箱最小占比：', min_rate, '数据量：', len(mydata1))

            for var in mydata1.columns[:-1]:  # 遍历变量
                print('正在分析变量:', var)
                try:
                    # 获取分箱结果
                    sample_bin = get_bin_lift(
                        data=mydata1,
                        flag_name=target_sub,
                        factor_name=var,
                        min_rate=min_rate,
                        sub_div_bin=sub_div_bin,
                        min_num=min_num,
                        method=method,
                        numOfSplit=numOfSplit
                    )

                    # 计算 KS
                    good = sample_bin['#Good'].values
                    bad = sample_bin['#Bad'].values

                    # 计算每个分箱的 KS 值
                    ks_values = calculate_ks(good, bad)
                    sample_bin['bin_ks'] = ks_values  # 每个分箱的 KS
                    sample_bin['total_ks'] = max(ks_values)  # 累计 KS（取最大值）

                    # 计算 IV 和 WOE
                    iv_total = sample_bin['IV(bin)'].max()  # 计算最大 IV
                    sample_bin['WOE'] = (sample_bin['%Good'] / sample_bin['%Bad']).apply(
                        lambda x: np.log(x) if x > 0 else 0)  # 计算 WOE

                    # 计算变量对应的最大 WOE
                    max_woe = sample_bin['WOE'].max()  # 最大 WOE
                    sample_bin['IV(total)'] = iv_total  # 设置为最大 IV 值
                    total_obs = sample_bin['#Obs'].sum()  # 总观测数
                    weighted_woe = (sample_bin['WOE'] * sample_bin['#Obs']).sum() / total_obs  # 加权平均 WOE
                    sample_bin['WOE(total)'] = weighted_woe  # 设置为加权平均值

                except Exception as e:
                    print(f"计算 KS 时出错: {e}")
                    value = ['(-inf,inf)', len(mydata1), 1, len(mydata1), 1,
                             sum(mydata1[target_sub] == 0), 1, sum(mydata1[target_sub] == 0), 1,
                             sum(mydata1[target_sub] == 1), 1, sum(mydata1[target_sub] == 1), 1,
                             sum(mydata1[target_sub] == 1) / len(mydata1),
                             np.nan, np.nan, np.nan, 1, 1, 1, np.nan, np.nan]  # 新增 KS 默认值
                    sample_bin = pd.DataFrame([value], columns=[
                        'Bin', '#Obs', '%Obs', '#Cum_Obs', '%Cum_Obs', '#Good', '%Good', '#Cum_Good', '%Cum_Good',
                        '#Bad', '%Bad', '#Cum_Bad', '%Cum_Bad', '%Bad_Rate', 'WOE', 'IV(bin)', 'IV(total)',
                        'Odds1', 'Odds2', 'Lift', 'bin_ks', 'total_ks'
                    ])

                # 添加变量信息到分箱结果
                sample_bin['变量英文名'] = var
                sample_bin['样本类型'] = sample_type_sub
                sample_bin['坏客户定义'] = target_sub

                var_msg = var_select02.loc[
                    (var_select02.样本类型 == sample_type_sub) &
                    (var_select02.变量英文名 == var) &
                    (var_select02.坏客户定义 == target_sub),
                    ['序号', '分析时间', '样本类型', '坏客户定义', '变量英文名', '变量中文名', '样本区间', '标签1', '标签2', '标签3']
                ]

                # 合并变量信息与分箱数据
                merge = pd.merge(var_msg, sample_bin, on=['变量英文名', '样本类型', '坏客户定义'], how='left')
                bin_summary_details = pd.concat([bin_summary_details, merge], ignore_index=True)

    # ── 给每个分箱加金额逾期率 ──
        money_col_aligned = money_col.loc[bindata.index].reset_index(drop=True)
        bindata_reset = bindata.reset_index(drop=True)
        bindata_with_money = bindata_reset.copy()
        bindata_with_money['money'] = money_col_aligned.values

        money_rate_list = []
        for _, row in bin_summary_details.iterrows():
            var      = row['变量英文名']
            bin_label = row['Bin']
            target_sub = row['坏客户定义']

            if var not in bindata_with_money.columns:
                money_rate_list.append(np.nan)
                continue
            try:
                # 根据 Bin 标签判断范围
                if bin_label == '缺失值':
                    mask = bindata_with_money[var].isnull()
                elif bin_label.startswith('(-inf,'):
                    upper = float(bin_label.split(',')[1].strip().rstrip(']'))
                    mask = bindata_with_money[var] <= upper
                elif bin_label.endswith('inf)'):
                    lower = float(bin_label.split('(')[1].split(',')[0].strip())
                    mask = bindata_with_money[var] > lower
                elif bin_label.startswith('(') and ',' in bin_label:
                    lower = float(bin_label.split('(')[1].split(',')[0].strip())
                    upper = float(bin_label.split(',')[1].strip().rstrip(']'))
                    mask = (bindata_with_money[var] > lower) & (bindata_with_money[var] <= upper)
                elif bin_label.startswith('[') and bin_label.endswith(']') and ',' not in bin_label:
                    val = float(bin_label.strip('[]'))
                    mask = bindata_with_money[var] == val
                else:
                    money_rate_list.append(np.nan)
                    continue

                sub = bindata_with_money[mask]
                total_money = sub['money'].sum()
                bad_money   = sub.loc[sub[target_sub] == 1, 'money'].sum()
                rate = bad_money / total_money if total_money > 0 else np.nan
                money_rate_list.append(rate)
            except:
                money_rate_list.append(np.nan)

        bin_summary_details['金额逾期率'] = money_rate_list
        return bin_summary_details









def get_summary(filter2):
    '''
    :param filter2: 变量效果分析和筛选的数据
    :return: 泛化变量汇总信息（含 KS、IV、WOE 等指标）
    '''
    aa = filter2

    # 获取唯一样本类型和坏客户定义组合
    summary_info = aa[['样本类型', '坏客户定义']].drop_duplicates().reset_index(drop=True)

    # 初始化统计列
    var_num = []
    var_num_label1 = []
    var_num_label2 = []
    var_num_label3 = []
    avg_iv = []
    max_ks = []
    avg_woe = []
    avg_woe_total = []

    for _, row in summary_info.iterrows():
        sample_type = row['样本类型']
        target = row['坏客户定义']

        # 筛选当前样本类型和坏客户定义的数据
        data = aa[(aa['样本类型'] == sample_type) & (aa['坏客户定义'] == target)]

        # 计算变量数量
        var_num.append(len(data))

        # 计算标签筛选后的变量数量
        data_label1 = data[data['标签1'] == 'Y']
        var_num_label1.append(len(data_label1))

        data_label2 = data[(data['标签1'] == 'Y') & (data['标签2'] == 'Y')]
        var_num_label2.append(len(data_label2))

        data_label3 = data[(data['标签1'] == 'Y') & (data['标签2'] == 'Y') & (data['标签3'] == 'Y')]
        var_num_label3.append(len(data_label3))

        # 计算 IV、KS、WOE 的平均值或最大值
        avg_iv.append(data['IV(total)'].mean() if 'IV(total)' in data.columns else np.nan)
        max_ks.append(data['total_ks'].max() if 'total_ks' in data.columns else np.nan)
        avg_woe.append(data['WOE'].mean() if 'WOE' in data.columns else np.nan)

    # 添加统计列到 summary_info
    summary_info['变量总数'] = var_num
    summary_info['标签1筛选变量数'] = var_num_label1
    summary_info['标签1剔除变量数'] = summary_info['变量总数'] - summary_info['标签1筛选变量数']
    summary_info['标签2筛选变量数'] = var_num_label2
    summary_info['标签2剔除变量数'] = summary_info['标签1筛选变量数'] - summary_info['标签2筛选变量数']
    summary_info['标签3筛选变量数'] = var_num_label3
    summary_info['标签3剔除变量数'] = summary_info['标签2筛选变量数'] - summary_info['标签3筛选变量数']
    summary_info['剩余变量占比'] = summary_info['标签3筛选变量数'] / summary_info['变量总数']

    # 添加 IV、KS、WOE 统计
    summary_info['平均IV'] = avg_iv
    summary_info['最大KS'] = max_ks
    summary_info['平均WOE'] = avg_woe
    #summary_info['平均WOE(total)'] = avg_woe_total
    # 最终汇总结果输出
    summary_info = summary_info[[
        '样本类型', '坏客户定义', '变量总数',
        '标签1剔除变量数', '标签1筛选变量数',
        '标签2剔除变量数', '标签2筛选变量数',
        '标签3剔除变量数', '标签3筛选变量数',
        '剩余变量占比', '平均IV', '最大KS', '平均WOE'
    ]]

    # 计算总计行
    total_row = {
        '样本类型': '总计',
        '坏客户定义': '',
        '变量总数': summary_info['变量总数'].sum(),
        '标签1剔除变量数': summary_info['标签1剔除变量数'].sum(),
        '标签1筛选变量数': summary_info['标签1筛选变量数'].sum(),
        '标签2剔除变量数': summary_info['标签2剔除变量数'].sum(),
        '标签2筛选变量数': summary_info['标签2筛选变量数'].sum(),
        '标签3剔除变量数': summary_info['标签3剔除变量数'].sum(),
        '标签3筛选变量数': summary_info['标签3筛选变量数'].sum(),
        '剩余变量占比': summary_info['标签3筛选变量数'].sum() / summary_info['变量总数'].sum(),
        '平均IV': summary_info['平均IV'].mean(),
        '最大KS': summary_info['最大KS'].max(),
        '平均WOE': summary_info['平均WOE'].mean()
    }

    # 添加总计行
    summary_info = pd.concat([summary_info, pd.DataFrame([total_row])], ignore_index=True)

    return summary_info
# 7.基于加载的函数进行描述性统计分析、变量分箱、规则效果分析和筛选、分析结果和待泛化策略自动化输出
# 变量描述性统计分析，ana_people表示策略分析人是谁
# 假设我们已经有 IV(total), KS(total), WOE 的计算逻辑
# 假设在 filter2 中这三列是已经计算过的，代码可能如下：

import datetime

# 变量描述性统计分析

# 策略测算效果分析和筛选

# 变量分析和筛选情况


# 8.基于xlsxwriter包自动化输出策略测算结果

# 1. 输出变量筛选汇总表

# 2. 输出变量基础分析和筛选表

# 3. 输出变量分箱明细表


# 4. 修改后的输出函数，用于输出变量效果分析和筛选表（包含IV、KS等指标）
