from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import types
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from tqdm import tqdm as _tqdm

from config import DEFAULT_DROP_COLS, BINNING_SUBPROCESS_TIMEOUT_SEC, OUTPUT_DIR, SKILL_PATHS
from services.data_service import (
    _coerce_time_series,
    _normalize_binary_label,
    _parse_cutoff_timestamp,
    read_dataframe,
)

# ProcessPoolExecutor 子进程共享状态（仅用于分箱 worker）
_MP_STATE: Dict[str, Any] = {}


def _iter_binning_progress(iterable, desc: str = ""):
    """Web 后台分箱禁用 tqdm 进度条，避免 Windows 下 stderr 刷 \r 触发 OSError(22)。"""
    return _tqdm(iterable, desc=desc, disable=True)


def _binning_worker_count(feature_count: int) -> int:
    """并行 worker 数：默认 CPU-1，上限 8，可通过 BINNING_WORKERS 覆盖（0=自动）。"""
    cpus = os.cpu_count() or 4
    default = max(1, min(max(cpus - 1, 1), 8, feature_count))
    env = os.getenv("BINNING_WORKERS", "").strip()
    if not env:
        return default
    try:
        n = int(env)
    except ValueError:
        return default
    if n == 0:
        return default
    if n < 0:
        return 1
    return min(n, feature_count)


def _mp_init_pool(
    script_path: str,
    method: str,
    label: str,
    y_values: Any,
    bin_num: int,
    init_bin_num: int,
) -> None:
    global _MP_STATE
    mod = _load_module(Path(script_path), f"binning_{method}_mp_{os.getpid()}")
    _MP_STATE = {
        "mod": mod,
        "method": method,
        "label": label,
        "y": y_values,
        "bin_num": bin_num,
        "init_bin_num": init_bin_num,
    }


def _mp_fit_column(task: Tuple[str, Any, bool]) -> Tuple[str, pd.DataFrame, Optional[list]]:
    col, x_values, is_categorical = task
    mod = _MP_STATE["mod"]
    method = _MP_STATE["method"]
    label = _MP_STATE["label"]
    y = pd.Series(_MP_STATE["y"], copy=False)
    bin_num = _MP_STATE["bin_num"]
    init_bin_num = _MP_STATE["init_bin_num"]

    if is_categorical:
        df = pd.DataFrame({col: x_values, label: y.values})
        d5 = mod.calculate_iv_cate(col, df, label)
        d5.insert(0, "feature", col)
        return col, d5, None

    x = pd.to_numeric(pd.Series(x_values, copy=False), errors="coerce")
    if method == "chisquare":
        edges, _ = mod.fit_chisquare_bins(x, y, bin_num, init_bin_num)
        d5 = mod.apply_bin_frequency(x, y, bin_edges=edges)
    else:
        d5, edges = mod.fit_bin_frequency(x, y, bin_num)
    d5.insert(0, "feature", col)
    return col, d5, edges


def _mp_apply_column(task: Tuple[str, Any, bool, Optional[list]]) -> Tuple[str, pd.DataFrame]:
    col, x_values, is_categorical, edges = task
    mod = _MP_STATE["mod"]
    label = _MP_STATE["label"]
    bin_num = _MP_STATE["bin_num"]
    y = pd.Series(_MP_STATE["y"], copy=False)

    if is_categorical:
        df = pd.DataFrame({col: x_values, label: y.values})
        d5_test = mod.calculate_iv_cate(col, df, label)
    else:
        x = pd.to_numeric(pd.Series(x_values, copy=False), errors="coerce")
        if edges is not None:
            d5_test = mod.apply_bin_frequency(x, y, bin_edges=edges)
        else:
            d5_test = mod.bin_frequency(x, y, bin_num)
    d5_test.insert(0, "feature", col)
    return col, d5_test


