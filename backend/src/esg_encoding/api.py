"""
ESG System API Endpoints
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Optional, Set
import os
import re
import json
import pandas as pd
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from loguru import logger
from dotenv import load_dotenv
import time
import threading
import copy
import asyncio
import hashlib

from .models import (
    ProcessingConfig,
    ChatRequest,
    ChatResponse,
    ComplianceAssessment,
    DisclosureAnalysis,
    DisclosureStatus,
    ReportContent,
    DocumentContent,
    TextSegment,
    MetricCollection,
    LoginRequest,
    RegisterRequest,
    AuthResponse
)
from .exceptions import InputError, AccessError
from .cross_analysis_models import (
    CrossCompareRequest,
    CrossCompareResponse,
    CrossReportsResponse,
    CrossRecordsRequest,
    CrossRecordsResponse,
    ExcelMetricsRequest,
    ExcelMetricsResponse,
    CrossDisclosedCacheResponse,
)
from .cross_analysis import (
    get_reports_info,
    compare_topic,
    extract_records_for_topic,
    CROSS_CACHE_DIR,
)
from .excel_metrics_extractor import extract_excel_metrics_for_files
from .cross_catalog import dimension_by_key
from .auth.service import login, register
from .auth.dependencies import get_current_user, get_current_user_optional
from .report_encoder import ReportEncoder
from .metric_processor import MetricProcessor
from .dual_channel_retrieval import DualChannelRetriever
from .disclosure_inference import DisclosureInferenceEngine, COMPLIANCE_VALUE_NA
from .esg_chatbot import ESGChatbot
from .hipporag_patch import enable_hipporag
from .file_manager import file_manager
from .excel_exporter import ExcelExporter

# Load environment variables (load early to ensure CORS config can read from .env)
load_dotenv()

# Create FastAPI application
app = FastAPI(
    title="ESG Analysis System API",
    description="Complete ESG report analysis and compliance assessment system",
    version="1.0.0"
)

# Get CORS origins from environment variable
# Default includes common localhost addresses and network IP
FRONTEND_ORIGINS_STR = os.getenv(
    "FRONTEND_ORIGINS",
    "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001,http://192.168.254.1:3001"
)
# Split and strip whitespace from each origin
FRONTEND_ORIGINS = [origin.strip() for origin in FRONTEND_ORIGINS_STR.split(",") if origin.strip()]

# Log CORS configuration for debugging
logger.info(f"CORS allowed origins: {FRONTEND_ORIGINS}")



# Expose ./uploads over HTTP so the frontend can load persisted JSON outputs and other static artifacts.
# In docker-compose, ./uploads is mounted to /workspace/uploads in backend.
try:
    _uploads_dir = str(file_manager.base_dir.resolve())
    if os.path.isdir(_uploads_dir):
        app.mount("/uploads", StaticFiles(directory=_uploads_dir), name="uploads")
        logger.info(f"Mounted /uploads -> {_uploads_dir}")
    else:
        logger.warning(f"Uploads dir not found: {_uploads_dir} (skip mount)")
except Exception as _e:
    logger.warning(f"Failed to mount /uploads: {_e}")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Global variables to store system components
system_components = {
    "config": None,
    "report_encoder": None,
    "metric_processor": None,
    "dual_retriever": None,
    "disclosure_engine": None,
    "chatbot": None,
    "current_report": None,
    "current_assessment": None,
    "current_metrics": None,
    "current_framework": None,  # Store framework (e.g., SASB, GRI)
    "current_industry": None,  # Store main industry
    "current_semi_industry": None,  # Store sub-industry
    "current_gri_sector": None,  # GRI sector slug when framework is GRI
    "current_gri_topic": None,   # GRI topic slug when framework is GRI
    "current_company": None  # Store company name
}

# Cache processed metric collections so upload/process analysis can reuse semantic expansions
_processed_metric_collections_cache = {}
_processed_metric_collections_lock = threading.Lock()

def _metric_scope_cache_key(framework: str, params: dict) -> str:
    fw = (framework or "").strip().upper()
    safe = lambda v: str(v or "").strip().lower()
    if fw == "SASB":
        return f"SASB::{safe(params.get('semiIndustry'))}"
    if fw == "GRI":
        return f"GRI::{safe(params.get('griSector'))}::{safe(params.get('griTopic'))}"
    if fw == "CDP":
        return f"CDP::{safe(params.get('semiIndustry'))}"
    if fw == "TCFD":
        return f"TCFD::{safe(params.get('semiIndustry'))}"
    return f"{fw}::{json.dumps(params or {}, ensure_ascii=False, sort_keys=True)}"

def _ensure_processed_metric_collection(processor, collection, cache_key: str):
    if collection is None:
        return None
    if getattr(collection, "semantic_expansions", None):
        return collection

    with _processed_metric_collections_lock:
        cached = _processed_metric_collections_cache.get(cache_key)
        if cached is not None:
            logger.info(f"Reusing cached semantic expansions for {cache_key}")
            return copy.deepcopy(cached)

    logger.info(f"Processing semantic expansions for {cache_key}")
    processed = processor.process_metric_collection(collection)

    with _processed_metric_collections_lock:
        _processed_metric_collections_cache[cache_key] = copy.deepcopy(processed)

    return processed

# -----------------------------
# Cross-analysis Excel metrics job state
# -----------------------------
_excel_metrics_jobs = {}  # key -> {"thread": Thread, "started_at": float}
_excel_metrics_jobs_lock = threading.Lock()

# Single global ESGChatbot: serialize context/session ops vs background upload (HippoRAG + load_context).
_chatbot_ops_lock = threading.RLock()


# Deleted deprecated function _parse_compliance_report() (179 lines)
# This function parsed Markdown reports with heuristic guessing and preset defaults.
# Now loading assessment data directly from JSON files for accuracy.


@app.on_event("startup")
async def startup_event():
    """Initialize system components on startup"""
    # Load environment variables from .env file
    load_dotenv()
    
    # Create default configuration
    config = ProcessingConfig()
    
    # Read LLM configuration from environment variables
    if os.getenv("LLM_API_KEY"):
        config.llm_api_key = os.getenv("LLM_API_KEY")
    if os.getenv("LLM_BASE_URL"):
        config.llm_base_url = os.getenv("LLM_BASE_URL")
    if os.getenv("LLM_MODEL"):
        config.llm_model = os.getenv("LLM_MODEL")

    # OCR configuration (optional; PaddleOCR must be installed separately)
    # Example:
    #   ENABLE_OCR=true
    #   OCR_LANG=ch
    #   OCR_USE_GPU=false
    #   OCR_RENDER_ZOOM=2.0
    #   OCR_PAGE_TEXT_THRESHOLD=50
    #   OCR_MIN_TEXT_LEN=12
    #   OCR_IMAGE_MIN_AREA=50000
    #   OCR_MAX_IMAGES_PER_PAGE=4
    #   ENABLE_OCR_TABLE=true
    #   OCR_TABLE_MAX_PER_IMAGE=2
    if os.getenv("ENABLE_OCR") is not None:
        config.enable_ocr = os.getenv("ENABLE_OCR", "true").lower() in ("1", "true", "yes", "y")
    if os.getenv("OCR_LANG"):
        config.ocr_lang = os.getenv("OCR_LANG")  # ch / en
    if os.getenv("OCR_USE_GPU") is not None:
        config.ocr_use_gpu = os.getenv("OCR_USE_GPU", "false").lower() in ("1", "true", "yes", "y")
    if os.getenv("OCR_RENDER_ZOOM"):
        try:
            config.ocr_render_zoom = float(os.getenv("OCR_RENDER_ZOOM", "2.0"))
        except Exception:
            pass
    if os.getenv("OCR_PAGE_TEXT_THRESHOLD"):
        try:
            config.ocr_page_text_threshold = int(os.getenv("OCR_PAGE_TEXT_THRESHOLD", "50"))
        except Exception:
            pass
    if os.getenv("OCR_MIN_TEXT_LEN"):
        try:
            config.ocr_min_text_len = int(os.getenv("OCR_MIN_TEXT_LEN", "12"))
        except Exception:
            pass
    if os.getenv("OCR_IMAGE_MIN_AREA"):
        try:
            config.ocr_image_min_area = int(os.getenv("OCR_IMAGE_MIN_AREA", "50000"))
        except Exception:
            pass
    if os.getenv("OCR_MAX_IMAGES_PER_PAGE"):
        try:
            config.ocr_max_images_per_page = int(os.getenv("OCR_MAX_IMAGES_PER_PAGE", "4"))
        except Exception:
            pass
    if os.getenv("ENABLE_OCR_TABLE") is not None:
        config.enable_ocr_table = os.getenv("ENABLE_OCR_TABLE", "true").lower() in ("1", "true", "yes", "y")
    if os.getenv("OCR_TABLE_MAX_PER_IMAGE"):
        try:
            config.ocr_table_max_per_image = int(os.getenv("OCR_TABLE_MAX_PER_IMAGE", "2"))
        except Exception:
            pass

    
    # Initialize components
    system_components["config"] = config
    system_components["report_encoder"] = ReportEncoder(config)
    system_components["metric_processor"] = MetricProcessor(config)
    system_components["dual_retriever"] = DualChannelRetriever(config)
    system_components["disclosure_engine"] = DisclosureInferenceEngine(config)
    system_components["chatbot"] = ESGChatbot(config)

    # Share the already-loaded embedding model with chatbot (avoid double load)
    try:
        system_components["chatbot"].set_embedding_model(
            system_components["report_encoder"].embedder.model
        )
    except Exception as e:
        logger.warning(f"Failed to share embedding model with chatbot: {e}")

    # Share embedding model with CrossAnalysis module (avoid first-request model load timeout)
    try:
        from . import cross_analysis as _ca
        _ca._model = system_components["report_encoder"].embedder.model
    except Exception as e:
        logger.warning(f"Failed to share embedding model with cross_analysis: {e}")

    # Enable HippoRAG augmentation (safe even if HippoRAG not installed; patched_search falls back)
    try:
        enable_hipporag(system_components["chatbot"], config)
    except Exception as e:
        logger.warning(f"Failed to enable HippoRAG (will fallback to base retrieval): {e}")
    
    logger.info("ESG Analysis System initialized successfully")


# Exception handlers
@app.exception_handler(InputError)
async def input_error_handler(request: Request, exc: InputError):
    """Handle InputError exceptions (HTTP 400)"""
    return JSONResponse(
        status_code=400,
        content={"error": str(exc)}
    )


@app.exception_handler(AccessError)
async def access_error_handler(request: Request, exc: AccessError):
    """Handle AccessError exceptions (HTTP 403)"""
    return JSONResponse(
        status_code=403,
        content={"error": str(exc)}
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle all other exceptions (HTTP 500)"""
    # Use {} placeholder so str(exc) is not interpreted as format string (avoids KeyError when exc contains '{...}')
    logger.error("Unhandled exception: {}", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "A system error ocurred"}
    )


def _parse_scope_slugs_json(raw: Optional[str], fallback: Optional[str]) -> List[str]:
    """Parse JSON array of scope slugs from multipart field `scopeSlugs`, else single fallback."""
    if raw:
        s = str(raw).strip()
        if s:
            try:
                data = json.loads(s)
                if isinstance(data, list):
                    out = [str(x).strip() for x in data if str(x).strip()]
                    if out:
                        return out
            except Exception:
                pass
    if fallback and str(fallback).strip():
        return [str(fallback).strip()]
    return []


def _compliance_manifest_path(assessment_dir: Path, file_id: str) -> Path:
    return assessment_dir / f"{file_id}_compliance_manifest.json"


