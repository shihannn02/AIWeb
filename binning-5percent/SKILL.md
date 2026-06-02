---
name: binning-5percent
description: 头尾5%分箱（Head-Tail 5% Binning）—— 首尾精细分箱，识别极端风险人群。完全复用 binning_v2 原始代码，不修改任何分箱逻辑，不暴露任何用户参数。
version: 1.5.0
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [risk, credit, binning, python, fintech, headtail]
    category: risk-strategy
---

# 头尾5%分箱（Head-Tail 5% Binning）

## 何时使用

- 需要识别变量两端极端风险人群（极低分 / 极高分的客户）
- 头尾精细分箱（5%粒度），中间粗略分箱
- 输出格式与 binning_v2 保持一致（~25列，每个变量约40-60箱）
- 作为全量分箱分析的第一步

## 核心原则

> **不要修改任何分箱逻辑，不要暴露任何用户参数。** 箱数、头尾比例、分箱方法全部按 binning_v2 原始代码固定。用户能改的只有：输入文件路径、输出文件路径、标签列名。

## 分箱逻辑

完全复用 binning_v2 的 `get_bin_lift()` 算法，未做任何修改：

1. **头部精细分箱**：变量最小值端（低值区），按 5% 样本比例逐段切分
2. **尾部精细分箱**：变量最大值端（高值区），按 5% 样本比例逐段切分
3. **中间粗略分箱**：头尾之外的中间区域，自动合并为较少的箱
4. **缺失值处理**：`-999` 和 NaN 统一替换为 NaN，`get_bin_lift` 内部自动处理为独立箱
5. **Train/Test**：**现支持 OOT 切分 + FIT/APPLY 模式**。FIT 阶段记录每个变量的 knot 索引位置（`get_bin_lift_with_edges`），APPLY 阶段对 test 数据用 `group_by_var_value` 重新计算 k 表后，使用相同的 knot 索引调用 `important_bin_calculate`。详见下方 FIT/APPLY 原理说明。
6. **输出**：仅保留原始 4 个 Sheet 中的 `2.变量分箱`（Sheet 3）

## 与其他分箱方法的区别

| 方法 | 箱数 | 特点 |
|-----------|------|------|
| 等频 | 10箱 | 每箱样本数相等，粒度均匀 |
| 卡方 | 10箱 | 合并 bad_rate 相似的相邻箱，箱间差异最大化 |
| 头尾5% | 默认40-60箱（可调） | 头尾每 min_rate 一段，中间合并，极端值清晰，箱数可通过参数调节 |

## 箱数控制参数

头尾5%算法通过 `get_bin_lift_with_edges` 的参数控制分段数量。用户可能有不同粒度需求（如"头尾各5箱"、"头尾各3箱"等）。修改 OOT 脚本或自定义脚本中的参数：

| 参数 | 默认值 | 作用 | 调低 → | 调高 → |
|------|:------:|------|---------|---------|
| `min_rate` | 0.01 | 每段最小样本占比（全量） | 更多更细的分段 | 更少更粗的分段 |
| `sub_div_bin` | 0.1 | 头尾划段的粒度目标值 | 更少的头尾段 | 更多的头尾段 |
| `min_num` | 20 | 每段最小样本数（绝对量） | 允许更小的段 | 强制更大的段 |
| `max_bins` | 50 | 最大总箱数上限 | 直接削减尾部的箱 | 允许更多箱 |

**核心公式：** `end_cnt = int(sub_div_bin / min_rate_act)`，其中 `min_rate_act = max(min_num/total, min_rate)`。`end_cnt` 即头部和尾部分别产生的段数。

**常见配置：**

| 目标 | `min_rate` | `sub_div_bin` | 效果 |
|------|:----------:|:-------------:|------|
| 头尾各5箱（共~10-12箱） | 0.02 | 0.1 | `end_cnt=5` |
| 头尾各10箱（默认） | 0.01 | 0.1 | `end_cnt=10` |
| 头尾各3箱（共~7-8箱） | 0.03 | 0.1 | `end_cnt=3` |
| 头尾各2箱（共~5-6箱） | 0.05 | 0.1 | `end_cnt=2` |

> 注意：`end_cnt` 是头/尾各自的分段数。总箱数 ≈ `end_cnt*2 + 中间合并段数 + 缺失值箱`（约 1-3 箱），中间区域自动合并，总箱数不精确等于 `end_cnt*2 + 1`。