def _parallel_binning_map(
    func,
    tasks: List[Any],
    script_path: Path,
    method: str,
    label: str,
    y_values: Any,
    bin_num: int,
    init_bin_num: int,
) -> List[Any]:
    """按 remain_cols 顺序并行执行分箱任务；变量过少时退回单线程。"""
    workers = _binning_worker_count(len(tasks))
    if workers <= 1 or len(tasks) <= 1:
        _mp_init_pool(str(script_path), method, label, y_values, bin_num, init_bin_num)
        return [func(task) for task in tasks]

    chunksize = max(1, len(tasks) // (workers * 4))
    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_mp_init_pool,
        initargs=(str(script_path), method, label, y_values, bin_num, init_bin_num),
    ) as executor:
        return list(executor.map(func, tasks, chunksize=chunksize))


def _re_sub_literal(pattern: str, repl: str, content: str, **kwargs) -> str:
    """re.sub with a literal replacement (Windows paths contain \\A, \\b, etc.)."""
    return re.sub(pattern, lambda m: repl, content, **kwargs)


def _indent_script_block(code: str, indent: str = "    ") -> str:
    """为注入 _ht5_main() 的脚本块补齐缩进。"""
    return "".join(indent + line if line.strip() else line for line in code.splitlines(keepends=True))


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class BinningJob:
    job_id: str
    status: JobStatus = JobStatus.PENDING
    message: str = "等待执行"
    output_path: Optional[Path] = None
    summary: Dict[str, Any] = field(default_factory=dict)
    progress: float = 0.0
    run_params: Dict[str, Any] = field(default_factory=dict)


_jobs: Dict[str, BinningJob] = {}
JOBS_DIR = OUTPUT_DIR / "jobs"
JOBS_DIR.mkdir(exist_ok=True)


