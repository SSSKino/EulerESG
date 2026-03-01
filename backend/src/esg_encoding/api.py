"""
ESG System API Endpoints
"""

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional
import os
import re
import json
import pandas as pd
from pathlib import Path
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv
import time
import threading
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
from .disclosure_inference import DisclosureInferenceEngine
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
    "current_company": None  # Store company name
}

# -----------------------------
# Cross-analysis Excel metrics job state
# -----------------------------
_excel_metrics_jobs = {}  # key -> {"thread": Thread, "started_at": float}
_excel_metrics_jobs_lock = threading.Lock()


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
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "A system error ocurred"}
    )


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


@app.post("/api/upload-report")
async def upload_report(
    file: UploadFile = File(...),
    industry: Optional[str] = Form(None),
    semiIndustry: Optional[str] = Form(None),
    framework: Optional[str] = Form(None),
    user_id: int = Depends(get_current_user)
):
    """
    Upload and process ESG report
    
    Args:
        file: PDF file
        industry: Main industry classification (optional)
        semiIndustry: Sub-industry (for SASB metrics selection)
        framework: Framework selection (SASB/GRI/TCFD)
        
    Returns:
        Processing results, including complete processing chain output (report processing + metrics matching + classification + knowledge base update)
    """
    # ===== DEBUG: Function called =====
    logger.info(f"=== UPLOAD_REPORT ENDPOINT CALLED ===")
    logger.info(f"File: {file.filename}")
    logger.info(f"Framework: {framework}")
    logger.info(f"Industry: {industry}")
    logger.info(f"SemiIndustry: {semiIndustry}")
    logger.info(f"=== END DEBUG ===")
    
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    try:
        logger.info("=== STARTING FILE PROCESSING ===")
        # Read file content
        content = await file.read()
        logger.info(f"File content read successfully, size: {len(content)} bytes")
        
        # Save file using file manager
        logger.info("Saving file using file manager...")
        file_info = file_manager.save_uploaded_file(
            file_content=content,
            filename=file.filename,
            file_type="report",
            industry=industry,
            framework=framework,
            semi_industry=semiIndustry,
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
        
        # Store framework and industry information
        system_components["current_framework"] = framework
        system_components["current_industry"] = industry
        system_components["current_semi_industry"] = semiIndustry
        # Extract company name from filename (remove extension)
        company_name = file.filename.rsplit('.', 1)[0] if file.filename else "Unknown Company"
        system_components["current_company"] = company_name
        logger.info(f"Stored framework and industry info - Framework: {framework}, Industry: {industry}, Semi-Industry: {semiIndustry}, Company: {company_name}")
        
        # Get report summary
        logger.info("Getting report summary...")
        summary = encoder.get_report_summary(report_content)
        logger.info("Report summary obtained")
        
        # Load corresponding metrics based on user-selected framework and industry
        metrics = None
        if framework == "SASB" and semiIndustry:
            # Use SASB metrics
            processor = system_components["metric_processor"]
            metrics = processor.load_sasb_metrics_by_industry(semiIndustry)
            
            system_components["current_metrics"] = metrics
            logger.info(f"Loaded SASB metrics for industry: {semiIndustry}")
        else:
            # Industry/semiIndustry must be provided
            raise ValueError("Industry classification (semiIndustry) is required for analysis. Please provide a valid industry.")
        
        # Now with report and metrics, perform complete processing chain
        if metrics:
            try:
                # Execute complete processing chain: dual-channel retrieval + disclosure inference engine classification
                dual_retriever = system_components["dual_retriever"]
                retrieval_results = dual_retriever.retrieve_for_collection(
                    report_content,
                    metrics
                )
                
                #print("======== CHECK ALL METRICS ========")
                #print(metrics)
                # Execute disclosure inference (classification)
                t_start = time.time()
                disclosure_engine = system_components["disclosure_engine"]
                assessment = disclosure_engine.analyze_compliance(
                    retrieval_results,
                    report_content,
                    file_info["file_path"],
                    metrics,  # Pass all metrics
                    framework=framework,
                    industry=industry,
                    semi_industry=semiIndustry
                )
                t_end = time.time()
                logger.info(f"Disclosure inference took {t_end - t_start} seconds")
                
                # Store assessment results
                system_components["current_assessment"] = assessment
                
                # Update chatbot knowledge base (including ESG content + classification results and comments)
                system_components["chatbot"].load_context(
                    report_content,
                    assessment
                )

                # Pre-build HippoRAG index so chat won"t fall back while indexing.
                try:
                    retriever = getattr(system_components["chatbot"], "_hipporag_retriever", None)
                    if retriever and getattr(retriever, "is_enabled", lambda: False)():
                        retriever.ensure_index(report_content.document_id, report_content)
                except Exception as e:
                    logger.warning(f"HippoRAG pre-index failed for {file_info['file_id']}: {e}")
                
                # Generate and save compliance report (canonical location: uploads/outputs/markdown/)
                compliance_report = disclosure_engine.generate_compliance_report(assessment)
                report_path = Path(file_manager.markdown_outputs) / f"compliance_report_{assessment.report_id}.md"
                report_path.parent.mkdir(parents=True, exist_ok=True)
                report_path.write_text(compliance_report, encoding="utf-8")

                # Save JSON / XLSX assessment data for frontend use (canonical: uploads/outputs/compliance_reports/)
                json_report_dir = Path(file_manager.compliance_outputs)
                json_report_dir.mkdir(parents=True, exist_ok=True)

                json_report_path = json_report_dir / f"{file_info['file_id']}_compliance.json"
                xlsx_report_path = json_report_dir / f"{file_info['file_id']}_compliance.xlsx"
                
                # Convert assessment data to JSON format
                assessment_json = {
                    "report_id": assessment.report_id,
                    "assessment_date": assessment.assessment_date.isoformat(),
                    "total_metrics": assessment.total_metrics_analyzed,
                    "overall_score": assessment.overall_compliance_score,
                    "total_metrics_analyzed": assessment.total_metrics_analyzed,
                    "overall_compliance_score": assessment.overall_compliance_score,
                    "report_file_path": str(report_path),
                    "framework": getattr(assessment, "framework", None),
                    "disclosure_summary": {
                        "fully_disclosed": assessment.disclosure_summary.get(DisclosureStatus.FULLY_DISCLOSED, 0),
                        "partially_disclosed": assessment.disclosure_summary.get(DisclosureStatus.PARTIALLY_DISCLOSED, 0),
                        "not_disclosed": assessment.disclosure_summary.get(DisclosureStatus.NOT_DISCLOSED, 0)
                    },
                    "metric_analyses": [
                        {
                            "metric_id": analysis.metric_id,
                            "metric_name": analysis.metric_name,
                            "metric_code": analysis.metric_code,
                            "disclosure_status": analysis.disclosure_status.value if hasattr(analysis.disclosure_status, 'value') else analysis.disclosure_status,
                            "reasoning": analysis.reasoning,
                            "unit": getattr(analysis, 'unit', ''),
                            "category": getattr(analysis, 'category', ''),
                            "topic": getattr(analysis, 'topic', ''),
                            "type": getattr(analysis, 'type', ''),
                            "page": getattr(analysis, 'page', None),
                            "value": getattr(analysis, 'value', None),
                            "context": getattr(analysis, 'context', None),
                            "evidence_segments": getattr(analysis, 'evidence_segments', None) or [],
                            "improvement_suggestions": getattr(analysis, 'improvement_suggestions', None) or []
                        }
                        for analysis in assessment.metric_analyses
                    ]
                }

                # Enforce UI/Output rules in the persisted JSON as well:
                # - not_disclosed -> remove page/value
                # - partially_disclosed -> replace value with a textual reason (no concrete numbers)
                for m in assessment_json.get("metric_analyses", []) or []:
                    s = str(m.get("disclosure_status", "") or "").strip().lower()
                    if "partial" in s:
                        m["value"] = (
                            "Partially disclosed: referenced in the report, but the disclosure is not clear enough to extract a specific value "
                            "(e.g., missing a precise figure, unit, or reporting period)."
                        )
                    elif "not" in s:
                        m["page"] = None
                        m["value"] = None
                
                with open(json_report_path, "w", encoding="utf-8") as f:
                    json.dump(assessment_json, f, indent=2, ensure_ascii=False)

                logger.info(f"Assessment JSON saved to: {json_report_path}")

                ### ======== JSON FLATTENING ========


                df_flat = pd.json_normalize(
                    assessment_json,
                    record_path='metric_analyses',
                    meta=[
                        'report_id', 
                        'assessment_date', 
                        'total_metrics', 
                        'overall_score',
                        ['disclosure_summary', 'fully_disclosed'],
                        ['disclosure_summary', 'partially_disclosed'],
                        ['disclosure_summary', 'not_disclosed']
                    ]
                )

                column_map = {
                    'metric_name': 'Metric',
                    'category': 'Category',
                    'unit': 'Unit',
                    'metric_code': 'Code',
                    'topic': 'Topic',
                    'type': 'Type',
                    'context': 'Value',         # Assuming 'context' is the ground-truth value from the doc
                    'page': 'Page',
                    'reasoning': 'Context',     # Assuming 'reasoning' is your model's output (like the 'ChatGPT' column)
                    'disclosure_status': 'Model Disclosure Status' # Adding this as it's important
                }
                df_renamed = df_flat.rename(columns=column_map)

                final_columns = [
                    'Metric',
                    'Category',
                    'Unit',
                    'Code',
                    'Topic',
                    'Type',
                    'Value',    # From JSON 'context'
                    'Page',     # From JSON 'page'
                    'Context',  # From JSON 'reasoning'
                    'Model Disclosure Status', # From JSON 'disclosure_status'
                    'ChatGPT',  # New empty column for benchmarking
                    'InputWrong',# New empty column
                    'comment'   # New empty column
                ]

                df_final = df_renamed.reindex(columns=final_columns)
                df_final.to_excel(xlsx_report_path, index=False, sheet_name="Benchmark")

                logger.info(f"Successfully converted JSON to Excel at: {xlsx_report_path}")
                
                file_manager.move_report_file(file_info["file_id"], "processed")
                logger.info(f"Complete processing chain finished. Score: {assessment.overall_compliance_score:.2%}")
                
                return {
                    "status": "success",
                    "message": "Report uploaded and fully processed",
                    "report_id": report_content.document_id,
                    "file_id": file_info["file_id"],
                    "summary": summary,
                    "assessment": {
                        "total_metrics": assessment.total_metrics_analyzed,
                        "overall_score": assessment.overall_compliance_score,
                        "disclosure_summary": assessment.disclosure_summary,
                        "report_path": str(report_path)
                    }
                }
                
            except Exception as assessment_error:
                error_str = str(assessment_error)
                logger.error(f"Error in assessment processing: {assessment_error}")
                
                # 检查是否是LLM访问错误
                is_llm_error = "403" in error_str or "AccessDenied" in error_str or "Unpurchased" in error_str or "LLM" in error_str
                
                # If inference fails, still save report but mark as partially processed
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
        else:
            # When no metrics, only process report and wait for metrics upload
            file_manager.move_report_file(file_info["file_id"], "processed")
            
            logger.info(f"Report processed, waiting for metrics: {file.filename}")
            
            return {
                "status": "success",
                "message": "Report processed, awaiting metrics for full analysis",
                "report_id": report_content.document_id,
                "file_id": file_info["file_id"],
                "summary": summary
            }
        
    except Exception as e:
        logger.error(f"Error processing report: {e}")
        # If processing fails, move to failed directory
        if 'file_info' in locals():
            file_manager.move_report_file(file_info["file_id"], "failed")
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
        retrieval_results = dual_retriever.retrieve_for_collection(
            system_components["current_report"],
            system_components["current_metrics"]
        )
        
        # 执行披露推理
        disclosure_engine = system_components["disclosure_engine"]
        assessment = disclosure_engine.analyze_compliance(
            retrieval_results,
            system_components["current_report"],
            system_components["current_report"].document_content.file_path,
            system_components["current_metrics"],  # 传入所有指标
            framework=system_components.get("current_framework"),
            industry=system_components.get("current_industry"),
            semi_industry=system_components.get("current_semi_industry")
        )
        
        # 存储评估结果
        system_components["current_assessment"] = assessment
        
        # 更新聊天机器人上下文
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

        json_report_path = json_report_dir / f"{file_id}_compliance.json"
        
        # 将评估数据转换为JSON格式
        assessment_json = {
            "report_id": assessment.report_id,
            "assessment_date": assessment.assessment_date.isoformat(),
            "total_metrics": assessment.total_metrics_analyzed,
            "overall_score": assessment.overall_compliance_score,
                    "total_metrics_analyzed": assessment.total_metrics_analyzed,
                    "overall_compliance_score": assessment.overall_compliance_score,
                    "report_file_path": str(report_path),
                    "framework": getattr(assessment, "framework", None),
                    "disclosure_summary": {
                "fully_disclosed": assessment.disclosure_summary.get(DisclosureStatus.FULLY_DISCLOSED, 0),
                "partially_disclosed": assessment.disclosure_summary.get(DisclosureStatus.PARTIALLY_DISCLOSED, 0),
                "not_disclosed": assessment.disclosure_summary.get(DisclosureStatus.NOT_DISCLOSED, 0)
            },
            "metric_analyses": [
                {
                    "metric_id": analysis.metric_id,
                    "metric_name": analysis.metric_name,
                    "disclosure_status": analysis.disclosure_status.value if hasattr(analysis.disclosure_status, 'value') else analysis.disclosure_status,
                    "reasoning": analysis.reasoning,
                    "unit": getattr(analysis, 'unit', ''),
                    "category": getattr(analysis, 'category', ''),
                    "topic": getattr(analysis, 'topic', ''),
                    "type": getattr(analysis, 'type', ''),
                    "page": getattr(analysis, 'page', None),
                    "value": getattr(analysis, 'value', None),
                    "context": getattr(analysis, 'context', None)
                }
                for analysis in assessment.metric_analyses
            ]
        }
        
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
                                "type": getattr(metric, 'sasb_type', '')
                            }
                            break
                
                excel_metrics.append({
                    "metric_id": analysis.metric_code if hasattr(analysis, 'metric_code') else analysis.metric_id,
                    "metric_name": analysis.metric_name,
                    "disclosure_status": analysis.disclosure_status.value if hasattr(analysis.disclosure_status, 'value') else analysis.disclosure_status,
                    "reasoning": analysis.reasoning,
                    "value": getattr(analysis, 'value', None),
                    "page": getattr(analysis, 'page', None),
                    "context": getattr(analysis, 'context', None),
                    "category": metric_info.get('category', getattr(analysis, 'category', '')),
                    "unit": metric_info.get('unit', getattr(analysis, 'unit', '')),
                    "topic": metric_info.get('topic', getattr(analysis, 'topic', '')),
                    "type": metric_info.get('type', getattr(analysis, 'type', ''))
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

    # 1) Load compliance assessment JSON for this file_id
    assessment_candidates = [
        assessment_dir / f"{file_id}_compliance.json",
    ]
    if stem:
        assessment_candidates.append(assessment_dir / f"{stem}_compliance.json")
    if legacy_outputs_dir.exists():
        assessment_candidates.append(legacy_outputs_dir / f"{file_id}_compliance.json")
        if stem:
            assessment_candidates.append(legacy_outputs_dir / f"{stem}_compliance.json")

    assessment_json_path = next((p for p in assessment_candidates if p.exists()), None)

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
                    # Support older key "status" (string) and newer key "disclosure_status"
                    status_raw = d.get("disclosure_status") or d.get("status")
                    d["disclosure_status"] = _parse_status(status_raw)
                    d.pop("status", None)
                    metric_analyses.append(DisclosureAnalysis(**d))
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
    
    history_list = _load_chat_history(file_id) # Returns List[Dict]
    assessment, report_content = _load_specific_report_context(file_id)
    
    chatbot.load_context(report_content, assessment)
    
    # We treat file_id as the session_id for simplicity
    chatbot.restore_session(session_id=file_id, history_data=history_list)
    
    # Ensure request.session_id matches file_id
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
            # Map string status to enum
            status_str = item["disclosure_status"]
            if status_str == "fully_disclosed" or status_str == "DisclosureStatus.FULLY_DISCLOSED":
                status = DisclosureStatus.FULLY_DISCLOSED
            elif status_str == "partially_disclosed" or status_str == "DisclosureStatus.PARTIALLY_DISCLOSED":
                status = DisclosureStatus.PARTIALLY_DISCLOSED
            else:
                status = DisclosureStatus.NOT_DISCLOSED

            analysis = DisclosureAnalysis(
                metric_id=item["metric_id"],
                metric_name=item["metric_name"],
                metric_code=item.get("metric_code", item["metric_id"]),
                disclosure_status=status,
                reasoning=item["reasoning"],
                evidence_segments=item.get("evidence_segments", []),
                improvement_suggestions=item.get("improvement_suggestions", []),
                category=item.get("category", ""),
                unit=item.get("unit", ""),
                type=item.get("type", ""),
                value=item.get("value"),
                page=item.get("page")
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

        # 如果没有数据，仍然允许聊天，但只能回答一般性问题
        if not latest_assessment and not report_content:
            logger.warning("No analysis data available for chat. Chatbot will work in general mode only.")
            # 不加载上下文，让chatbot回答一般性问题
            # chatbot.load_context()  # 不调用，让chatbot使用默认行为
        # 加载到聊天机器人
        elif latest_assessment and report_content:
            # 如果有评估和报告内容，创建增强的知识库
            enhanced_content = _create_enhanced_knowledge_base(latest_assessment, report_content)
            chatbot.load_context(
                compliance_assessment=latest_assessment,
                report_content=enhanced_content if enhanced_content else report_content
            )
            segments_count = len(enhanced_content.document_content.segments) if enhanced_content and hasattr(enhanced_content, 'document_content') else (len(report_content.document_content.segments) if report_content and hasattr(report_content, 'document_content') else 0)
            logger.info(f"Loaded enhanced knowledge base: {latest_assessment.total_metrics_analyzed} metrics + {segments_count} content segments")
        elif latest_assessment:
            # 只有评估数据
            chatbot.load_context(compliance_assessment=latest_assessment)
            logger.info(f"Loaded assessment data: {latest_assessment.total_metrics_analyzed} metrics")
        elif report_content:
            # 只有报告内容
            chatbot.load_context(report_content=report_content)
            segments_count = len(report_content.document_content.segments) if hasattr(report_content, 'document_content') and report_content.document_content else 0
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
        # Key normalization
        if "value" not in a and "data" in a:
            a["value"] = a.get("data")
        a.setdefault("page", None)
        a.setdefault("value", None)
        a.setdefault("unit", None)
        a.setdefault("context", a.get("specific_data_found") or a.get("evidence") or "")
        a.setdefault("evidence_segments", [])
        a.setdefault("improvement_suggestions", [])

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

        if status_norm == "not_disclosed":
            a["page"] = None
            a["value"] = None
        elif status_norm == "partially_disclosed":
            a["value"] = (
                "Partially disclosed: referenced in the report, but the disclosure is not clear enough to extract a specific value "
                "(e.g., missing a precise figure, unit, or reporting period)."
            )
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
        return {
            "metric_id": analysis.metric_id,
            "metric_name": analysis.metric_name,
            "disclosure_status": analysis.disclosure_status,
            "reasoning": analysis.reasoning,
            "page": getattr(analysis, "page", None),
            "value": getattr(analysis, "value", None),
            "unit": getattr(analysis, "unit", None),
            "context": getattr(analysis, "context", None) or "",
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

        # 按上传时间倒序，找到第一个存在合规 JSON 的文件
        for f in sorted(user_files, key=lambda x: x["upload_time"], reverse=True):
            for d in (canonical_dir, legacy_dir):
                json_file = d / f"{f['file_id']}_compliance.json"
                if json_file.exists():
                    logger.info(f"Loading latest assessment for user {user_id} from: {json_file}")
                    with open(json_file, 'r', encoding='utf-8') as fp:
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


@app.get("/api/assessment/{file_id}")
async def get_assessment_by_file(file_id: str, user_id: int = Depends(get_current_user)):
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
        search_dirs = [canonical_dir]
        if legacy_dir.exists():
            search_dirs.append(legacy_dir)

        candidate_names = [f"{file_id}_compliance.json"]
        if base_name:
            candidate_names.append(f"{base_name}_compliance.json")

        json_file = None
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



@app.get("/api/cross-analysis/reports", response_model=CrossReportsResponse)
async def cross_analysis_reports(ids: str):
    """
    Cross Analysis: batch resolve report display names (company/organization) and basic metadata.
    ids: comma-separated file_ids
    """
    file_ids = [x.strip() for x in (ids or "").split(",") if x.strip()]
    if len(file_ids) < 2:
        raise HTTPException(status_code=400, detail="At least two file_ids are required")

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
    safe_filename = str(file_info.get("safe_filename") or "")
    base_name = Path(safe_filename).stem if safe_filename else ""

    canonical_dir = Path(file_manager.compliance_outputs)
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

    records: list[dict] = []
    mtimes: list[float] = []

    for fid in file_ids:
        file_info = file_manager.get_file_info(fid, user_id=user_id)
        if not file_info:
            # access denied / missing
            continue

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
            value_str = "" if value is None else str(value)
            unit = str(a.get("unit") or a.get("Unit") or "").strip() or None
            page = to_page(a.get("page") or a.get("Page") or a.get("page_number") or a.get("pageNumber"))
            typ = normalize_type_label(a.get("type") or a.get("Type"))
            cat = normalize_category_label(a.get("category") or a.get("Category"))
            # detail should come from reasoning (not context)
            detail = str(a.get("reasoning") or "").strip()

            # Only keep fully_disclosed (compat: if status missing but value looks numeric, keep it)
            ds = str(a.get("disclosure_status") or "").strip().lower()
            if ds and ds != "fully_disclosed":
                continue
            if (not ds) and (not looks_numeric(value_str)):
                continue

            # year must be derived from report name per requirement
            year = _extract_year_from_text(name) or name_year

            topic = metric_name or metric_code or metric_id or "Metric"
            sub_topic = metric_code or metric_id or ""

            records.append(
                {
                    # keep old cross-analysis record shape (v2) but also expose value
                    "id": fid,
                    "name": name,
                    "primary_navigation": typ,
                    "secondary_navigation": cat,
                    "topic": topic,
                    "page": page,
                    # UI adapter can read either `data` or `value`
                    "data": value_str,
                    "value": value_str,
                    "year": year,
                    "unit": unit,
                    "detail": detail,
                    "disclosure_status": a.get("disclosure_status"),
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


@app.delete("/api/files/{file_id}")
async def delete_file(file_id: str, user_id: int = Depends(get_current_user)):
    """
    完全删除文件及其所有相关数据 (只能删除自己的文件)
    
    Args:
        file_id: 文件ID
        user_id: 当前用户ID (从token自动获取)
        
    Returns:
        删除结果
    """
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found or access denied")
    
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

        # 4. 删除合规分析报告
        compliance_paths = [
            Path(file_manager.compliance_outputs) / f"{file_id}_compliance.json",
        ]
        if stem:
            compliance_paths.append(Path(file_manager.compliance_outputs) / f"{stem}_compliance.json")

        # Legacy location (older builds): backend/outputs
        legacy_outputs_dir = Path(__file__).resolve().parents[2] / "outputs"
        if legacy_outputs_dir.exists():
            compliance_paths.extend(list(legacy_outputs_dir.glob(f"*{file_id}*.md")))
            compliance_paths.extend(list(legacy_outputs_dir.glob(f"*{file_id}*compliance*.json")))

        for comp_path in compliance_paths:
            if comp_path.exists():
                comp_path.unlink()
                deleted_items.append(f"合规报告: {comp_path.name}")

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
            system_components["current_company"] = None
        
        # 6. 清理聊天机器人上下文
        if system_components.get("chatbot"):
            chatbot = system_components["chatbot"]
            # 清理与该文件相关的聊天上下文
            if getattr(chatbot, "report_content", None) is not None:
                rc = chatbot.report_content
                if stem and hasattr(rc, "document_id") and stem in str(getattr(rc, "document_id", "")):
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

        # 获取用户自己的报告文件列表，按上传时间倒序
        user_files = file_manager.list_user_files(user_id, file_type="report")
        for f in sorted(user_files, key=lambda x: x["upload_time"], reverse=True):
            # 先查 JSON 拿 report_id
            json_file = None
            for d in json_dirs:
                p = d / f"{f['file_id']}_compliance.json"
                if p.exists():
                    json_file = p
                    break
            if not json_file:
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
        legacy_outputs_dir = Path(__file__).resolve().parents[2] / "outputs"
        json_dirs = [Path(file_manager.compliance_outputs), legacy_outputs_dir]

        json_file = None
        for d in json_dirs:
            p = d / f"{file_id}_compliance.json"
            if p.exists():
                json_file = p
                break
        if not json_file:
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
