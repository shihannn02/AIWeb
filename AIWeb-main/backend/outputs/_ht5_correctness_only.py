import os, re, sys, time, subprocess
from pathlib import Path
import pandas as pd

ROOT = Path(r"E:\AIWeb-main\AIWeb-main\backend")
NEW = ROOT.parent / "binning-5percent" / "references" / "binning_headtail5_oot.py"
OLD_PAR = ROOT / "outputs" / "_ht5_gen_test.py"
SAMPLE = ROOT / "uploads" / "_test_sample.csv"
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

def run(content, env, tag):
    xlsx = OUT / f"_{tag}.xlsx"
    tmp = OUT / f"_{tag}.py"
    c = re.sub(r"^output_file = .*", lambda m: f"output_file = r'{xlsx}'", content, 1, flags=re.M)
    tmp.write_text(c, encoding="utf-8")
    t0 = time.perf_counter()
    r = subprocess.run([sys.executable, str(tmp)], capture_output=True, text=True, env=env, cwd=str(OUT), timeout=120)
    el = time.perf_counter() - t0
    tmp.unlink(missing_ok=True)
    print(f"{tag}: {el:.2f}s rc={r.returncode}")
    if r.returncode:
        print((r.stderr or r.stdout)[-1500:]); sys.exit(1)
    return xlsx

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
            if col not in da.columns: continue
            d = (pd.to_numeric(da[col], errors="coerce") - pd.to_numeric(db[col], errors="coerce")).abs().max()
            if pd.notna(d) and d > 1e-9:
                print(f"  {sheet}.{col} maxdiff={d}"); ok = False
    return ok

nc = NEW.read_text(encoding="utf-8")
oc = OLD_PAR.read_text(encoding="utf-8")
e = os.environ.copy(); e["BINNING_WORKERS"] = "1"
print("=== reproducibility ===")
print("match:", compare(run(patch(nc, SAMPLE, OUT/"r1.xlsx"), e, "run1"), run(patch(nc, SAMPLE, OUT/"r2.xlsx"), e, "run2")))
print("=== new vs old serial ===")
print("match:", compare(run(patch(nc, SAMPLE, OUT/"new.xlsx"), e, "new"), run(patch(oc, SAMPLE, OUT/"old.xlsx"), e, "old")))
