---
name: zhunru
description: 信贷风控变量分箱 SOP，完成数据准备、全量分箱、变量筛选，输出原始分箱结果和筛选后候选变量两个 Excel 文件。
version: 1.17.1
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [risk, credit, binning, python, fintech]
    category: risk-strategy
related_skills: [binning-quantile, binning-chisquare, binning-5percent, feature-filter]
---

# 变量分箱 SOP（准入拒绝版）

## 何时使用

- 对信贷数据做全量变量分箱分析
- 筛选高坏率变量，制定**准入拒绝规则**（排除高风险客群）
- 输出候选变量供规则串联分析使用

---

> ⚠️ **一次只跑1种分箱**：等频 / 卡方 / 头尾5% 三选一，不可同时跑多个。用户在第〇步选择一种即可。
>
> ⚠️ **定位：准入拒绝** — 本 SOP 默认做排除规则筛选（头/尾箱高坏率），不是准入规则（头/尾箱低坏率）。用户的标准流程是：先分箱 → feature-filter 筛选高坏率变量（60%阈值） → 用户挑变量 → 串联分析输出最终准入文档。

## 准入策略完整流程

```mermaid
flowchart TD
    A([开始]) --> B[收集参数\n数据路径 / 标签列 / 时间列 / OOT比例等]
    B --> C{分箱方法是哪种？}

    C -->|等频 / 卡方| D[用户输入箱数]
    C -->|头尾5%| E[无需输入箱数]

    D --> F[数据验证\n检查标签列、大盘坏率、文件路径]
    E --> F

    F --> G{如何切分 Train / Test？}

    G -->|自行划分| H1[用户提供已切分的\ntrain + test 文件]
    G -->|条件划分| H2[用户指定日期截止点\n如 apply_date < "2026-05-01"]
    G -->|AI 划分| H3[AI 按时间列自动\n切分 (OOT_RATIO)]

    H1 --> I[执行分箱\nbinning.py]
    H2 --> I
    H3 --> I

    I --> J[输出分箱 Excel\n仅2个Sheet: 2.变量分箱 + Test分箱明细]
    J --> K1[调用 feature-filter skill\n输入坏率阈值 → 一次性输出7个Sheet到原文件]
    K1 --> K2[原文件更新为7个Sheet\n候选变量汇总 / 训练筛选变量 / 测试筛选变量\nTrain月度稳定性 / Test月度稳定性]
    K2 --> K[用户人工复核\n对比 train / test，评估稳定性]

    K -->|未收到指令| K

    K -->|收到指令| L[用户指定特征 + 阈值方向\n选定变量后做串联分析]

    L --> M[串联分析\n按最高坏率排序，丢弃弱规则\ntrain / test 分开计算命中和坏率]

    M --> N[输出报告\n单 Sheet：通过率 / 逾期率 / lift]

    N --> O([结束])
```

用户进入 skill 后，按以下交互流程执行：

### 第〇步：收集参数

先让用户输入以下信息：

1. **数据文件路径**（如 `~/Desktop/test_file/data.xlsx`）
2. **输出保存路径**（如 `~/Desktop/test_file/`）
3. **逾期标签列名**（如 `overdue_flag2`）
4. **时间列名**（如 `create_time_x`，用于 OOT 切分）
5. **分箱方法** — ⚠️ **请选择1种分箱，不可同时跑多个**：等频 / 卡方 / 头尾5%
6. **Train / Test 切分方式**（三选一）：
   - **自行划分**：用户已准备好 train + test 两个文件，分别给出路径
   - **AI 自动划分**（默认方法）：先过滤标签（`data[data[label].isin([0,1])]`）→ `sort_values('create_time_x')` 按时间排序 → `iloc[:int(len*ratio)]` 切分，用户指定 `OOT_RATIO`（如 `0.2` 表示 80% train / 20% oot，`0` 表示不切分全量用）
   > ⚠️ **用 iloc 切分，不用 quantile：** 用户明确要求 `iloc[:int(len*ratio)]` 而非 `time_col.quantile(ratio)`。quantile 切分可能把同一天的部分样本分到 Train、部分分到 Test，造成边界混淆。
   - **条件划分**：用户指定一个日期截止点（如 `apply_date < "2026-05-01"`），按时间列做字符串比较切分。适用于用户知道具体日期边界而非比例的场景。格式要求：时间列必须为 ISO 格式 `YYYY-MM-DD`（字符串比较与日期排序一致）。用法：`train = data[data[time_col] < cutoff].copy(); test = data[data[time_col] >= cutoff].copy()`。分箱时 FIT/APPLY 模式与 AI 自动划分相同——从 Train 学边界，同一边界应用至 Test。
7. **全局开关**：
   - `OOT_RATIO` — 仅在 AI 划分模式下使用
   - 所有过滤开关默认全关：`LONGTAIL_FILTER=False`, `MISSING_FILTER=False`, `CONCENTRATION_FILTER=False`, `PSI_FILTER=False`, `IV_FILTER=False`, `CORR_FILTER=False`, `INCLUDE_CAT=True`
   - 需要删除的额外列（如衍生计算字段 `risk_over_days`）

> ⚠️ **不要在第〇步问筛选阈值。** 筛选阈值（绝对/相对坏率阈值、单调性开关）应在 **第一步·五设定基线时** 才问。用户需要看到分箱结果和大盘坏率后，才能做出有依据的决策。先跑分箱出数据，再问阈值。

### 第一步：输出 2-Sheet 分箱文件（原始文件永不动）

