---
name: feature-filter
description: 分箱后变量筛选 — 头/尾箱高坏率判定 + U型识别 + Train坏率颜色标记 + 月度稳定性，输出7-Sheet完整报告
version: 3.4.0
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [risk, credit, binning, python, fintech, feature-filter]
    category: risk-strategy
---

# 变量筛选（feature-filter）

## 何时使用

- 已有分箱结果（Train分箱明细 + Test分箱明细），需要筛选候选变量
- 用户指定坏率阈值，筛选头/尾箱高风险的排除规则变量
- 识别 U 型分布变量供人工判断
- 基于 Train 坏率自动颜色标记，Test 原始分箱输出供人工核对

## 前置依赖

需要先完成分箱，得到包含以下两个 Sheet 的 Excel 文件：
- `Train分箱明细` — 所有变量在 Train 上的分箱结果（"2.变量分箱" Sheet）
- `Test分箱明细` — 所有变量在 Test 上（用 Train 同一边界）的分箱结果

> 分箱可用 `binning-quantile` / `binning-chisquare` / `binning-5percent` 中的任意一种生成。

## 筛选标准

### 筛选条件 0：箱样本量门槛

头箱或尾箱的 `total`（样本数）必须 ≥ 20，否则不考虑该变量。

### 筛选条件 1：头/尾箱高坏率（排除规则）

- **头箱**：按 `min_bin` 排序后的第一箱（最低值端）
- **尾箱**：按 `min_bin` 排序后的最后一箱（最高值端）
- 头箱或尾箱的 `bad_rate` > 用户设置的阈值，且该箱 `total ≥ 20`，则视为候选
- 第一箱低于阈值、第二箱才高于阈值→**不予考虑**（高坏率未从最极端端开始）
- **不前置要求单调性**

### 筛选条件 2：U 型分布（人工判断）

- 头尾坏率偏高，中间某 2-3 箱坏率偏低
- 标记为「U型人工判断」，**无色背景**，由用户自行决定是否保留

### 筛选条件 3：颜色标记（基于 Train 坏率）

不自动做 Test 验证。颜色基于选中箱（头箱或尾箱）的 Train bad_rate 判定：

| 效果 | 条件 | 颜色 |
|------|------|:----:|
| 🟢 好 | 选中箱 bad_rate ≥ 65% | 绿色 |
| 🟠 一般 | 阈值 ≤ bad_rate < 65% | 橙色 |
| 🔴 不好 | bad_rate < 阈值 | 红色（极少出现） |

## 输出文件结构

### feature_filter.py 输出（v3.2.0 — xlsxwriter 格式，输出到原文件）

输出到**原文件**（覆盖写入），使用 xlsxwriter 重建全部 7 个 Sheet。Sheet1-2 数据与格式和原始分箱完全一致：

- **Sheet 1-2（"2.变量分箱" + Test分箱明细）**：用 `details_result_output` 写入，**蓝色数据条、交替灰白背景**，32列格式与用户的 `binning_v2.py` 一致。
- **Sheet 3（候选变量汇总）**：头/尾箱高坏率变量，橙色标记好变量，分类分组空行。
- **Sheet 4（训练筛选变量）**：候选在 Train 上的精简分箱明细，`%Bad_Rate` 蓝色 data bar。
- **Sheet 5（测试筛选变量）**：候选在 Test 上的精简分箱明细，供人工对比。
- **Sheet 6（Train月度稳定性）**：按箱×月份矩阵，蓝色 data bar。
- **Sheet 7（Test月度稳定性）**：同上。