def _load_compliance_manifest(assessment_dir: Path, file_id: str) -> Optional[dict]:
    p = _compliance_manifest_path(assessment_dir, file_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_compliance_manifest(
    assessment_dir: Path,
    file_id: str,
    framework: str,
    outputs: List[dict],
    expected_scope_keys: Optional[List[str]] = None,
) -> None:
    assessment_dir.mkdir(parents=True, exist_ok=True)
    default_sk = None
    if outputs:
        default_sk = outputs[0].get("scope_key")
    elif expected_scope_keys:
        default_sk = expected_scope_keys[0]
    body: dict = {
        "file_id": file_id,
        "framework": framework,
        "default_scope_key": default_sk,
        "outputs": outputs,
    }
    if expected_scope_keys:
        body["expected_scope_keys"] = expected_scope_keys
    _compliance_manifest_path(assessment_dir, file_id).write_text(
        json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _compliance_file_stem_for_scope(fw: str, file_info: dict, scope_key: str) -> str:
    """Filename segment before `_{file_id}_compliance.*` (matches upload naming)."""
    sk = str(scope_key).strip()
    if not sk:
        return _sanitize_compliance_filename_part("report")
    if fw == "GRI":
        gs = (file_info.get("gri_sector") or "").strip()
        return _sanitize_compliance_filename_part(f"GRI_{gs}_{sk}")
    if fw == "SASB":
        return _sanitize_compliance_filename_part(sk)
    if fw == "CDP":
        return _sanitize_compliance_filename_part(f"CDP_{sk}")
    if fw == "TCFD":
        return _sanitize_compliance_filename_part(f"TCFD_{sk}")
    return _sanitize_compliance_filename_part(sk)


def _compliance_json_path_for_scope(
    assessment_dir: Path,
    file_id: str,
    fw: str,
    file_info: dict,
    scope_key: str,
) -> Optional[Path]:
    """Resolve per-scope compliance JSON path (matches upload naming)."""
    sk = str(scope_key).strip()
    if not sk:
        return None
    part = _compliance_file_stem_for_scope(fw, file_info, sk)
    p = assessment_dir / f"{part}_{file_id}_compliance.json"
    return p if p.is_file() else None


def _paths_for_scope_compliance_bundle(
    file_manager, file_id: str, file_info: dict, scope_key: str
) -> tuple[Path, Path, Path]:
    """json, xlsx, markdown paths for one scope (may not exist on disk)."""
    fw = (file_info.get("framework") or "").strip()
    part = _compliance_file_stem_for_scope(fw, file_info, scope_key)
    compliance_dir = Path(file_manager.compliance_outputs)
    json_p = compliance_dir / f"{part}_{file_id}_compliance.json"
    xlsx_p = compliance_dir / f"{part}_{file_id}_compliance.xlsx"
    md_stem = _sanitize_compliance_filename_part(f"{part}")
    md_p = Path(file_manager.markdown_outputs) / f"compliance_report_{file_id}_{md_stem}.md"
    return json_p, xlsx_p, md_p


def _build_scope_rows(
    file_manager, file_info: dict, m: Optional[dict]
) -> List[dict]:
    """One row per expected scope for UI; ready when output JSON exists (or manifest lists it)."""
    file_id = file_info.get("file_id")
    if not file_id:
        return []
    fw = (file_info.get("framework") or "").strip()
    expected: List[str] = []
    if m and isinstance(m.get("expected_scope_keys"), list) and m["expected_scope_keys"]:
        expected = [str(x).strip() for x in m["expected_scope_keys"] if str(x).strip()]
    else:
        raw = file_info.get("scope_slugs_json")
        if raw:
            try:
                slugs = json.loads(raw)
                if isinstance(slugs, list) and len(slugs) > 1:
                    expected = [str(x).strip() for x in slugs if str(x).strip()]
            except Exception:
                pass
    if len(expected) <= 1:
        return []

    done_manifest: Set[str] = set()
    if m:
        for o in m.get("outputs") or []:
            sk = o.get("scope_key")
            if sk is not None:
                done_manifest.add(str(sk))

    assessment_dir = Path(file_manager.compliance_outputs)
    rows: List[dict] = []
    for sk in expected:
        path = _compliance_json_path_for_scope(
            assessment_dir, str(file_id), fw, file_info, sk
        )
        ready = path is not None or sk in done_manifest
        rows.append(
            {
                "scope_key": sk,
                "ready": ready,
                "label": _slug_to_label(sk),
            }
        )
    return rows


def _scope_progress_for_report(file_manager, file_info: dict) -> dict:
    """Derive multi-scope analysis progress from manifest and/or compliance JSON files."""
    if file_info.get("file_type") != "report":
        return {}
    file_id = file_info.get("file_id")
    if not file_id:
        return {}
    assessment_dir = Path(file_manager.compliance_outputs)
    m = _load_compliance_manifest(assessment_dir, file_id)
    scope_rows = _build_scope_rows(file_manager, file_info, m)
    n_done = 0
    n_exp = 0

    if m:
        outs = m.get("outputs") or []
        n_done = len(outs) if isinstance(outs, list) else 0
        try:
            glob_n = len(list(assessment_dir.glob(f"*{file_id}*_compliance.json")))
            n_done = max(n_done, glob_n)
        except Exception:
            pass
        exp = m.get("expected_scope_keys")
        if isinstance(exp, list) and len(exp) > 0:
            n_exp = len(exp)
        elif n_done > 0:
            n_exp = n_done
    else:
        try:
            n_done = len(list(assessment_dir.glob(f"*{file_id}*_compliance.json")))
        except Exception:
            n_done = 0
        raw = file_info.get("scope_slugs_json")
        if raw:
            try:
                slugs = json.loads(raw)
                if isinstance(slugs, list) and slugs:
                    n_exp = len(slugs)
            except Exception:
                pass

    unknown_total = bool(not m and n_done > 0 and n_exp == 0)

    st = str(file_info.get("status", "")).lower()
    partial = (n_exp > 0 and n_done > 0 and n_done < n_exp) or (
        unknown_total and st == "pending"
    )
    all_done = (n_exp > 0 and n_done >= n_exp) or (
        unknown_total and n_done > 0 and st == "processed"
    )
    return {
        "scope_analysis_completed": n_done,
        "scope_analysis_total": n_exp,
        "scope_analysis_partial": partial,
        "scope_analysis_all_done": all_done,
        "scope_analysis_unknown_total": unknown_total,
        "scope_rows": scope_rows,
    }


def _enrich_file_records_with_scope_progress(
    file_manager, files: List[dict]
) -> List[dict]:
    out: List[dict] = []
    for f in files:
        if f.get("file_type") != "report":
            out.append(f)
            continue
        extra = _scope_progress_for_report(file_manager, f)
        merged = {**f, **extra}
        out.append(merged)
    return out


def _json_path_from_manifest(
    assessment_dir: Path, file_id: str, scope_key: Optional[str]
) -> Optional[Path]:
    m = _load_compliance_manifest(assessment_dir, file_id)
    if not m:
        return None
    outs = m.get("outputs") or []
    if not outs:
        return None
    want = (scope_key or "").strip()
    if want:
        for o in outs:
            if o.get("scope_key") == want:
                fn = o.get("json_filename")
                if fn:
                    p = assessment_dir / fn
                    if p.is_file():
                        return p
    fn0 = outs[0].get("json_filename")
    if fn0:
        p0 = assessment_dir / fn0
        if p0.is_file():
            return p0
    return None


def _sanitize_compliance_filename_part(s: Optional[str]) -> str:
    """Make a string safe for use in compliance output filenames (e.g. subindustry)."""
    if not s or not str(s).strip():
        return "report"
    s = str(s).strip()
    for c in '<>:"/\\|?*':
        s = s.replace(c, "_")
    return s[:80] if len(s) > 80 else s


def _slug_to_label(slug: str) -> str:
    """Convert slug (e.g. coal_sector) to display label (e.g. Coal Sector)."""
    if not slug:
        return ""
    return slug.replace("_", " ").strip().title()


def _get_gri_sectors_and_topics() -> dict:
    """Scan backend/data/gri_metrics/*.json and return sectors + topics per sector.
    Filenames are {sector_slug}_{topic_slug}.json (e.g. coal_sector_climate_change.json).
    Sector slug ends with '_sector' or '_sectors'; we split on that to get sector vs topic.
    """
    gri_dir = Path(__file__).parent.parent.parent / "data" / "gri_metrics"
    if not gri_dir.exists():
        return {"sectors": [], "topicsBySector": {}}
    sectors_set = set()
    topics_by_sector = {}
    for p in gri_dir.glob("*.json"):
        stem = p.stem
        sector_slug, topic_slug = None, None
        if "_sectors_" in stem:
            idx = stem.index("_sectors_") + len("_sectors_")
            sector_slug = stem[:idx].rstrip("_")
            topic_slug = stem[idx:].lstrip("_")
        elif "_sector_" in stem:
            idx = stem.index("_sector_") + len("_sector_")
            sector_slug = stem[:idx].rstrip("_")
            topic_slug = stem[idx:].lstrip("_")
        if not sector_slug or not topic_slug:
            continue
        sectors_set.add(sector_slug)
        if sector_slug not in topics_by_sector:
            topics_by_sector[sector_slug] = set()
        topics_by_sector[sector_slug].add(topic_slug)
    sectors = sorted(sectors_set)
    sectors_list = [{"slug": s, "label": _slug_to_label(s)} for s in sectors]
    topics_by_sector_list = {
        s: [{"slug": t, "label": _slug_to_label(t)} for t in sorted(topics_by_sector[s])]
        for s in sectors
    }
    return {"sectors": sectors_list, "topicsBySector": topics_by_sector_list}


def _assessment_date_sydney_iso(dt: datetime) -> str:
    """Return assessment_date as ISO string in Australia/Sydney timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(ZoneInfo("Australia/Sydney")).isoformat()


def _compliance_result_filename(report_name: str, llm_model: Optional[str]) -> str:
    """Build filename field: report name + LLM model name + 'result', e.g. BMW2024ESG_GPT5.2result."""
    stem = (Path(report_name).stem if report_name else "report").strip()
    model = (llm_model or "LLM").strip()
    for c in '<>:"/\\|?*':
        model = model.replace(c, "_")
    return f"{stem}_{model}result"


# Compliance JSON uses the same metric shape as SASB exports; GRI/CDP/TCFD rows are normalized to match.
_SASB_EXPORT_METRIC_TYPE = "Sustainability Disclosure Topics & Metrics"


def _metric_row_from_disclosure_analysis(analysis: DisclosureAnalysis) -> dict:
    """Canonical per-metric object for compliance JSON (SASB key order)."""
    disclosure_status = (
        analysis.disclosure_status.value
        if hasattr(analysis.disclosure_status, "value")
        else analysis.disclosure_status
    )
    page = getattr(analysis, "page", None)
    value = getattr(analysis, "value", None)
    context = getattr(analysis, "context", None)
    reasoning = analysis.reasoning
    definition = getattr(analysis, "definition", "") or ""
    category = getattr(analysis, "category", "") or ""
    unit = getattr(analysis, "unit", "") or ""
    topic = getattr(analysis, "topic", "") or ""
    type_name = getattr(analysis, "type", "") or ""
    metric_code = getattr(analysis, "metric_code", "") or getattr(analysis, "metric_id", "") or ""
    metric_name = getattr(analysis, "metric_name", "") or ""

    return {
        "metric_id": analysis.metric_id,
        "metric_name": metric_name,
        "metric_code": metric_code,
        "disclosure_status": disclosure_status,
        "reasoning": reasoning,
        "unit": unit,
        "category": category,
        "topic": topic,
        "type": type_name,
        "definition": definition,
        "page": page,
        "value": value,
        "context": context,
        "Metric": metric_name,
        "Category": category,
        "Unit": unit,
        "Code": metric_code,
        "Topic": topic,
        "Type": type_name,
        "Definition": definition,
        "Value": value,
        "Page": page,
        "Context": context,
        "Disclosure Status": disclosure_status,
        "LLM Analysis": reasoning,
        "evidence_segments": list(getattr(analysis, "evidence_segments", None) or []),
        "improvement_suggestions": list(
            getattr(analysis, "improvement_suggestions", None) or []
        ),
    }


def _sync_metric_alias_fields(m: dict) -> None:
    """Keep canonical/export keys and legacy keys in sync from one source of truth."""
    metric = str(m.get("Metric") or m.get("metric_name") or m.get("metric") or "").strip()
    category = str(m.get("Category") or m.get("category") or "").strip()
    unit = str(m.get("Unit") or m.get("unit") or "").strip()
    code = str(m.get("Code") or m.get("metric_code") or m.get("metric_id") or m.get("code") or "").strip()
    topic = str(m.get("Topic") or m.get("topic") or "").strip()
    typ = str(m.get("Type") or m.get("type") or "").strip()
    definition = str(m.get("Definition") or m.get("definition") or "").strip()
    value = m.get("Value") if "Value" in m else m.get("value")
    page = m.get("Page") if "Page" in m else m.get("page")
    context = m.get("Context") if "Context" in m else m.get("context")
    disclosure_status = m.get("Disclosure Status") or m.get("disclosure_status") or m.get("Model Disclosure Status") or ""
    llm_analysis = m.get("LLM Analysis") or m.get("reasoning") or m.get("Reasoning") or m.get("Analysis") or ""

    m["Metric"] = metric
    m["Category"] = category
    m["Unit"] = unit
    m["Code"] = code
    m["Topic"] = topic
    m["Type"] = typ
    m["Definition"] = definition
    m["Value"] = value
    m["Page"] = page
    m["Context"] = context
    m["Disclosure Status"] = disclosure_status
    m["LLM Analysis"] = llm_analysis
    _sync_metric_alias_fields(m)


def _normalize_non_sasb_compliance_metric_row(
    m: dict, framework: Optional[str]
) -> None:
    """Preserve framework fields for GRI/CDP/TCFD while ensuring stable output keys."""
    fw = (framework or "").strip().upper()
    if fw not in ("GRI", "CDP", "TCFD"):
        return

    metric = str(m.get("Metric") or m.get("metric_name") or "").strip()
    category = str(m.get("Category") or m.get("category") or "").strip()
    unit = str(m.get("Unit") or m.get("unit") or "").strip()
    code = str(m.get("Code") or m.get("metric_code") or m.get("metric_id") or "").strip()
    topic = str(m.get("Topic") or m.get("topic") or "").strip()
    typ = str(m.get("Type") or m.get("type") or "").strip()
    definition = str(m.get("Definition") or m.get("definition") or "").strip()
    value = m.get("Value") if "Value" in m else m.get("value")
    page = m.get("Page") if "Page" in m else m.get("page")
    context = m.get("Context") if "Context" in m else m.get("context")
    disclosure_status = m.get("Disclosure Status") or m.get("disclosure_status") or m.get("Model Disclosure Status") or ""
    llm_analysis = m.get("LLM Analysis") or m.get("reasoning") or ""

    if not category:
        category = "Quantitative" if unit else "Discussion and Analysis"
    if context is None:
        context = ""

    m["Metric"] = metric
    m["Category"] = category
    m["Unit"] = unit
    m["Code"] = code
    m["Topic"] = topic
    m["Type"] = typ
    m["Definition"] = definition
    m["Value"] = value
    m["Page"] = page
    m["Context"] = context
    m["Disclosure Status"] = disclosure_status
    m["LLM Analysis"] = llm_analysis
    _sync_metric_alias_fields(m)


def _apply_partial_disclosure_json_rules(metric_rows: Optional[List[dict]]) -> None:
    """Match upload/analyze pipelines: partial/not disclosed value and page handling."""
    for m in metric_rows or []:
        if not isinstance(m, dict):
            continue
        s = str(m.get("disclosure_status", "") or "").strip().lower()
        if "partial" in s:
            v = m.get("value")
            if not (isinstance(v, (int, float)) and not isinstance(v, bool)):
                m["value"] = COMPLIANCE_VALUE_NA
        elif "not" in s:
            m["page"] = None
            m["value"] = COMPLIANCE_VALUE_NA

        m["Value"] = m.get("value")
        m["Page"] = m.get("page")
        m["Context"] = m.get("context")
        m["Disclosure Status"] = m.get("disclosure_status")
        m["LLM Analysis"] = m.get("reasoning")


def _build_compliance_assessment_json(
    assessment: ComplianceAssessment,
    report_path_str: str,
    result_filename: str,
) -> dict:
    """Root + metric_analyses in the same shape as SASB compliance JSON."""
    fw = getattr(assessment, "framework", None)
    metric_rows = [
        _metric_row_from_disclosure_analysis(a) for a in assessment.metric_analyses
    ]
    for row in metric_rows:
        _normalize_non_sasb_compliance_metric_row(row, fw)
    _apply_partial_disclosure_json_rules(metric_rows)
    return {
        "report_id": assessment.report_id,
        "assessment_date": _assessment_date_sydney_iso(assessment.assessment_date),
        "filename": result_filename,
        "total_metrics": assessment.total_metrics_analyzed,
        "overall_score": assessment.overall_compliance_score,
        "total_metrics_analyzed": assessment.total_metrics_analyzed,
        "overall_compliance_score": assessment.overall_compliance_score,
        "report_file_path": report_path_str,
        "framework": fw,
        "disclosure_summary": {
            "fully_disclosed": assessment.disclosure_summary.get(
                DisclosureStatus.FULLY_DISCLOSED, 0
            ),
            "partially_disclosed": assessment.disclosure_summary.get(
                DisclosureStatus.PARTIALLY_DISCLOSED, 0
            ),
            "not_disclosed": assessment.disclosure_summary.get(
                DisclosureStatus.NOT_DISCLOSED, 0
            ),
        },
        "metric_analyses": metric_rows,
    }


def _resolve_compliance_json_path(
    assessment_dir: Path,
    legacy_dir: Path,
    file_id: str,
    stem: Optional[str] = None,
) -> Optional[Path]:
    """Locate compliance JSON for a file_id (exact names first, then glob for Subindustry_fileid_compliance.json)."""
    jp = _json_path_from_manifest(assessment_dir, file_id, None)
    if jp is not None and jp.is_file():
        return jp
    candidates: list[Path] = [
        assessment_dir / f"{file_id}_compliance.json",
        legacy_dir / f"{file_id}_compliance.json",
    ]
    if stem:
        candidates.append(assessment_dir / f"{stem}_compliance.json")
        candidates.append(legacy_dir / f"{stem}_compliance.json")
    for p in candidates:
        if p.exists():
            return p
    for d in (assessment_dir, legacy_dir):
        if not d.exists():
            continue
        for p in d.glob(f"*{file_id}*_compliance.json"):
            if p.is_file():
                return p
    return None


@app.get("/")
async def root():
    """API root path"""
    return {
        "message": "ESG Analysis System API",
        "version": "1.0.0",
        "endpoints": {
            "upload_report": "/api/upload-report",
            "upload_metrics": "/api/upload-metrics",
            "analyze_compliance": "/api/analyze-compliance",
            "chat": "/api/chat",
            "get_assessment": "/api/assessment",
            "get_session_history": "/api/chat/history/{session_id}"
        }
    }


# Authentication routes
@app.post("/auth/register", response_model=AuthResponse)
async def register_endpoint(request: RegisterRequest):
    """
    Register a new user
    
    Args:
        request: Registration request with email, password, and name
        
    Returns:
        AuthResponse with token and userId
    """
    result = await register(request.email, request.password, request.name)
    return AuthResponse(**result)


@app.post("/auth/login", response_model=AuthResponse)
async def login_endpoint(request: LoginRequest):
    """
    Login user
    
    Args:
        request: Login request with email and password
        
    Returns:
        AuthResponse with token and userId
    """
    result = await login(request.email, request.password)
    return AuthResponse(**result)


def _sync_upload_report_body(
    content: bytes,
    filename: str,
    industry: Optional[str],
    semiIndustry: Optional[str],
    framework: Optional[str],
    griSector: Optional[str],
    griTopic: Optional[str],
    scopeSlugs: Optional[str],
    clientUploadKey: Optional[str],
    user_id: int,
) -> dict:
    """PDF encode + assessment off the event loop (keeps /api/files responsive)."""
    try:
        logger.info("Saving file using file manager...")
        file_info = file_manager.save_uploaded_file(
            file_content=content,
            filename=filename,
            file_type="report",
            industry=industry,
            framework=framework,
            semi_industry=semiIndustry,
            gri_sector=griSector,
            gri_topic=griTopic,
            client_upload_key=clientUploadKey,
            user_id=user_id
        )
        logger.info(f"File saved at: {file_info['file_path']}")
        
        # Process PDF
        logger.info("Starting PDF processing...")
        encoder = system_components["report_encoder"]
        report_content = encoder.encode_pdf(file_info["file_path"], save_markdown=True)

        # IMPORTANT: Align document_id with file_id so all downstream (chat/cache/output filenames)
        # use a single stable identifier.
        try:
            report_content.document_id = file_info["file_id"]
            report_content.document_content.document_id = file_info["file_id"]
        except Exception:
            pass

        # Persist segments + embeddings for fast chat retrieval after restart.
        # (This is crucial for "load previous embeddings" requirement.)
        try:
            file_manager.save_report_artifacts(file_info["file_id"], report_content)
        except Exception as e:
            logger.warning(f"Failed to persist report artifacts for {file_info['file_id']}: {e}")
        logger.info("PDF processing completed")
        
        # Store processing results
        system_components["current_report"] = report_content
        logger.info("Report content stored in system components")
        
        # Store framework and industry / GRI information
        system_components["current_framework"] = framework
        system_components["current_industry"] = industry
        system_components["current_semi_industry"] = semiIndustry
        system_components["current_gri_sector"] = griSector
        system_components["current_gri_topic"] = griTopic
        # Extract company name from filename (remove extension)
        company_name = filename.rsplit('.', 1)[0] if filename else "Unknown Company"
        system_components["current_company"] = company_name
        logger.info(f"Stored framework and industry info - Framework: {framework}, Industry: {industry}, Semi-Industry: {semiIndustry}, GRI: {griSector}/{griTopic}, Company: {company_name}")
        
        # Get report summary
        logger.info("Getting report summary...")
        summary = encoder.get_report_summary(report_content)
        logger.info("Report summary obtained")
        
        # Build scope list: one retrieval + assessment per slug; single PDF encode above.
        fw = (framework or "").strip()
        processor = system_components["metric_processor"]
        scopes_list: List[tuple[str, dict]] = []

        if fw == "GRI":
            topics = _parse_scope_slugs_json(scopeSlugs, griTopic)
            if not griSector or not str(griSector).strip() or not topics:
                raise ValueError(
                    "GRI sector and at least one topic are required. Use griTopic or scopeSlugs JSON array."
                )
            gs = str(griSector).strip()
            for t in topics:
                scopes_list.append((t, {"griSector": gs, "griTopic": t}))
        elif fw == "SASB":
            semis = _parse_scope_slugs_json(scopeSlugs, semiIndustry)
            if not semis:
                raise ValueError(
                    "SASB sub-industry is required. Use semiIndustry or scopeSlugs JSON array."
                )
            for s in semis:
                scopes_list.append((s, {"semiIndustry": s}))
        elif fw == "CDP":
            topics = _parse_scope_slugs_json(scopeSlugs, semiIndustry)
            if not topics:
                raise ValueError("CDP Topic is required. Use semiIndustry or scopeSlugs JSON array.")
            for t in topics:
                scopes_list.append((t, {"semiIndustry": t}))
        elif fw == "TCFD":
            topics = _parse_scope_slugs_json(scopeSlugs, semiIndustry)
            if not topics:
                raise ValueError("TCFD Topic is required. Use semiIndustry or scopeSlugs JSON array.")
            for t in topics:
                scopes_list.append((t, {"semiIndustry": t}))
        else:
            raise ValueError("Please select a framework (SASB, GRI, CDP, or TCFD) and the required options.")

        _, p0 = scopes_list[0]
        if fw == "GRI":
            system_components["current_gri_topic"] = p0["griTopic"]
            system_components["current_gri_sector"] = p0["griSector"]
            system_components["current_semi_industry"] = semiIndustry
        elif fw == "SASB":
            system_components["current_semi_industry"] = p0["semiIndustry"]
        elif fw in ("CDP", "TCFD"):
            system_components["current_semi_industry"] = p0["semiIndustry"]

        # Pre-build HippoRAG index once per document (not per scope).
        try:
            with _chatbot_ops_lock:
                retriever = getattr(system_components["chatbot"], "_hipporag_retriever", None)
                if retriever and getattr(retriever, "is_enabled", lambda: False)():
                    retriever.ensure_index(report_content.document_id, report_content)
        except Exception as e:
            logger.warning(f"HippoRAG pre-index failed for {file_info['file_id']}: {e}")

        dual_retriever = system_components["dual_retriever"]
        disclosure_engine = system_components["disclosure_engine"]
        json_report_dir = Path(file_manager.compliance_outputs)
        json_report_dir.mkdir(parents=True, exist_ok=True)
        config = system_components.get("config")
        llm_model_name = getattr(config, "llm_model", None) if config else None

        manifest_rows: List[dict] = []
        last_assessment = None
        last_report_path_str = ""
        expected_scope_keys = [s[0] for s in scopes_list]

        try:
            finfo_early = file_manager.metadata.get("files", {}).get(file_info["file_id"])
            if isinstance(finfo_early, dict):
                finfo_early["scope_slugs_json"] = json.dumps(
                    expected_scope_keys, ensure_ascii=False
                )
                file_manager._save_metadata()
        except Exception as e:
            logger.warning(f"Early scope_slugs_json patch failed: {e}")

        _write_compliance_manifest(
            json_report_dir,
            file_info["file_id"],
            fw,
            [],
            expected_scope_keys=expected_scope_keys,
        )

        try:
            for scope_key, params in scopes_list:
                if fw == "GRI":
                    metrics = processor.load_gri_metrics_by_sector_topic(
                        params["griSector"], params["griTopic"]
                    )
                    semi_for_disclosure = (
                        f"GRI {params['griSector']} {params['griTopic']}".strip()
                    )
                    sanitized_part = _sanitize_compliance_filename_part(
                        f"GRI_{params['griSector']}_{params['griTopic']}"
                    )
                elif fw == "SASB":
                    metrics = processor.load_sasb_metrics_by_industry(params["semiIndustry"])
                    semi_for_disclosure = params["semiIndustry"]
                    sanitized_part = _sanitize_compliance_filename_part(params["semiIndustry"])
                elif fw == "CDP":
                    metrics = processor.load_cdp_metrics_by_topic(params["semiIndustry"])
                    semi_for_disclosure = params["semiIndustry"] or "CDP"
                    sanitized_part = _sanitize_compliance_filename_part(
                        f"CDP_{params['semiIndustry']}"
                    )
                else:  # TCFD
                    metrics = processor.load_tcfd_metrics_by_topic(params["semiIndustry"])
                    semi_for_disclosure = params["semiIndustry"] or "TCFD"
                    sanitized_part = _sanitize_compliance_filename_part(
                        f"TCFD_{params['semiIndustry']}"
                    )

                metrics_cache_key = _metric_scope_cache_key(fw, params)
                processed_metrics = _ensure_processed_metric_collection(
                    processor, metrics, metrics_cache_key
                )
                system_components["current_metrics"] = processed_metrics
                logger.info(f"Loaded metrics for scope_key={scope_key} ({fw}) and enabled semantic expansions")

                retrieval_results = dual_retriever.retrieve_for_collection(
                    report_content, processed_metrics
                )
                t_start = time.time()
                assessment = disclosure_engine.analyze_compliance(
                    retrieval_results,
                    report_content,
                    file_info["file_path"],
                    metrics,
                    framework=framework,
                    industry=industry,
                    semi_industry=semi_for_disclosure,
                )
                logger.info(
                    f"Disclosure inference scope={scope_key} took {time.time() - t_start:.2f}s"
                )
                last_assessment = assessment

                compliance_report = disclosure_engine.generate_compliance_report(assessment)
                md_stem = _sanitize_compliance_filename_part(f"{sanitized_part}")
                report_path = (
                    Path(file_manager.markdown_outputs)
                    / f"compliance_report_{file_info['file_id']}_{md_stem}.md"
                )
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(compliance_report, encoding="utf-8")
                last_report_path_str = str(report_path)

                json_filename = f"{sanitized_part}_{file_info['file_id']}_compliance.json"
                json_report_path = json_report_dir / json_filename
                xlsx_report_path = json_report_dir / f"{sanitized_part}_{file_info['file_id']}_compliance.xlsx"

                assessment_json = _build_compliance_assessment_json(
                    assessment,
                    str(report_path),
                    _compliance_result_filename(filename or "", llm_model_name),
                )

                with open(json_report_path, "w", encoding="utf-8") as f:
                    json.dump(assessment_json, f, indent=2, ensure_ascii=False)

                df_flat = pd.json_normalize(
                    assessment_json,
                    record_path="metric_analyses",
                    meta=[
                        "report_id",
                        "assessment_date",
                        "filename",
                        "total_metrics",
                        "overall_score",
                        ["disclosure_summary", "fully_disclosed"],
                        ["disclosure_summary", "partially_disclosed"],
                        ["disclosure_summary", "not_disclosed"],
                    ],
                )
                def _pick_series(*names, default=""):
                    for name in names:
                        if name in df_flat.columns:
                            return df_flat[name]
                    return pd.Series([default] * len(df_flat))

                df_final = pd.DataFrame({
                    "Metric": _pick_series("Metric", "metric_name"),
                    "Category": _pick_series("Category", "category"),
                    "Unit": _pick_series("Unit", "unit"),
                    "Code": _pick_series("Code", "metric_code", "metric_id"),
                    "Topic": _pick_series("Topic", "topic"),
                    "Type": _pick_series("Type", "type"),
                    "Definition": _pick_series("Definition", "definition"),
                    "Value": _pick_series("Value", "value"),
                    "Page": _pick_series("Page", "page"),
                    "Context": _pick_series("Context", "context"),
                    "Disclosure Status": _pick_series("Disclosure Status", "disclosure_status", "Model Disclosure Status"),
                    "LLM Analysis": _pick_series("LLM Analysis", "reasoning"),
                    "ChatGPT": _pick_series("ChatGPT"),
                    "InputWrong": _pick_series("InputWrong"),
                    "comment": _pick_series("comment"),
                })
                df_final.to_excel(xlsx_report_path, index=False, sheet_name="Benchmark")

                manifest_rows.append(
                    {
                        "scope_key": scope_key,
                        "json_filename": json_filename,
                        "overall_score": float(assessment.overall_compliance_score or 0.0),
                    }
                )
                _write_compliance_manifest(
                    json_report_dir,
                    file_info["file_id"],
                    fw,
                    manifest_rows,
                    expected_scope_keys=expected_scope_keys,
                )

            system_components["current_assessment"] = last_assessment
            if last_assessment:
                with _chatbot_ops_lock:
                    system_components["chatbot"].load_context(report_content, last_assessment)

            # Persist primary scope on file record for listings / cross-analysis defaults
            try:
                finfo = file_manager.metadata.get("files", {}).get(file_info["file_id"])
                if isinstance(finfo, dict):
                    finfo["scope_slugs_json"] = json.dumps([s[0] for s in scopes_list], ensure_ascii=False)
                    if fw == "GRI":
                        finfo["gri_topic"] = scopes_list[0][0]
                        finfo["gri_sector"] = scopes_list[0][1]["griSector"]
                        finfo["semi_industry"] = None
                    elif fw == "SASB":
                        finfo["semi_industry"] = scopes_list[0][0]
                    elif fw in ("CDP", "TCFD"):
                        finfo["semi_industry"] = scopes_list[0][0]
                    file_manager._save_metadata()
            except Exception as e:
                logger.warning(f"Failed to patch file metadata with multi-scope info: {e}")

            file_manager.move_report_file(file_info["file_id"], "processed")
            if last_assessment:
                logger.info(
                    f"Complete processing chain finished ({len(scopes_list)} scope(s)). "
                    f"Last score: {last_assessment.overall_compliance_score:.2%}"
                )

            return {
                "status": "success",
                "message": "Report uploaded and fully processed",
                "report_id": report_content.document_id,
                "file_id": file_info["file_id"],
                "summary": summary,
                "scopes": manifest_rows,
                "assessment": {
                    "total_metrics": last_assessment.total_metrics_analyzed if last_assessment else 0,
                    "overall_score": last_assessment.overall_compliance_score if last_assessment else 0,
                    "disclosure_summary": last_assessment.disclosure_summary if last_assessment else {},
                    "report_path": last_report_path_str,
                },
            }

        except Exception as assessment_error:
            error_str = str(assessment_error)
            logger.error(f"Error in assessment processing: {assessment_error}")

            try:
                _write_compliance_manifest(
                    json_report_dir,
                    file_info["file_id"],
                    fw,
                    manifest_rows,
                    expected_scope_keys=expected_scope_keys,
                )
            except Exception as me:
                logger.warning(f"Failed to write partial compliance manifest: {me}")

            is_llm_error = "403" in error_str or "AccessDenied" in error_str or "Unpurchased" in error_str or "LLM" in error_str

            file_manager.move_report_file(file_info["file_id"], "processed")

            error_message = "Report processed but assessment failed"
            if is_llm_error:
                error_message = (
                    "分析失败：LLM模型访问被拒绝。请检查 `backend/config/.env` 文件中的 `LLM_MODEL` 配置，"
                    "确保使用可访问的模型（如 'qwen-plus' 或 'qwen-turbo'）。"
                )

            return {
                "status": "partial_success",
                "message": error_message,
                "report_id": report_content.document_id,
                "file_id": file_info["file_id"],
                "summary": summary,
                "error": str(assessment_error),
                "error_type": "llm_access_denied" if is_llm_error else "unknown"
            }

    except Exception as e:
        logger.error(f"Error processing report: {e}")
        # If processing fails, move to failed directory
        if 'file_info' in locals():
            file_manager.move_report_file(file_info["file_id"], "failed")
        raise


@app.post("/api/upload-report")
async def upload_report(
    file: UploadFile = File(...),
    industry: Optional[str] = Form(None),
    semiIndustry: Optional[str] = Form(None),
    framework: Optional[str] = Form(None),
    griSector: Optional[str] = Form(None),
    griTopic: Optional[str] = Form(None),
    scopeSlugs: Optional[str] = Form(None),
    clientUploadKey: Optional[str] = Form(None),
    user_id: int = Depends(get_current_user)
):
    """
    Upload and process ESG report
    
    Args:
        file: PDF file
        industry: Main industry classification (optional, for SASB)
        semiIndustry: Sub-industry (for SASB metrics selection)
        framework: Framework selection (SASB/GRI/TCFD)
        griSector: GRI sector slug (when framework=GRI)
        griTopic: GRI topic slug (when framework=GRI); if scopeSlugs is set, this is optional fallback for a single topic
        scopeSlugs: Optional JSON array of scope slugs (GRI topic slugs, SASB semi-industries, CDP/TCFD topic slugs).
            One PDF encode; one retrieval+assessment per slug; separate *_compliance.json per scope.
        
    Returns:
        Processing results, including complete processing chain output (report processing + metrics matching + classification + knowledge base update)

    Note:
        Heavy work runs in a thread pool (`asyncio.to_thread`) so the event loop can still
        serve GET /api/files and other requests while analysis runs. HippoRAG ``ensure_index`` and
        final ``chatbot.load_context`` use ``_chatbot_ops_lock`` so opening Chat does not race
        the shared ``ESGChatbot`` instance. Concurrent uploads still share global ``system_components``
        (last write wins); use one analysis at a time for stable chat context.
    """
    # ===== DEBUG: Function called =====
    logger.info(f"=== UPLOAD_REPORT ENDPOINT CALLED ===")
    logger.info(f"File: {file.filename}")
    logger.info(f"Framework: {framework}")
    logger.info(f"Industry: {industry}")
    logger.info(f"SemiIndustry: {semiIndustry}")
    logger.info(f"GRI Sector: {griSector}, GRI Topic: {griTopic}")
    logger.info(f"scopeSlugs: {scopeSlugs}")
    logger.info(f"=== END DEBUG ===")
    
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    try:
        logger.info("=== STARTING FILE PROCESSING ===")
        content = await file.read()
        logger.info(f"File content read successfully, size: {len(content)} bytes")
        return await asyncio.to_thread(
            _sync_upload_report_body,
            content,
            file.filename or "",
            industry,
            semiIndustry,
            framework,
            griSector,
            griTopic,
            scopeSlugs,
            clientUploadKey,
            user_id,
        )
    except Exception as e:
        logger.error(f"Error processing report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/upload-metrics")
async def upload_metrics(
    file: Optional[UploadFile] = File(None),
    metrics_json: Optional[str] = Form(None),
    user_id: int = Depends(get_current_user)
):
    """
    上传ESG指标（支持Excel文件或JSON）
    
    Args:
        file: Excel文件（可选）
        metrics_json: JSON格式的指标数据（可选）
        
    Returns:
        处理结果
    """
    try:
        processor = system_components["metric_processor"]
        file_info = None
        
        if file:
            # 处理Excel文件
            if not file.filename.endswith(('.xlsx', '.xls')):
                raise HTTPException(status_code=400, detail="Only Excel files are supported")
            
            # 读取文件内容
            content = await file.read()
            
            # 使用文件管理器保存文件
            file_info = file_manager.save_uploaded_file(
                file_content=content,
                filename=file.filename,
                file_type="metrics",
                user_id=user_id
            )
            
            # 从Excel加载指标
            metrics = processor.load_metrics_from_excel(file_info["file_path"])
            
        elif metrics_json:
            # 从JSON加载指标
            metrics_data = json.loads(metrics_json)
            metrics = MetricCollection(**metrics_data)
            
            # 保存JSON到文件系统
            json_content = metrics_json.encode('utf-8')
            file_info = file_manager.save_uploaded_file(
                file_content=json_content,
                filename="uploaded_metrics.json",
                file_type="metrics",
                user_id=user_id
            )
            
        else:
            # Metrics file is required
            raise HTTPException(status_code=400, detail="Metrics file (Excel or JSON) is required. Please upload a metrics file.")
        
        # 处理指标（语义扩展） - LLM is required
        processed_metrics = processor.process_metric_collection(metrics)
        
        # 存储处理结果
        system_components["current_metrics"] = processed_metrics
        
        logger.info(f"Successfully processed {len(processed_metrics.metrics)} metrics")
        
        result = {
            "status": "success",
            "message": f"Processed {len(processed_metrics.metrics)} metrics",
            "collection_id": processed_metrics.collection_id,
            "metrics_count": len(processed_metrics.metrics)
        }
        
        if file_info:
            result["file_id"] = file_info["file_id"]
        
        # Add report summary information if available
        if system_components["current_report"]:
            summary = encoder.get_report_summary(system_components["current_report"])
            result["total_pages"] = summary.get("total_pages", 0)
            result["total_segments"] = summary.get("total_segments", 0)
        
        return result
        
    except Exception as e:
        logger.error(f"Error processing metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/analyze-compliance")
async def analyze_compliance():
    """
    执行合规分析
    
    Returns:
        合规评估结果
    """
    # 检查是否已加载报告和指标
    if not system_components["current_report"]:
        raise HTTPException(status_code=400, detail="No report loaded. Please upload a report first.")
    
    if not system_components["current_metrics"]:
        raise HTTPException(status_code=400, detail="No metrics loaded. Please upload metrics first.")
    
    try:
        # 执行双通道检索
        dual_retriever = system_components["dual_retriever"]
        metric_processor = system_components["metric_processor"]
        current_metrics = system_components["current_metrics"]
        if current_metrics and not getattr(current_metrics, "semantic_expansions", None):
            cache_key = f"runtime::{getattr(current_metrics, 'collection_name', 'metrics')}"
            current_metrics = _ensure_processed_metric_collection(metric_processor, current_metrics, cache_key)
            system_components["current_metrics"] = current_metrics
        retrieval_results = dual_retriever.retrieve_for_collection(
            system_components["current_report"],
            current_metrics
        )
        
        # 执行披露推理
        disclosure_engine = system_components["disclosure_engine"]
        fw = system_components.get("current_framework")
        semi_label = system_components.get("current_semi_industry")
        if fw == "GRI":
            gs, gt = system_components.get("current_gri_sector"), system_components.get("current_gri_topic")
            semi_label = f"GRI {gs or ''} {gt or ''}".strip() or "GRI"
        assessment = disclosure_engine.analyze_compliance(
            retrieval_results,
            system_components["current_report"],
            system_components["current_report"].document_content.file_path,
            system_components["current_metrics"],  # 传入所有指标
            framework=fw,
            industry=system_components.get("current_industry"),
            semi_industry=semi_label
        )
        
        # 存储评估结果
        system_components["current_assessment"] = assessment
        
        # 更新聊天机器人上下文
        with _chatbot_ops_lock:
            system_components["chatbot"].load_context(
                system_components["current_report"],
                assessment
            )

        # 生成合规报告
        compliance_report = disclosure_engine.generate_compliance_report(assessment)
        
        # 保存报告（canonical location: uploads/outputs/markdown/）
        report_path = Path(file_manager.markdown_outputs) / f"compliance_report_{assessment.report_id}.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(compliance_report, encoding="utf-8")
        
        # 保存JSON评估数据供前端使用（canonical: uploads/outputs/compliance_reports/）
        json_report_dir = Path(file_manager.compliance_outputs)
        json_report_dir.mkdir(parents=True, exist_ok=True)

        # Best-effort resolve file_id from current report path
        file_id = None
        try:
            current_path = str(system_components["current_report"].document_content.file_path)
            for fid, info in file_manager.metadata.get("files", {}).items():
                if info.get("file_path") == current_path:
                    file_id = fid
                    break
        except Exception:
            file_id = None
        if not file_id:
            file_id = str(getattr(assessment, "report_id", "unknown"))
        fw = system_components.get("current_framework")
        gs, gt = None, None
        if fw == "GRI":
            gs, gt = system_components.get("current_gri_sector"), system_components.get("current_gri_topic")
            sanitized_subindustry = _sanitize_compliance_filename_part(f"GRI_{gs or ''}_{gt or ''}" if (gs or gt) else "GRI_report")
        else:
            sanitized_subindustry = _sanitize_compliance_filename_part(system_components.get("current_semi_industry") or "report")
        json_report_path = json_report_dir / f"{sanitized_subindustry}_{file_id}_compliance.json"

        # 将评估数据转换为JSON格式（assessment_date 悉尼时间，filename = 报告名 + LLM 模型名）
        file_meta = file_manager.metadata.get("files", {}).get(file_id, {})
        report_name = file_meta.get("original_name") or file_meta.get("safe_filename") or file_id
        config = system_components.get("config")
        llm_model_name = getattr(config, "llm_model", None) if config else None
        assessment_json = _build_compliance_assessment_json(
            assessment,
            str(report_path),
            _compliance_result_filename(report_name, llm_model_name),
        )

        with open(json_report_path, "w", encoding="utf-8") as f:
            json.dump(assessment_json, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Assessment JSON saved to: {json_report_path}")
        
        # Export results to Excel
        excel_path = None
        try:
            excel_exporter = ExcelExporter()
            
            # Prepare metric analyses for Excel export
            excel_metrics = []
            for analysis in assessment.metric_analyses:
                # Find corresponding metric for additional info
                metric_info = {}
                if system_components["current_metrics"]:
                    for metric in system_components["current_metrics"].metrics:
                        if metric.metric_id == analysis.metric_id or metric.metric_code == analysis.metric_code:
                            metric_info = {
                                "category": getattr(metric, 'sasb_category', analysis.category),
                                "unit": metric.unit or "",
                                "topic": getattr(metric, 'sasb_topic', ''),
                                "type": getattr(metric, 'sasb_type', ''),
                                "definition": getattr(metric, 'definition', '') or ''
                            }
                            break
                
                excel_metrics.append({
                    "metric_id": analysis.metric_code if hasattr(analysis, 'metric_code') else analysis.metric_id,
                    "metric_code": getattr(analysis, 'metric_code', '') or analysis.metric_id,
                    "metric_name": analysis.metric_name,
                    "disclosure_status": analysis.disclosure_status.value if hasattr(analysis.disclosure_status, 'value') else analysis.disclosure_status,
                    "reasoning": analysis.reasoning,
                    "value": getattr(analysis, 'value', None),
                    "page": getattr(analysis, 'page', None),
                    "context": getattr(analysis, 'context', None),
                    "category": metric_info.get('category', getattr(analysis, 'category', '')),
                    "unit": metric_info.get('unit', getattr(analysis, 'unit', '')),
                    "topic": metric_info.get('topic', getattr(analysis, 'topic', '')),
                    "type": metric_info.get('type', getattr(analysis, 'type', '')),
                    "definition": metric_info.get('definition', getattr(analysis, 'definition', '')),
                })
            
            # Validate required metadata exists before export
            if not system_components.get("current_industry") or not system_components.get("current_semi_industry"):
                raise ValueError("Industry information missing. Cannot export Excel report.")

            excel_path = excel_exporter.export_analysis_results(
                metric_analyses=excel_metrics,
                industry=system_components["current_industry"],
                semi_industry=system_components["current_semi_industry"],
                company_name=system_components.get("current_company", "Unknown Company"),
                report_id=assessment.report_id
            )
            logger.info(f"Excel report exported to: {excel_path}")
        except Exception as e:
            logger.error(f"Error exporting to Excel: {e}")
            # Don't fail the whole request if Excel export fails
        
        logger.info(f"Compliance analysis completed. Score: {assessment.overall_compliance_score:.2%}")
        
        result = {
            "status": "success",
            "assessment": {
                "report_id": assessment.report_id,
                "total_metrics": assessment.total_metrics_analyzed,
                "overall_score": assessment.overall_compliance_score,
                "disclosure_summary": assessment.disclosure_summary,
                "report_path": str(report_path)
            }
        }
        
        # Add Excel path if export was successful
        if excel_path:
            result["assessment"]["excel_path"] = str(excel_path)
        
        return result
        
    except Exception as e:
        logger.error(f"Error in compliance analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ======== DEBUG BEGINS ========

def _get_chat_history_path(file_id: str) -> Path:
    """Get path for chat history JSON file"""
    backend_dir = Path(__file__).parent.parent.parent
    history_dir = backend_dir / "outputs" / "chat_histories"
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir / f"{file_id}_chat.json"

def _load_chat_history(file_id: str) -> list:
    """Load chat history from disk"""
    history_path = _get_chat_history_path(file_id)
    if history_path.exists():
        try:
            with open(history_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading chat history: {e}")
    return []

def _save_chat_history(file_id: str, history: list):
    """Save chat history to disk"""
    history_path = _get_chat_history_path(file_id)
    try:
        with open(history_path, 'w', encoding='utf-8') as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving chat history: {e}")

def _load_specific_report_context(file_id: str):
    """Load report content (extracted markdown) and compliance assessment for a given file_id.

    - Robust to process restarts (in-memory context is empty)
    - Backward compatible with older *_compliance.json schemas
    - Best-effort: chat can still work with assessment only
    """
    report_content_obj = None
    assessment_obj = None

    # Resolve file metadata early (needed for robust path resolution)
    file_info = None
    try:
        file_info = file_manager.get_file_info(file_id)
    except Exception:
        file_info = None

    safe_filename = str((file_info or {}).get("safe_filename") or "")
    stem = Path(safe_filename).stem if safe_filename else ""

    # Fast path: reuse in-memory report if it appears to belong to this file.
    # NOTE: ReportContent.document_id is not guaranteed to equal file_id (it can be doc_<stem>_<hash>),
    # so we also match by stem.
    try:
        cr = system_components.get("current_report")
        if cr is not None:
            docid = str(getattr(cr, "document_id", "") or "")
            if docid == file_id or (stem and stem in docid):
                report_content_obj = cr
    except Exception:
        pass

    # Canonical output roots (consistent with FileManager)
    reports_root = Path(file_manager.reports_dir)
    assessment_dir = Path(file_manager.compliance_outputs)
    markdown_outputs_dir = Path(file_manager.markdown_outputs)
    legacy_outputs_dir = Path(__file__).resolve().parents[2] / "outputs"

    def _parse_status(v) -> DisclosureStatus:
        if v is None:
            return DisclosureStatus.NOT_DISCLOSED
        s = str(v).strip().lower()
        s = s.replace("-", "_").replace(" ", "_")
        if s in {"fully_disclosed", "fully", "full", "complete", "disclosed"} or "fully" in s:
            return DisclosureStatus.FULLY_DISCLOSED
        if s in {"partially_disclosed", "partial", "partly"} or "partial" in s:
            return DisclosureStatus.PARTIALLY_DISCLOSED
        return DisclosureStatus.NOT_DISCLOSED

    def _summary_from_metrics(metrics):
        summary = {
            DisclosureStatus.FULLY_DISCLOSED: 0,
            DisclosureStatus.PARTIALLY_DISCLOSED: 0,
            DisclosureStatus.NOT_DISCLOSED: 0,
        }
        for m in metrics:
            try:
                summary[m.disclosure_status] = summary.get(m.disclosure_status, 0) + 1
            except Exception:
                pass
        return summary

    # 1) Load compliance assessment JSON for this file_id (supports Subindustry_fileid_compliance.json naming)
    assessment_json_path = _resolve_compliance_json_path(
        assessment_dir, legacy_outputs_dir, file_id, stem
    )

    if assessment_json_path is not None and assessment_json_path.exists():
        try:
            assessment_data = json.loads(assessment_json_path.read_text(encoding="utf-8"))

            # metric_analyses (preferred key)
            raw_metrics = assessment_data.get("metric_analyses") or []
            metric_analyses = []
            for item in raw_metrics:
                if not isinstance(item, dict):
                    logger.warning(f"Invalid metric analysis item (not dict) in {assessment_json_path}: {type(item)}")
                    continue
                try:
                    d = dict(item)
                    # Support legacy and canonical status keys
                    status_raw = (
                        d.get("disclosure_status")
                        or d.get("Disclosure Status")
                        or d.get("Model Disclosure Status")
                        or d.get("status")
                    )
                    d["disclosure_status"] = _parse_status(status_raw)
                    d.pop("status", None)
                    # Build from explicit kwargs to avoid KeyError for missing "type"/"category"/"topic"
                    metric_analyses.append(DisclosureAnalysis(
                        metric_id=d.get("metric_id", d.get("metric_code", d.get("Code", ""))),
                        metric_name=d.get("metric_name", d.get("Metric", "")),
                        metric_code=d.get("metric_code", d.get("Code", d.get("metric_id", ""))),
                        disclosure_status=d["disclosure_status"],
                        reasoning=d.get("reasoning", d.get("LLM Analysis", d.get("Reasoning", d.get("Analysis", "")))),
                        evidence_segments=d.get("evidence_segments", []) or [],
                        improvement_suggestions=d.get("improvement_suggestions", []) or [],
                        category=d.get("category", d.get("Category", "")),
                        topic=d.get("topic", d.get("Topic", "")),
                        unit=d.get("unit", d.get("Unit", "")) or "",
                        type=d.get("type", d.get("Type", "")),
                        definition=d.get("definition", d.get("Definition", "")),
                        value=d.get("value", d.get("Value")),
                        context=d.get("context", d.get("Context")),
                        page=d.get("page", d.get("Page")),
                    ))
                except Exception as e:
                    logger.warning(f"Invalid metric analysis item in {assessment_json_path}: {e}")
                    continue

            # Backward compatible keys
            total_metrics = (
                assessment_data.get("total_metrics_analyzed")
                or assessment_data.get("total_metrics")
                or len(metric_analyses)
            )
            overall_score = (
                assessment_data.get("overall_compliance_score")
                or assessment_data.get("overall_score")
                or 0.0
            )
            framework = assessment_data.get("framework") or "SASB"

            # disclosure_summary: accept either enum-keyed, string-keyed, or missing
            summary_raw = assessment_data.get("disclosure_summary")
            disclosure_summary = None
            if isinstance(summary_raw, dict) and summary_raw:
                tmp = {}
                for k, v in summary_raw.items():
                    try:
                        tmp[_parse_status(k)] = int(v)
                    except Exception:
                        continue
                if tmp:
                    disclosure_summary = tmp
            if not disclosure_summary:
                disclosure_summary = _summary_from_metrics(metric_analyses)

            # report_file_path
            report_file_path = assessment_data.get("report_file_path")
            if not report_file_path and isinstance(file_info, dict):
                report_file_path = file_info.get("file_path")
            report_file_path = str(report_file_path or "")

            assessment_obj = ComplianceAssessment(
                report_id=assessment_data.get("report_id") or file_id,
                framework=framework,
                total_metrics_analyzed=int(total_metrics or 0),
                overall_compliance_score=float(overall_score or 0.0),
                disclosure_summary=disclosure_summary,
                metric_analyses=metric_analyses,
                report_file_path=report_file_path,
            )
            logger.info(f"Loaded specific assessment for {file_id}: {assessment_obj.total_metrics_analyzed} metrics")
        except Exception as e:
            logger.warning(f"Failed to load assessment JSON for {file_id}: {e}")

    # 2) Load report content + embeddings for chat retrieval
    # Priority:
    #   (a) persisted artifacts (segments + embeddings matrix)  -> fastest / best quality
    #   (b) extracted markdown -> parse into segments -> compute embeddings once -> persist
    #   (c) assessment-only
    try:
        # (a) artifacts
        if report_content_obj is None:
            art = file_manager.load_report_artifacts(file_id)
            if art:
                pdf_path_str = (file_info or {}).get("file_path") if isinstance(file_info, dict) else ""
                segments = art.get("segments") or []
                markdown_text = "\n\n".join([getattr(s, "content", "") for s in segments])
                document_content = DocumentContent(
                    document_id=file_id,
                    file_path=str(pdf_path_str or ""),
                    segments=segments,
                    markdown_content=markdown_text,
                )
                report_content_obj = ReportContent(
                    document_id=file_id,
                    document_content=document_content,
                    embeddings=[],
                )
                # Attach fast retrieval cache (avoid converting large matrix to python lists)
                setattr(report_content_obj, "_embedding_matrix", art.get("embedding_matrix"))
                setattr(report_content_obj, "_embedding_segment_ids", art.get("embedding_segment_ids"))
                logger.info(f"Loaded persisted segments+embeddings for {file_id} from {art.get('embeddings_path')}")

        # (b) no artifacts: load markdown -> parse -> compute embeddings -> persist
        if report_content_obj is None:
            pdf_path_str = (file_info or {}).get("file_path") if isinstance(file_info, dict) else None
            candidates = []

            if pdf_path_str:
                pdf_path = Path(pdf_path_str)
                stem = pdf_path.stem
                candidates.append(pdf_path.parent / f"{stem}_extracted.md")
                candidates.append(pdf_path.parent / f"{stem}.md")
                candidates.append(reports_root / "pending" / f"{stem}_extracted.md")
                candidates.append(reports_root / "processed" / f"{stem}_extracted.md")
                candidates.append(reports_root / "failed" / f"{stem}_extracted.md")
            candidates.append(markdown_outputs_dir / f"{file_id}.md")
            if legacy_outputs_dir.exists():
                candidates.append(legacy_outputs_dir / "markdown" / f"{file_id}.md")

            markdown_text = None
            for p in candidates:
                if p and p.exists():
                    markdown_text = p.read_text(encoding="utf-8", errors="ignore")
                    break

            if markdown_text:
                import re
                seg_pat = re.compile(r"\*\*(?P<sid>[A-Za-z0-9_:-]+)\*\*\s*\n\n(?P<body>.*?)(?:\n\n---\n|\Z)", re.DOTALL)
                segments: List[TextSegment] = []
                for m in seg_pat.finditer(markdown_text):
                    sid = m.group("sid").strip()
                    body = (m.group("body") or "").strip()
                    if not body:
                        continue
                    # Best-effort page from "P###" prefix
                    page = 1
                    mm = re.match(r"P(\d{3})_", sid)
                    if mm:
                        try:
                            page = int(mm.group(1))
                        except Exception:
                            page = 1
                    segments.append(TextSegment(segment_id=sid, content=body, page_number=page, position_y=0.0))

                if not segments:
                    # fallback: one big segment
                    segments = [TextSegment(segment_id=f"{file_id}:md", content=markdown_text, page_number=1, position_y=0.0)]

                document_content = DocumentContent(
                    document_id=file_id,
                    file_path=str(pdf_path_str or ""),
                    segments=segments,
                    markdown_content=markdown_text,
                )

                # Compute embeddings once (sync) then persist. This guarantees semantic retrieval quality.
                try:
                    encoder = system_components.get("report_encoder")
                    if encoder is not None:
                        # embed_document() returns a ReportContent (NOT a list of SegmentEmbedding).
                        embedded_report = encoder.embedder.embed_document(document_content)

                        tmp_report = ReportContent(
                            document_id=file_id,
                            document_content=document_content,
                            embeddings=getattr(embedded_report, "embeddings", []),
                        )
                        file_manager.save_report_artifacts(file_id, tmp_report)
                        report_content_obj = tmp_report
                        # Also attach matrix cache for faster search
                        art2 = file_manager.load_report_artifacts(file_id)
                        if art2:
                            setattr(report_content_obj, "_embedding_matrix", art2.get("embedding_matrix"))
                            setattr(report_content_obj, "_embedding_segment_ids", art2.get("embedding_segment_ids"))
                        logger.info(f"Computed+persisted embeddings for {file_id} from extracted markdown")
                except Exception as e:
                    logger.warning(f"Failed to compute embeddings for {file_id} (will fallback to keyword): {e}")
                    report_content_obj = ReportContent(document_id=file_id, document_content=document_content, embeddings=[])

            else:
                logger.warning(f"No extracted markdown found for file_id={file_id}; searched {len(candidates)} locations")
    except Exception as e:
        logger.warning(f"Failed to load report content for file_id={file_id}: {e}")

    # Return order matches call sites: (assessment, report_content)
    return assessment_obj, report_content_obj



@app.get("/api/chat/{file_id}/history")
async def get_file_chat_history(file_id: str, user_id: int = Depends(get_current_user)):
    """
    Get persistent chat history for a specific file (只能访问自己的文件)
    """
    # 检查文件是否属于当前用户
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")
    
    history = _load_chat_history(file_id)
    return {
        "file_id": file_id,
        "messages": history
    }

@app.post("/api/chat/{file_id}")
async def chat_with_file(
    file_id: str, 
    request: ChatRequest,
    user_id: int = Depends(get_current_user)
):
    # 检查文件是否属于当前用户
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")
    
    chatbot = system_components["chatbot"]

    history_list = _load_chat_history(file_id)  # Returns List[Dict]
    assessment, report_content = _load_specific_report_context(file_id)

    with _chatbot_ops_lock:
        chatbot.load_context(report_content, assessment)
        chatbot.restore_session(session_id=file_id, history_data=history_list)
        request.session_id = file_id
        response = chatbot.chat(request)
        updated_history = chatbot.get_session_history_as_dict(file_id)

    _save_chat_history(file_id, updated_history)

    return response

@app.delete("/api/chat/{file_id}")
async def clear_file_chat(file_id: str, user_id: int = Depends(get_current_user)):
    """Clear chat history for a specific file (只能删除自己的文件)"""
    try:
        # 检查文件是否属于当前用户
        file_info = file_manager.get_file_info(file_id, user_id=user_id)
        if not file_info:
            raise HTTPException(status_code=404, detail="File not found or access denied")
        history_path = _get_chat_history_path(file_id)
        if history_path.exists():
            history_path.unlink() # Delete the file
            
        # Also clear from memory if this is the active file
        # ...
        
        return {"status": "success", "message": "Chat history cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        


# ======== DEBUG ENDS ========

def _load_latest_assessment_for_chat():
    """
    为聊天机器人加载最新的评估数据（从JSON文件）
    """
    try:
        # 获取最新的JSON评估数据（优先使用 uploads/outputs/compliance_reports/）
        canonical_dir = Path(file_manager.compliance_outputs)
        legacy_dir = Path(__file__).resolve().parents[2] / "outputs"  # legacy backend/outputs
        json_files = list(canonical_dir.glob("*_compliance.json"))
        if legacy_dir.exists():
            json_files.extend(list(legacy_dir.glob("*_compliance.json")))

        if not json_files:
            logger.warning("No assessment JSON files found")
            return None

        # 使用最新的JSON文件
        json_file = sorted(json_files, key=lambda x: x.stat().st_mtime)[-1]
        logger.info(f"Loading assessment from JSON: {json_file}")

        with open(json_file, 'r', encoding='utf-8') as f:
            assessment_data = json.load(f)

        # 从JSON重建ComplianceAssessment对象
        from .models import ComplianceAssessment, DisclosureAnalysis, DisclosureStatus

        # 创建metric analyses from JSON
        metric_analyses = []
        for item in assessment_data["metric_analyses"]:
            # Map string status to enum (support canonical and legacy keys)
            status_raw = (
                item.get("disclosure_status")
                or item.get("Disclosure Status")
                or item.get("Model Disclosure Status")
                or item.get("status")
            )
            try:
                status = _parse_status(status_raw)
            except Exception:
                status = DisclosureStatus.NOT_DISCLOSED

            analysis = DisclosureAnalysis(
                metric_id=item.get("metric_id", item.get("metric_code", item.get("Code", ""))),
                metric_name=item.get("metric_name", item.get("Metric", "")),
                metric_code=item.get("metric_code", item.get("Code", item.get("metric_id", ""))),
                disclosure_status=status,
                reasoning=item.get("reasoning", item.get("LLM Analysis", item.get("Reasoning", item.get("Analysis", "")))),
                evidence_segments=item.get("evidence_segments", []),
                improvement_suggestions=item.get("improvement_suggestions", []),
                category=item.get("category", item.get("Category", "")),
                topic=item.get("topic", item.get("Topic", "")),
                unit=item.get("unit", item.get("Unit", "")),
                type=item.get("type", item.get("Type", "")),
                definition=item.get("definition", item.get("Definition", "")),
                value=item.get("value", item.get("Value")),
                context=item.get("context", item.get("Context")),
                page=item.get("page", item.get("Page"))
            )
            metric_analyses.append(analysis)

        # 创建ComplianceAssessment对象 (使用.get()提供默认值以兼容旧JSON)
        assessment = ComplianceAssessment(
            report_id=assessment_data.get("report_id", "unknown"),
            total_metrics_analyzed=assessment_data.get("total_metrics_analyzed", len(metric_analyses)),
            overall_compliance_score=assessment_data.get("overall_compliance_score", 0.0),
            disclosure_summary=assessment_data.get("disclosure_summary", {}),
            metric_analyses=metric_analyses,
            report_file_path=assessment_data.get("report_file_path", "")
        )

        return assessment

    except Exception as e:
        logger.error(f"Failed to load assessment JSON for chat: {e}")
        return None

def _load_report_content_for_chat():
    """
    加载原始报告内容用于聊天检索
    """
    try:
        from .models import ReportContent, ReportSegment
        
        # 查找提取的markdown文件（通常随 PDF 一起存放在 uploads/reports/**）
        reports_dir = Path(file_manager.reports_dir)
        markdown_files = list(reports_dir.glob("**/*_extracted.md"))
        
        if not markdown_files:
            logger.warning("No extracted markdown files found for chat")
            return None
            
        # 使用最新的markdown文件
        markdown_file = sorted(markdown_files, key=lambda x: x.stat().st_mtime)[-1]
        logger.info(f"Loading report content from: {markdown_file}")
        
        with open(markdown_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 简单分段处理 - 按段落分割
        segments = []
        paragraphs = content.split('\n\n')
        
        for i, paragraph in enumerate(paragraphs):
            if paragraph.strip():
                segment = ReportSegment(
                    segment_id=f"seg_{i}",
                    content=paragraph.strip(),
                    page_number=1  # 简化处理
                )
                segments.append(segment)
        
        # 创建DocumentContent对象
        from .models import DocumentContent, SegmentEmbedding, TextSegment
        
        # 创建TextSegment列表
        text_segments = []
        for segment in segments[:500]:  # 限制段落数量
            text_segment = TextSegment(
                segment_id=segment.segment_id,
                content=segment.content,
                page_number=segment.page_number,
                position_y=getattr(segment, 'position_y', 0.0)  # 使用默认值兼容旧数据
            )
            text_segments.append(text_segment)
        
        document_content = DocumentContent(
            document_id=markdown_file.stem,
            file_path=str(markdown_file),
            segments=text_segments,
            markdown_content=content
        )
        
        # 创建空的嵌入列表（简化处理）
        embeddings = []
        
        # 创建ReportContent对象
        report_content = ReportContent(
            document_id=markdown_file.stem,
            document_content=document_content,
            embeddings=embeddings
        )
        
        logger.info(f"Loaded {len(report_content.document_content.segments)} segments for chat")
        return report_content
        
    except Exception as e:
        logger.error(f"Failed to load report content for chat: {e}")
        return None

def _create_enhanced_knowledge_base(assessment, report_content):
    """
    创建增强的知识库，结合评估结果和原始报告内容
    """
    try:
        from .models import ReportSegment
        
        if not assessment:
            return report_content
            
        # 创建评估结果的文档片段
        assessment_segments = []
        
        # 1. 总体评估信息
        summary_text = f"""
ESG合规评估总结:
- 报告ID: {assessment.report_id}
- 分析指标总数: {assessment.total_metrics_analyzed}
- 整体合规分数: {assessment.overall_compliance_score:.1%}
- 完全披露指标: {assessment.disclosure_summary.get('fully_disclosed', 0)}个
- 部分披露指标: {assessment.disclosure_summary.get('partially_disclosed', 0)}个  
- 未披露指标: {assessment.disclosure_summary.get('not_disclosed', 0)}个
"""
        
        summary_segment = ReportSegment(
            segment_id="assessment_summary",
            content=summary_text,
            page_number=0,
            embedding=None
        )
        assessment_segments.append(summary_segment)
        
        # 2. 具体指标分析
        if hasattr(assessment, 'metric_analyses') and assessment.metric_analyses:
            for i, analysis in enumerate(assessment.metric_analyses):
                # Validate required fields exist
                if not hasattr(analysis, 'metric_id') or not hasattr(analysis, 'metric_name'):
                    logger.warning(f"Skipping metric analysis {i} - missing required fields")
                    continue
                if not hasattr(analysis, 'disclosure_status') or not hasattr(analysis, 'reasoning'):
                    logger.warning(f"Skipping metric {analysis.metric_id} - missing disclosure_status or reasoning")
                    continue

                metric_text = f"""
指标分析 {i+1}:
- 指标ID: {analysis.metric_id}
- 指标名称: {analysis.metric_name}
- 披露状态: {analysis.disclosure_status}
- 分析理由: {analysis.reasoning}
"""

                metric_segment = ReportSegment(
                    segment_id=f"metric_analysis_{i}",
                    content=metric_text,
                    page_number=0,
                    embedding=None
                )
                assessment_segments.append(metric_segment)
        
        # 合并评估段落和原始报告段落
        from .models import ReportContent, DocumentContent, TextSegment
        
        if report_content:
            # 将assessment_segments转换为TextSegment
            text_segments = []
            for seg in assessment_segments:
                text_seg = TextSegment(
                    segment_id=seg.segment_id,
                    content=seg.content,
                    page_number=seg.page_number,
                    position_y=0.0
                )
                text_segments.append(text_seg)
            
            # 合并原始报告的segments
            if hasattr(report_content, 'document_content') and report_content.document_content:
                text_segments.extend(report_content.document_content.segments)
            
            # 创建DocumentContent
            original_file_path = ""
            original_markdown = ""
            if hasattr(report_content, 'document_content') and report_content.document_content:
                original_file_path = report_content.document_content.file_path
                original_markdown = getattr(report_content.document_content, 'markdown_content', '')
            
            document_content = DocumentContent(
                document_id=f"enhanced_{assessment.report_id}",
                file_path=original_file_path,
                segments=text_segments,
                markdown_content=original_markdown
            )
            
            # 创建ReportContent
            enhanced_content = ReportContent(
                document_id=f"enhanced_{assessment.report_id}",
                document_content=document_content,
                embeddings=report_content.embeddings if hasattr(report_content, 'embeddings') else []
            )
        else:
            # 只有评估数据，没有原始报告
            text_segments = []
            for seg in assessment_segments:
                text_seg = TextSegment(
                    segment_id=seg.segment_id,
                    content=seg.content,
                    page_number=seg.page_number,
                    position_y=0.0
                )
                text_segments.append(text_seg)
            
            document_content = DocumentContent(
                document_id=f"assessment_{assessment.report_id}",
                file_path="",
                segments=text_segments,
                markdown_content=""
            )
            
            enhanced_content = ReportContent(
                document_id=f"enhanced_{assessment.report_id}",
                document_content=document_content,
                embeddings=[]
            )
        
        logger.info(f"Created enhanced knowledge base with {len(assessment_segments)} assessment segments and {len(report_content.document_content.segments) if report_content and hasattr(report_content, 'document_content') and report_content.document_content else 0} report segments")
        return enhanced_content
        
    except Exception as e:
        logger.error(f"Failed to create enhanced knowledge base: {e}")
        return report_content

@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    处理聊天请求
    
    Args:
        request: 聊天请求
        
    Returns:
        ChatResponse: 聊天响应
    """
    try:
        chatbot = system_components["chatbot"]
        
        # 优先使用内存中的数据（如果存在）
        latest_assessment = system_components.get("current_assessment")
        report_content = system_components.get("current_report")
        
        logger.info(f"Chat request received. Memory state: assessment={latest_assessment is not None}, report={report_content is not None}")
        
        # 如果内存中没有数据，尝试从文件系统加载
        if not latest_assessment:
            logger.info("No assessment in memory, trying to load from files...")
            latest_assessment = _load_latest_assessment_for_chat()
            logger.info(f"Loaded assessment from files: {latest_assessment is not None}")

        if not report_content:
            logger.info("No report content in memory, trying to load from files...")
            report_content = _load_report_content_for_chat()
            logger.info(f"Loaded report content from files: {report_content is not None}")

        with _chatbot_ops_lock:
            # 如果没有数据，仍然允许聊天，但只能回答一般性问题
            if not latest_assessment and not report_content:
                logger.warning(
                    "No analysis data available for chat. Chatbot will work in general mode only."
                )
            elif latest_assessment and report_content:
                enhanced_content = _create_enhanced_knowledge_base(
                    latest_assessment, report_content
                )
                chatbot.load_context(
                    compliance_assessment=latest_assessment,
                    report_content=enhanced_content if enhanced_content else report_content,
                )
                segments_count = (
                    len(enhanced_content.document_content.segments)
                    if enhanced_content and hasattr(enhanced_content, "document_content")
                    else (
                        len(report_content.document_content.segments)
                        if report_content
                        and hasattr(report_content, "document_content")
                        else 0
                    )
                )
                logger.info(
                    f"Loaded enhanced knowledge base: {latest_assessment.total_metrics_analyzed} metrics + {segments_count} content segments"
                )
            elif latest_assessment:
                chatbot.load_context(compliance_assessment=latest_assessment)
                logger.info(
                    f"Loaded assessment data: {latest_assessment.total_metrics_analyzed} metrics"
                )
            elif report_content:
                chatbot.load_context(report_content=report_content)
                segments_count = (
                    len(report_content.document_content.segments)
                    if hasattr(report_content, "document_content")
                    and report_content.document_content
                    else 0
                )
                logger.info(f"Loaded report content: {segments_count} segments")

            response = chatbot.chat(request)
        return response
        
    except HTTPException:
        raise
    except RuntimeError as e:
        # 如果是LLM访问错误，返回更友好的错误信息
        error_msg = str(e)
        if "LLM模型访问被拒绝" in error_msg or "AccessDenied" in error_msg:
            logger.error(f"LLM access denied error in chat: {e}")
            raise HTTPException(
                status_code=503,  # Service Unavailable - 更合适的错误码
                detail=error_msg
            )
        else:
            logger.error(f"Runtime error in chat: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Error in chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))




def _normalize_assessment_payload(payload: dict) -> dict:
    """Ensure assessment payloads always contain page/value/unit/context fields for frontend stability.

    - Does NOT guess missing values; only fills missing keys with null/empty defaults.
    - Keeps backward compatibility with older *_compliance.json schemas.
    """
    if not isinstance(payload, dict):
        return {
            "report_id": "unknown",
            "assessment_date": datetime.now().isoformat(),
            "total_metrics": 0,
            "overall_score": 0,
            "disclosure_summary": {},
            "metric_analyses": [],
        }

    mas = payload.get("metric_analyses")
    if not isinstance(mas, list):
        mas = []

    norm = []
    for a in mas:
        if not isinstance(a, dict):
            continue
        # Key normalization (ensure exact output fields and legacy fields both exist)
        if "value" not in a:
            if "Value" in a:
                a["value"] = a.get("Value")
            elif "data" in a:
                a["value"] = a.get("data")
        if "page" not in a and "Page" in a:
            a["page"] = a.get("Page")
        a.setdefault("page", None)
        a.setdefault("value", None)
        a.setdefault("unit", a.get("Unit"))
        a.setdefault("context", a.get("Context") or a.get("specific_data_found") or a.get("evidence") or "")
        a.setdefault("reasoning", a.get("LLM Analysis") or a.get("Reasoning") or a.get("Analysis") or "")
        a.setdefault("disclosure_status", a.get("Disclosure Status") or a.get("Model Disclosure Status") or a.get("status") or "")
        a.setdefault("evidence_segments", [])
        a.setdefault("improvement_suggestions", [])
        a.setdefault("metric_name", a.get("Metric") or a.get("metric") or "")
        a.setdefault("metric_code", a.get("Code") or a.get("code") or a.get("metric_id") or "")
        a.setdefault("type", a.get("Type") or a.get("type") or "")
        a.setdefault("category", a.get("Category") or a.get("category") or "")
        a.setdefault("topic", a.get("Topic") or a.get("topic") or "")
        a.setdefault("definition", a.get("Definition") or a.get("definition") or "")

        a["Metric"] = a.get("Metric") or a.get("metric_name") or ""
        a["Category"] = a.get("Category") or a.get("category") or ""
        a["Unit"] = a.get("Unit") or a.get("unit") or ""
        a["Code"] = a.get("Code") or a.get("metric_code") or a.get("metric_id") or ""
        a["Topic"] = a.get("Topic") or a.get("topic") or ""
        a["Type"] = a.get("Type") or a.get("type") or ""
        a["Definition"] = a.get("Definition") or a.get("definition") or ""
        a["Value"] = a.get("Value") if "Value" in a else a.get("value")
        a["Page"] = a.get("Page") if "Page" in a else a.get("page")
        a["Context"] = a.get("Context") or a.get("context") or ""
        a["Disclosure Status"] = a.get("Disclosure Status") or a.get("disclosure_status") or a.get("Model Disclosure Status") or ""
        a["LLM Analysis"] = a.get("LLM Analysis") or a.get("reasoning") or ""
        _sync_metric_alias_fields(a)

        # --- UI/Output rules for disclosure statuses ---
        # 1) not_disclosed -> do not output any page/value
        # 2) partially_disclosed -> value should be a textual reason (no concrete numbers)
        status_raw = str(a.get("disclosure_status", "") or "").strip().lower()
        # Normalize common legacy variants
        if "partial" in status_raw:
            status_norm = "partially_disclosed"
        elif "not" in status_raw:
            status_norm = "not_disclosed"
        elif "full" in status_raw:
            status_norm = "fully_disclosed"
        else:
            status_norm = status_raw

        def _payload_value_is_numeric(v) -> bool:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return True
            if isinstance(v, str):
                s = v.strip()
                if not s:
                    return False
                s = s.replace(",", "")
                # Accept common quantitative strings such as "45%", "123 tonnes", "56.7 m3", "1.2e3 kWh"
                m = re.match(r'^[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', s)
                return m is not None
            return False

        if status_norm == "not_disclosed":
            a["page"] = None
            a["value"] = COMPLIANCE_VALUE_NA
        elif status_norm in ("fully_disclosed", "partially_disclosed"):
            if not _payload_value_is_numeric(a.get("value")):
                a["value"] = COMPLIANCE_VALUE_NA

        a["disclosure_status"] = status_norm or a.get("disclosure_status") or a.get("Disclosure Status") or ""
        a["Value"] = a.get("value")
        a["Page"] = a.get("page")
        a["Context"] = a.get("context") or ""
        a["Disclosure Status"] = a.get("disclosure_status") or a.get("Disclosure Status") or ""
        a["LLM Analysis"] = a.get("reasoning") or a.get("LLM Analysis") or ""
        _sync_metric_alias_fields(a)
        norm.append(a)

    payload["metric_analyses"] = norm
    return payload

@app.get("/api/assessment")
async def get_assessment(limit: int = 0):
    """
    获取当前的合规评估结果（内存态）。

    修复点：补齐前端需要的 page/value/unit/context/evidence_segments 等字段。
    limit=0 表示返回全部；否则返回前 limit 条。
    """
    if not system_components["current_assessment"]:
        raise HTTPException(status_code=404, detail="No assessment available")

    assessment = system_components["current_assessment"]

    def to_item(analysis: DisclosureAnalysis) -> dict:
        disclosure_status = analysis.disclosure_status.value if hasattr(analysis.disclosure_status, "value") else analysis.disclosure_status
        reasoning = analysis.reasoning
        page = getattr(analysis, "page", None)
        value = getattr(analysis, "value", None)
        context = getattr(analysis, "context", None) or ""
        unit = getattr(analysis, "unit", None) or ""
        category = getattr(analysis, "category", "")
        topic = getattr(analysis, "topic", "")
        type_name = getattr(analysis, "type", "")
        definition = getattr(analysis, "definition", "")
        metric_code = getattr(analysis, "metric_code", "") or analysis.metric_id
        return {
            "metric_id": analysis.metric_id,
            "metric_name": analysis.metric_name,
            "metric_code": metric_code,
            "disclosure_status": disclosure_status,
            "reasoning": reasoning,
            "page": page,
            "value": value,
            "unit": unit,
            "category": category,
            "topic": topic,
            "type": type_name,
            "definition": definition,
            "Metric": analysis.metric_name,
            "Category": category,
            "Unit": unit,
            "Code": metric_code,
            "Topic": topic,
            "Type": type_name,
            "Definition": definition,
            "Value": value,
            "Page": page,
            "Context": context,
            "Disclosure Status": disclosure_status,
            "LLM Analysis": reasoning,
            "context": context,
            "evidence_segments": getattr(analysis, "evidence_segments", None) or [],
            "improvement_suggestions": getattr(analysis, "improvement_suggestions", None) or [],
        }

    items = [to_item(a) for a in (assessment.metric_analyses or [])]
    if limit and limit > 0:
        items = items[:limit]

    payload = {
        "report_id": assessment.report_id,
        "assessment_date": assessment.assessment_date.isoformat(),
        "total_metrics": assessment.total_metrics_analyzed,
        "overall_score": assessment.overall_compliance_score,
        "disclosure_summary": assessment.disclosure_summary,
        "metric_analyses": items,
    }
    return _normalize_assessment_payload(payload)


@app.get("/api/assessment/latest")
async def get_latest_assessment(user_id: int = Depends(get_current_user)):
    """
    获取当前用户最新的合规评估结果（从JSON文件）

    Returns:
        最新的评估结果
    """
    try:
        canonical_dir = Path(file_manager.compliance_outputs)
        legacy_dir = Path(__file__).resolve().parents[2] / "outputs"  # legacy backend/outputs

        # 从元数据中过滤当前用户的报告
        user_files = file_manager.list_user_files(user_id, file_type="report")
        if not user_files:
            return {
                "report_id": "unknown",
                "assessment_date": datetime.now().isoformat(),
                "total_metrics": 0,
                "overall_score": 0,
                "disclosure_summary": {},
                "metric_analyses": [],
                "status": "not_analyzed",
                "message": "No analysis reports available"
            }

        # 按上传时间倒序，找到第一个存在合规 JSON 的文件（支持 Subindustry_fileid_compliance.json 命名）
        for f in sorted(user_files, key=lambda x: x["upload_time"], reverse=True):
            json_file = _find_assessment_json_path(f["file_id"], f)
            if json_file and json_file.exists():
                logger.info(f"Loading latest assessment for user {user_id} from: {json_file}")
                with open(json_file, "r", encoding="utf-8") as fp:
                    return _normalize_assessment_payload(json.load(fp))

        # 若用户有文件但尚未生成合规结果
        return {
            "report_id": "unknown",
            "assessment_date": datetime.now().isoformat(),
            "total_metrics": 0,
            "overall_score": 0,
            "disclosure_summary": {},
            "metric_analyses": [],
            "status": "not_analyzed",
            "message": "No analysis reports available"
        }

    except Exception as e:
        logger.error(f"Failed to get latest assessment: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get latest assessment: {str(e)}")


@app.get("/api/history")
async def get_user_history(
    file_type: Optional[str] = None,
    status: Optional[str] = None,
    user_id: int = Depends(get_current_user)
):
    """
    获取当前用户的历史记录
    
    Args:
        file_type: 文件类型过滤 (可选)
        status: 状态过滤 (可选)
        user_id: 当前用户ID (从token自动获取)
        
    Returns:
        用户的历史文件列表
    """
    try:
        files = file_manager.list_user_files(user_id, file_type=file_type, status=status)
        
        return {
            "status": "success",
            "user_id": user_id,
            "files": files,
            "total_count": len(files)
        }
    except Exception as e:
        logger.error(f"Error getting user history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/assessment/{file_id}/scopes")
async def list_assessment_scopes_for_file(file_id: str, user_id: int = Depends(get_current_user)):
    """List per-scope compliance outputs when upload used multiple scopeSlugs (manifest)."""
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")
    canonical_dir = Path(file_manager.compliance_outputs)
    m = _load_compliance_manifest(canonical_dir, file_id)
    if not m:
        return {
            "file_id": file_id,
            "outputs": [],
            "default_scope_key": None,
            "expected_scope_keys": [],
            "pending_scope_keys": [],
        }
    outs = m.get("outputs") or []
    exp = m.get("expected_scope_keys")
    expected = exp if isinstance(exp, list) else []
    done_keys = {str(o.get("scope_key", "")) for o in outs if isinstance(o, dict)}
    pending = [k for k in expected if str(k) not in done_keys]
    return {
        "file_id": file_id,
        "framework": m.get("framework"),
        "default_scope_key": m.get("default_scope_key"),
        "outputs": outs,
        "expected_scope_keys": expected,
        "pending_scope_keys": pending,
    }


@app.get("/api/assessment/{file_id}")
async def get_assessment_by_file(
    file_id: str,
    user_id: int = Depends(get_current_user),
    scope: Optional[str] = None,
):
    """
    根据文件ID获取合规评估结果（从JSON文件）(只能访问自己的文件)

    Args:
        file_id: 文件ID
        user_id: 当前用户ID (从token自动获取)

    Returns:
        评估结果
    """
    try:
        # 检查文件是否属于当前用户
        file_info = file_manager.get_file_info(file_id, user_id=user_id)
        if not file_info:
            raise HTTPException(status_code=404, detail="File not found or access denied")
        # --- Locate assessment JSON ---
        # Canonical location:
        #   uploads/outputs/compliance_reports/{file_id}_compliance.json
        # Legacy location (older builds):
        #   backend/outputs/*.json
        safe_filename = str(file_info.get("safe_filename") or "")
        base_name = Path(safe_filename).stem if safe_filename else ""

        canonical_dir = Path(file_manager.compliance_outputs)
        legacy_dir = Path(__file__).resolve().parents[2] / "outputs"  # backend/outputs

        json_file = _json_path_from_manifest(canonical_dir, file_id, scope)
        if json_file is None or not json_file.is_file():
            json_file = None

        search_dirs = [canonical_dir]
        if legacy_dir.exists():
            search_dirs.append(legacy_dir)

        if json_file is None:
            candidate_names = [f"{file_id}_compliance.json"]
            if base_name:
                candidate_names.append(f"{base_name}_compliance.json")

            for d in search_dirs:
                for name in candidate_names:
                    p = d / name
                    if p.exists():
                        json_file = p
                        break
                if json_file is not None:
                    break

        if json_file is None:
            # Best-effort fuzzy match (keep strict to compliance-like names to avoid false positives)
            fuzzy_patterns = [
                f"*{file_id}*compliance*.json",
                f"*{file_id}*_compliance.json",
            ]
            if base_name:
                fuzzy_patterns.extend([
                    f"*{base_name}*compliance*.json",
                    f"*{base_name}*_compliance.json",
                ])
            matches = []
            for d in search_dirs:
                for pat in fuzzy_patterns:
                    matches.extend(list(d.glob(pat)))
            matches = [m for m in matches if m.is_file()]
            if matches:
                matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                json_file = matches[0]

        if json_file is None:
            logger.warning(f"No JSON assessment found for file_id: {file_id}")
            return {
                "report_id": file_id,
                "assessment_date": datetime.now().isoformat(),
                "total_metrics": 0,
                "overall_score": 0,
                "disclosure_summary": {},
                "metric_analyses": [],
                "status": "not_analyzed",
                "message": "No analysis available for this file yet"
            }

        logger.info(f"Loading assessment from: {json_file}")

        with open(json_file, 'r', encoding='utf-8') as f:
            assessment_data = json.load(f)

        return _normalize_assessment_payload(assessment_data)

    except Exception as e:
        logger.error(f"Failed to load assessment for {file_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load assessment: {str(e)}")


@app.get("/api/chat/history/{session_id}")
async def get_chat_history(session_id: str):
    """
    获取聊天历史
    
    Args:
        session_id: 会话ID
        
    Returns:
        聊天历史
    """
    chatbot = system_components["chatbot"]
    with _chatbot_ops_lock:
        history = chatbot.get_session_history(session_id)

    if not history:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {
        "session_id": session_id,
        "messages": [
            {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp.isoformat()
            }
            for msg in history
        ]
    }


@app.delete("/api/chat/session/{session_id}")
async def clear_chat_session(session_id: str):
    """
    清除聊天会话
    
    Args:
        session_id: 会话ID
        
    Returns:
        操作结果
    """
    chatbot = system_components["chatbot"]
    with _chatbot_ops_lock:
        success = chatbot.clear_session(session_id)

    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    
    return {"status": "success", "message": f"Session {session_id} cleared"}


@app.get("/api/system/status")
async def get_system_status():
    """
    获取系统状态
    
    Returns:
        系统状态信息
    """
    storage_stats = file_manager.get_storage_stats()
    
    return {
        "status": "operational",
        "components": {
            "report_loaded": system_components["current_report"] is not None,
            "metrics_loaded": system_components["current_metrics"] is not None,
            "assessment_available": system_components["current_assessment"] is not None,
            "llm_configured": system_components["config"].llm_api_key is not None
        },
        "report_info": {
            "document_id": system_components["current_report"].document_id if system_components["current_report"] else None,
            "segments_count": len(system_components["current_report"].document_content.segments) if system_components["current_report"] else 0
        } if system_components["current_report"] else None,
        "metrics_info": {
            "collection_id": system_components["current_metrics"].collection_id if system_components["current_metrics"] else None,
            "metrics_count": len(system_components["current_metrics"].metrics) if system_components["current_metrics"] else 0
        } if system_components["current_metrics"] else None,
        "storage_stats": storage_stats
    }


@app.get("/api/gri/options")
async def get_gri_options(user_id: int = Depends(get_current_user)):
    """
    Return GRI sector and topic options for framework dropdowns.
    sectors: [{ slug, label }]; topicsBySector: { sector_slug: [{ slug, label }] }.
    """
    return _get_gri_sectors_and_topics()


@app.get("/api/files")
async def list_files(
    file_type: Optional[str] = None, 
    status: Optional[str] = None,
    user_id: int = Depends(get_current_user)
):
    """
    列出当前用户的文件
    
    Args:
        file_type: 文件类型过滤 ('report', 'metrics')
        status: 状态过滤 ('pending', 'processed', 'failed', 'uploaded')
        user_id: 当前用户ID (从token自动获取)
        
    Returns:
        文件列表 (只返回当前用户的文件)
    """
    try:
        if file_type:
            files = file_manager.list_files_by_type(file_type, status, user_id=user_id)
        else:
            all_files = []
            for ftype in ['report', 'metrics']:
                all_files.extend(file_manager.list_files_by_type(ftype, status, user_id=user_id))
            files = sorted(all_files, key=lambda x: x["upload_time"], reverse=True)

        files = _enrich_file_records_with_scope_progress(file_manager, files)

        return {
            "status": "success",
            "files": files,
            "total_count": len(files)
        }
        
    except Exception as e:
        logger.error(f"Error listing files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/files/{file_id}")
async def get_file_info(file_id: str, user_id: int = Depends(get_current_user)):
    """
    获取文件详细信息 (只能获取自己的文件)

    Args:
        file_id: 文件ID
        user_id: 当前用户ID (从token自动获取)

    Returns:
        文件信息
    """
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")

    return {
        "status": "success",
        "file_info": file_info
    }


@app.get("/api/files/{file_id}/pdf")
async def serve_pdf(file_id: str, user_id: int = Depends(get_current_user)):
    """
    提供PDF文件下载/查看服务（必须登录且只能访问自己的文件）

    Args:
        file_id: 文件ID
        user_id: 当前用户ID (从token自动获取)

    Returns:
        PDF文件响应
    """
    # 只允许访问属于当前用户的文件
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")

    file_path = Path(file_info["file_path"])
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="PDF file not found on server")

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename=file_info.get("safe_filename", "report.pdf")
    )



def _validate_cross_analysis_compatibility(file_ids: list[str]) -> None:
    """Raise HTTPException 400 if reports are not comparable: different framework, or GRI with different sector/topic."""
    if len(file_ids) < 2:
        return
    reports = get_reports_info(file_ids)
    if len(reports) < 2:
        raise HTTPException(status_code=400, detail="Could not resolve at least two reports.")
    frameworks = [str(r.framework or "").strip() for r in reports]
    uniq_fw = set(frameworks)
    if len(uniq_fw) > 1:
        raise HTTPException(
            status_code=400,
            detail="Cross analysis requires the same framework for all reports (e.g. SASB with SASB, GRI with GRI).",
        )
    if uniq_fw == {"GRI"}:
        sectors = [str(getattr(r, "gri_sector", None) or "").strip() for r in reports]
        topics = [str(getattr(r, "gri_topic", None) or "").strip() for r in reports]
        if len(set(sectors)) > 1 or len(set(topics)) > 1:
            raise HTTPException(
                status_code=400,
                detail="GRI cross analysis requires the same Sector and Topic for all reports.",
            )


@app.get("/api/cross-analysis/reports", response_model=CrossReportsResponse)
async def cross_analysis_reports(ids: str):
    """
    Cross Analysis: batch resolve report display names (company/organization) and basic metadata.
    ids: comma-separated file_ids
    """
    file_ids = [x.strip() for x in (ids or "").split(",") if x.strip()]
    if len(file_ids) < 2:
        raise HTTPException(status_code=400, detail="At least two file_ids are required")
    _validate_cross_analysis_compatibility(file_ids)
    reports = get_reports_info(file_ids)
    return CrossReportsResponse(reports=reports)


@app.post("/api/cross-analysis/compare", response_model=CrossCompareResponse)
async def cross_analysis_compare(req: CrossCompareRequest):
    """
    Cross Analysis: semantic extraction + alignment for a topic across multiple reports.
    - Prefer vector recall from persisted embeddings (.npz).
    - Best-effort numeric extraction; fall back to concise summary.
    - Returns evidence with page for PDF preview.
    """
    file_ids = list(req.file_ids)
    _validate_cross_analysis_compatibility(file_ids)
    # Resolve display labels
    reports = get_reports_info(file_ids)
    label_map = {r.file_id: (r.display_name, r.short_name, r.confidence, getattr(r, "report_year", None)) for r in reports}

    labels = req.labels
    metric_display_name = None
    issue_display_name = None
    if labels is not None:
        metric_display_name = labels.metric_zh or labels.metric_en
        issue_display_name = labels.issue_zh or labels.issue_en

    results = compare_topic(
        file_ids=file_ids,
        topic_key=req.topic_key,
        query_pack=req.query_pack,
        top_n_candidates=req.top_n_candidates,
        top_k_evidence=req.top_k_evidence,
        report_labels=label_map,
        metric_display_name=metric_display_name,
        issue_display_name=issue_display_name,
    )

    return CrossCompareResponse(
        topic_key=req.topic_key,
        reports=results,
        generated_at=datetime.utcnow().isoformat() + "Z",
    )


@app.post("/api/cross-analysis/records", response_model=CrossRecordsResponse)
async def cross_analysis_records(req: CrossRecordsRequest):
    """Cross Analysis: issue-level disclosure records for table rendering.

    This endpoint extracts and caches records (id/name/topic/type/detail/year/data/unit/context/page),
    and can persist JSON outputs under:
        uploads/outputs/cross_analysis/output/
    """
    file_ids = list(req.file_ids)
    _validate_cross_analysis_compatibility(file_ids)
    reports = get_reports_info(file_ids)
    label_map = {r.file_id: (r.display_name, r.short_name, r.confidence, getattr(r, "report_year", None)) for r in reports}

    # Compute effective issue keys when caller omitted (backend default = all issues under topic)
    issue_keys = list(req.issue_keys or [])
    if not issue_keys:
        dim = dimension_by_key(req.topic_key)
        issue_keys = [i.issue_key for i in (dim.issues or [])] if dim else []

    records = extract_records_for_topic(
        file_ids=file_ids,
        topic_key=req.topic_key,
        issue_keys=issue_keys,
        top_n_candidates=req.top_n_candidates,
        top_k_evidence=req.top_k_evidence,
        report_labels=label_map,
        persist_output=req.persist_output,
    )

    return CrossRecordsResponse(
        topic_key=req.topic_key,
        issue_keys=issue_keys,
        records=records,
        generated_at=datetime.utcnow().isoformat() + "Z",
    )


# -------------------------
# Cross Analysis disclosed-data cache (assessment-driven)
# -------------------------

_cross_disclosed_locks: dict = {}
_cross_disclosed_locks_guard = threading.Lock()


def _cross_disclosed_cache_dir() -> Path:
    """Where we persist assessment-driven cross-analysis JSON outputs."""
    d = CROSS_CACHE_DIR / "output" / "json"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cross_disclosed_cache_key(file_ids: list[str], version: str = "v3") -> str:
    """Stable key for a file-id combination."""
    ids_sorted = sorted([str(x).strip() for x in (file_ids or []) if str(x).strip()])
    base = version + "|" + "|".join(ids_sorted)
    h = hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
    return f"disclosed_{version}_{h}"  # short, filesystem-friendly


def _cross_disclosed_lock_for(key: str) -> threading.Lock:
    with _cross_disclosed_locks_guard:
        lk = _cross_disclosed_locks.get(key)
        if lk is None:
            lk = threading.Lock()
            _cross_disclosed_locks[key] = lk
        return lk


def _safe_strip_file_ext(name: str) -> str:
    try:
        s = str(name or "").strip()
        if not s:
            return ""
        return re.sub(r"\.[^/.]+$", "", s)
    except Exception:
        return str(name or "")


def _extract_year_from_text(text: str) -> Optional[str]:
    """Extract a 4-digit year from a label like 'Bosch (2024)' or 'Bosch_2024_ESG'.

    If multiple years exist, return the latest one.
    """
    try:
        s = str(text or "")
        years = re.findall(r"\b(19\d{2}|20\d{2})\b", s)
        if not years:
            return None
        # choose the latest year
        return str(max(int(y) for y in years))
    except Exception:
        return None


def _find_assessment_json_path(file_id: str, file_info: dict) -> Optional[Path]:
    """Locate the assessment JSON for a given file_id (canonical + legacy)."""
    canonical_dir = Path(file_manager.compliance_outputs)
    manifest_path = _json_path_from_manifest(canonical_dir, file_id, None)
    if manifest_path is not None and manifest_path.is_file():
        return manifest_path

    safe_filename = str(file_info.get("safe_filename") or "")
    base_name = Path(safe_filename).stem if safe_filename else ""

    legacy_dir = Path(__file__).resolve().parents[2] / "outputs"  # backend/outputs
    search_dirs = [canonical_dir]
    if legacy_dir.exists():
        search_dirs.append(legacy_dir)

    candidate_names = [f"{file_id}_compliance.json"]
    if base_name:
        candidate_names.append(f"{base_name}_compliance.json")

    for d in search_dirs:
        for name in candidate_names:
            p = d / name
            if p.exists():
                return p

    # strict fuzzy match
    fuzzy_patterns = [f"*{file_id}*compliance*.json", f"*{file_id}*_compliance.json"]
    if base_name:
        fuzzy_patterns.extend([f"*{base_name}*compliance*.json", f"*{base_name}*_compliance.json"])
    matches: list[Path] = []
    for d in search_dirs:
        for pat in fuzzy_patterns:
            matches.extend(list(d.glob(pat)))
    matches = [m for m in matches if m.is_file()]
    if matches:
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return matches[0]
    return None


def _normalize_nav_label(v: Optional[str], default: str) -> str:
    s = str(v or "").strip()
    return s if s else default


def _build_disclosed_records_for_files(file_ids: list[str], user_id: int) -> tuple[list[dict], list[dict], list[float]]:
    """Build CrossExtractedRecord-like dicts from per-report assessments.

    Returns:
      - records (list of dict)
      - reports_info (list of dict from get_reports_info)
      - assessment_mtimes (list of mtime floats used for cache invalidation)
    """
    # Labels + years from backend heuristics
    reports = get_reports_info(file_ids)
    report_map = {r.file_id: r for r in reports}

    # Simple normalizers (keep consistent with frontend expectations)
    def normalize_type_label(x: Optional[str]) -> str:
        s = _normalize_nav_label(x, "Metrics")
        return (
            s.replace("discolosure", "Disclosure")
             .replace("Sustainability Disclosure", "Disclosure")
             .replace("activity metric", "Activity Metrics")
             .replace("Activity Metric", "Activity Metrics")
        )

    def normalize_category_label(x: Optional[str]) -> str:
        s = _normalize_nav_label(x, "General")
        return (
            s.replace("discussion and analysis", "Discussion and Analysis")
             .replace("quantitative", "Quantitative")
             .replace("qualitative", "Qualitative")
        )

    def strip_metric_prefix(name: str) -> str:
        s = str(name or "").strip()
        if not s:
            return ""
        s = re.sub(r"^\(\d+\)\s*", "", s)
        s = re.sub(r"^\d+\s*[\.|\)]\s*", "", s)
        return s.strip()

    def to_page(v) -> Optional[int]:
        if v is None:
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, float) and v == v:
            return int(v)
        m = re.search(r"\d+", str(v))
        return int(m.group(0)) if m else None

    def looks_numeric(v: str) -> bool:
        return bool(re.search(r"\d", str(v or "")))

    def is_purely_numeric_value(v) -> bool:
        """True if value is a single number (int/float or with %), for GRI cross-analysis comparison."""
        if v is None:
            return False
        s = str(v).strip()
        if not s:
            return False
        s = s.rstrip("%").strip()
        try:
            float(s)
            return True
        except ValueError:
            return False

    records: list[dict] = []
    mtimes: list[float] = []

    for fid in file_ids:
        file_info = file_manager.get_file_info(fid, user_id=user_id)
        if not file_info:
            # access denied / missing
            continue

        rep = report_map.get(fid)
        framework = (file_info or {}).get("framework") or (getattr(rep, "framework", None) if rep else None)

        assessment_path = _find_assessment_json_path(fid, file_info)
        if assessment_path is None or not assessment_path.exists():
            continue
        try:
            mtimes.append(float(assessment_path.stat().st_mtime))
        except Exception:
            pass

        try:
            with open(assessment_path, "r", encoding="utf-8") as f:
                assessment_data = json.load(f)
            assessment_data = _normalize_assessment_payload(assessment_data)
        except Exception as e:
            logger.warning(f"Failed to read assessment json for {fid}: {e}")
            continue

        analyses = assessment_data.get("metric_analyses") or []

        # prefer filename stem as label
        rep = report_map.get(fid)
        label = ""
        if rep is not None:
            label = _safe_strip_file_ext(getattr(rep, "filename", "") or "")
            if not label:
                label = _safe_strip_file_ext(getattr(rep, "display_name", "") or "")
            if not label:
                label = _safe_strip_file_ext(getattr(rep, "short_name", "") or "")
        if not label:
            label = fid

        # report year fallback
        report_year = None
        if rep is not None:
            try:
                ry = getattr(rep, "report_year", None)
                report_year = str(ry).strip() if ry is not None else None
            except Exception:
                report_year = None

        # Normalize display name to the old UI-friendly format: "<Company> (<Year>)" when possible.
        name = label
        name_year = _extract_year_from_text(name)
        if (not name_year) and report_year:
            # only append when name doesn't already contain a year
            name = f"{label} ({report_year})"
            name_year = _extract_year_from_text(name)
        if not name_year:
            name_year = report_year

        for a in analyses:
            metric_name = strip_metric_prefix(a.get("metric_name") or a.get("metric") or a.get("Metric") or "")
            metric_id = str(a.get("metric_id") or a.get("metricId") or a.get("metric_code") or a.get("code") or a.get("Code") or "").strip()
            metric_code = str(a.get("metric_code") or a.get("code") or a.get("Code") or "").strip()

            value = a.get("value")
            ds = str(a.get("disclosure_status") or "").strip().lower()
            is_disclosed = ds == "fully_disclosed" or ((not ds) and value is not None and looks_numeric(str(value)))
            value_str = "" if value is None else str(value)
            if not is_disclosed:
                value_str = ""
            # GRI cross-analysis: only purely numeric values count as disclosed; otherwise treat as not disclosed
            disclosure_status = a.get("disclosure_status")
            if framework == "GRI" and value_str and not is_purely_numeric_value(value_str):
                value_str = ""
                is_disclosed = False
                disclosure_status = "not_disclosed"
            unit = str(a.get("unit") or a.get("Unit") or "").strip() or None
            page = to_page(a.get("page") or a.get("Page") or a.get("page_number") or a.get("pageNumber"))
            typ = normalize_type_label(a.get("type") or a.get("Type"))
            cat = normalize_category_label(a.get("category") or a.get("Category"))
            detail = str(a.get("reasoning") or "").strip()

            year = _extract_year_from_text(name) or name_year

            # Primary nav = type; Secondary nav = SASB Topic (type → topic hierarchy).
            # For "Activity Metrics", secondary = metric_name only (no category like "Quantitative").
            sasb_topic = str(a.get("topic") or a.get("Topic") or "").strip() or cat
            topic_label = metric_name or metric_code or metric_id or "Metric"
            sub_topic = metric_code or metric_id or ""
            secondary_nav = (
                topic_label
                if typ and str(typ).strip().lower() in ("activity metrics",)
                else sasb_topic
            )

            records.append(
                {
                    "id": fid,
                    "name": name,
                    "primary_navigation": typ,
                    "secondary_navigation": secondary_nav,
                    "topic": topic_label,
                    "sub_topic": sub_topic,
                    "category": normalize_category_label(a.get("category") or a.get("Category")) or None,
                    "page": page,
                    "data": value_str,
                    "value": value_str or "",  # CrossDisclosedRecord.value is str; use "" for not disclosed
                    "year": year,
                    "unit": unit,
                    "detail": detail,
                    "disclosure_status": disclosure_status,
                    "metric_id": metric_id or None,
                }
            )

    reports_payload = [r.model_dump() for r in reports]
    return records, reports_payload, mtimes


