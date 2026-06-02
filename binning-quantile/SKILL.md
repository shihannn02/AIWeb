---
name: binning-quantile
description: 等频分箱（Quantile Binning）—— 用 pd.qcut 对连续变量做等频分箱，支持 OOT 切分
version: 1.0.0
platforms: [macos, linux, windows]
metadata:
  hermes:
    tags: [risk, credit, binning, python, fintech, quantile]
    category: risk-strategy
related_skills: [zhunru, binning-chisquare]
---

# 等频分箱（Quantile Binning）

## 何时使用

- 需要对数值型连续变量做**等频分箱**（每箱样本数大致相等）
- 分箱后用于后续变量筛选、规则制定
- 作为全量分箱分析的第一步

## 与 zhunru skill 的关系

`binning-quantile` 是独立分箱 skill，对应变量分箱 SOP 中的**等频分箱**方法。
完成分箱后，结果可用 zhunru 中的筛选脚本做进一步分析。

## 分箱逻辑

核心函数 `fit_bin_frequency()` + `apply_bin_frequency()` 实现等频分箱：

1. **FIT 模式（Train）**：`pd.qcut` 学习等频边界 → 输出 train 分箱结果
2. **APPLY 模式（Test）**：用 train 学到的同一边界做 `pd.cut` → 输出 test 分箱结果
3. 特殊值（`-999`, `-9999`, `-1111`）单独拆成独立箱
4. 按 min_bin 排序，特殊值箱放最后
5. 自动计算 bad_rate / woe / iv / ks / lift 等指标

> 🔑 **Train 和 Test 的分箱边界完全一致**，确保 train/test 效果可对比。

## 参数说明（修改 `scripts/binning.py` 顶部）

| 参数 | 说明 | 示例 |
|------|------|------|
| `file_path` | 输入数据文件路径 | `r'C:\Users\6\Desktop\test_file\data.xlsx'` |
| `output_file` | 输出 Excel 路径 | `r'C:\Users\6\Desktop\test_file\分箱结果_等频.xlsx'` |
| `label` | 逾期标签列 | `'overdue_flag2'` |
| `time_col` | 时间列（用于 OOT 切分） | `'create_time_x'` |
| `drop_cols` | 需删除的无关列 | 见下方模板 |
| `bin_num` | **等频箱数**（用户输入） | `10` |
| `OOT_RATIO` | 划分比例，`0`=全量不分, `0.2`=80%train/20%oot | `0.2` |
| `INCLUDE_CAT` | 保留类别变量 | `True` |

**所有过滤开关保持关闭（不要改动）：**
```python
LONGTAIL_FILTER = False
MISSING_FILTER = False
CONCENTRATION_FILTER = False
PSI_FILTER = False
IV_FILTER = False
CORR_FILTER = False
```

## 典型 drop_cols 模板

```python
drop_cols = [
    'id_x', 'id_y', 'client_id', 'apply_id', 'pkid', 'pid',
    'product', 'serial_number', 'money', 'fact_money',
    'fact_repay_money', 'create_time_x', 'create_time_y',
    'risk_over_days'  # 衍生计算字段，避免数据泄露
]
```

> ⚠️ **重要：** 如果 `OOT_RATIO > 0`，`create_time_x` **不能**放在 `drop_cols` 中，否则 OOT 切分时 KeyError。脚本会在切分完成后自动删除该列。

## 操作步骤

### 第〇步：数据预处理（如果标签含异常值）

部分数据集的标签列（如 `overdue_flag`）包含 `-1` 或 `NaN`，这些值应提前过滤：

```python
# 只保留 overdue_flag = 0 或 1 的行
data = data[data['overdue_flag'].isin([0, 1])].copy()
data['overdue_flag'] = data['overdue_flag'].astype(int)
```

> 不处理的话，大盘坏率会失真（-1 和 NaN 被当作 0 计入分母）。

### 第一步：收集参数

1. **数据文件路径**（如 `r'C:\\Users\\6\\Desktop\\test_file\\data.xlsx'`）  
   → 实际可能是 `.csv` 文件，注意扩展名
2. **CSV 编码**：Metabase 导出文件常为 GBK 编码，需 `pd.read_csv(..., encoding='gbk')`
3. **输出保存路径**（如 `r'C:\\Users\\6\\Desktop\\test_file\\分箱结果_等频.xlsx'`）
4. **等频箱数**（如 `10`）
5. **OOT 比例**（`0`=不划分，`0.2`=80%train/20%oot）
6. **需要删除的额外列**（如有，特别注意 `risk_over_days` 是衍生计算字段会引起数据泄露）