收集完参数后，执行：
1. **数据准备与分箱**（根据切分方式三选一，注意边界一致性）：
   - **AI 自动划分**：运行 binning 脚本（含 OOT 切分）→ 输出 `分箱结果.xlsx`，包含 **两个 Sheet**：`2.变量分箱` + `Test分箱明细`。**Test 分箱必须用 Train 学到的同一边界**（FIT/APPLY 模式），不可对 test 独立重新分箱。独立分箱 skill（如 `binning-quantile`）已内建此逻辑。
   - **条件划分**：用户指定日期截止点（如 `apply_date < "2026-05-01"`）。先过滤标签（仅0/1），再按 `data[time_col] < cutoff` 切分。后续分箱逻辑与 AI 自动划分一致：FIT/APPLY 模式。时间列需为 ISO 格式（`YYYY-MM-DD`），切分后删除时间列和 ID 列再执行分箱。
   - **用户自行划分**：对 train 文件运行分箱（OOT_RATIO=0），**记录每列的分箱边界**，然后用同一边界对 test 文件做分箱。不推荐独立对 test 做 `pd.qcut` — 区间不一致会导致 train/test 无法对比。
2. 运行 **排除规则无单调筛选**（用用户指定的阈值）→ 输出候选变量（到新文件，不修改原分箱）
3. 月度稳定性自动计算（内置在 feature_filter.py 中）
4. **输出结果**：`{原路径}_筛选结果.xlsx`，包含 7 个完整 Sheet：

| Sheet | 内容 | 说明 |
|-------|------|------|
| 候选变量汇总 | 头/尾箱高坏率变量 + 颜色标记 + 中文名 + 分类分组 | 🟢好 / 🟠一般 / 🔴不好 / U型人工判断 |
| 训练筛选变量 | 候选变量在 Train 上的精简分箱明细 | %Bad_Rate 蓝色数据条 |
| 测试筛选变量 | 候选变量在 Test 上的精简分箱明细 | 供人工对比稳定性 |
| Train月度稳定性 | 按箱×月份矩阵：总人数/逾期数/坏率/金额逾期率 | 蓝色 data bar |
| Test月度稳定性 | 同上，在 Test 各月份上 | 蓝色 data bar |

**关键点：** 这一步只是产出数据供人看，不做最终决策。

### 第一步·五：设定排除规则坏率阈值（筛选候选变量前）

分箱结果出来后，准备做变量筛选前，**直接问坏率阈值**（不做准入 baseline，因为默认走排除规则）。

**流程：**
1. 通报大盘坏率（如 `Train大盘坏率: 47.35%`）
2. 问用户：**"你想设多少%拒绝？"**
3. 用户指定绝对阈值（如 `60%`、`70%`）
4. 该阈值直接传给 `feature-filter` 作为 `HEAD_TAIL_THRESHOLD`：

| 筛选模式 | 参数 | 示例 |
|---------|------|------|
| 排除规则（默认） | `HEAD_TAIL_THRESHOLD` = 用户指定值 | 用户说"60%"则设 0.60

> 基线影响筛选松紧度：基线越低，筛选越严格，只留下区分度最强的变量。

---

### 第二步：用户人工筛选

用户打开 Excel，查看各候选特征的：
- 候选变量汇总（颜色标记：🟢好 / 🟠一般 / 🔴不好 / 无色=U型人工判断）
- 训练筛选变量（Train完整分箱）
- 测试筛选变量（Test完整分箱，供核对自动判定的准确性）

**用户自行判断**哪些特征符合业务逻辑、分箱稳定、可解释性强。用户告诉 Agent 选中的特征列表。

> ⚠️ **阈值方向自动确定：** 不需要用户指定方向。头箱高坏率 → `变量 < 箱边界` 拒绝（低端客群），尾箱高坏率 → `变量 > 箱边界` 拒绝（高端客群）。`check_head_tail` 的返回值 `('head', ...)` 或 `('tail', ...)` 已指明方向。

然后用户选择串联排序方式：**按拒绝数量**（命中人数多的规则优先，覆盖面大）或**按坏率**（坏率高的规则优先，区分度强）。

### 第三步：输出最终准入文档

根据用户选中的特征和串联排序方式，执行 **串联分析**：

**串联排序方式（用户选择）：**
- **按坏率**（默认）：最高坏率优先，先拒掉最危险的客群，区分度最强
- **按拒绝数量**：命中人数多的规则优先，覆盖面更大

**阈值方向自动确定：**
- 头箱高坏率 → `变量 < 箱边界` 拒绝（该箱在低值端）
- 尾箱高坏率 → `变量 > 箱边界` 拒绝（该箱在高值端）

**规则丢弃逻辑：** 自动丢弃 lift < 1.10 或命中人数 < 5 的弱规则

**输出单一 Sheet 报告：**

```
                   现数据    调整后
总单数              191      123
逾期单数            109       58
件数逾期率         57.07%   47.15%
通过率            100.00%   64.40%
金额逾期率         57.07%   47.15%

增益效果：
上线：                        总样本  lift  badrate  规则命中  命中人数  拒绝率  坏客数  坏比率(串行)  lift_2  剩余样本数
ios评分卡0916 <= 354          191  1.39  79.31%     29       29   15.18%   23    79.31%    1.39    162
user_num > 2                 191  1.29  80.00%     20       15    7.85%   11    73.33%    1.38    147
```

---

## 可用脚本

> ⚠️ **一次只跑1种分箱**：等频 / 卡方 / 头尾5% 三选一，不可同时跑多个。用户在第〇步选择一种后，只加载对应的独立分箱 skill。

### 分箱 Skill（独立调用，按分箱方法选择）