@app.get("/api/cross-analysis/disclosed-cache", response_model=CrossDisclosedCacheResponse)
async def cross_analysis_disclosed_cache(ids: str, user_id: int = Depends(get_current_user)):
    """Cross Analysis: build (and cache) disclosed-data records from per-report assessment outputs.

    Cache location:
      uploads/outputs/cross_analysis/output/json/{cache_key}.json

    If the same file-id combination is selected again, backend will directly return
    the cached JSON (unless any underlying per-report assessment JSON was updated).
    """

    file_ids = [x.strip() for x in (ids or "").split(",") if x.strip()]
    if len(file_ids) < 2:
        raise HTTPException(status_code=400, detail="At least two file_ids are required")
    _validate_cross_analysis_compatibility(file_ids)

    # Access check early
    for fid in file_ids:
        if not file_manager.get_file_info(fid, user_id=user_id):
            raise HTTPException(status_code=404, detail=f"File not found or access denied: {fid}")

    ids_sorted = sorted(file_ids)
    cache_key = _cross_disclosed_cache_key(ids_sorted)
    cache_dir = _cross_disclosed_cache_dir()
    cache_path = cache_dir / f"{cache_key}.json"

    lock = _cross_disclosed_lock_for(cache_key)
    with lock:
        # If cache exists, validate freshness using assessment mtimes.
        if cache_path.exists():
            try:
                cache_mtime = float(cache_path.stat().st_mtime)
            except Exception:
                cache_mtime = 0.0

            # Gather current assessment mtimes
            _mtimes: list[float] = []
            for fid in ids_sorted:
                fi = file_manager.get_file_info(fid, user_id=user_id)
                if not fi:
                    continue
                ap = _find_assessment_json_path(fid, fi)
                if ap and ap.exists():
                    try:
                        _mtimes.append(float(ap.stat().st_mtime))
                    except Exception:
                        pass

            latest_assessment_mtime = max(_mtimes) if _mtimes else 0.0
            if latest_assessment_mtime <= cache_mtime:
                with open(cache_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                payload["from_cache"] = True
                # Normalize cached records: value/data must be str; category optional
                for r in payload.get("records") or []:
                    if isinstance(r, dict):
                        if r.get("value") is None:
                            r["value"] = ""
                        if r.get("data") is None:
                            r["data"] = r.get("value", "") or ""
                        if "category" not in r:
                            r["category"] = None
                return payload

        # Build new
        records, _reports_payload, _mtimes = _build_disclosed_records_for_files(ids_sorted, user_id=user_id)
        payload = {
            "cache_key": cache_key,
            "file_ids": ids_sorted,
            "from_cache": False,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "records": records,
        }

        # Atomic write
        tmp = cache_path.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, cache_path)
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass

        return payload