def _persist_job(job: BinningJob) -> None:
    meta = {
        "job_id": job.job_id,
        "status": job.status.value,
        "message": job.message,
        "output_path": str(job.output_path) if job.output_path else None,
        "summary": job.summary,
        "run_params": job.run_params,
        "progress": job.progress,
    }
    (JOBS_DIR / f"{job.job_id}.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )


def _restore_job(job_id: str) -> Optional[BinningJob]:
    meta_path = JOBS_DIR / f"{job_id}.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    out = meta.get("output_path")
    job = BinningJob(
        job_id=meta["job_id"],
        status=JobStatus(meta.get("status", "completed")),
        message=meta.get("message", ""),
        output_path=Path(out) if out else None,
        summary=meta.get("summary") or {},
        progress=float(meta.get("progress", 100)),
        run_params=meta.get("run_params") or {},
    )
    _jobs[job_id] = job
    return job


def get_job(job_id: str) -> Optional[BinningJob]:
    job = _jobs.get(job_id)
    if job:
        return job
    return _restore_job(job_id)


def _load_module(script_path: Path, module_name: str) -> types.ModuleType:
    content = script_path.read_text(encoding="utf-8")
    # Strip top-level data loading that runs on import
    content = re.sub(
        r"file_path = .*?\noutput_file = .*?\n+"
        r"data = pd\.read_(?:excel|csv)\([^\)]*\)\n"
        r"(?:df1\s*=\s*data\.copy\(\)\n"
        r"if 'product' in data\.columns:\n"
        r"    df1 = data\[data\['product'\] != 'unKnow'\]\.copy\(\)\n|"
        r"df1\s*=\s*data\[.*?\]\.copy\(\)\n)"
        r"print\(f'='\*50\)\n"
        r"print\(f'数据大小：\{df1\.shape\}'\)\n",
        "file_path = ''\noutput_file = ''\n",
        content,
        count=1,
        flags=re.DOTALL,
    )
    content = re.sub(
        r"df1 = data\.drop\(columns=\[c for c in drop_cols if c in data\.columns\], errors='ignore'\)[^\n]*\n",
        "",
        content,
        count=1,
    )
    content = re.sub(
        r"df1 = df1\.drop\(columns=\[c for c in drop_cols if c in df1\.columns\], errors='ignore'\)[^\n]*\n",
        "",
        content,
        count=1,
    )

    module = types.ModuleType(module_name)
    module.__file__ = str(script_path)
    sys.modules[module_name] = module

    # Stub optional imports only when packages are missing (do not mask real sklearn)
    try:
        import sklearn.linear_model  # noqa: F401
        import sklearn.tree  # noqa: F401
    except ImportError:
        sklearn_mod = types.ModuleType("sklearn")

        sklearn_model = types.ModuleType("sklearn.model_selection")
        sklearn_model.train_test_split = lambda *args, **kwargs: (args[0], args[1]) if args else (None, None)
        sklearn_mod.model_selection = sklearn_model

        sklearn_linear = types.ModuleType("sklearn.linear_model")
        sklearn_linear.LogisticRegression = object
        sklearn_mod.linear_model = sklearn_linear

        sklearn_tree = types.ModuleType("sklearn.tree")
        sklearn_tree.DecisionTreeClassifier = object
        sklearn_mod.tree = sklearn_tree

        sklearn_metrics = types.ModuleType("sklearn.metrics")
        sklearn_metrics.roc_curve = lambda *args, **kwargs: ([], [], [])
        sklearn_metrics.roc_auc_score = lambda *args, **kwargs: 0.5
        sklearn_mod.metrics = sklearn_metrics

        sklearn_decomp = types.ModuleType("sklearn.decomposition")
        sklearn_decomp.PCA = object
        sklearn_mod.decomposition = sklearn_decomp

        sys.modules["sklearn"] = sklearn_mod
        sys.modules["sklearn.model_selection"] = sklearn_model
        sys.modules["sklearn.linear_model"] = sklearn_linear
        sys.modules["sklearn.tree"] = sklearn_tree
        sys.modules["sklearn.metrics"] = sklearn_metrics
        sys.modules["sklearn.decomposition"] = sklearn_decomp

    try:
        import rulelift  # noqa: F401
    except ImportError:
        rulelift_mod = types.ModuleType("rulelift")
        rulelift_mod.VariableAnalyzer = object
        rulelift_mod.load_example_data = lambda: None
        sys.modules["rulelift"] = rulelift_mod

    try:
        import matplotlib.pyplot  # noqa: F401
    except ImportError:
        matplotlib_mod = types.ModuleType("matplotlib")
        pyplot_mod = types.ModuleType("matplotlib.pyplot")
        pyplot_mod.show = lambda: None
        matplotlib_mod.pyplot = pyplot_mod
        sys.modules["matplotlib"] = matplotlib_mod
        sys.modules["matplotlib.pyplot"] = pyplot_mod

    exec(compile(content, str(script_path), "exec"), module.__dict__)
    return module


def _resolve_drop_cols(
    drop_cols: Optional[List[str]],
    label: str,
    time_col: str,
    split_mode: str,
) -> List[str]:
    """合并用户勾选与默认丢弃列，并保护标签列/切分时间列。"""
    selected = list(drop_cols) if drop_cols is not None else list(DEFAULT_DROP_COLS)
    # 去重保序
    seen = set()
    merged: List[str] = []
    for col in selected:
        if col not in seen:
            seen.add(col)
            merged.append(col)

    protected = {label}
    if split_mode in ("ai", "cutoff") and time_col:
        protected.add(time_col)

    return [c for c in merged if c not in protected]


def _prepare_dataframe(
    file_path: Path,
    label: str,
    drop_cols: List[str],
    product_filter: bool = True,
) -> pd.DataFrame:
    data = read_dataframe(file_path)
    if product_filter and "product" in data.columns:
        data = data[data["product"] != "unKnow"].copy()
    normalized_label = _normalize_binary_label(data[label])
    data = data[normalized_label.isin([0, 1])].copy()
    data[label] = normalized_label.loc[data.index].astype(int)
    data = data.drop(columns=[c for c in drop_cols if c in data.columns], errors="ignore")
    return data


def _split_train_test(
    df: pd.DataFrame,
    label: str,
    time_col: str,
    split_mode: str,
    oot_ratio: float,
    cutoff_date: Optional[str],
) -> Tuple[pd.DataFrame, pd.DataFrame, bool]:
    if split_mode == "manual":
        return df.copy(), df.iloc[0:0].copy(), False

    if time_col not in df.columns:
        raise ValueError(f"时间列 '{time_col}' 不存在，无法切分 Train/Test")

    if split_mode == "cutoff":
        if not cutoff_date:
            raise ValueError("条件划分需要指定 cutoff_date")
        times = _coerce_time_series(df[time_col])
        cutoff = _parse_cutoff_timestamp(cutoff_date)
        if times.isna().all():
            raise ValueError(f"时间列 '{time_col}' 无法解析为日期，请检查格式")
        train = df[times < cutoff].copy()
        test = df[times >= cutoff].copy()
        return train, test, len(test) > 0

    # AI auto split: 按解析后的时间排序再切 OOT
    if oot_ratio <= 0 or oot_ratio >= 1:
        return df.copy(), df.iloc[0:0].copy(), False

    sort_key = _coerce_time_series(df[time_col])
    sorted_df = df.assign(__sort_time=sort_key).sort_values("__sort_time").drop(
        columns="__sort_time"
    ).reset_index(drop=True)
    split_idx = int(len(sorted_df) * (1 - oot_ratio))
    train = sorted_df.iloc[:split_idx].copy()
    test = sorted_df.iloc[split_idx:].copy()
    return train, test, len(test) > 0


def _run_quantile_or_chisquare(
    method: str,
    train_ori: pd.DataFrame,
    oot_ori: pd.DataFrame,
    has_oot: bool,
    label: str,
    time_col: str,
    bin_num: int,
    init_bin_num: int,
    output_file: Path,
) -> Dict[str, Any]:
    script_path = SKILL_PATHS[method]
    mod = _load_module(script_path, f"binning_{method}")

    mod.LONGTAIL_FILTER = False
    mod.MISSING_FILTER = False
    mod.CONCENTRATION_FILTER = False
    mod.PSI_FILTER = False
    mod.IV_FILTER = False
    mod.CORR_FILTER = False
    mod.INCLUDE_CAT = True
    mod.OOT_RATIO = 0 if has_oot else 0

    cat_bounds = mod.fit_preprocess(train_ori)
    train = mod.transform_preprocess(train_ori, cat_bounds)
    oot = mod.transform_preprocess(oot_ori, cat_bounds)

    for col in (time_col, "create_time_x", "create_time_y"):
        if col in train.columns:
            train = train.drop(columns=[col])
        if col in oot.columns:
            oot = oot.drop(columns=[col])

    remain_cols = [c for c in train.columns if c != label]

    all_train_d5: List[pd.DataFrame] = []
    all_test_d5: List[pd.DataFrame] = []
    all_bin_edges: Dict[str, list] = {}

    fit_tasks = [
        (col, train[col].values, train[col].dtype in ("category", "object"))
        for col in remain_cols
    ]
    fit_results = _parallel_binning_map(
        _mp_fit_column,
        fit_tasks,
        script_path,
        method,
        label,
        train[label].values,
        bin_num,
        init_bin_num,
    )
    for col, d5, edges in fit_results:
        if edges is not None:
            all_bin_edges[col] = edges
        all_train_d5.append(d5)

    if has_oot:
        apply_tasks = [
            (col, oot[col].values, oot[col].dtype in ("category", "object"), all_bin_edges.get(col))
            for col in remain_cols
        ]
        apply_results = _parallel_binning_map(
            _mp_apply_column,
            apply_tasks,
            script_path,
            method,
            label,
            oot[label].values,
            bin_num,
            init_bin_num,
        )
        for _col, d5_test in apply_results:
            all_test_d5.append(d5_test)

    train_df = pd.concat(all_train_d5, ignore_index=True)
    test_df = pd.concat(all_test_d5, ignore_index=True) if all_test_d5 else pd.DataFrame()

    if has_oot and len(test_df) > 0 and len(oot_ori) > 0:
        from services.feature_review_service import rebuild_test_binning_sheet

        te = oot_ori.copy()
        te["overdue_flag"] = _normalize_binary_label(te[label]).astype(int)
        test_df = rebuild_test_binning_sheet(train_df, te)

    from services.binning_excel_format import write_formatted_binning_workbook

    write_formatted_binning_workbook(
        output_file,
        train_df,
        test_df if len(test_df) > 0 else None,
    )

    return {
        "method": method,
        "feature_count": len(remain_cols),
        "train_rows": len(train_df),
        "test_rows": len(test_df),
        "train_samples": len(train_ori),
        "test_samples": len(oot_ori) if has_oot else 0,
        "sheets": ["Train分箱明细"] + (["Test分箱明细"] if len(test_df) > 0 else []),
        "test_aligned": bool(has_oot and len(test_df) > 0),
    }


def _normalize_label_in_script(label: str) -> str:
    return f"""
def _normalize_label_series(series):
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors='coerce')
    cleaned = series.astype(str).str.strip()
    mapping = {{'0': 0, '1': 1, '0.0': 0, '1.0': 1, 'True': 1, 'False': 0, 'true': 1, 'false': 0}}
    return cleaned.map(mapping)

data[label] = _normalize_label_series(data[label])
data = data[data[label].isin([0, 1])].copy()
data[label] = data[label].astype(int)
if len(data) == 0:
    raise ValueError(f'标签列 {{label}} 无有效 0/1 样本，请检查标签列或数据格式')
"""


def _run_headtail5(
    file_path: Path,
    output_file: Path,
    label: str,
    time_col: str,
    oot_ratio: float,
    split_mode: str,
    cutoff_date: Optional[str],
    drop_cols: List[str],
    test_file_path: Optional[Path] = None,
) -> Dict[str, Any]:
    script_path = SKILL_PATHS["headtail5"]
    content = script_path.read_text(encoding="utf-8")

    file_path = file_path.resolve()
    output_file = output_file.resolve()
    # zhunru SOP：原始标签列须进 drop_cols，避免作为变量参与分箱（数据泄漏）
    script_drop_cols = list(drop_cols)
    if label not in script_drop_cols:
        script_drop_cols.append(label)

    replacements = {
        r"^file_path = .*": f"file_path = r'{file_path}'",
        r"^output_file = .*": f"output_file = r'{output_file}'",
        r"^label = .*": f"label = '{label}'",
        r"^time_col = .*": f"time_col = '{time_col}'",
        r"^cutoff_date = .*": f"cutoff_date = {repr(cutoff_date)}",
        r"^OOT_RATIO = .*": f"OOT_RATIO = {oot_ratio if split_mode == 'ai' else 0}",
        r"^drop_cols = \[.*?\]": f"drop_cols = {script_drop_cols!r}",
    }
    for pattern, repl in replacements.items():
        if pattern.startswith(r"^drop_cols"):
            content = _re_sub_literal(
                r"^drop_cols = \[.*?\]",
                repl,
                content,
                count=1,
                flags=re.MULTILINE | re.DOTALL,
            )
        else:
            content = _re_sub_literal(pattern, repl, content, count=1, flags=re.MULTILINE)

    uses_ht5_main = "def _ht5_main():" in content

    if split_mode == "manual" and test_file_path:
        test_path = test_file_path.resolve()
        content = _re_sub_literal(
            r"^TEST_FILE_PATH = .*",
            f"TEST_FILE_PATH = r'{test_path}'",
            content,
            count=1,
            flags=re.MULTILINE,
        )
    elif uses_ht5_main:
        content = _re_sub_literal(
            r"^TEST_FILE_PATH = .*",
            "TEST_FILE_PATH = None",
            content,
            count=1,
            flags=re.MULTILINE,
        )

    if uses_ht5_main:
        # 脚本已在 _ht5_main() 内完成读数/切分，切勿再 regex 注入（会破坏缩进）
        pass
    else:
        read_block = """# ==================== READ DATA ====================
if file_path.endswith('.csv'):
    data = None
    for _enc in ('utf-8', 'gbk', 'gb18030', 'latin1'):
        try:
            data = pd.read_csv(file_path, encoding=_enc)
            break
        except UnicodeDecodeError:
            continue
    if data is None:
        data = pd.read_csv(file_path)
else:
    data = pd.read_excel(file_path)

print(f'数据大小: {data.shape}')
print(f'标签列 {label} 存在: {label in data.columns}')

if label not in data.columns:
    raise KeyError(f'标签列 {label} 不存在于数据中')
"""
        content = re.sub(
            r"# ==================== READ DATA ====================.*?(?=# ==================== SPLIT TRAIN/TEST)",
            read_block + "\n",
            content,
            count=1,
            flags=re.DOTALL,
        )

        label_norm = _normalize_label_in_script(label)

        if split_mode == "manual" and test_file_path:
            test_path = test_file_path.resolve()
            split_block = f"""# ==================== SPLIT TRAIN/TEST (manual upload) ====================
{label_norm}
train_data = data.copy()
test_file_path = r'{test_path}'
if test_file_path.endswith('.csv'):
    test_data = None
    for _enc in ('utf-8', 'gbk', 'gb18030', 'latin1'):
        try:
            test_data = pd.read_csv(test_file_path, encoding=_enc)
            break
        except UnicodeDecodeError:
            continue
    if test_data is None:
        test_data = pd.read_csv(test_file_path)
else:
    test_data = pd.read_excel(test_file_path)
test_data[label] = _normalize_label_series(test_data[label])
test_data = test_data[test_data[label].isin([0, 1])].copy()
test_data[label] = test_data[label].astype(int)
print(f'Train: {{len(train_data)}}, Test: {{len(test_data)}}')
if len(train_data) == 0 or len(test_data) == 0:
    raise ValueError('自行划分模式下 Train 或 Test 无有效 0/1 样本')
actual_oot = True
OOT_RATIO = 0.2
"""
        elif split_mode == "cutoff" and cutoff_date:
            split_block = f"""# ==================== SPLIT TRAIN/TEST (cutoff per zhunru SOP) ====================
{label_norm}
if time_col not in data.columns:
    raise ValueError(f'时间列 {{time_col}} 不存在，无法切分 Train/Test')
_times = pd.to_datetime(data[time_col], errors='coerce')
_cutoff = pd.to_datetime('{cutoff_date}', errors='coerce')
if _times.isna().all() or pd.isna(_cutoff):
    raise ValueError('时间列或 cutoff_date 无法解析为日期，请检查格式')
train_data = data[_times < _cutoff].copy()
test_data = data[_times >= _cutoff].copy()
print(f'Train: {{len(train_data)}}, Test: {{len(test_data)}}')
if len(train_data) == 0 or len(test_data) == 0:
    raise ValueError('条件切分后 Train 或 Test 为空，请检查 cutoff_date 或时间列格式')
actual_oot = True
"""
        elif split_mode == "ai" and oot_ratio > 0:
            split_block = f"""# ==================== SPLIT TRAIN/TEST (iloc per zhunru SOP) ====================
{label_norm}
if time_col not in data.columns:
    raise ValueError(f'时间列 {{time_col}} 不存在，无法切分 Train/Test')
data = data.assign(__sort_time=pd.to_datetime(data[time_col], errors='coerce')).sort_values('__sort_time').drop(columns='__sort_time').reset_index(drop=True)
split_idx = int(len(data) * (1 - OOT_RATIO))
train_data = data.iloc[:split_idx].copy()
test_data = data.iloc[split_idx:].copy()
print(f'Train: {{len(train_data)}}, Test: {{len(test_data)}}')
if len(train_data) == 0 or len(test_data) == 0:
    raise ValueError('Train/Test 切分后样本为空，请检查 OOT 比例或时间列')
actual_oot = True
"""
        else:
            split_block = f"""# ==================== SPLIT TRAIN/TEST (no split) ====================
{label_norm}
train_data = data.copy()
test_data = data.copy()
actual_oot = False
OOT_RATIO = 0
"""

        content = _re_sub_literal(
            r"# ==================== SPLIT TRAIN/TEST ====================.*?(?=# ==================== PREPROCESS)",
            split_block + "\n",
            content,
            count=1,
            flags=re.DOTALL,
        )

    temp_script = OUTPUT_DIR / f"_headtail5_{uuid.uuid4().hex}.py"
    temp_script.write_text(content, encoding="utf-8")
    subprocess_env = os.environ.copy()
    try:
        result = subprocess.run(
            [sys.executable, str(temp_script.resolve())],
            capture_output=True,
            text=True,
            timeout=BINNING_SUBPROCESS_TIMEOUT_SEC,
            cwd=str(OUTPUT_DIR),
            env=subprocess_env,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "头尾5%分箱执行失败").strip()
            raise RuntimeError(err[-3000:])
    finally:
        temp_script.unlink(missing_ok=True)

    from services.binning_excel_format import reformat_binning_workbook

    reformat_binning_workbook(output_file)

    sheets = ["Train分箱明细"]
    has_test = (
        (split_mode == "manual" and test_file_path is not None)
        or (split_mode == "cutoff" and cutoff_date)
        or (split_mode == "ai" and oot_ratio > 0)
    )
    if has_test:
        sheets.append("Test分箱明细")

    return {
        "method": "headtail5",
        "stdout": result.stdout[-2000:] if result.stdout else "",
        "sheets": sheets,
        "test_rows": 0,
    }


def run_binning_job(
    file_path: Path,
    method: str,
    label: str,
    time_col: str,
    bin_num: int,
    init_bin_num: int,
    oot_ratio: float,
    split_mode: str,
    cutoff_date: Optional[str],
    drop_cols: Optional[List[str]],
    train_file_path: Optional[Path] = None,
    test_file_path: Optional[Path] = None,
) -> Tuple[Path, Dict[str, Any]]:
    drop_cols = _resolve_drop_cols(drop_cols, label, time_col, split_mode)
    output_file = OUTPUT_DIR / f"分箱结果_{method}_{uuid.uuid4().hex[:8]}.xlsx"

    if split_mode == "manual":
        if not train_file_path or not test_file_path:
            raise ValueError("自行划分模式需要同时上传 train 和 test 文件")
        train_ori = _prepare_dataframe(train_file_path, label, drop_cols)
        oot_ori = _prepare_dataframe(test_file_path, label, drop_cols)
        has_oot = True
    else:
        df = _prepare_dataframe(file_path, label, drop_cols)
        train_ori, oot_ori, has_oot = _split_train_test(
            df, label, time_col, split_mode, oot_ratio, cutoff_date
        )

    portfolio_bad_rate = float(train_ori[label].mean()) if len(train_ori) else 0.0

    if method == "headtail5":
        summary = _run_headtail5(
            file_path if split_mode != "manual" else train_file_path,
            output_file,
            label,
            time_col,
            oot_ratio if split_mode == "ai" else 0,
            split_mode,
            cutoff_date,
            drop_cols,
            test_file_path=test_file_path if split_mode == "manual" else None,
        )
    elif method in ("quantile", "chisquare"):
        summary = _run_quantile_or_chisquare(
            method,
            train_ori,
            oot_ori if has_oot else train_ori,
            has_oot,
            label,
            time_col,
            bin_num,
            init_bin_num,
            output_file,
        )
    else:
        raise ValueError(f"未知分箱方法: {method}")

    # quantile/chisquare 已在 _run_quantile_or_chisquare 内按 Train 边界重算 Test，无需二次对齐
    if has_oot and not oot_ori.empty and method == "headtail5":
        from services.feature_review_service import align_test_binning_workbook

        te = oot_ori.copy()
        te["overdue_flag"] = _normalize_binary_label(te[label]).astype(int)
        align_test_binning_workbook(output_file, te)

    summary["portfolio_bad_rate"] = portfolio_bad_rate
    summary["output_file"] = str(output_file.name)
    return output_file, summary


def create_binning_job() -> str:
    job_id = uuid.uuid4().hex
    _jobs[job_id] = BinningJob(job_id=job_id)
    return job_id


def execute_binning_job(job_id: str, **kwargs: Any) -> None:
    job = _jobs.get(job_id)
    if not job:
        return

    try:
        job.status = JobStatus.RUNNING
        job.message = "正在执行分箱..."
        job.progress = 5.0
        job.run_params = {
            k: str(v) if isinstance(v, Path) else v
            for k, v in kwargs.items()
            if v is not None
        }
        output_path, summary = run_binning_job(**kwargs)
        job.progress = 100.0
        job.status = JobStatus.COMPLETED
        job.message = "分箱完成"
        job.output_path = output_path
        job.summary = summary
        from services.feature_review_service import clear_review_cache
        clear_review_cache(job_id)
        _persist_job(job)
    except Exception as exc:
        job.status = JobStatus.FAILED
        job.message = str(exc)
        job.progress = 0.0
        _persist_job(job)


def start_binning_job(**kwargs: Any) -> str:
    """兼容旧调用：同步执行（测试用）。"""
    job_id = create_binning_job()
    execute_binning_job(job_id, **kwargs)
    return job_id