| Skill | 方法 | 输出格式 |
| --- | --- | --- |
| `binning-quantile` | 等频分箱（`pd.qcut`） | 2-Sheet：Train分箱明细 + Test分箱明细 |
| `binning-chisquare` | 卡方分箱（卡方合并法） | 2-Sheet：Train分箱明细 + Test分箱明细 |
| `binning-5percent` | 头尾5%分箱（首尾精细，中间粗略） | 2-Sheet（OOT时）：Train分箱明细 + Test分箱明细 |\n\n> **等频和卡方** 内建 **FIT/APPLY 模式**：从 train 学习边界(FIT)，用同一边界对 test 分箱(APPLY)。\n> **头尾5%** 也支持 FIT/APPLY（通过记录 knot 索引位置重放），使用 `binning-5percent` skill 的 `references/binning_headtail5_oot.py`。
> 头尾5%支持两种切分方式：`cutoff_date` 条件切分（如 `apply_date < "2026-05-01"`）或 `OOT_RATIO` 比例切分。详见 `binning-5percent` skill。

### 筛选脚本（zhunru 内置）

| 脚本 / Skill | 作用 | 输出 |
| --- | --- | --- |
| **推荐** `feature-filter` (skill) | 头/尾箱高坏率 + U型识别 | 3-Sheet Excel（Test数据输出供人工比对） |
| `scripts/filter_by_head_tail.py` | **准入+排除双向筛选**：头/尾箱 bad_rate 低于大盘(准入) 或 某箱高于大盘(排除)，含单调性检查 | 筛选变量汇总 + 分箱明细 Excel |
| `scripts/filter_bins.py` | KS + bad_rate 双条件筛选 | 筛选变量汇总 + 分箱明细 Excel |

## 参考文件

| 文件 | 说明 |
| --- | --- |
| `references/串联分析.py` | 规则串联/并联评估脚本。自动处理 `#` 列名。 |
| `references/串联分析报告.py` | **修正版串联评估**：单一Sheet格式，区分规则命中/命中人数、badrate/坏比率(串行)，自动丢弃弱规则 |
| `references/admission-rule-filtering.py` | 准入规则筛选（仅看单调性 + 最低箱坏率，无 KS 要求） |
| `references/筛选排除特征.py` | **排除规则候选特征筛选**：单调 + 高风险箱识别 + 3-sheet 输出 |
| `references/排除规则无单调筛选.py` | **排除规则无单调**：头/尾箱高坏率(绝对或相对阈值) + 业务解释性 + 中文翻译 + 3-sheet 输出 |
| `references/binning_custom_special_values.py` | **自定义分箱**：-999/0/NaN 各单独一箱 + 其余值等频分箱，支持 FIT/APPLY 和条件切分 |

## 操作步骤

### 第零步：数据验证

```python
import os, pandas as pd
path = '~/Desktop/test_file'
path = os.path.expanduser(path)
if os.path.isdir(path):
    files = [f for f in os.listdir(path) if f.endswith(('.xlsx','.xls','.csv'))]
    print(f"目录内有: {files}")
elif os.path.isfile(path):
    print(f"文件: {path}")

# 自动检测文件类型和编码
if path.endswith('.csv'):
    df = pd.read_csv(path, encoding='gbk')  # 中文数据常为 GBK 编码
else:
    df = pd.read_excel(path)

label = 'overdue_flag2'

# 验证标签列实际列名（可能叫 overdue_flag / overdue_flag2 / fpd7）
overdue_cols = [c for c in df.columns if 'overdue' in c.lower() or 'fpd' in c.lower() or 'flag' in c.lower()]
print(f"可能标签列: {overdue_cols}")
print(f"标签列 '{label}' 存在: {label in df.columns}")

# 检查标签列是否有异常值（-1, NaN 等）
if label in df.columns:
    vc = df[label].value_counts(dropna=False)
    print(f"标签分布: {dict(vc)}")
    
# 大盘坏率（仅含 0/1 的有效样本）
valid_mask = df[label].isin([0, 1]) if label in df.columns else slice(None)
print(f"大盘坏率(仅0/1): {df.loc[valid_mask, label].mean():.2%}")
print(f"有效样本数(仅0/1): {valid_mask.sum()}")
```

> ⚠️ **标签异常值处理：** `overdue_flag` 列常含 `-1`（屏蔽客）、`NaN`（未到观察期）。分箱前必须 `data = data[data[label].isin([0, 1])]`，否则大盘坏率严重失真。

### 第一步：运行分箱（固定模板——不做任何筛选）

> 🔗 **独立分箱 Skill（推荐）：** 根据分箱方法直接使用对应独立 skill，无需手动修改参数：
> - **等频分箱** → `binning-quantile`（内建 FIT/APPLY 模式，自动输出 Train+Test 双 Sheet）
> - **卡方分箱** → `binning-chisquare`（内建 FIT/APPLY 模式，内含 bbbrisk 卡方算法库）
> - **头尾5%分箱** → `binning-5percent`（头尾各5%精细分箱，识别极端风险人群，OOT时输出 Train分箱明细+Test分箱明细 双Sheet）
>
> 以下步骤适用于手动配置场景，或作为独立 skill 的后备参考。