# -------------------------
# Cross Analysis Excel-metrics cache helpers
# -------------------------

def _excel_metrics_cache_paths():
    """Return canonical/legacy cache directories and files."""
    out_dir = CROSS_CACHE_DIR / "output"
    legacy_dir = CROSS_CACHE_DIR / "excel_output"
    return {
        "out_dir": out_dir,
        "legacy_dir": legacy_dir,
        "all_records": [out_dir / "all_records.json", legacy_dir / "all_records.json"],
        "processed": [out_dir / "processed_ids.json", legacy_dir / "processed_ids.json"],
    }


def _ensure_excel_metrics_dirs():
    paths = _excel_metrics_cache_paths()
    paths["out_dir"].mkdir(parents=True, exist_ok=True)
    paths["legacy_dir"].mkdir(parents=True, exist_ok=True)


def _rebuild_excel_metrics_cache_from_per_report() -> bool:
    """Rebuild global all_records.json / processed_ids.json from per-report json files.

    This is used when users delete output/all_records.json or processed_ids.json.
    Returns True if a rebuild was performed.
    """
    import json as _json

    paths = _excel_metrics_cache_paths()
    out_dir = paths["out_dir"]
    legacy_dir = paths["legacy_dir"]

    # Prefer canonical output dir; fallback to legacy.
    for base in (out_dir, legacy_dir):
        if not base.exists():
            continue
        per_files = [p for p in base.glob('*.json') if p.name not in ('all_records.json', 'processed_ids.json')]
        if not per_files:
            continue

        merged = []
        processed = set()
        for p in per_files:
            try:
                payload = _json.loads(p.read_text(encoding='utf-8'))
                if isinstance(payload, list):
                    merged.extend([x for x in payload if isinstance(x, dict)])
            except Exception:
                continue
            # filename is <file_id>.json
            processed.add(p.stem)

        if not merged and not processed:
            continue

        # dedup conservatively
        seen = set()
        uniq = []
        for x in merged:
            k = (
                str(x.get('id') or '').strip(),
                x.get('Primary Navigation'),
                x.get('Secondary Navigation'),
                x.get('Topic'),
                x.get('Sub-topic'),
                x.get('year'),
                x.get('data'),
                x.get('unit'),
                x.get('page'),
            )
            if k in seen:
                continue
            seen.add(k)
            uniq.append(x)

        _ensure_excel_metrics_dirs()
        (out_dir / 'all_records.json').write_text(_json.dumps(uniq, ensure_ascii=False, indent=2), encoding='utf-8')
        (legacy_dir / 'all_records.json').write_text(_json.dumps(uniq, ensure_ascii=False, indent=2), encoding='utf-8')
        (out_dir / 'processed_ids.json').write_text(_json.dumps(sorted(processed), ensure_ascii=False, indent=2), encoding='utf-8')
        (legacy_dir / 'processed_ids.json').write_text(_json.dumps(sorted(processed), ensure_ascii=False, indent=2), encoding='utf-8')

        logger.info(f"[ExcelMetrics] Rebuilt cache from per-report JSON: reports={len(processed)} records={len(uniq)}")
        return True

    return False


