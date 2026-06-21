"""FastAPI application factory and router wiring."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger

from ..exceptions import AccessError, InputError
from ..file_manager import file_manager
from . import error_handlers
from ..services import system_service
from .routers import auth, chat, compliance, cross_analysis, excel_metrics, files, reports, system

load_dotenv()

app = FastAPI(
    title="ESG Analysis System API",
    description="Complete ESG report analysis and compliance assessment system",
    version="1.0.0",
)

FRONTEND_ORIGINS_STR = os.getenv(
    "FRONTEND_ORIGINS",
    "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000,http://127.0.0.1:3001,http://192.168.254.1:3001",
)
FRONTEND_ORIGINS = [origin.strip() for origin in FRONTEND_ORIGINS_STR.split(",") if origin.strip()]
logger.info(f"CORS allowed origins: {FRONTEND_ORIGINS}")

try:
    _uploads_dir = str(file_manager.base_dir.resolve())
    if os.path.isdir(_uploads_dir):
        app.mount("/uploads", StaticFiles(directory=_uploads_dir), name="uploads")
        logger.info(f"Mounted /uploads -> {_uploads_dir}")
    else:
        logger.warning(f"Uploads dir not found: {_uploads_dir} (skip mount)")
except Exception as _e:
    logger.warning(f"Failed to mount /uploads: {_e}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

app.add_event_handler("startup", system_service.startup_event)
app.add_exception_handler(InputError, error_handlers.input_error_handler)
app.add_exception_handler(AccessError, error_handlers.access_error_handler)
app.add_exception_handler(Exception, error_handlers.general_exception_handler)

app.include_router(system.router)
app.include_router(auth.router)
app.include_router(reports.router)
app.include_router(compliance.router)
app.include_router(chat.router)
app.include_router(files.router)
app.include_router(cross_analysis.router)
app.include_router(excel_metrics.router)