> ⚠️ 根据用户选择的切分方式有三种路径：
> - **AI 自动划分（推荐用 iloc 切分）**：修改 `scripts/binning.py` 参数后运行。使用 **iloc 切分法**（非 quantile 分位点切分）：
>   1. 先过滤标签列：`data = data[data[label].isin([0, 1])]`，排除 -1 和 NaN
>   2. `sort_values('create_time_x').reset_index(drop=True)` 按时间排序——**注意这是字符串排序**，不是 `parse_time` 后的 datetime 排序。用户明确要求字符串排序（如 `"10:04"` 排在 `"5:13"` 前面）。如果误用 datetime 排序（`parse_time` → `sort_values('_tp')`），Train/Test 切分边界会偏移，导致 bad count 差异约 9 例/2652 条。
>   3. `split_idx = int(len(data) * ratio)` → `data.iloc[:split_idx]` = Train，`data.iloc[split_idx:]` = Test
>   4. 这样避免了 quantile 分位点落在同一天时样本混淆的问题（用户明确要求）
> - **条件划分**：用户指定日期截止点（如 `apply_date < "2026-05-01"` 为 Train）。不等同于 iloc 比例切分——用户可能知道业务上的日期边界（如新产品上线日期、规则变更日期）而非比例。用法：
>   1. 先过滤标签：`data = data[data[label].isin([0, 1])]`
>   2. 直接按条件切分：`train = data[data[time_col] < cutoff].copy(); test = data[data[time_col] >= cutoff].copy()`
>   3. 时间列必须是 ISO 格式字符串（`YYYY-MM-DD`），字符串比较与日期排序一致
>   4. 后续分箱逻辑与 AI 自动划分相同——FIT/APPLY 模式，从 Train 学边界
>   5. 切分后删除时间列和 ID 列，再执行分箱
>   > ⚠️ **标签列必须加入 drop_cols：** 头尾5%的 OOT 脚本在 `preprocess()` 中创建 `dpd_7` 从 `data[label]`，但原始标签列（如 `target3`）需手动删除。如果不加入 `drop_cols`，原标签列会作为普通变量参与分箱，造成**数据泄漏**（`target3` 自己的坏率=100% 通过筛选）。正确做法：`"target3"` 放在 `drop_cols` 中，只保留脚本生成的 `dpd_7` 作为标签。
> - **用户自行划分**：分别对 train 和 test 文件运行 `scripts/binning.py`（OOT_RATIO=0），各自独立分箱，test 分箱结果用于后续验证

**标准配置（已固化，不要改动）：**

| 参数 | 值 | 说明 |
|------|:---:|------|
| `bin_num` | `10` | 10 箱 |
| `OOT_RATIO` | `0` 或用户指定 | 是否划分 OOT |
| `LONGTAIL_FILTER` | `False` | — |
| `MISSING_FILTER` | `False` | — |
| `CONCENTRATION_FILTER` | `False` | — |
| `PSI_FILTER` | `False` | — |
| `IV_FILTER` | `False` | — |
| `CORR_FILTER` | `False` | — |
| `INCLUDE_CAT` | `True` | 保留类别变量 |

此步骤只做两件事：
1. 删除 ID/金额/时间等无关列（`drop_cols` 参数控制）
2. 对所有剩余变量做 10 箱分箱，输出原始分箱结果

**典型 drop_cols：**
```python
drop_cols = ['id_x', 'id_y', 'client_id', 'apply_id', 'pkid', 'pid',
             'product', 'serial_number', 'money', 'fact_money',
             'fact_repay_money', 'create_time_x', 'create_time_y']
```

**修改 `scripts/binning.py` 顶部参数：**

| 参数 | 说明 | 示例 |
| --- | --- | --- |
| `file_path` | 输入数据文件路径 | `'data.xlsx'` |
| `output_file` | 输出路径 | `'分箱结果.xlsx'` |
| `label` | 标签列 | `'overdue_flag2'` |
| `time_col` | 时间列 | `'create_time_x'` |
| `drop_cols` | 需删除的无关列 | 见上表 |

**运行：**
```bash
python scripts/binning.py
```

> ⚠️ 路径陷阱：binning.py 在 `scripts/` 子目录，而数据文件可能在 skill 根目录或桌面。使用绝对路径或确保 `cwd` 正确。

### 筛选用 feature-filter（一次性输出 7-Sheet 完整文件到原文件）

> 🔗 **标准用法：调用 `feature-filter` skill**，分箱完成后直接运行：
> ```
> python C:\Users\6\AppData\Local\hermes\skills\feature-filter\scripts\feature_filter.py
> ```
> 交互式输入：分箱结果路径 + 坏率阈值（如 0.60 = 60%）
>
> **输出到原文件**（覆盖写入），包含 7 个 Sheet：
> | Sheet | 内容 | 说明 |
> |-------|------|------|
> | 1-2 | "2.变量分箱" + Test分箱明细 | 原始分箱数据，蓝色数据条格式（与原始一致，32列完整格式） |
> | 3 | 候选变量汇总 | 头/尾箱高坏率变量（颜色标记 + 分类分组） |
> | 4 | 训练筛选变量 | 候选在 Train 的精简分箱明细 |
> | 5 | 测试筛选变量 | 候选在 Test 的精简分箱明细 |
> | 6 | Train月度稳定性 | 按箱×月份矩阵，蓝色 data bar |
> | 7 | Test月度稳定性 | 同上 |
>
> **关键：** 直接写入原分箱文件。`feature_filter.py` 用 xlsxwriter 重建全部7个Sheet，Sheet 1-2 用 `details_result_output`（蓝色数据条、交替灰白背景），32列格式与用户的 `binning_v2.py` 一致。不生成 `_完整.xlsx` 新文件。
>
> **特性：**
> - 已内置头尾5%与等频/卡方列名兼容（`normalize_cols` 自动识别中英文列名）
> - 不前置要求单调性
> - U 型分布自动识别并标记为无色人工判断
> - 颜色标记基于 Train 坏率（≥65%绿 / 阈值~65%橙 / <阈值红）
> - 使用 xlsxwriter 重建全部7个Sheet（不依赖 openpyxl 追加，避免格式破坏）

