"""分箱结果 Excel 统一格式：Train / Test 使用相同的数据条与交替背景。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

# 与等频 / 卡方分箱导出一致
BAR_COLOR = "#5DADE2"
ROW_GRAY = "#EBEBEB"
ROW_WHITE = "#FFFFFF"


def bad_rate_col_index(df: pd.DataFrame) -> Optional[int]:
    for name in ("%Bad_Rate", "bad_rate"):
        if name in df.columns:
            return int(df.columns.get_loc(name))
    return None


def feature_col_for_format(df: pd.DataFrame) -> Optional[str]:
    for name in ("feature", "变量英文名"):
        if name in df.columns:
            return name
    return None


def resolve_binning_sheet_names(excel_path: str) -> Tuple[str, Optional[str]]:
    xls = pd.ExcelFile(excel_path)
    test_sheet = None
    train_sheet = None
    for s in xls.sheet_names:
        if "test" in s.lower() and "分箱" in s:
            test_sheet = s
    for s in xls.sheet_names:
        if s == "train分箱结果":
            train_sheet = s
            break
        if "train" in s.lower() and "分箱" in s:
            train_sheet = s
            break
    if train_sheet is None:
        for s in xls.sheet_names:
            if s != test_sheet and "分箱" in s:
                train_sheet = s
                break
    if train_sheet is None:
        for s in xls.sheet_names:
            if "train" in s.lower():
                train_sheet = s
                break
    if train_sheet is None:
        raise ValueError(f"找不到 Train 分箱 Sheet: {xls.sheet_names}")
    return train_sheet, test_sheet


def write_formatted_binning_sheet(
    writer: pd.ExcelWriter,
    workbook,
    df: pd.DataFrame,
    sheet_name: str,
) -> None:
    """写入单个分箱 Sheet：bad_rate 蓝色数据条 + 按变量灰白交替背景。"""
    if df is None:
        return
    df = df.copy()
    df.to_excel(writer, sheet_name=sheet_name, index=False)
    if df.empty:
        return

    ws = writer.sheets[sheet_name]
    nrows = len(df)

    br_col = bad_rate_col_index(df)
    if br_col is not None:
        ws.conditional_format(1, br_col, nrows, br_col, {
            "type": "data_bar",
            "bar_color": BAR_COLOR,
            "bar_solid": True,
        })

    feat_col = feature_col_for_format(df)
    if not feat_col:
        return
    fmt_gray = workbook.add_format({"bg_color": ROW_GRAY})
    fmt_white = workbook.add_format({"bg_color": ROW_WHITE})
    color_flag = 0
    prev_feature = None
    for i, feat in enumerate(df[feat_col].tolist()):
        if feat != prev_feature:
            color_flag = 1 - color_flag
            prev_feature = feat
        ws.set_row(i + 1, None, fmt_gray if color_flag else fmt_white)


def write_formatted_binning_workbook(
    path: Path,
    train_df: pd.DataFrame,
    test_df: Optional[pd.DataFrame] = None,
    train_sheet: str = "Train分箱明细",
    test_sheet: str = "Test分箱明细",
) -> None:
    """重写分箱 Excel，Train / Test 使用同一套格式规则。"""
    writer = pd.ExcelWriter(path, engine="xlsxwriter")
    workbook = writer.book
    write_formatted_binning_sheet(writer, workbook, train_df, train_sheet)
    if test_df is not None and not test_df.empty:
        write_formatted_binning_sheet(writer, workbook, test_df, test_sheet)
    writer.close()


def reformat_binning_workbook(path: Path) -> None:
    """读取已有分箱 Excel 并重写为统一格式（用于头尾5%脚本原始输出后处理）。"""
    from services.feature_review_service import read_binning_sheets

    train_df, test_df = read_binning_sheets(str(path))
    train_sheet, test_sheet = resolve_binning_sheet_names(str(path))
    write_formatted_binning_workbook(
        path,
        train_df,
        test_df,
        train_sheet=train_sheet,
        test_sheet=test_sheet or "Test分箱明细",
    )