def _load_processed_ids() -> set:
    import json as _json
    paths = _excel_metrics_cache_paths()
    for p in paths['processed']:
        if p.exists():
            try:
                raw = _json.loads(p.read_text(encoding='utf-8'))
                if isinstance(raw, list):
                    return {str(x).strip() for x in raw if str(x).strip()}
            except Exception:
                pass
    return set()


def _ensure_processed_ids_from_all_records() -> bool:
    """If processed_ids.json is missing but all_records.json exists, rebuild processed_ids."""
    import json as _json
    paths = _excel_metrics_cache_paths()
    all_file = next((p for p in paths['all_records'] if p.exists()), None)
    if not all_file:
        return False

    processed_file = next((p for p in paths['processed'] if p.exists()), None)
    if processed_file:
        return False

    try:
        payload = _json.loads(all_file.read_text(encoding='utf-8'))
        if not isinstance(payload, list):
            return False
    except Exception:
        return False

    processed = sorted({str(r.get('id') or '').strip() for r in payload if isinstance(r, dict) and str(r.get('id') or '').strip()})
    if not processed:
        return False

    _ensure_excel_metrics_dirs()
    (paths['out_dir'] / 'processed_ids.json').write_text(_json.dumps(processed, ensure_ascii=False, indent=2), encoding='utf-8')
    (paths['legacy_dir'] / 'processed_ids.json').write_text(_json.dumps(processed, ensure_ascii=False, indent=2), encoding='utf-8')
    logger.info(f"[ExcelMetrics] Rebuilt processed_ids.json from all_records.json: reports={len(processed)}")
    return True