以下旧版脚本保留但不再推荐：

提供三种筛选模式：

#### 模式 A：准入规则筛选（头/尾箱低风险）

使用 `filter_by_head_tail.py`，筛选条件：**头箱或尾箱 bad_rate 明显低于大盘坏率**。

**核心逻辑：**
- 单调递增 → 检查头箱（低 value 端）的 bad_rate
- 单调递减 → 检查尾箱（高 value 端）的 bad_rate
- 头/尾箱 bad_rate < 大盘坏率 - `BAD_RATE_THRESHOLD` 才算通过
- 默认 `BAD_RATE_THRESHOLD=0.08`（8pp）

#### 模式 B（标准）：排除规则筛选（单调 + 高风险箱）

从分箱结果中筛选可用于**拒绝规则**的候选变量。使用 `references/筛选排除特征.py`。

**筛选条件（三个同时满足）：**
1. **大致单调**（`tolerance=2`，允许 2 次打破排序）
2. **最高箱 bad_rate > 大盘 + 8pp**（且该箱 ≥ 10 条样本）
3. **最低箱 bad_rate < 大盘 - 5pp**（说明变量有区分度）

**输出 Excel（3 个 Sheet）：**
| Sheet | 内容 | 格式 |
|-------|------|------|
| `全部分箱` | 所有变量的完整 10 箱结果 | bad_rate 列蓝色数据条 |
| `候选特征` | 3 列：特征名 / 选择原因 / 最高 bad_rate | 供人工快速排查 |
| `分箱明细` | 候选变量的完整分箱数据（21 列全字段） | 按 feature 交替灰白背景，bad_rate 列红色数据条 |

#### 模式 C（灵活）：排除规则无单调筛选

不卡单调性，只筛选**头箱或尾箱 bad_rate 较高**的变量，适合快速定位高风险客群分段。使用 `references/排除规则无单调筛选.py`。

**筛选条件（两种方式选其一）：**
1. **相对阈值**：头/尾箱 bad_rate > 大盘 + 8pp（≥ 10 条样本）
2. **绝对阈值**：头/尾箱 bad_rate > 0.7（≥ 10 条样本，大盘高时更适用）
3. **不检查单调性**

**特点：**
- 输出带 **中文翻译**（基于特征名模式匹配，可扩展 `TRANSLATION_MAP`）
- 输出带 **业务解释性说明**（变量类型 + 高风险方向 + 区分度评估）
- 3-sheet 格式：全部分箱 / 候选特征（特征名+中文释义+选择原因+最高坏率）/ 分箱明细

**参数调整：**
```python
# 绝对阈值模式（优先）
HEAD_TAIL_THRESHOLD = 0.7  # 头/尾箱坏率绝对值必须 > 0.7

# 相对阈值模式（备选）
# BAD_RATE_MARGIN = 0.08   # 头/尾箱 > 大盘 + 8pp
```

**输出 Excel 后续可配合 Test 验证**（见第四步）：Sheet2 新增 `test效果` 列，Sheet4 新增 Test 分箱明细。

#### 调整经验

| 阈值 | 结果 | 适用场景 |
|------|------|---------|
| 10pp | 严格，只出最强变量 | 大盘坏率高时 |
| 8pp | 适中 | **默认** |
| 5pp | 宽松，适合探索 | 大盘坏率低时 |

**修改参数后运行：**
```bash
python scripts/filter_by_head_tail.py
```

**输出：** `筛选变量.xlsx`（两个 Sheet）
- `筛选变量`：符合条件的变量汇总，含 head/tail bad_rate、单调方向
- `分箱明细`：完整分箱表（feature / min_bin / max_bin / bad / total / bad_rate / woe / iv / ks 等 21 列）

### 第三步：规则串联/并联评估

筛选出候选变量后，做规则串联评估（依次拒绝高风险客群）。

**关键指标定义（注意区分）：**

| 指标 | 含义 | 计算方式 |
|------|------|---------|
| `badrate` | 规则本身在全量数据上的坏率 | `规则命中者在全量中的逾期数 / 规则全量命中人数` |
| `规则命中` | 全量数据中满足该规则的总人数 | `df.query(rule).shape[0]` |
| `命中人数` | 串联到当前环节实际能命中的人数 | 前序规则已拒绝的人不再计入 |
| `坏比率(串行)` | 串联实际命中这批人的坏率 | `串联命中者的逾期数 / 串联命中人数` |
| `lift` | 串联坏率 / 大盘坏率 | 衡量规则区分能力 |
| `lift_2` | 串联坏率 / 前序环节剩余人群坏率 | 衡量在当前剩余人群中的额外区分力 |

**串联规则排序原则：** 按 highest badrate 优先排列。坏率高的规则放在前面拒绝，确保先拒掉最高风险客群。

**规则丢弃逻辑：** 当 lift < 1.10 或命中人数 < 5 时，该规则在串联中效果不足，应删除。

**报告格式（单一Sheet）：**

```
                   现数据    调整后
总单数              191      126
逾期单数            109       59
件数逾期率         57.07%   46.83%
通过率            100.00%   65.97%
金额逾期率         57.07%   46.83%

增益效果：
上线：                        总样本  lift  badrate  规则命中  命中人数  拒绝率  坏客数  坏比率(串行)  lift_2  剩余样本数
ios_score <= 463               191  1.56  88.89%     18       18    9.42%   16    88.89%    1.56    173
ios评分卡250107 <= 611         191  1.31  83.33%     30       20   10.47%   15    75.00%    1.40    153
```

