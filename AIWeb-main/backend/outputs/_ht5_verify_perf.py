#!/usr/bin/env python3
"""Verify headtail5 correctness + benchmark old parallel vs new serial."""
import os
import re
import sys
import time
import uuid
import subprocess
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
NEW_SCRIPT = ROOT.parent / "binning-5percent" / "references" / "binning_headtail5_oot.py"
OLD_SCRIPT = ROOT / "outputs" / "_ht5_gen_test.py"
SAMPLE = ROOT / "uploads" / "_test_sample.csv"
LARGE = ROOT / "uploads" / "0bf52924998148c79374a7d3998871ca.csv"
OUT = ROOT / "outputs"


def patch_script(content: str, data_path: Path, out_path: Path, label: str = "overdue_flag2",
                 time_col: str = "create_time_x", oot: float = 0.2) -> str:
    c = content
    c = re.sub(r"^file_path = .*", f"file_path = r'{data_path}'", c, count=1, flags=re.M)
    c = re.sub(r"^output_file = .*", f"output_file = r'{out_path}'", c, count=1, flags=re.M)
    c = re.sub(r"^label = .*", f"label = '{label}'", c, count=1, flags=re.M)
    c = re.sub(r"^time_col = .*", f"time_col = '{time_col}'", c, count=1, flags=re.M)
    c = re.sub(r"^OOT_RATIO = .*", f"OOT_RATIO = {oot}", c, count=1, flags=re.M)
    c = re.sub(r"^cutoff_date = .*", "cutoff_date = None", c, count=1, flags=re.M)
    c = re.sub(
        r"^drop_cols = \[.*?\]",
        "drop_cols = ['id_x','id_y','client_id','apply_id','pkid','pid','serial_number','is_old','create_time_y','risk_over_days','fact_money','fact_repay_money','expire']",
        c,
        count=1,
        flags=re.M | re.S,
    )
    c = re.sub(r"^TEST_FILE_PATH = .*", "TEST_FILE_PATH = None", c, count=1, flags=re.M)
    return c


def run_script(content: str, env: dict, tag: str) -> tuple[float, Path]:
    tmp = OUT / f"_verify_{tag}_{uuid.uuid4().hex[:8]}.py"
    xlsx = OUT / f"_verify_{tag}_{uuid.uuid4().hex[:8]}.xlsx"
    content = re.sub(r"^output_file = .*", f"output_file = r'{xlsx}'", content, count=1, flags=re.M)
    tmp.write_text(content, encoding="utf-8")
    t0 = time.perf_counter()
    r = subprocess.run(
        [sys.executable, str(tmp)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(OUT),
        timeout=3600,
    )
    elapsed = time.perf_counter() - t0
    tmp.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError(f"{tag} failed:\n{(r.stderr or r.stdout)[-2000:]}")
    return elapsed, xlsx


def load_sheets(path: Path) -> dict[str, pd.DataFrame]:
    return {s: pd.read_excel(path, sheet_name=s) for s in pd.ExcelFile(path).sheet_names}


def compare_outputs(a: Path, b: Path) -> list[str]:
    diffs = []
    sa, sb = load_sheets(a), load_sheets(b)
    if set(sa) != set(sb):
        diffs.append(f"sheet names differ: {set(sa)} vs {set(sb)}")
        return diffs
    num_cols = [
        "#Obs", "%Obs", "#Good", "%Good", "#Bad", "%Bad", "%Bad_Rate",
        "WOE", "IV(bin)", "IV(total)", "Lift", "bin_ks", "total_ks", "金额逾期率",
    ]
    for sheet in sa:
        da, db = sa[sheet].sort_values(["变量英文名", "Bin"]).reset_index(drop=True), \
                 sb[sheet].sort_values(["变量英文名", "Bin"]).reset_index(drop=True)
        if da.shape != db.shape:
            diffs.append(f"{sheet}: shape {da.shape} vs {db.shape}")
            continue
        for col in da.columns:
            if col in num_cols:
                va = pd.to_numeric(da[col], errors="coerce")
                vb = pd.to_numeric(db[col], errors="coerce")
                if not va.equals(vb) and (va - vb).abs().max(skipna=True) > 1e-9:
                    diffs.append(f"{sheet}.{col}: max diff {(va - vb).abs().max()}")
            else:
                if not da[col].astype(str).equals(db[col].astype(str)):
                    diffs.append(f"{sheet}.{col}: string mismatch")
    return diffs


def main():
    env_base = os.environ.copy()
    new_content = NEW_SCRIPT.read_text(encoding="utf-8")
    old_content = OLD_SCRIPT.read_text(encoding="utf-8")

    print("=== Correctness: old serial (workers=1) vs new default (Windows serial) ===")
    env_old = env_base.copy()
    env_old["BINNING_WORKERS"] = "1"
    env_new = env_base.copy()
    env_new.pop("BINNING_FORCE_PARALLEL", None)

    t_old, out_old = run_script(patch_script(old_content, SAMPLE, OUT / "_old.xlsx"), env_old, "old_serial")
    t_new, out_new = run_script(patch_script(new_content, SAMPLE, OUT / "_new.xlsx"), env_new, "new_serial")
    diffs = compare_outputs(out_old, out_new)
    print(f"old serial: {t_old:.2f}s  new serial: {t_new:.2f}s")
    if diffs:
        print("DIFFS FOUND:")
        for d in diffs[:20]:
            print(" ", d)
        sys.exit(1)
    print("OK: outputs match")

    print("\n=== Performance on large dataset (807 vars, ~13k rows) ===")
    # Old: forced parallel (BINNING_WORKERS=8) — reproduces slowness
    env_old_par = env_base.copy()
    env_old_par["BINNING_WORKERS"] = "8"
    t_old_par, out_old_par = run_script(
        patch_script(old_content, LARGE, OUT / "_old_par.xlsx", label="target", time_col="apply_date", oot=0),
        env_old_par,
        "old_parallel",
    )
    env_new_def = env_base.copy()
    t_new_def, out_new_def = run_script(
        patch_script(new_content, LARGE, OUT / "_new_def.xlsx", label="target", time_col="apply_date", oot=0),
        env_new_def,
        "new_default",
    )
    diffs2 = compare_outputs(out_old_par, out_new_def)
    print(f"old parallel (workers=8): {t_old_par:.1f}s")
    print(f"new default (Windows serial): {t_new_def:.1f}s")
    print(f"speedup: {t_old_par / t_new_def:.2f}x")
    if diffs2:
        print("WARNING: large dataset outputs differ (first 5):")
        for d in diffs2[:5]:
            print(" ", d)
    else:
        print("OK: large dataset outputs match")

    for p in [out_old, out_new, out_old_par, out_new_def]:
        p.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