@app.post("/api/cross-analysis/excel-metrics", response_model=ExcelMetricsResponse)
async def cross_analysis_excel_metrics(req: ExcelMetricsRequest):
    """Extract ESG metric records using the Excel catalog.

    Key fixes:
    - Avoid long-running HTTP requests causing Next.js proxy ECONNRESET (socket hang up)
      by defaulting to async background extraction.
    - Still supports sync extraction when EXCEL_METRICS_ASYNC=0.
    """

    file_ids = [str(x).strip() for x in (req.file_ids or []) if str(x).strip()]
    if not file_ids:
        return ExcelMetricsResponse(records=[], generated_at=datetime.utcnow().isoformat() + "Z")

    want_ids = sorted(set(file_ids))

    def _load_ready_cache(want):
        # Canonical cache location is outputs/cross_analysis/output/all_records.json
        candidates = [
            CROSS_CACHE_DIR / "output" / "all_records.json",
            CROSS_CACHE_DIR / "excel_output" / "all_records.json",
        ]
        cache_file = next((pp for pp in candidates if pp.exists()), None)
        if not cache_file:
            return None

        # processed gate (required to avoid partial cache reads)
        processed_candidates = [
            CROSS_CACHE_DIR / "output" / "processed_ids.json",
            CROSS_CACHE_DIR / "excel_output" / "processed_ids.json",
        ]
        processed_file = next((pp for pp in processed_candidates if pp.exists()), None)
        if not processed_file:
            return None

        try:
            import json as _json
            processed = _json.loads(processed_file.read_text(encoding="utf-8"))
            if not isinstance(processed, list):
                return None
            processed_set = {str(x).strip() for x in processed if str(x).strip()}
        except Exception:
            return None

        if not set(want).issubset(processed_set):
            return None

        try:
            import json as _json
            payload = _json.loads(cache_file.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                return []
        except Exception:
            return []

        want_set = set(want)
        recs = [r for r in payload if isinstance(r, dict) and str(r.get("id") or "").strip() in want_set]
        return recs

    cached = _load_ready_cache(want_ids)
    if cached is not None and len(cached) >= 0:
        # Cache ready (may be empty but processed_ids says done)
        return ExcelMetricsResponse(records=cached, generated_at=datetime.utcnow().isoformat() + "Z")

    # If users deleted output files, we may still have per-report JSONs. Rebuild once.
    try:
        if _rebuild_excel_metrics_cache_from_per_report():
            cached2 = _load_ready_cache(want_ids)
            if cached2 is not None:
                return ExcelMetricsResponse(records=cached2, generated_at=datetime.utcnow().isoformat() + "Z")
    except Exception:
        pass

    # IMPORTANT: Excel-metrics extraction can take minutes (LLM + vector recall + rerank).
    # Next.js dev/proxy frequently aborts long requests, producing ECONNRESET (socket hang up).
    # Therefore the **safe default** is:
    #   - start extraction in a background thread on cache-miss
    #   - return quickly (records from cache if any; otherwise empty)
    # Frontend can poll /api/cross-analysis/excel-metrics/cache until ready.
    #
    # To force synchronous extraction (only if your reverse proxy timeouts are tuned), set:
    #   EXCEL_METRICS_FORCE_SYNC=1  (or EXCEL_METRICS_ASYNC=0)
    async_enabled = os.getenv("EXCEL_METRICS_ASYNC", "1").strip().lower() in ("1", "true", "yes", "y")
    force_sync = os.getenv("EXCEL_METRICS_FORCE_SYNC", "0").strip().lower() in ("1", "true", "yes", "y")
    # Historical env var; kept for backward compatibility.
    sync_on_miss = os.getenv("EXCEL_METRICS_SYNC_ON_CACHE_MISS", "0").strip().lower() in ("1", "true", "yes", "y")

    def _job_key():
        raw = "|".join(want_ids) + "|" + str(req.catalog_path or "")
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _run_extract():
        try:
            extract_excel_metrics_for_files(
                file_ids=list(want_ids),
                catalog_path=req.catalog_path,
                top_n_candidates=req.top_n_candidates,
                persist_output=req.persist_output,
            )
        except Exception as e:
            logger.error(f"[ExcelMetrics] background extract failed: {e}")

    # Helper: start (or reuse) background extraction job.
    def _start_job() -> None:
        key = _job_key()
        with _excel_metrics_jobs_lock:
            st = _excel_metrics_jobs.get(key)
            th = st.get("thread") if isinstance(st, dict) else None
            if th is None or (hasattr(th, "is_alive") and not th.is_alive()):
                # Ensure output dirs exist early so /cache stops 404-ing after users delete outputs.
                try:
                    _ensure_excel_metrics_dirs()
                except Exception:
                    pass
                t = threading.Thread(target=_run_extract, name=f"excel-metrics-{key[:8]}", daemon=True)
                _excel_metrics_jobs[key] = {"thread": t, "started_at": time.time()}
                t.start()

    # Default behavior: background job on cache-miss (fast response).
    # If force_sync enabled OR async disabled OR sync_on_miss enabled => do sync extraction.
    if (not async_enabled) or force_sync or sync_on_miss:
        logger.info(f"[ExcelMetrics] Cache miss -> start sync extraction for {len(want_ids)} reports")
        t0 = time.time()
        records_all = extract_excel_metrics_for_files(
            file_ids=list(want_ids),
            catalog_path=req.catalog_path,
            top_n_candidates=req.top_n_candidates,
            persist_output=req.persist_output,
        )
        dt = time.time() - t0
        want_set = set(want_ids)
        records = [r for r in (records_all or []) if isinstance(r, dict) and str(r.get('id') or '').strip() in want_set]
        logger.info(f"[ExcelMetrics] Sync extraction done: records={len(records)} elapsed={dt:.1f}s")
        return ExcelMetricsResponse(records=records, generated_at=datetime.utcnow().isoformat() + "Z")

    # Async path: start job and return cached snapshot (often empty on cold start).
    _start_job()
    # Return any currently available records for these ids (partial cache) to avoid blanking UI.
    partial = []
    try:
        candidates = [
            CROSS_CACHE_DIR / "output" / "all_records.json",
            CROSS_CACHE_DIR / "excel_output" / "all_records.json",
        ]
        cache_file = next((pp for pp in candidates if pp.exists()), None)
        if cache_file:
            import json as _json
            payload = _json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                want_set = set(want_ids)
                partial = [r for r in payload if isinstance(r, dict) and str(r.get("id") or "").strip() in want_set]
    except Exception:
        partial = []
    return ExcelMetricsResponse(records=partial, generated_at=datetime.utcnow().isoformat() + "Z")



@app.get("/api/cross-analysis/excel-metrics/cache", response_model=ExcelMetricsResponse)
async def cross_analysis_excel_metrics_cache(ids: str):
    """Return cached Excel-metrics extraction.

    Fixes:
    - Uses processed_ids.json as the completion signal.
    - Returns 404 until ALL requested ids have been processed (even if some yield 0 records).
    """

    want_ids = [x.strip() for x in (ids or "").split(",") if x.strip()]
    if not want_ids:
        raise HTTPException(status_code=400, detail="ids is required")

    want = set(want_ids)

    # Canonical cache location
    candidates = [
        CROSS_CACHE_DIR / "output" / "all_records.json",
        CROSS_CACHE_DIR / "excel_output" / "all_records.json",
    ]
    cache_file = next((pp for pp in candidates if pp.exists()), None)
    if not cache_file:
        # Users may delete output/all_records.json but keep per-report JSONs; rebuild once.
        try:
            rebuilt = _rebuild_excel_metrics_cache_from_per_report()
        except Exception:
            rebuilt = False
        if rebuilt:
            cache_file = next((pp for pp in candidates if pp.exists()), None)
        if not cache_file:
            raise HTTPException(status_code=404, detail="Cache not found")

    processed_candidates = [
        CROSS_CACHE_DIR / "output" / "processed_ids.json",
        CROSS_CACHE_DIR / "excel_output" / "processed_ids.json",
    ]
    processed_file = next((pp for pp in processed_candidates if pp.exists()), None)
    if not processed_file:
        # Derive processed_ids from cache payload and persist to avoid permanent 404.
        try:
            import json as _json
            payload0 = _json.loads(cache_file.read_text(encoding='utf-8'))
            if not isinstance(payload0, list):
                payload0 = []
            processed_set0 = {str(r.get('id') or '').strip() for r in payload0 if isinstance(r, dict) and str(r.get('id') or '').strip()}
            _ensure_excel_metrics_dirs()
            (CROSS_CACHE_DIR / 'output' / 'processed_ids.json').write_text(_json.dumps(sorted(processed_set0), ensure_ascii=False, indent=2), encoding='utf-8')
            (CROSS_CACHE_DIR / 'excel_output' / 'processed_ids.json').write_text(_json.dumps(sorted(processed_set0), ensure_ascii=False, indent=2), encoding='utf-8')
            processed_file = next((pp for pp in processed_candidates if pp.exists()), None)
        except Exception:
            processed_file = None
        if not processed_file:
            raise HTTPException(status_code=404, detail="Cache not ready")

    try:
        import json as _json
        processed = _json.loads(processed_file.read_text(encoding="utf-8"))
        if not isinstance(processed, list):
            raise ValueError("processed_ids is not a list")
        processed_set = {str(x).strip() for x in processed if str(x).strip()}
    except Exception:
        raise HTTPException(status_code=404, detail="Cache not ready")

    if not want.issubset(processed_set):
        missing = sorted([x for x in want if x not in processed_set])
        raise HTTPException(status_code=404, detail={"message": "Cache not ready", "missing_ids": missing})

    try:
        import json as _json
        payload = _json.loads(cache_file.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            payload = []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read cache: {e}")

    records = [r for r in payload if isinstance(r, dict) and str(r.get("id") or "").strip() in want]
    generated_at = datetime.utcfromtimestamp(cache_file.stat().st_mtime).isoformat() + "Z"
    return ExcelMetricsResponse(records=records, generated_at=generated_at)

@app.post("/api/system/cleanup-orphaned-reports")
async def cleanup_orphaned_reports():
    """
    清理孤儿报告文件（没有对应元数据的报告）
    """
    try:
        # Active IDs / base names derived from metadata
        active_files = list((file_manager.metadata or {}).get("files", {}).values())
        active_file_ids = {str(x.get("file_id") or "").strip() for x in active_files if str(x.get("file_id") or "").strip()}
        active_base_names = {
            Path(str(x.get("safe_filename") or "")).stem
            for x in active_files
            if str(x.get("safe_filename") or "").strip()
        }

        def _is_orphan(name: str) -> bool:
            return (not any(fid and fid in name for fid in active_file_ids)) and (not any(bn and bn in name for bn in active_base_names))

        deleted_items = []

        # Canonical output locations under uploads/
        scan_specs = [
            (Path(file_manager.markdown_outputs), "*.md"),
            (Path(file_manager.compliance_outputs), "*.json"),
            (Path(file_manager.embeddings_outputs), "*.*"),
        ]

        # Legacy output location (older builds): backend/outputs
        legacy_outputs_dir = Path(__file__).resolve().parents[2] / "outputs"
        if legacy_outputs_dir.exists():
            scan_specs.append((legacy_outputs_dir, "*.md"))
            scan_specs.append((legacy_outputs_dir, "*compliance*.json"))

        for d, pat in scan_specs:
            if not d.exists():
                continue
            for p in d.glob(pat):
                if not p.is_file():
                    continue
                if _is_orphan(p.name):
                    try:
                        p.unlink()
                        deleted_items.append(str(p))
                    except Exception:
                        # Best-effort cleanup: ignore individual failures
                        pass
        
        return {
            "status": "success",
            "message": f"Cleaned up {len(deleted_items)} orphaned output files",
            "deleted_files": deleted_items
        }
    
    except Exception as e:
        logger.error(f"Failed to cleanup orphaned reports: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Cleanup failed: {str(e)}")


def _expected_scope_keys_from_meta(file_info: dict, m: Optional[dict]) -> List[str]:
    if m and isinstance(m.get("expected_scope_keys"), list) and m["expected_scope_keys"]:
        return [str(x).strip() for x in m["expected_scope_keys"] if str(x).strip()]
    raw = file_info.get("scope_slugs_json")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
    return []


def _unlink_compliance_reports_dir_for_file_id(
    file_id: str,
    stem: Optional[str],
    deleted_items: List[str],
) -> None:
    """Delete every artifact under compliance_reports/ that belongs to this upload (JSON, XLSX, manifest)."""
    canonical = Path(file_manager.compliance_outputs)
    if not canonical.is_dir():
        return
    fid = str(file_id).strip()
    if not fid:
        return
    candidates: Set[Path] = set()

    for suffix in ("_compliance.json", "_compliance.xlsx", "_compliance_manifest.json"):
        p = canonical / f"{fid}{suffix}"
        if p.is_file():
            candidates.add(p)

    for pattern in (
        f"*{fid}*_compliance*.json",
        f"*{fid}*_compliance*.xlsx",
    ):
        try:
            for p in canonical.glob(pattern):
                if p.is_file():
                    candidates.add(p)
        except Exception as e:
            logger.warning(f"Compliance glob failed {pattern!r} in {canonical}: {e}")

    if stem:
        st = str(stem).strip()
        if st:
            for suffix in ("_compliance.json", "_compliance.xlsx"):
                p = canonical / f"{st}{suffix}"
                if p.is_file():
                    candidates.add(p)

    for p in sorted(candidates, key=lambda x: str(x)):
        try:
            p.unlink()
            deleted_items.append(f"合规报告: {p.name}")
        except Exception as e:
            logger.warning(f"Failed to remove compliance artifact {p}: {e}")


def _unlink_compliance_markdown_for_file_id(file_id: str, deleted_items: List[str]) -> None:
    """Remove compliance_report_* markdown written next to PDF pipeline (uploads/outputs/markdown/)."""
    md_dir = Path(file_manager.markdown_outputs)
    if not md_dir.is_dir():
        return
    fid = str(file_id).strip()
    if not fid:
        return
    candidates: Set[Path] = set()
    single = md_dir / f"compliance_report_{fid}.md"
    if single.is_file():
        candidates.add(single)
    try:
        for p in md_dir.glob(f"compliance_report_{fid}_*.md"):
            if p.is_file():
                candidates.add(p)
    except Exception as e:
        logger.warning(f"Compliance markdown glob failed for {fid}: {e}")
    for p in sorted(candidates, key=lambda x: str(x)):
        try:
            p.unlink()
            deleted_items.append(f"合规Markdown: {p.name}")
        except Exception as e:
            logger.warning(f"Failed to remove {p}: {e}")


def _patch_file_primary_scope_fields(file_id: str, fw: str, new_exp: List[str]) -> None:
    finfo = file_manager.metadata.get("files", {}).get(file_id)
    if not isinstance(finfo, dict) or not new_exp:
        return
    finfo["scope_slugs_json"] = json.dumps(new_exp, ensure_ascii=False)
    if fw == "GRI":
        finfo["gri_topic"] = new_exp[0]
        # gri_sector unchanged
        finfo["semi_industry"] = None
    elif fw == "SASB":
        finfo["semi_industry"] = new_exp[0]
    elif fw in ("CDP", "TCFD"):
        finfo["semi_industry"] = new_exp[0]
    file_manager._save_metadata()


def _try_delete_one_scope_only(
    file_id: str, file_info: dict, scope_key: str
) -> Optional[dict]:
    """
    Delete one multi-scope compliance bundle; update manifest + metadata.
    Returns response dict if handled; None => caller should run full file delete.
    """
    assessment_dir = Path(file_manager.compliance_outputs)
    m = _load_compliance_manifest(assessment_dir, file_id)
    expected = _expected_scope_keys_from_meta(file_info, m)
    fw = (file_info.get("framework") or "").strip() or "SASB"

    json_p, xlsx_p, md_p = _paths_for_scope_compliance_bundle(
        file_manager, file_id, file_info, scope_key
    )
    deleted_items: List[str] = []
    for p in (json_p, xlsx_p, md_p):
        try:
            if p.exists():
                p.unlink()
                deleted_items.append(f"合规输出: {p.name}")
        except Exception as e:
            logger.warning(f"Failed to remove {p}: {e}")

    # Multi-scope upload: drop this slug from manifest + metadata, keep PDF
    if len(expected) >= 2 and scope_key in expected:
        new_exp = [x for x in expected if x != scope_key]
        if not new_exp:
            return None
        outputs = [
            o
            for o in (m.get("outputs") or [])
            if str(o.get("scope_key")) != str(scope_key)
        ]
        _write_compliance_manifest(
            assessment_dir, file_id, fw, outputs, expected_scope_keys=new_exp
        )
        _patch_file_primary_scope_fields(file_id, fw, new_exp)
        return {
            "status": "success",
            "message": "Removed this analysis scope; report PDF kept",
            "deleted_items": deleted_items,
            "scope_only": True,
        }

    # Single scope in manifest matches this row → remove whole report
    if len(expected) == 1 and expected[0] == scope_key:
        return None

    # Drift / legacy: strip manifest outputs for this key; keep file record + PDF
    if m:
        outputs = [
            o
            for o in (m.get("outputs") or [])
            if str(o.get("scope_key")) != str(scope_key)
        ]
        ek = [x for x in (m.get("expected_scope_keys") or []) if str(x) != str(scope_key)]
        _write_compliance_manifest(
            assessment_dir,
            file_id,
            fw,
            outputs,
            expected_scope_keys=ek if ek else None,
        )
    return {
        "status": "success",
        "message": "Removed compliance outputs for this scope",
        "deleted_items": deleted_items,
        "scope_only": True,
    }


@app.delete("/api/files/{file_id}")
async def delete_file(
    file_id: str,
    scope_key: Optional[str] = None,
    user_id: int = Depends(get_current_user),
):
    """
    删除文件。若提供 scope_key（多子范围上传中的一行），只删除该 scope 的合规 JSON/XLSX/MD 并更新 manifest，
    保留 PDF 与其它 scope；若该 scope 是清单中最后一个配置项，则退化为整份报告删除。
    """
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")

    sk = (scope_key or "").strip()
    if sk and file_info.get("file_type") == "report":
        partial = _try_delete_one_scope_only(file_id, file_info, sk)
        if partial is not None:
            return partial

    try:
        deleted_items = []
        
        # 1. 删除主PDF文件
        file_path = Path(file_info["file_path"])
        if file_path.exists():
            file_path.unlink()
            deleted_items.append(f"PDF文件: {file_path.name}")
        
        # 2. 删除提取的Markdown文件
        safe_filename = str(file_info.get("safe_filename") or "")
        stem = Path(safe_filename).stem if safe_filename else file_path.stem
        markdown_paths = [
            # Most common: saved next to the PDF (pending/processed/failed)
            file_path.parent / f"{stem}_extracted.md",
            # Optional: centralized markdown outputs
            Path(file_manager.markdown_outputs) / f"{stem}_extracted.md",
        ]

        for md_path in markdown_paths:
            if md_path.exists():
                md_path.unlink()
                deleted_items.append(f"Markdown文件: {md_path.name}")

        # 3. 删除嵌入向量文件（以 FileManager 的落盘规则为准）
        embeddings_paths = [
            Path(file_manager.embeddings_outputs) / f"{file_id}_segments.json",
            Path(file_manager.embeddings_outputs) / f"{file_id}_embeddings.npz",
            Path(file_manager.embeddings_outputs) / f"{file_id}_embeddings_meta.json",
            # Legacy variants (best-effort)
            Path(file_manager.embeddings_outputs) / f"{stem}_embeddings.json",
            Path(file_manager.embeddings_outputs) / f"{stem}_embeddings.npy",
        ]

        for emb_path in embeddings_paths:
            if emb_path.exists():
                emb_path.unlink()
                deleted_items.append(f"嵌入文件: {emb_path.name}")

        # 4. 删除合规输出：uploads/outputs/compliance_reports（JSON / XLSX / manifest 及一切含 file_id 的合规文件）
        _unlink_compliance_reports_dir_for_file_id(file_id, stem, deleted_items)
        _unlink_compliance_markdown_for_file_id(file_id, deleted_items)

        # Legacy location (older builds): backend/outputs
        legacy_outputs_dir = Path(__file__).resolve().parents[2] / "outputs"
        if legacy_outputs_dir.exists():
            legacy_paths: List[Path] = []
            legacy_paths.extend(legacy_outputs_dir.glob(f"*{file_id}*.md"))
            legacy_paths.extend(legacy_outputs_dir.glob(f"*{file_id}*compliance*.json"))
            legacy_paths.extend(legacy_outputs_dir.glob(f"*{file_id}*compliance*.xlsx"))
            for comp_path in legacy_paths:
                if comp_path.is_file():
                    try:
                        comp_path.unlink()
                        deleted_items.append(f"合规报告(legacy): {comp_path.name}")
                    except Exception as e:
                        logger.warning(f"Failed to remove legacy output {comp_path}: {e}")

        # 5. 清理系统组件中的相关数据
        # NOTE: ReportContent.document_id = doc_<stem>_<hash> (not the file_id)
        cleared_current = False
        current_report = system_components.get("current_report")
        if current_report and stem and hasattr(current_report, "document_id") and stem in str(getattr(current_report, "document_id", "")):
            system_components["current_report"] = None
            cleared_current = True
            deleted_items.append("内存中的报告内容")
        
        current_assessment = system_components.get("current_assessment")
        if current_assessment and hasattr(current_assessment, "report_id") and str(getattr(current_assessment, "report_id", "")) == str(file_id):
            system_components["current_assessment"] = None
            cleared_current = True
            deleted_items.append("内存中的评估结果")

        # Clear derived caches only when they are tied to the cleared current context
        if cleared_current:
            system_components["current_metrics"] = None
            system_components["current_framework"] = None
            system_components["current_industry"] = None
            system_components["current_semi_industry"] = None
            system_components["current_gri_sector"] = None
            system_components["current_gri_topic"] = None
            system_components["current_company"] = None
        
        # 6. 清理聊天机器人上下文
        if system_components.get("chatbot"):
            chatbot = system_components["chatbot"]
            with _chatbot_ops_lock:
                if getattr(chatbot, "report_content", None) is not None:
                    rc = chatbot.report_content
                    if stem and hasattr(rc, "document_id") and stem in str(
                        getattr(rc, "document_id", "")
                    ):
                        chatbot.report_content = None
                        chatbot.compliance_assessment = None
                        deleted_items.append("聊天机器人上下文")
        
        # 7. 从元数据中删除
        del file_manager.metadata["files"][file_id]
        file_manager._save_metadata()
        deleted_items.append("文件元数据")
        
        logger.info(f"File and related data deleted: {file_id}")
        logger.info(f"Deleted items: {', '.join(deleted_items)}")
        
        return {
            "status": "success",
            "message": "File and all related data deleted successfully",
            "deleted_items": deleted_items
        }
        
    except Exception as e:
        logger.error(f"Error deleting file and related data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/files/cleanup")
async def cleanup_old_files(days: int = 30):
    """
    清理旧文件
    
    Args:
        days: 保留天数
        
    Returns:
        清理结果
    """
    try:
        cleaned_count = file_manager.cleanup_old_files(days)
        return {
            "status": "success",
            "message": f"Cleaned up {cleaned_count} files older than {days} days"
        }
        
    except Exception as e:
        logger.error(f"Error cleaning up files: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/reports/latest")
async def get_latest_report(user_id: int = Depends(get_current_user)):
    """
    获取当前用户最新的合规分析报告（markdown）
    
    Returns:
        最新报告内容
    """
    try:
        json_dirs = [
            Path(file_manager.compliance_outputs),
            Path(__file__).resolve().parents[2] / "outputs",  # legacy backend/outputs
        ]
        md_dirs = [
            Path(file_manager.markdown_outputs),
            Path(file_manager.compliance_outputs),  # some legacy runs wrote markdown alongside JSON
            Path(__file__).resolve().parents[2] / "outputs",
        ]

        # 获取用户自己的报告文件列表，按上传时间倒序（支持 Subindustry_fileid_compliance.json 命名）
        user_files = file_manager.list_user_files(user_id, file_type="report")
        for f in sorted(user_files, key=lambda x: x["upload_time"], reverse=True):
            json_file = _find_assessment_json_path(f["file_id"], f)
            if not json_file or not json_file.exists():
                continue

            with open(json_file, "r", encoding="utf-8") as jf:
                assessment_data = json.load(jf)
            report_id = assessment_data.get("report_id")
            if not report_id:
                continue

            # 再用 report_id 找 markdown
            md_file = None
            for d in md_dirs:
                p = d / f"compliance_report_{report_id}.md"
                if p.exists():
                    md_file = p
                    break
            if not md_file:
                continue

            content = md_file.read_text(encoding="utf-8")
            return {
                "status": "success",
                "report_file": md_file.name,
                "content": content,
                "created_at": datetime.fromtimestamp(md_file.stat().st_mtime).isoformat()
            }

        raise HTTPException(status_code=404, detail="No reports found for current user")
        
    except Exception as e:
        logger.error(f"Error fetching latest report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/reports/{file_id}")
async def get_report_by_file_id(file_id: str, user_id: int = Depends(get_current_user)):
    """
    Get compliance analysis report for a specific file (只能访问自己的文件)

    Args:
        file_id: The file ID
        user_id: 当前用户ID (从token自动获取)

    Returns:
        Report content for the specified file
    """
    try:
        # 检查文件是否属于当前用户
        file_info = file_manager.get_file_info(file_id, user_id=user_id)
        if not file_info:
            raise HTTPException(status_code=404, detail="File not found or access denied")
        json_file = _find_assessment_json_path(file_id, file_info)
        if not json_file or not json_file.exists():
            raise HTTPException(status_code=404, detail=f"No assessment found for file {file_id}")

        # Read JSON to get report_id
        with open(json_file, 'r', encoding='utf-8') as f:
            assessment_data = json.load(f)

        report_id = assessment_data.get('report_id')
        if not report_id:
            logger.warning(f"Report ID not found in assessment data for file_id={file_id}")
            raise HTTPException(status_code=404, detail="Report ID not found in assessment data")

        # Now load the markdown report using the report_id
        md_dirs = [
            Path(file_manager.markdown_outputs),
            Path(file_manager.compliance_outputs),
            legacy_outputs_dir,
        ]
        report_file = None
        for d in md_dirs:
            p = d / f"compliance_report_{report_id}.md"
            if p.exists():
                report_file = p
                break

        # Fallback: try to find any markdown report containing the report_id
        if not report_file:
            for d in [x for x in md_dirs if x.exists()]:
                matches = list(d.glob(f"*{report_id}*.md"))
                if matches:
                    report_file = matches[0]
                    break

        if not report_file:
            raise HTTPException(status_code=404, detail=f"Report file not found for report_id {report_id}")

        # Read the markdown content
        with open(report_file, 'r', encoding='utf-8') as f:
            content = f.read()

        return {
            "status": "success",
            "file_id": file_id,
            "report_id": report_id,
            "report_file": report_file.name,
            "content": content,
            "created_at": datetime.fromtimestamp(report_file.stat().st_mtime).isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching report for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
async def health_check():
    """
    Health check endpoint to monitor system status
    """
    try:
        import time
        
        return {
            "status": "healthy",
            "timestamp": time.time(),
            "services": {
                "api": "running",
                "llm_client": bool(system_components.get("llm_client")),
                "embedding_model": bool(system_components.get("content_embedder"))
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