**常见问题：**\n- 串联中后序规则的 `命中人数` 远小于 `规则命中` → 该规则与前序规则重叠度高，考虑调整顺序或删除\n- 多个评分卡变量串联时重叠度高，优先选区分力最强、覆盖面最大的\n- `#` 列名需替换为 `_` 后再 query\n- **count变量与flag变量在OLC数据中常为近重复**：`olc_qry_cnt_7d_phone > 0` 和 `olc_qry_hit_7d_flag_phone > 0` 在 OLC 数据中命中完全相同的客群（47人, 65.96%）。因为 count>0 和 flag=1 来自同一数据源。选变量时如果两者同时出现，提醒用户它们是同一规则的不同命名，选一个即可。\n- **用户选7个变量但串联只有3个存活很常见**：重叠规则在后序会被自动丢弃（lift=0 或 lift<1.10）。在用户提交变量列表后，建议先检查候选变量之间是否存在明显的全量命中重叠（看名称前缀是否相同、看 feature-filter 的 sheet 中命中人数是否一致），提前告知用户哪些变量会互相覆盖。\n- **串联分析呈现归一化结果**：当用户选3个低重叠变量（如 `id_cnt_call_3d`、`olc_qry_cnt_7d_phone`、`olc_qry_last_empty_flag_phone`）时，全部规则通过，lift 1.30-1.36，拒绝182人(1%)，坏率47.35%→47.20%。这适合作为「干净串联」的参考基线——覆盖小而准。

### 第四步：Test（OOT）验证（人工比对）

在 Train 上筛选出候选变量后，**脚本不做自动 Test 验证**，而是将候选变量的 Test 分箱明细直接输出到 Excel 的 Sheet3（测试筛选变量），供人工对比 Train/Test 的分箱稳定性。

**对比要点：**
1. 每个候选特征在 Test 上的同区间坏率是否与 Train 接近
2. 同一条阈值规则（如 `> X 拒绝`）在 Test 上的命中人数和坏率
3. 判定参考标准：
   - **好**：Test 坏率 > Test 大盘 × 1.15（lift ≥ 1.15）且 ≥ 3 条
   - **一般**：Test 坏率 > Test 大盘但 lift < 1.15
   - **不好**：Test 坏率 ≤ Test 大盘
   - **样本太少**：Test 命中 < 3 人（无法判断）

**注意事项：**
- OOT 样本量小（<50 条）时 test 验证的统计意义有限，结果仅供参考
- 即使 test 判定为"样本太少"，如果 train 上 lift 很高且业务逻辑合理，仍可保留

## 常见陷阱

- **用户说的"文件"可能是目录：** 先用 `os.path.isdir()` 检查。
- **标签列名可能不匹配：** 搜索含 `overdue` / `label` / `flag` 的列名验证。
- **大盘坏率 > 40% 时阈值需调整：** 默认 8pp 可能太严格。先用大盘坏率 * 0.8~1.0 作为参考。
- **大盘坏率 > 50% 时改用绝对阈值：** 大盘 57% 时用 margin 8pp 会筛掉中等变量，直接设 `HEAD_TAIL_THRESHOLD=0.7` 更干净。
- **小样本（<500 条）分箱质量差：** 统计意义弱，`MONOTONE_TOLERANCE` 放宽到 2，分箱结果仅供参考。
- **极宽数据（变量数 > 样本数）：** 运行时间与变量数成正比，889 列 × 191 行约 10 秒。
- **依赖库 `rulelift` 需提前安装：** `pip install rulelift`
- **写文件路径用绝对路径：** binning.py 在 `scripts/` 子目录，相对路径可能找不到文件。
- **列名中的 `#` 会破坏 pandas query：** `#` 被 pandas 解析器视为注释开头。用 `df.rename(columns={c: c.replace('#', '_') for c in df.columns if '#' in c})` 替换后再 query。所有引用该列名的规则也需同步替换。这条是串联分析脚本中常见的报错来源。
- **准入规则筛选必须检查单调性：** 仅头/尾箱低不够，bad_rate 序列必须单调递增或递减（`MONOTONE_TOLERANCE=2`，小样本放宽），非单调的变量不应保留。用户明确要求"有排序的才能留下"。
- **大盘坏率 > 50% 时 `BAD_RATE_THRESHOLD` 从 10pp 降到 8pp：** 大盘 57%、阈值 10pp = 头箱需 < 47%，很多中等变量刚被筛掉。先用 8pp。
- **筛选结果变量过多时加两个硬约束：** (1) `MIN_BINS=3` 排除 2 箱的假信号，(2) `MIN_OBS=10` 低风险箱至少 10 条。
- **用户说出规则后先确认方向再跑：** 匹配规则 = 拒绝(排除) 还是 匹配规则 = 保留(准入)。方向搞反会得出"逾期率不降反升"的离谱结果，此时需要反转阈值方向（`<=` ↔ `>`）。
- **设定基线时先通报大盘坏率：** 用户可能不知道当前大盘坏率，必须在问 baseline 之前先告知（如"当前大盘坏率 57.07%，你想设多少？"），否则用户无法做决策。
- **基线与阈值的关系：** 大盘 57% 时，用户说 baseline 50% 不等于直接设绝对阈值 0.5。排除规则的绝对阈值应参考分箱结果中最高箱的坏率，准入规则才参考 baseline。分清后再设参数。
- **OOT_RATIO>0 时 create_time_x 不能放在 drop_cols 中：** 该列被 drop 后又用于 OOT 切分排序 → KeyError。需先保留时间列做切分，分完 OOT 后再手动 drop（`train.drop(columns=['create_time_x'])`）。
- **串联中 `规则命中` ≠ `命中人数`：** 规则命中是全量数据中满足条件的人数（固定值），命中人数是串联到当前环节仍未被前序规则拒掉的实际人数（递减值）。两者差异大 → 与前序规则重叠度高。
- **串联中 `badrate` ≠ `坏比率(串行)`：** badrate 是用全量数据计算该规则命中者的坏率（规则本身的纯度），坏比率(串行) 是用串联剩余人群计算的实际坏率。后序规则的坏比率(串行) 会低于 badrate，因为一部分高风险者已被前序规则拒掉。
- **衍生计算字段需提前删除：** 如 `risk_over_days`（风险逾期天数）是逾期标签衍生出的计算字段，加入分箱会引入数据泄露，必须放到 `drop_cols` 中。
- **串联规则用最高坏率排序：** 坏率最高的规则放最前面，确保优先拒掉最高风险的客群，提升整体 Lift。
- **用户交互流程：** 先收集参数 → 输出 4-Sheet 分箱文件供人工判断 → 用户选定特征 → 输出最终串联报告。不要跳过人工判断环节直接出最终报告。
- **自行划分时 binning.py 的 OOT_RATIO 设为 0：** 如果用户提供了已切分的 train/test，binning.py 的 OOT_RATIO 必须为 0（不再切分），否则会再次按时间列切分导致数据错乱。test 文件单独做一次 OOT_RATIO=0 的分箱，结果用于后续验证。
- **Test 分箱必须用 Train 的边界（FIT/APPLY 模式）：** 当 AI 自动划分时，binning 脚本必须区分 FIT 和 APPLY 两阶段：
  - **FIT（Train）**：从 train 数据学习分箱边界（如 `pd.qcut` 的 quantile 边界、头尾 5% 的阈值、卡方合并的切点）
  - **APPLY（Test）**：用 train 学到的同一边界对 test 做 `pd.cut` 分箱
  - ❌ **错误做法**：对 test 独立重新分箱（如 `pd.qcut(test, n)`）—— 这会导致 train 和 test 的分箱区间不一致，无法对比坏率稳定性
  - ✅ **正确做法**：独立分箱 skill（如 `binning-quantile`）已将此模式内建，输出 Excel 包含 Train分箱明细 + Test分箱明细 两个 Sheet，边界完全一致