**修改位置（OOT 脚本）：**
```python
# 在 references/binning_headtail5_oot.py 顶部参数区修改：
target_min_rate = {'dpd_7': [0.02]}  # min_rate
sub_div_bin = 0.1                     # sub_div_bin（已有此行）
min_num = 20                          # 或增大到 50 使分段更集中
```

**警告：** 非特殊情况不建议修改这些参数。头尾5%的默认参数（头尾各10段 = ~40-60箱）已针对极端风险识别优化。减少箱数会降低头尾粒度，削弱识别能力。

## 重要约束

> **官方代码来源：** `C:\\Users\\6\\Desktop\\bi\\` 是头尾5%分箱的唯一正确版本。`binning_headtail5_oot.py` 支持两种切分方式：
> - **OOT_RATIO 比例切分**（默认）：按时间 quantile 切分
> - **cutoff_date 条件切分**：按指定日期截止点切分（如 `apply_date < "2026-05-01"`），时间列需为 ISO 格式
> 
> Sheet名为 `Train分箱明细` + `Test分箱明细`，含 `金额逾期率` 列。

## 文件结构

```
scripts/
├── binning.py                # 主脚本（原始 binning_v2 代码，仅改文件路径 + 只输出 Sheet3，无 OOT）
├── _binning_v2_core.py       # 核心函数模块（提取自 binning_v2，仅函数定义无执行代码）
└── binning_v2_original.py    # 原始 binning_v2 代码（未修改备份）
references/
└── binning_headtail5_oot.py  # 带 FIT/APPLY 和 OOT 切分的增强版脚本
```

## 操作步骤

### 第一步：修改参数（无 OOT 版本）

编辑 `scripts/binning.py`，只需要改前几行：

```python
file_path = r'C:\\Users\\6\\Desktop\\新准入规则\\诗涵建模样本.csv'    # 输入数据
output_file = r'C:\\Users\\6\\Desktop\\新准入规则\\分箱结果_头尾5.xlsx'  # 输出路径
label = 'fpd7'                     # 标签列
```

> ⚠️ 只有这 3 项可以改。不要修改分箱逻辑、头尾比例、箱数等任何其他参数。

### 第一步（OOT版本）：使用带 FIT/APPLY 的脚本

当需要 OOT 切分时，使用 `references/binning_headtail5_oot.py`：

**切分方式二选一：**

```python
# 选项1：条件切分 — 按指定日期截止点（如 apply_date < "2026-05-01" 为 Train）
cutoff_date = "2026-05-01"  # ISO 格式 YYYY-MM-DD，设为 None 则用 OOT_RATIO

# 选项2：比例切分 — 按时间 quantile 切分
OOT_RATIO = 0.2  # 0 = no split, 0.2 = 80% train / 20% test (仅 cutoff_date=None 时生效)
```

完整参数示例（条件切分）：
```python
file_path = r'C:\Users\6\Desktop\...\数据.csv'
output_file = r'C:\Users\6\Desktop\...\分箱结果_头尾5.xlsx'
label = 'target3'
time_col = 'apply_date'
cutoff_date = "2026-05-01"  # apply_date < "2026-05-01" = Train
OOT_RATIO = 0.2              # 被 cutoff_date 覆盖，自动失效
drop_cols = ['client_id', 'apply_id', 'money', ...]
```