**关键约束：**
- 直接写入原文件（xlsxwriter 重建全部7个Sheet），不要用 openpyxl 追加。
- 数据读取用 pandas（只读模式），输出用 `pd.ExcelWriter(engine='xlsxwriter')` 重建整个文件。
- Sheet 1-2 用 `details_result_output`（蓝色数据条，32列格式），包含 Odds1/Odds2 和全部累计统计列 (#Cum_Obs, %Cum_Obs, #Cum_Good, %Cum_Good, #Cum_Bad, %Cum_Bad)。
- NaN/INF 值必须在写入前清理（`fillna('')` + `replace([inf, -inf], '')`），否则 xlsxwriter 报 TypeError。
- 月度稳定性内置在 feature_filter.py 中，通过 `build_stability_data()` + `_write_stability_xlsx()` 实现。硬编码 label=target3，CSV 路径从分箱结果同目录推导。
- 脚本顶部的 `raw_path`、`cutoff_date`、`label_col` 为硬编码，需手动修改以匹配不同数据源。

| 列 | 说明 |
|------|------|
| 变量名字 | 特征名 |
| 最高坏率 | 该变量所有箱中最高 bad_rate |
| 变量效果 | 🟢好 / 🟠一般 / 🔴不好 / U型人工判断 |
| 选出的原因 | 头箱高 / 尾箱高 / U型分布 的具体说明 |
| 中文名字 | 特征的中文释义（基于模式匹配） |
| 样本占比 | 选中箱人数占全量 Train 样本的比例 |

### Sheet 2：训练筛选变量（Train）

候选变量在 Train 上的精简分箱明细。

### Sheet 3：测试筛选变量（Test）

候选变量在 Test 上的精简分箱明细。

### Sheet 4：Train月度稳定性

按箱×月份矩阵，显示每个筛选变量分月的 总人数/逾期数/坏率/金额逾期率。

### Sheet 5：Test月度稳定性

同上，在 Test 各月份上。

| 列 | 说明 |
|------|------|
| 变量名字 | 特征名 |
| 最高坏率 | 该变量所有箱中最高 bad_rate |
| 变量效果 | 🟢好 / 🟠一般 / 🔴不好 / U型人工判断 |
| 选出的原因 | 头箱高 / 尾箱高 / U型分布 的具体说明 |
| 中文名字 | 特征的中文释义（基于模式匹配） |
| 样本占比 | 选中箱人数占全量 Train 样本的比例 |

**颜色标记（整行）：**
- 🟠 好 → 橙色（只有效果好的变量会标色）
- 一般 / 不好 / U型人工判断 → 无色（透明背景）

**分类分组：** 候选变量按中文含义自动分类（空号异常查询/查询时间间隔/查询频次/查询命中/通讯录通话/身份信息等），同类放一起，不同类之间空一行。避免从同一类中重复选变量（如 7天通话次数 和 24小时通话次数 只需要选一个）。

### Sheet 2：训练筛选变量（Train）

候选变量在 Train 上的精简分箱明细：

| 列 | 说明 |
|------|------|
| 变量名称 | 特征名 |
| Bin | 分箱区间 |
| %Bad_Rate | 坏率（蓝色数据条） |
| IV(total) | 总IV值 |
| total_ks | 总KS值 |

### Sheet 3：测试筛选变量（Test）

候选变量在 Test 上的精简分箱明细，与 Sheet2 格式完全一致，供人工对比 Train/Test 稳定性。

| 列 | 说明 |
|------|------|
| 变量名称 | 特征名 |
| Bin | 分箱区间 |
| %Bad_Rate | 坏率（蓝色数据条） |
| IV(total) | 总IV值 |
| total_ks | 总KS值 |

### Sheet 6：Train月度稳定性（由 monthly_stability_v3.py 生成）

按箱拆分，分月显示每个筛选变量的总人数、逾期数、坏率、金额逾期率。

每个筛选变量在 Train 各月份上的整体坏率矩阵。

**格式：**
- 第一行：月份名，后续每2列为1个变量（逾期率 + 金额逾期率）
- 列：`月份 | feature1_逾期率 | feature1_金额逾期率 | feature2_逾期率 | feature2_金额逾期率 | ... | 大盘_逾期率 | 大盘_金额逾期率`
- 行：各月份 + 合计行
- 逾期率列使用 **Excel 条件格式 data bar**（蓝色渐变 `#5B9BD5`）
- 仅对筛选变量输出

### Sheet 7：Test月度稳定性（由 monthly_stability_v3.py 生成）

同上，在 Test 各月份上，格式与 Sheet6 完全一致。

## 操作流程

### 第一步：收集参数

1. **分箱结果 Excel 路径**（需含 Train分箱明细 + Test分箱明细，仅2个Sheet）
2. **坏率阈值**（如 0.6 = 60%）

系统自动完成以下全部流程，输出到**原文件**（覆盖写入）：
- Sheet 3: 候选变量汇总（xlsxwriter，橙色标记好变量）
- Sheet 4: 训练筛选变量（xlsxwriter，蓝色 data bar）
- Sheet 5: 测试筛选变量（xlsxwriter）
- Sheet 6: Train月度稳定性（xlsxwriter，蓝色 data bar）
- Sheet 7: Test月度稳定性（xlsxwriter）

**关键：** 直接写入原分箱文件，Sheet 1-2 用 `details_result_output`（蓝色数据条、交替灰白背景），32列格式与用户的 `binning_v2.py` 一致。不生成 `_完整.xlsx` 新文件。
注意：脚本硬编码原始 CSV 路径（与分箱结果同目录），标签列 target3，时间截止 2026-05-01。

### v3.3.0 修复记录

修复了以下 BUG（2026-05-29）：

1. **Sheet1-2 空列问题**：`write_xlsxwriter_output` 原来硬编码 32 列格式（含 `#Cum_Obs`、`Odds1` 等），但头尾5%分箱输出只有 24 列，不存在的列被填充为 NaN。**修复**：改为动态列，只写实际存在的列。

2. **月度稳定性单值箱（如 `[5.0]`、`[14.0]`）数据为 0**：`parse_bin_boundary` 对缺失箱和单值箱返回了 4 元素元组，但 `assign_bin` 只处理 ≥5 元素元组，4 元素被跳过。**修复**：全部返回统一的 5 元素格式 `(lo, hi, left_closed, right_closed, is_missing)`。

3. **-999 值未映射到缺失值箱**：原始 CSV 的 -999 值在月度稳定性中未被转为 NaN，导致落入 `(-inf, 3.0]` 等首箱而非 `缺失值` 箱。**修复**：`assign_bin` 加入 `SPECIAL_VALUES` 检查，`build_stability_data` 中先 `replace(SPECIAL_VALUES, np.nan)` 再分箱。

### 第二步：运行脚本

```bash
python C:\\Users\\6\\AppData\\Local\\hermes\\skills\\feature-filter\\scripts\\feature_filter.py
```

脚本交互式询问参数后执行，一次性完成筛选 + 月度稳定性。

## 已知陷阱

- **xlsxwriter 重建全部7个Sheet写入原文件：** feature_filter.py 直接用 `pd.ExcelWriter(engine='xlsxwriter')` 重建整个文件（覆盖写），不依赖 openpyxl 追加。Sheet 1-2 的 `details_result_output` 函数已内建蓝色数据条和交替灰白背景，不需要从原始文件复制格式。
- **⚠️ 关键BUG：`normalize_cols` 会重命名列名为英文，写 Sheet 1-2 时如果用中文列名选数据会全空！** `normalize_cols()` 把 '变量英文名' → 'feature'、'#Bad' → 'bad'、'#Obs' → 'total'、'%Bad_Rate' → 'bad_rate' 等。而 `write_xlsxwriter_output` 中的 `required_cols_binning` 用的是中文列名（'变量英文名', '#Bad' 等）。如果在 `main()` 中只传 `normalize_cols` 后的数据给写函数，`train_out = train_df.copy()` 后再 `train_out[required_cols_binning]` 会全部返回空。**修复：`write_xlsxwriter_output` 必须接收原始未标准化的数据（`train_raw`/`test_raw`）用于 Sheet 1-2，标准化后的数据（`train`/`test`）用于分析和月度稳定性。** 签名：`def write_xlsxwriter_output(output_path, analysis_rows, train_df, test_df, train_raw, test_raw, candidate_features, raw_input_path, label_col):`
- **Sheet 1-2 列数必须匹配用户的 `binning_v2.py`：** `required_cols_binning` 必须包含全部32列（含 Odds1/Odds2 和所有累计统计列）。否则 Sheet 1-2 的列会减少，用户会认为格式不对。
- **`read_binning_sheets` 必须支持 `'2.变量分箱'` Sheet 名：** 头尾5%分箱输出的 Train Sheet 名为 `'2.变量分箱'`（不是 `'Train分箱明细'`）。`read_binning_sheets` 需要先检查 `'2.变量分箱'`，再检查 `'Train分箱明细'`，最后回退到不含 test 的分箱 Sheet。旧版代码只查 `'train' in s.lower()`，匹配不到 `'2.变量分箱'`。
- **`get_chinese_name` 和 `classify_category` 需与 data 特征名同步：** 当前翻译覆盖了 `phone_*`、`id_*`、`olc_*` 三类特征。如果新增特征类型（如 `tt_score`、`white_type` 等），需在 `get_chinese_name` 中添加对应的翻译模式。`classify_category` 返回前缀字母排序的类别名（A_ 到 ZZ_），同类之间通过空行分隔。

- **count-vs-flag near-duplicates in OLC data**: `olc_qry_cnt_7d_phone > 0` and `olc_qry_hit_7d_flag_phone > 0` hit exactly the same people (47人, 65.96% bad rate). In OLC provider data, **count columns** (`*_cnt_*`) and **flag columns** (`*_flag_*`) for the same query and same time window are often derived from the same source — count>0 and flag=1 are identical. When both appear as candidates, flag to the user that they're the same rule under different names and pick one.\n- **串行串联中被完全重叠的规则自动丢弃**: 如果规则A和规则B在 Train 全量上命中完全相同的客群（如两个 OLC 查询变量都命中47人），串联时规则A先应用后，规则B的串联命中人数=0，lift=0.0，自动被丢弃。这不算bug——重叠规则在后序无额外区分价值。用户在变量选中如果有明显重叠的候选，建议串联前告知用户并精简。\n- **special value bins (-999/NaN) don't participate in head/tail checking**: always sorted last, excluded from head/tail bad_rate judgment
- **U-shape detection needs ≥5 bins**: can't detect with fewer bins
- **Overall bad rate computed from bad/total**: script auto-computes
- **Color uses openpyxl**: must be installed
- **Sample percentage based on single feature total**: avoid cross-feature double counting
- **Color BEFORE category blank rows**: in `write_result_excel`, must color first then insert_rows — otherwise row numbers shift and orange goes to wrong rows. Fixed in v1.3.1.
- **parse_bin_boundary uses comma-split, avoid regex trap**: `(-inf, 0.0]` with `re.findall` only catches `['0.0']`, misclassifies as single-value bin or hi=inf.
- **Monthly stability: number format on percentage columns**: values are decimals (0.5376), must set `number_format='0.00%'` on 坏率 and 金额逾期率 columns. Common bug: column index calculation wrong. Sub-header starts at column 2 (col 1=bin label), so first data column = 2, month group formula = `2 + mi*4 + si`.
- **Monthly stability: APPEND only, don't recreate**: use openpyxl `load_workbook` + `create_sheet` + `save`, never `pd.ExcelWriter` which recreates the whole file. See `scripts/append_monthly_stability.py`.
