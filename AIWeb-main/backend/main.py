from pathlib import Path
from typing import List, Optional
import asyncio
from functools import partial

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from auth import authenticate_user, create_access_token, get_current_user
from config import DEFAULT_DROP_COLS, OUTPUT_DIR, UPLOAD_DIR
import threading

from services.binning_runner import create_binning_job, execute_binning_job, get_job
from services.feature_review_service import (
    build_feature_review,
    build_threshold_overview,
    evaluate_reject_preview,
    get_feature_detail,
    warmup_review_cache,
)
from services.serial_strategy_service import (
    _collect_selected_features,
    build_serial_analysis,
    export_serial_excel,
)
from services.data_service import (
    get_upload_path,
    guess_best_label,
    guess_best_time_col,
    read_dataframe,
    save_upload,
    validate_dataset,
)

app = FastAPI(title="风控准入分箱平台", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
APP_BUILD = "20260617a"


@app.middleware("http")
async def no_cache_static_assets(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/js/") or path.startswith("/css/") or path == "/index.html":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class BinningRequest(BaseModel):
    file_id: str
    method: str = Field(description="quantile | chisquare | headtail5")
    label: str
    time_col: str = "create_time_x"
    bin_num: int = 10
    init_bin_num: int = 20
    oot_ratio: float = 0.2
    split_mode: str = Field(default="ai", description="ai | cutoff | manual")
    cutoff_date: Optional[str] = None
    drop_cols: Optional[List[str]] = None
    train_file_id: Optional[str] = None
    test_file_id: Optional[str] = None


class ValidateRequest(BaseModel):
    label: str
    split_mode: str = "ai"
    time_col: Optional[str] = None
    oot_ratio: float = 0.2
    cutoff_date: Optional[str] = None
    train_file_id: Optional[str] = None
    test_file_id: Optional[str] = None


@app.post("/api/auth/login", response_model=LoginResponse)
def login(body: LoginRequest):
    if not authenticate_user(body.username, body.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return LoginResponse(access_token=create_access_token(body.username))


def _guess_label(df) -> str:
    return guess_best_label(df)


def _parse_uploaded_file(path: Path):
    df = read_dataframe(path)
    label = guess_best_label(df)
    info = validate_dataset(df, label=label)
    return label, info


@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    _: str = Depends(get_current_user),
):
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件为空")
    file_id, path = save_upload(file.filename or "data.xlsx", content)
    loop = asyncio.get_running_loop()
    label, info = await loop.run_in_executor(None, partial(_parse_uploaded_file, path))
    return {
        "file_id": file_id,
        "filename": file.filename,
        "rows": info["rows"],
        "columns": info["columns"],
        "column_names": info["column_names"],
        "label": info["label"],
        "suggested_label": info["suggested_label"],
        "label_candidates": info["label_candidates"],
        "time_candidates": info["time_candidates"],
        "suggested_time_col": guess_best_time_col(info["column_names"]),
        "default_drop_cols": [c for c in DEFAULT_DROP_COLS if c in info["column_names"]],
        "valid_samples": info["valid_samples"],
        "portfolio_bad_rate": info["portfolio_bad_rate"],
    }


@app.get("/api/config/default-drop-cols")
def get_default_drop_cols(_: str = Depends(get_current_user)):
    return {"default_drop_cols": DEFAULT_DROP_COLS}


def _run_validate(
    file_id: str,
    label: str,
    split_mode: str,
    time_col: Optional[str],
    oot_ratio: float,
    cutoff_date: Optional[str],
    train_file_id: Optional[str],
    test_file_id: Optional[str],
):
    path = get_upload_path(file_id)
    if not path:
        raise HTTPException(status_code=404, detail="文件不存在")
    df = read_dataframe(path)

    train_df = None
    test_df = None
    if split_mode == "manual":
        if train_file_id and test_file_id:
            train_path = get_upload_path(train_file_id)
            test_path = get_upload_path(test_file_id)
            if not train_path or not test_path:
                raise HTTPException(status_code=404, detail="Train/Test 文件不存在")
            train_df = read_dataframe(train_path)
            test_df = read_dataframe(test_path)

    return validate_dataset(
        df,
        label,
        split_mode=split_mode,
        time_col=time_col,
        oot_ratio=oot_ratio,
        cutoff_date=cutoff_date,
        train_df=train_df,
        test_df=test_df,
    )


@app.get("/api/files/{file_id}/validate")
def validate_file_get(
    file_id: str,
    label: str,
    split_mode: str = "ai",
    time_col: Optional[str] = None,
    oot_ratio: float = 0.2,
    cutoff_date: Optional[str] = None,
    train_file_id: Optional[str] = None,
    test_file_id: Optional[str] = None,
    _: str = Depends(get_current_user),
):
    return _run_validate(
        file_id, label, split_mode, time_col, oot_ratio,
        cutoff_date, train_file_id, test_file_id,
    )


@app.post("/api/files/{file_id}/validate")
def validate_file_post(
    file_id: str,
    body: ValidateRequest,
    _: str = Depends(get_current_user),
):
    return _run_validate(
        file_id,
        body.label,
        body.split_mode,
        body.time_col,
        body.oot_ratio,
        body.cutoff_date,
        body.train_file_id,
        body.test_file_id,
    )


@app.post("/api/binning/run")
def run_binning(
    body: BinningRequest,
    _: str = Depends(get_current_user),
):
    file_path = get_upload_path(body.file_id)
    if not file_path:
        raise HTTPException(status_code=404, detail="数据文件不存在")

    train_path = get_upload_path(body.train_file_id) if body.train_file_id else None
    test_path = get_upload_path(body.test_file_id) if body.test_file_id else None

    if body.method not in ("quantile", "chisquare", "headtail5"):
        raise HTTPException(status_code=400, detail="分箱方法必须是 quantile / chisquare / headtail5")

    job_id = create_binning_job()
    kwargs = dict(
        file_path=file_path,
        method=body.method,
        label=body.label,
        time_col=body.time_col,
        bin_num=body.bin_num,
        init_bin_num=body.init_bin_num,
        oot_ratio=body.oot_ratio,
        split_mode=body.split_mode,
        cutoff_date=body.cutoff_date,
        drop_cols=body.drop_cols,
        train_file_path=train_path,
        test_file_path=test_path,
    )
    threading.Thread(target=execute_binning_job, args=(job_id,), kwargs=kwargs, daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/binning/{job_id}")
def binning_status(job_id: str, _: str = Depends(get_current_user)):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "message": job.message,
        "progress": job.progress,
        "summary": job.summary,
        "download_ready": job.output_path is not None and job.output_path.exists(),
    }


class FeatureReviewRequest(BaseModel):
    bad_rate_threshold: float = 0.60
    min_hit_count: int = 10
    include_features: Optional[List[str]] = None


class RejectRuleItem(BaseModel):
    feature: str
    operator: str = ">"
    threshold: float = 0.0
    values: Optional[List[str]] = None


class RejectPreviewRequest(BaseModel):
    rules: List[RejectRuleItem] = []


class SerialAnalysisRequest(BaseModel):
    rules: List[RejectRuleItem] = []
    sort_mode: str = Field(default="selection", description="selection | bad_rate | hit_count")
    lift_min: float = 1.10
    min_hit: int = 5


@app.get("/api/binning/{job_id}/review")
def feature_review_get(
    job_id: str,
    bad_rate_threshold: float = 0.60,
    min_hit_count: int = 10,
    include_features: Optional[str] = None,
    include_threshold_overview: bool = True,
    _: str = Depends(get_current_user),
):
    try:
        extra = [f.strip() for f in (include_features or "").split(",") if f.strip()]
        return build_feature_review(
            job_id,
            bad_rate_threshold=bad_rate_threshold,
            min_hit_count=min_hit_count,
            include_features=extra or None,
            include_threshold_overview=include_threshold_overview,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/binning/{job_id}/review")
def feature_review_post(
    job_id: str,
    body: FeatureReviewRequest,
    _: str = Depends(get_current_user),
):
    try:
        return build_feature_review(
            job_id,
            bad_rate_threshold=body.bad_rate_threshold,
            min_hit_count=body.min_hit_count,
            include_features=body.include_features,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/binning/{job_id}/warmup")
def review_warmup(job_id: str, _: str = Depends(get_current_user)):
    try:
        return warmup_review_cache(job_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/binning/{job_id}/threshold-overview")
def threshold_overview(
    job_id: str,
    min_hit_count: int = 10,
    _: str = Depends(get_current_user),
):
    try:
        return build_threshold_overview(job_id, min_hit_count=min_hit_count)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/binning/{job_id}/reject-preview")
def reject_preview(
    job_id: str,
    body: RejectPreviewRequest,
    _: str = Depends(get_current_user),
):
    try:
        rules = [r.model_dump() for r in body.rules]
        return evaluate_reject_preview(job_id, rules)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"拒绝影响计算失败: {e}")


@app.post("/api/binning/{job_id}/serial-analysis")
def serial_analysis(
    job_id: str,
    body: SerialAnalysisRequest,
    _: str = Depends(get_current_user),
):
    try:
        rules = [r.model_dump() for r in body.rules]
        return build_serial_analysis(
            job_id,
            rules,
            sort_mode=body.sort_mode,
            lift_min=body.lift_min,
            min_hit=body.min_hit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"串联分析失败: {e}")


@app.post("/api/binning/{job_id}/serial-analysis/export")
def serial_analysis_export(
    job_id: str,
    body: SerialAnalysisRequest,
    _: str = Depends(get_current_user),
):
    try:
        rules = [r.model_dump() for r in body.rules]
        analysis = build_serial_analysis(
            job_id,
            rules,
            sort_mode=body.sort_mode,
            lift_min=body.lift_min,
            min_hit=body.min_hit,
        )
        data = export_serial_excel(
            analysis,
            job_id,
            _collect_selected_features(rules, analysis),
        )
        filename = f"serial_{job_id[:8]}.xlsx"
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"串联分析导出失败: {e}")


@app.get("/api/binning/{job_id}/feature-detail")
def feature_detail_query(
    job_id: str,
    feature: str,
    bad_rate_threshold: float = 0.60,
    include_stability: bool = False,
    _: str = Depends(get_current_user),
):
    try:
        return get_feature_detail(
            job_id,
            feature,
            bad_rate_threshold=bad_rate_threshold,
            include_stability=include_stability,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"特征明细加载失败: {e}")


@app.get("/api/binning/{job_id}/features/{feature:path}")
def feature_detail(
    job_id: str,
    feature: str,
    bad_rate_threshold: float = 0.60,
    include_stability: bool = False,
    _: str = Depends(get_current_user),
):
    try:
        return get_feature_detail(
            job_id,
            feature,
            bad_rate_threshold=bad_rate_threshold,
            include_stability=include_stability,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"特征明细加载失败: {e}")


@app.get("/api/binning/{job_id}/download")
def download_result(job_id: str, _: str = Depends(get_current_user)):
    job = get_job(job_id)
    if not job or not job.output_path or not job.output_path.exists():
        raise HTTPException(status_code=404, detail="结果文件不存在")
    return FileResponse(
        job.output_path,
        filename=job.output_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "app_build": APP_BUILD}


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