### OOT 切分规则（用户指定方法——iloc 切分）

用户要求用 **iloc 切分法**（非 quantile 分位点切分）：

```python
# 1. 先过滤标签列（排除 -1 和 NaN）
df = df[df[label].isin([0, 1])].copy()
# 2. 按时间排序——字符串排序，不是 datetime 排序
df = df.sort_values('create_time_x').reset_index(drop=True)
# 3. iloc 切分
split_idx = int(len(df) * 0.8)
train = df.iloc[:split_idx]
test  = df.iloc[split_idx:]
```

> ⚠️ **关键陷阱：字符串排序 ≠ datetime 排序**
> - `sort_values('create_time_x')` 对日期字符串按 ASCII 码排序
> - `parse_time` → `sort_values('_tp')` 按真实时间排序
> - 两者结果不同：字符串排序下 `"10:04"` 排在 `"5:13"` 前面（`'1' < '5'`），但实际时间 `5:13` 才是早上
> - 误用 datetime 排序会导致 Train/Test 切分边界偏移，bad count 差异约 9 例/2652 条
> - **用户明确要求字符串排序**，不要加 `parse_time` 转换

> ❌ quantile 切分可能把同一天的部分样本分到 Train/Test 两边
> ✅ iloc 切分：严格按位置分，无边界混淆

### 第一步：收集参数

修改 `scripts/binning.py` 顶部参数区域：
- `file_path` → 数据路径
- `output_file` → 输出路径
- `bin_num` → 用户指定的箱数
- `OOT_RATIO` → 用户指定的比例
- `drop_cols` → 按需调整
- 如果输入是 `.csv`（GBK 编码），改 `pd.read_excel()` 为 `pd.read_csv(..., encoding='gbk')`
- 如果标签列有 -1/NaN，添加 `data = data[data[label].isin([0, 1])]` 过滤

### 第三步：运行

**Windows 上避免中文路径问题：** terminal 工具对含中文的路径和输出易出乱码。推荐两种方式：

**方式 A（推荐）：** 将脚本拷贝到无中文路径下运行
```bash
cp scripts/binning.py /c/Users/6/Desktop/binning_run.py
# 修改该文件中的 file_path/output_file 为无中文路径
python /c/Users/6/Desktop/binning_run.py
```

**方式 B（直接）：** 在 execute_code 中用 subprocess 调用 hermes venv python
```python
import subprocess
python = r'C:\Users\6\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe'
result = subprocess.run([python, script_path], capture_output=True, text=True, timeout=600)
```

**方式 C（最稳）：** 在 execute_code 中用 exec() 直接跑
```python
with open(script_path, 'r', encoding='utf-8') as f:
    code = f.read()
exec(compile(code, script_path, 'exec'), {'__name__': '__main__'})
```

原始命令（当路径无中文时）：
```bash
python C:\\Users\\6\\AppData\\Local\\hermes\\skills\\binning-quantile\\scripts\\binning.py
```

### 第四步：检查输出

输出的 Excel 包含 **2 个 Sheet**：

| Sheet | 内容 | 说明 |
|-------|------|------|
| `Train分箱明细` | 所有变量在 train 上的等频分箱结果 | **等频边界从 train 学习** |
| `Test分箱明细` | 所有变量在 test 上**用同一边界**的分箱结果 | 仅当 OOT_RATIO>0 时生成 |

**关键：train 和 test 的分箱边界完全一致。** 边界从 train 的 `pd.qcut` 学得，test 用 `pd.cut` + 同一边界，保证可对比。

Train/Test 共有格式：
- bad_rate 列蓝色数据条
- 按 feature 交替灰白背景
- 每行包含：min_bin / max_bin / bin_label / bad / acu_badnum / total / acu_allnum / bad_rate / cum_bad_rate / acu_badrate / badattr / woe / iv / ks / lift

### 第五步：设定准入坏率基线（筛选候选变量前）

分箱结果出来后，准备做变量筛选前，**先设定准入坏率基线**。

**流程：**

1. 确认大盘坏率（分箱运行时会输出，如 `大盘坏率: 57.07%`）
2. 问用户：**"大盘坏率 X%，你想把准入的坏率 baseline 设成多少？"**
3. 用户指定目标坏率（如 `50%`、`40%` 或直接设 `大盘 - 15pp` 等）
4. 这个基线将用于后续筛选脚本的参数：