- **用户自行划分 train/test 时需手动保证边界一致性：** 如果用户自行划分，需先运行 train 分箱并记录边界，再将同一边界应用到 test。不推荐独立对 test 重新分箱。
- **`product` 列不存在时 `data[data['product']!='unKnow']` 会崩：** 部分数据没有 product 列，直接 `.copy()` 会 KeyError。脚本顶部需加保护：`if 'product' in data.columns: df1 = data[data['product']!='unKnow'].copy() else: df1 = data.copy()`。小特征集（2-4列）最容易触发。
- **输入文件为 .csv 时 `pd.read_excel` 会崩：** 接收文件后先检查扩展名 `.endswith('.csv')`，选对 reader（`pd.read_csv` / `pd.read_excel`）。
- **`pd.cut` 从 chi2 或自定义边界接收重复分割点时需加 `duplicates='drop'`：** 卡方合并可能出现相邻箱共享边界值，不加会报 `ValueError: Bin edges must be unique`。始终设 `pd.cut(x, bins=bin_edges, duplicates='drop')`。
- **FIT/APPLY 模式下 test 的箱数可能少于 train：** test 某些箱内样本数为 0 时 groupby 不生成该行，导致箱数不一致。属正常现象。
- **用 iloc 切分，不用 quantile：** 用户明确要求 `iloc[:int(len*ratio)]` 而非 `time_col.quantile(ratio)`。quantile 切分可能把同一天的部分样本分到 Train、部分分到 Test，造成边界混淆。
- **排序用字符串排序，不是 datetime 排序：** `sort_values('create_time_x')` 是字符串排序（按 ASCII 码），不是 `parse_time` 后的 datetime 排序。两者结果不同——字符串排序下 `"10:04"` 排在 `"5:13"` 前面（因为 `'1' < '5'`），而 datetime 排序才是真正的时间顺序。用户明确要求用字符串排序，这个排序方式和最终分箱用的数据集一致。用 datetime 排序会导致分箱边界偏移、Train/Test 的 bad count 差异（~9 例/2652）。标准流程：`data = data.sort_values('create_time_x').reset_index(drop=True)`（不加 parse_time）。
- **train.csv 是 UTF-8 编码：** 用户按 iloc 切分后保存的 `train.csv` 用 `df.to_csv('train.csv', index=False)`（默认 UTF-8），不是原始 CSV 的 GBK 编码。后续读取时用 `pd.read_csv(path)` 即可，不要用 `encoding='gbk'`。原始 `新客特征*.csv` 是 GBK 编码。
- **CSV 文件中文编码：** Metabase 导出的中文数据常为 GBK 编码，不能用 `pd.read_excel()`。用 `pd.read_csv(path, encoding='gbk')`。不确定时尝试 gbk/gb2312/gb18030/latin1。
- **Test 阶段 "loop of ufunc" 错误：** OOT 中某些特征的正常值全部为特殊值（如 -999）时，`np.log()` 报错。不影响最终筛选结果，在 Test 循环中加 `try/except` 跳过该特征即可。
- **Windows 中文路径乱码：** terminal 工具和 write_file 工具对含中文路径支持差。将脚本和输出路径改为纯英文（如 `C:\Users\6\Desktop\binning_run.py`），读取 log 用 `read_file` 或 `execute_code` 而非 terminal。
- **risk_over_days 强制删除：** 该列是逾期天数衍生字段，纳入分箱会导致严重数据泄露。用户可能不知道，必须主动建议加入 `drop_cols`。
- **稀疏特征分箱差异（旧脚本 vs 新脚本）：** 极稀疏特征（95%+ 为 -999 缺失值）在新版 `fit_bin_frequency` 中会正确分离几个正常值做 qcut，旧版误将全列视为特殊值输出 1 箱。新版正确，旧版错误。如果用户拿旧结果和新结果对比发现箱数不同，这是原因。
- **train.csv 为 UTF-8 编码：** 用户按 `iloc` 切分后保存的 `train.csv` 是 UTF-8 编码（不是原始 CSV 的 GBK）。后续读取时用 `pd.read_csv(path, encoding='utf-8')`，不要用 gbk。
- **条件划分时时间列必须是 ISO 格式字符串：** `YYYY-MM-DD` 格式的字符串比较等价于日期排序。如果时间列是其他格式（如 `MM/DD/YYYY`、`YYYY/MM/DD` 或纯数字时间戳），字符串比较不能正确排序，必须 `pd.to_datetime()` 转换后再比较。检查方法：`df[time_col].iloc[0]` 看格式。
- **条件划分无 `OOT_RATIO` 参数：** 用户指定具体日期截止点，无需计算比例。报告 Train/Test 数量和坏率时直接输出绝对值，无需提 `OOT_RATIO`。
- **不同分箱方法 KS 相同不一定说明算法等价：** 信贷数据中大量特征的区分度来自特殊值箱（-999/0/NaN）。当预处理逻辑将特殊值单独成箱时，等频、卡方、头尾5%对这些箱的处理完全一样，KS 的最大累积差异可能被特殊值箱锁定。数据显示 99/113 个特征的 max KS 来自特殊值箱而非正常值箱。此时换任何分箱方法 KS 都不会变。**诊断方法：** 检查分箱结果中 `bin_ks` 列，看 max KS 落在哪个箱子。如果来自 `特殊值(-999)` / `特殊值(0)` / `空值(NaN)` 箱，说明变量的区分度本质上是缺失信号驱动而非排序性驱动，不要误判为"分箱方法没区别"。
- **头尾5% 支持条件切分：** `binning-5percent` 的 `binning_headtail5_oot.py` 现已支持两种切分方式：
  - `cutoff_date` 参数（推荐）：指定日期截止点（如 `"2026-05-01"`），时间列需 ISO 格式
  - `OOT_RATIO` 参数（备选）：按时间 quantile 比例切分
  当 `cutoff_date` 和 `OOT_RATIO` 都设置时，`cutoff_date` 优先。
