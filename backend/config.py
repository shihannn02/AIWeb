import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

SECRET_KEY = os.getenv("RISK_SECRET_KEY", "oklik-risk-dev-secret-change-in-prod")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 8

# Default login (override via env in production)
ADMIN_USERNAME = os.getenv("RISK_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("RISK_ADMIN_PASS", "risk123")

SKILL_PATHS = {
    "quantile": PROJECT_ROOT / "2026_05_31_binning-quantile" / "binning-quantile" / "scripts" / "binning.py",
    "chisquare": PROJECT_ROOT / "2026_05_31_binning-quantile" / "binning-chisquare" / "scripts" / "binning.py",
    "headtail5": PROJECT_ROOT / "binning-5percent" / "references" / "binning_headtail5_oot.py",
}

DEFAULT_DROP_COLS = [
    "id_x", "id_y", "client_id", "apply_id", "pkid", "pid",
    "product", "serial_number", "money", "fact_money",
    "fact_repay_money", "create_time_y", "risk_over_days",
]

# 分箱任务最长等待（秒）；可通过环境变量 BINNING_SUBPROCESS_TIMEOUT_SEC 覆盖
BINNING_SUBPROCESS_TIMEOUT_SEC = int(
    os.getenv("BINNING_SUBPROCESS_TIMEOUT_SEC", str(2 * 60 * 60))
)