| 筛选模式 | 参数 | 示例 |
|---------|------|------|
| 相对阈值（排除规则） | `BAD_RATE_MARGIN` = baseline - 大盘 | 大盘57%, 基线50%, 则 margin = 7pp |
| 绝对阈值（排除规则） | `HEAD_TAIL_THRESHOLD` = 用户指定的值 | 用户说"0.7"则设0.7 |
| 准入规则筛选 | `BAD_RATE_THRESHOLD` = 大盘 - 基线 | 大盘57%, 基线50%, 则要求头箱 < 50% |

> 基线影响筛选的松紧度：基线设得越低，筛选越严格，只留下区分度最强的变量。

---

**后续步骤：** 基线设定后，结合 zhunru skill 的筛选脚本（如 `filter_by_head_tail.py`、`筛选排除特征.py`）进行候选变量筛选。

## 已知陷阱

- **大盘坏率 > 50% 时**：等频分箱的某几箱坏率可能接近 100%，这是数据分布导致，不影响后续筛选
- **小样本（<500 条）**：`pd.qcut` 可能报 `duplicates='drop'` 后箱数不足 10 箱，属正常现象
- **极宽数据（变量数 > 样本数）**：运行时间与变量数成正比，3,060 列 × 3,315 行约 30 秒
- **依赖库**：需安装 `pandas`, `numpy`, `openpyxl` / `xlsxwriter`, `tqdm`, `rulelift`
- **product 列不存在时需加保护**：`data[data['product']!='unKnow'].copy()` KeyError。脚本已加 `if 'product' in data.columns` 保护。
- **CSV 编码**：中文数据常为 GBK 编码，`pd.read_csv(path, encoding='gbk')`。遇到 `UnicodeDecodeError` 时尝试 gbk/gb2312/gb18030/latin1。
- **标签列有异常值**：`overdue_flag` 常见值 0（好）/1（坏）/-1（屏蔽）/NaN（未到观察期）。分箱前必须 `data[data[label].isin([0, 1])]`，否则大盘坏率失真。
- **risk_over_days 是衍生字段**：该列直接从逾期天数计算得出，纳入分箱会导致严重的数据泄露。务必加入 `drop_cols`。
- **Train 第一箱 `bin_label` 显示具体下界，Test 第一箱显示 `(-inf`**：`fit_bin_frequency` 从 IntervalIndex 提取边界时，第一个 left 被替换为 `-np.inf` 以保证 test 上低于 train 最小值的样本落入第一箱。这是预期行为——**右边界一致**才是关键，第一箱的左边界扩展不影响对比。
- **`qcut_result.categories` 报错**：`pd.qcut` 返回 Series（cat 类型），不是直接返回 IntervalIndex。取类别用 `qcut_result.cat.categories`，不是 `qcut_result.categories`。
- **Test 阶段 "loop of ufunc does not support argument 0 of type float"**：当 Test（OOT）中某些特征的正常值全部为特殊值（如 -999）时，`np.log()` 会报错。解法：在 Test 循环中加 `try/except` 跳过这些特征，它们对筛选无贡献。
- **Excel Sheet 名不能用转义序列**：`write_sheet('Train\\u5206\\u7bb1\\u660e\\u7ec6', ...)` 中的 `\\u` 在 Python 字符串中被当作字面字符而非 unicode 转义。必须用实际中文：`'Train分箱明细'`。否则 xlsxwriter 报 `Invalid character in sheetname`。
- **write_file 不支持中文路径**：Windows 上 `write_file` 对含中文路径输出乱码。必须用 `execute_code` + Python `open()`。
- **Windows 终端中文乱码**：`terminal` 工具对中文路径/输出显示为乱码。脚本/输出路径尽量用英文。读 log 用 `read_file` 或 `execute_code`。
- **`pd.cut` 需加 `duplicates='drop'`**：等频分箱边界可能有重复值，不加报 `ValueError: Bin edges must be unique`。
- **字符串排序陷阱（⚠️ 高频错误）：** `sort_values('create_time_x')` 是字符串排序，`parse_time` → `sort_values('_tp')` 是 datetime 排序。两者结果不同——字符串把 `"10:04"` 排在 `"5:13"` 前。误用 datetime 排序会导致 Train/Test 的 bad count 偏差约 9/2652 = 0.3pp。用户要求字符串排序，不加 `parse_time` 转换。
- **train.csv 是 UTF-8：** 用户 iloc 切分后 `df.to_csv('train.csv')` 默认 UTF-8 编码，不是原始 CSⅤ 的 GBK。读取时 `pd.read_csv('train.csv')` 即可，不需要 `encoding='gbk'`。
