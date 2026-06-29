import os, re, sys, time, subprocess
from pathlib import Path
import pandas as pd

ROOT = Path(r"E:\AIWeb-main\AIWeb-main\backend")
NEW = ROOT.parent / "binning-5percent" / "references" / "binning_headtail5_oot.py"
OLD_PAR = ROOT / "outputs" / "_ht5_gen_test.py"
SAMPLE = ROOT / "uploads" / "_test_sample.csv"
LARGE = ROOT / "uploads" / "0bf52924998148c79374a7d3998871ca.csv"
OUT = ROOT / "outputs"


def patch(content, data, out, label="overdue_flag2", time_col="create_time_x", oot=0.2):
    c = content
    c = re.sub(r"^file_path = .*", lambda m: f"file_path = r'{data}'", c, 1, flags=re.M)
    c = re.sub(r"^output_file = .*", lambda m: f"output_file = r'{out}'", c, 1, flags=re.M)
    c = re.sub(r"^label = .*", f"label = '{label}'", c, 1, flags=re.M)
    c = re.sub(r"^time_col = .*", f"time_col = '{time_col}'", c, 1, flags=re.M)
    c = re.sub(r"^OOT_RATIO = .*", f"OOT_RATIO = {oot}", c, 1, flags=re.M)
    c = re.sub(r"^cutoff_date = .*", "cutoff_date = None", c, 1, flags=re.M)
    c = re.sub(
        r"^drop_cols = \[.*?\]",
        "drop_cols = ['id_x','id_y','client_id','apply_id','pkid','pid','serial_number','is_old','create_time_y','risk_over_days','fact_money','fact_repay_money','expire','overdue_flag2']",
        c, 1, flags=re.M | re.S,
    )
    c = re.sub(r"^TEST_FILE_PATH = .*", "TEST_FILE_PATH = None", c, 1, flags=re.M)
    return c


def patch_large(content, out):
    c = content
    c = re.sub(r"^file_path = .*", lambda m: f"file_path = r'{LARGE}'", c, 1, flags=re.M)
    c = re.sub(r"^output_file = .*", lambda m: f"output_file = r'{out}'", c, 1, flags=re.M)
    c = re.sub(r"^label = .*", "label = 'target'", c, 1, flags=re.M)
    c = re.sub(r"^time_col = .*", "time_col = 'apply_date'", c, 1, flags=re.M)
    c = re.sub(r"^OOT_RATIO = .*", "OOT_RATIO = 0", c, 1, flags=re.M)
    c = re.sub(r"^cutoff_date = .*", "cutoff_date = None", c, 1, flags=re.M)
    c = re.sub(
        r"^drop_cols = \[.*?\]",
        "drop_cols = ['client_id','apply_id','serial_number','money','fact_money','fact_repay_money','create_time_y','risk_over_days','target']",
        c, 1, flags=re.M | re.S,
    )
    c = re.sub(r"^TEST_FILE_PATH = .*", "TEST_FILE_PATH = None", c, 1, flags=re.M)
    return c


def run(content, env, tag, timeout=3600):
    xlsx = OUT / f"_{tag}.xlsx"
    tmp = OUT / f"_{tag}.py"
    c = re.sub(r"^output_file = .*", lambda m: f"output_file = r'{xlsx}'", content, 1, flags=re.M)
    tmp.write_text(c, encoding="utf-8")
    t0 = time.perf_counter()
    r = subprocess.run([sys.executable, str(tmp)], capture_output=True, text=True, env=env, cwd=str(OUT), timeout=timeout)
    el = time.perf_counter() - t0
    tmp.unlink(missing_ok=True)
    print(f"{tag}: {el:.2f}s rc={r.returncode}")
    if r.returncode:
        print((r.stderr or r.stdout)[-2000:])
        sys.exit(1)
    return el, xlsx


def compare(a, b):
    sa = pd.read_excel(a, sheet_name=None)
    sb = pd.read_excel(b, sheet_name=None)
    nums = ["#Obs", "%Bad_Rate", "WOE", "IV(bin)", "Lift", "bin_ks", "total_ks"]
    ok = True
    for sheet in sa:
        da = sa[sheet].sort_values(["变量英文名", "Bin"]).reset_index(drop=True)
        db = sb[sheet].sort_values(["变量英文名", "Bin"]).reset_index(drop=True)
        if da.shape != db.shape:
            print(f"  {sheet} shape {da.shape} vs {db.shape}"); ok = False; continue
        for col in nums:
            if col not in da.columns:
                continue
            d = (pd.to_numeric(da[col], errors="coerce") - pd.to_numeric(db[col], errors="coerce")).abs().max()
            if pd.notna(d) and d > 1e-9:
                print(f"  {sheet}.{col} maxdiff={d}"); ok = False
    return ok


nc = NEW.read_text(encoding="utf-8")
e = os.environ.copy()

print("=== 1. New script reproducibility ===")
_, o1 = run(patch(nc, SAMPLE, OUT / "r1.xlsx"), e, "run1", timeout=120)
_, o2 = run(patch(nc, SAMPLE, OUT / "r2.xlsx"), e, "run2", timeout=120)
print("reproducible:", compare(o1, o2))

print("\n=== 2. New serial vs old serial (workers=1) ===")
oc = OLD_PAR.read_text(encoding="utf-8")
e1 = e.copy(); e1["BINNING_WORKERS"] = "1"
_, on = run(patch(nc, SAMPLE, OUT / "new.xlsx"), e1, "new_serial", timeout=120)
_, oo = run(patch(oc, SAMPLE, OUT / "old.xlsx"), e1, "old_serial", timeout=120)
print("serial match:", compare(on, oo))

print("\n=== 3. Performance large dataset ===")
e8 = e.copy(); e8["BINNING_WORKERS"] = "8"
t_old, _ = run(patch_large(oc, OUT / "op.xlsx"), e8, "old_parallel_8w", timeout=3600)
t_new, _ = run(patch_large(nc, OUT / "nd.xlsx"), e, "new_default", timeout=3600)
print(f"old parallel: {t_old:.1f}s")
print(f"new default:  {t_new:.1f}s")
print(f"speedup:      {t_old/t_new:.2f}x")