完整参数示例（比例切分）：
```python
file_path = r'C:\Users\6\Desktop\...\数据.csv'
output_file = r'C:\Users\6\Desktop\...\分箱结果_头尾5.xlsx'
label = 'overdue_flag2'
time_col = 'create_time_x'
cutoff_date = None           # 关闭条件切分，使用 OOT_RATIO
OOT_RATIO = 0.2              # 0.2 = 80% train / 20% test
drop_cols = [...]

运行：
```bash
python C:\\Users\\6\\AppData\\Local\\hermes\\skills\\binning-5percent\\references\\binning_headtail5_oot.py
```

输出 2 个 Sheet：
- `Train分箱明细` — 所有变量的头尾5%分箱结果（同原格式）
- `Test分箱明细` — 用 train 同一边界对 test 的分箱结果（同原格式）

### 第二步：检查输出

无 OOT 时，输出 1 个 Sheet：
| Sheet | 列数 | 格式 |
|-------|------|------|
| `2.变量分箱` | 25列 | binning_v2 原始格式，%Bad_Rate 粉色数据条 |

使用 OOT 时，输出 2 个 Sheet：
| Sheet | 列数 | 格式 |
|-------|------|------|
| `Train分箱明细` | 25列 | binning_v2 原始格式，%Bad_Rate 粉色数据条 |
| `Test分箱明细` | 25列 | 同格式，适合对比 train/test 稳定性 |

## FIT/APPLY 原理说明

头尾5%分箱原始代码用 `get_bin_lift()` 内部计算分箱点（knot 索引），不暴露切割边界。因此标准 FIT/APPLY 无法直接复用。本 skill 的 OOT 脚本采用以下替代方案：

1. **FIT（Train）**：使用 `get_bin_lift_with_edges()` 替代原 `get_bin_lift()`，额外返回：
   - `k`：按变量值排序后的逐值统计表（`group_by_var_value` 输出）
   - `knots`：分箱在 k 中的索引位置列表
2. **APPLY（Test）**：对 test 数据先调用 `group_by_var_value()` 得到 `k_test`，再用 train 的 `knots` 索引调用 `important_bin_calculate(k_test, k1_test, ..., [0] + knots + [len(k_test)-1])`
3. **效果相同**：因 `important_bin_calculate` 对 k 表按 knots 索引切分，索引位置在 train 上对应的是特定的变量取值边界，在 test 上相同的索引位置意味着相似的累计分布分段。

**限制：** test 的 knot 索引位置与 train 完全相同，而非等频/等距。这意味着 test 上各箱的样本占比会与 train 有差异（这是 head-tail 5% 的固有效果）。

核心列：`Bin`, `#Obs`, `%Obs`, `#Good`, `%Good`, `#Bad`, `%Bad`, `%Bad_Rate`, `WOE`, `IV(bin)`, `IV(total)`, `Lift`, `bin_ks`, `total_ks`

## 已知陷阱

- **不要删除 `_binning_v2_core.py` 和 `binning_v2_original.py`**：主脚本运行依赖它们。
- **参数调整箱数**：默认每5%一段产生40-60箱。如需调整头尾段数（如"头尾各5箱"），修改 `min_rate` 和 `sub_div_bin` 参数控制 `end_cnt`（见上方箱数控制参数表）。不要在核心分箱逻辑函数内部改算法。
- **CSV 自动识别**：脚本根据 `.csv` 扩展名自动选择 `pd.read_csv()` vs `pd.read_excel()`。
- **product 列保护**：部分数据没有 `product` 列，脚本自动检测并跳过过滤。
- **money 列缺失**：原始代码依赖 `money` 列计算金额逾期率。如果数据没有该列，脚本自动补充默认值 1000。
- **FIT/APPLY 时 knot 索引越界**：test 数据量小于 train 时，train 的 knot 索引可能超出 `len(k_test)-1`。OOT 脚本中已含 clamp 保护：`clamped_knot = [k for k in knot if k < max_idx]`。当所有索引都越界时，回退到 2 箱（取中间点）。迁移到其他脚本时需复制此保护。
- **`np.nan` 与 `==` 比较始终为 False**：`np.nan == np.nan` 返回 `False`。判断一个值是否为 NaN 必须用 `pd.isna(x)` 或 `np.isnan(x)`。在映射 `cols_map` 时，NaN 列（如 `Odds1`/`Odds2`）需用 `isinstance(src, float) and np.isnan(src)` 判断，或直接检查 `isinstance(src, str) and src in d4.columns`。
- **大规模数据**：头尾5%分箱在 1 万个样本 + 875 变量上约 1-2 秒，效率高于等频和卡方。
- **输出 Sheet 名**：无 OOT 时固定为 `2.变量分箱`；使用 OOT 脚本时固定为 `Train分箱明细` + `Test分箱明细`。
- **`col_seq` 与 `row_data` 维度不匹配（`binning_headtail5_oot.py`）**：`col_seq` 定义 42 列（15 固定 + 26 个 d{i} + 标签1），但 `row_data` 只有 40 项（14 固定 + 25 个 NaN + Y）。运行时报 `ValueError: 42 columns passed, passed data had 40 columns`。修复：将 `range(26)` 改为 `range(24)` 使 col_seq 变为 40 列（15+24+1），与 row_data 的 14+25+1=40 匹配。已在最新版脚本中修复。
- **条件切分支持**：`cutoff_date` 参数支持 ISO 格式 (YYYY-MM-DD) 字符串比较切分。如果时间列为其他格式（如 MM/DD/YYYY），脚本不会自动转换，会按字符串比较可能出错。此时应先用 `pd.to_datetime()` 预处理数据再使用条件切分。
