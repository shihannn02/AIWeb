---
name: binning-chisquare
description: 卡方分箱（Chi-Square Binning）—— 用卡方合并算法对连续变量分箱，支持 OOT 切分
version: 1.0.0
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [risk, credit, binning, python, fintech, chisquare]
    category: risk-strategy
related_skills: [zhunru, binning-quantile, binning-5percent]
---

# 卡方分箱（Chi-Square Binning）

## 何时使用

- 需要对数值型连续变量做**卡方分箱**（卡方合并法）
- 分箱后用于后续变量筛选、规则制定
- 作为全量分箱分析的第一步

## 分箱逻辑

卡方分箱使用 bbbrisk 库的 chi2 算法：

1. **初始化**：先用等频分成 `init_bin_num` 个初始箱（默认 20）
2. **合并**：逐对计算相邻两箱的卡方值，合并卡方值最小（差异最小）的两箱
3. **迭代**：重复合并直到达到目标 `bin_num` 箱
4. **特殊值处理**：`-999`, `-9999`, `-1111` 单独拆成独立箱（同等频）
5. **FIT/APPLY 模式**：
   - **FIT（Train）**：卡方算法从 train 学习分箱边界
   - **APPLY（Test）**：用 train 学到的边界对 test 分箱

> 卡方分箱与等频分箱的核心区别：卡方会合并 bad_rate 相似的相邻箱，使得每箱的坏率差异最大化；等频只保证每箱样本数相近。

## 缺失值处理

卡方分箱的缺失值处理方式与等频完全一致：
- 所有缺失值/异常值统一替换为 `-999`
- `-999` 作为特殊值单独拆箱，不影响正常值的卡方合并

## 输出格式

与等频分箱完全一致：bad_rate 蓝色数据条 + 按 feature 交替灰白背景 + 20 列全字段

| Sheet | 内容 | 说明 |
|-------|------|------|
| `Train分箱明细` | 所有变量在 train 上的卡方分箱结果 | 边界从 train 的 chi2 算法学习 |
| `Test分箱明细` | 所有变量在 test 上用同一边界的分箱结果 | 仅当 OOT_RATIO>0 时生成 |

## 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `file_path` | 输入数据文件路径 | 需用户指定 |
| `output_file` | 输出 Excel 路径 | .../分箱结果_卡方.xlsx |
| `label` | 逾期标签列 | `overdue_flag2` |
| `time_col` | 时间列（OOT 切分用） | `create_time_x` |
| `drop_cols` | 删除的无关列 | ID/金额/衍生字段等 |
| `bin_num` | 卡方分箱目标箱数 | `10` |
| `init_bin_num` | 卡方初始箱数 | `20` |
| `OOT_RATIO` | OOT 划分比例 | `0.2` |
| `INCLUDE_CAT` | 保留类别变量 | `True` |

**所有过滤开关保持关闭**（LONGTAIL/MISSING/CONCENTRATION/PSI/IV/CORR 全 False）。

## 操作步骤

### 第〇步：数据预处理

```python
# 过滤标签异常值（-1 = 屏蔽, NaN = 未到观察期）
data = data[data[label].isin([0, 1])].copy()
data[label] = data[label].astype(int)

# 按时间排序（字符串排序，不是 datetime 排序）
data = data.sort_values('create_time_x').reset_index(drop=True)

# iloc 切分（非 quantile 分位点切分）
split_idx = int(len(data) * 0.8)
train = data.iloc[:split_idx]
test  = data.iloc[split_idx:]
```

### 第一步：收集参数

1. 数据文件路径
2. 输出保存路径
3. 逾期标签列名（默认 `overdue_flag2`）
4. 时间列名（默认 `create_time_x`）
5. 目标箱数（默认 `10`）
6. OOT 比例（`0`=不划分，`0.2`=80%train/20%oot）
7. 初始箱数（默认 `20`）
8. 需要删除的额外列

### 第二步：修改参数

修改 `scripts/binning.py` 顶部参数区域。

### 第三步：运行

```bash
python C:\Users\6\AppData\Local\hermes\skills\binning-chisquare\scripts\binning.py
```

## 已知陷阱

- **卡方失败时自动回退到等频**：chi2 算法在极少数变量上可能因数值问题失败，脚本会自动回退到 `pd.qcut`
- **重复边界**：卡方合并过程中可能产生重复的边界值，`pd.cut` 已设置 `duplicates='drop'`
- **依赖 bbbrisk**：脚本内置了 bbbrisk 包（`scripts/bbbrisk/`），无需额外安装
- **product 列不存在时需加保护**：`data[data['product']!='unKnow'].copy()` 在无 product 列的数据上 KeyError。脚本顶部已加 `if 'product' in data.columns` 保护，但迁移到新数据时仍需确认。
- **输入为 .csv 时自动切换读法**：脚本默认 `pd.read_excel()`，如果输入是 .csv 文件，需手动改为 `pd.read_csv()`。建议在参数区加扩展名判断。
- **字符串排序陷阱（⚠️ 高频错误）：** `sort_values('create_time_x')` 是字符串排序，`parse_time` → `sort_values('_tp')` 是 datetime 排序。字符串排序下 `"10:04"` 排在 `"5:13"` 前面（`'1' < '5'`），但真实时间 `5:13` 更早。误用 datetime 排序会导致 Train/Test 切分边界偏移，bad count 差异约 9/2652 = 0.3pp。用户明确要求字符串排序。
- **`train.csv` 是 UTF-8 编码：** 用户 iloc 切分后用 `df.to_csv('train.csv')` 保存，默认 UTF-8，不是原始 CSⅤ 的 GBK。读取时不需要 `encoding='gbk'`。
- **Test 阶段 \"loop of ufunc\" 错误：** OOT 中某些特征正常值全为 -999 时，`np.log()` 报错。加 `try/except` 跳过即可。