- **原始分箱文件永不动：** 分箱结果 `分箱结果_头尾5.xlsx` 只有 2 个 Sheet（Train分箱明细 + Test分箱明细），**永远不允许修改或重写**。不要用 `load_workbook` + `save()` 打开这个文件，xlsxwriter 格式被 openpyxl 重新保存后格式会变。所有后续操作（筛选、月度稳定性）必须输出到**新文件** `{原文件名}_筛选结果.xlsx`。
- **feature_filter.py 现已内置月度稳定性：** 一次运行输出 7 个 Sheet（筛选 + 月度），不需要独立脚本。原始 2-Sheet 分箱文件永不碰。
- **xlsxwriter 与 openpyxl 互不兼容：** 永远不要用 `openpyxl.load_workbook()` 打开 xlsxwriter 生成的文件再 `save()`。保存后 xlsxwriter 特有的条件格式（数据条）、列宽、合并单元格样式会丢失。改用 xlsxwriter 新建完整文件。
- **feature_filter.py 使用 xlsxwriter 输出全部 7 个 Sheet：** 读取数据用 pandas（只读），输出用 `pd.ExcelWriter(engine='xlsxwriter')` + `build_output_worksheet`。NaN/INF 值先清理（`fillna('')` + `replace([inf, -inf], '')`），否则 xlsxwriter 报 TypeError。
- **feature-filter 的 `normalize_cols` 会重命名列名：** 该函数将中文列名（`变量英文名` → `feature`、`#Bad` → `bad`、`#Obs` → `total`）转为英文内部名。写 Sheet 1-2 时必须用**原始未标准化数据**（`train_raw`/`test_raw`），否则 `required_cols_binning`（中文列名）在标准化后的数据中找不到对应列，导致 Sheet 1-2 的 `#Bad`、`变量英文名`、`变量中文名` 等列全部为空。修复已在 feature-filter v3.2.0+ 中内置：`write_xlsxwriter_output` 接收 `train_raw`/`test_raw` 参数用于 Sheet 1-2。
- **feature-filter 输出文件改为原文件（v3.2.0+）：** 不再生成 `_完整.xlsx` 新文件，而是直接用 xlsxwriter 重建原文件（覆盖写），新增 Sheet 3-7。因此原始分箱文件在 feature-filter 运行后会被覆盖，但 Sheet 1-2 数据和格式不变。
- **头尾5%代码已在 skill 内固化：** 脚本已替换为用户 `C:\\Users\\6\\Desktop\\bi\\` 的正确版本。核心函数（`important_bin_calculate` 7参数签名、`calculate_ks`）已内建在 `binning_headtail5_oot.py` 中，无需从外部同步。

