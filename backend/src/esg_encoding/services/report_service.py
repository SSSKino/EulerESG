"""Report upload and retrieval service functions."""

from .common import *  # noqa: F401,F403
from fastapi import Header, Query
from fastapi.responses import StreamingResponse

from ..auth.service import get_user_id_from_authorization
from ..gpu_model_lifecycle import with_backend_model_task
from .report_jobs import (
    TERMINAL_STATUSES,
    create_report_job,
    get_executor as get_report_job_executor,
    get_report_job_events_since,
    get_report_job_owner,
    snapshot_report_job,
    update_report_job,
)


def _runtime_resource_snapshot() -> dict:
    snapshot: dict = {}
    try:
        import resource
        rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        snapshot["peak_rss_mb"] = round(rss / (1024 if os.name != "darwin" else 1024 * 1024), 2)
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            snapshot["gpu_allocated_mb"] = round(torch.cuda.memory_allocated() / 1048576, 2)
            snapshot["gpu_peak_allocated_mb"] = round(torch.cuda.max_memory_allocated() / 1048576, 2)
    except Exception:
        pass
    return snapshot



def _emit_upload_progress(progress_cb, stage: str, message: str, progress: Optional[float] = None, **extra) -> None:
    """Send upload/report processing progress to the active SSE job."""
    if not progress_cb:
        return
    try:
        progress_cb(stage=stage, message=message, progress=progress, extra=extra or None)
    except Exception as exc:
        logger.debug(f"Upload progress callback skipped: {exc}")


def _patch_file_metadata(file_id: str, **updates) -> None:
    """Best-effort metadata patch for dashboard polling and refresh."""
    try:
        finfo = file_manager.metadata.get("files", {}).get(file_id)
        if isinstance(finfo, dict):
            finfo.update({k: v for k, v in updates.items() if v is not None})
            file_manager._save_metadata()
    except Exception as exc:
        logger.debug(f"File metadata patch skipped for {file_id}: {exc}")


def _ocr_progress_metadata_updates(extra: Optional[dict]) -> dict:
    """Extract internal OCR progress fields for backend-side diagnostics only."""
    if not isinstance(extra, dict):
        return {}

    progress = extra.get("paddle_progress")
    if not isinstance(progress, dict):
        progress = extra

    field_map = {
        "total_pages": "processing_total_pages",
        "pages_done": "processing_pages_done",
        "pages_success": "processing_pages_success",
        "pages_failed": "processing_pages_failed",
        "total_units": "processing_total_units",
        "units_done": "processing_units_done",
        "units_success": "processing_units_success",
        "units_failed": "processing_units_failed",
        "units_running": "processing_units_running",
        "units_queued": "processing_units_queued",
        "page_batch_size": "processing_page_batch_size",
        "running_batches": "processing_running_batches",
    }
    return {
        target_key: progress.get(source_key)
        for source_key, target_key in field_map.items()
        if progress.get(source_key) is not None
    }


def _format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@with_backend_model_task("upload_report")
def _sync_upload_report_body(
    content: Optional[bytes],
    filename: str,
    industry: Optional[str],
    semiIndustry: Optional[str],
    framework: Optional[str],
    griSector: Optional[str],
    griTopic: Optional[str],
    scopeSlugs: Optional[str],
    user_id: int,
    pre_saved_file_info: Optional[dict] = None,
    progress_cb=None,
) -> dict:
    """PDF encode + assessment off the event loop (keeps /api/files responsive)."""
    pipeline_started = time.perf_counter()
    performance: dict = {"scopes": [], "resources_start": _runtime_resource_snapshot()}
    try:
        if pre_saved_file_info is not None:
            file_info = dict(pre_saved_file_info)
            logger.info(f"Using pre-saved report file: {file_info.get('file_path')}")
            _emit_upload_progress(progress_cb, "file_saved", "File saved. Starting processing.", 5, file_id=file_info.get("file_id"))
        else:
            logger.info("Saving file using file manager...")
            _emit_upload_progress(progress_cb, "saving", "Saving uploaded PDF.", 2)
            file_info = file_manager.save_uploaded_file(
                file_content=content or b"",
                filename=filename,
                file_type="report",
                industry=industry,
                framework=framework,
                semi_industry=semiIndustry,
                gri_sector=griSector,
                gri_topic=griTopic,
                user_id=user_id
            )
            logger.info(f"File saved at: {file_info['file_path']}")
            _emit_upload_progress(progress_cb, "file_saved", "File saved. Starting processing.", 5, file_id=file_info.get("file_id"))

        _patch_file_metadata(file_info["file_id"], status="processing", processing_stage="processing", processing_progress=5)

        # Process PDF
        logger.info("Starting PDF processing...")
        _emit_upload_progress(progress_cb, "pdf_processing", "Extracting report content with PaddleOCR-VL.", 8, file_id=file_info.get("file_id"))
        encoder = system_components["report_encoder"]
        old_progress_cb = getattr(getattr(encoder, "extractor", None), "progress_callback", None)
        if getattr(encoder, "extractor", None) is not None:
            encoder.extractor.progress_callback = progress_cb
        encode_started = time.perf_counter()
        try:
            report_content = encoder.encode_pdf(file_info["file_path"], save_markdown=True)
        finally:
            if getattr(encoder, "extractor", None) is not None:
                encoder.extractor.progress_callback = old_progress_cb
        performance["encode_seconds"] = round(time.perf_counter() - encode_started, 3)
        table_segments = [segment for segment in report_content.document_content.segments if segment.segment_type == "table"]
        performance["table_quality"] = {
            "first_pass_tables": len(table_segments),
            "review_candidates": sum(1 for segment in table_segments if segment.review_status == "needs_review"),
            "second_pass_tables": sum(1 for segment in table_segments if int(segment.parse_pass or 1) > 1),
            "replaced_tables": sum(1 for segment in table_segments if (segment.structured_data or {}).get("second_pass_replaced")),
            "conflicted_tables": sum(1 for segment in table_segments if segment.conflicts),
            "second_pass_budget_ratio": float(os.getenv("REPORT_TABLE_SECOND_PASS_MAX_RATIO", "0.30") or "0.30"),
        }

        # IMPORTANT: Align document_id with file_id so all downstream (chat/cache/output filenames)
        # use a single stable identifier.
        try:
            report_content.document_id = file_info["file_id"]
            report_content.document_content.document_id = file_info["file_id"]
        except Exception:
            pass

        # Persist segments + embeddings for fast chat retrieval after restart.
        # (This is crucial for "load previous embeddings" requirement.)
        persist_started = time.perf_counter()
        try:
            file_manager.save_report_artifacts(file_info["file_id"], report_content)
        except Exception as e:
            logger.warning(f"Failed to persist report artifacts for {file_info['file_id']}: {e}")
        performance["artifact_persist_seconds"] = round(time.perf_counter() - persist_started, 3)
        logger.info("PDF processing completed")
        _emit_upload_progress(progress_cb, "pdf_processed", "Report text extraction and embeddings completed.", 50, file_id=file_info.get("file_id"))

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
        _emit_upload_progress(progress_cb, "summary_ready", "Report summary created.", 52, file_id=file_info.get("file_id"))

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
        _emit_upload_progress(progress_cb, "assessment_start", f"Starting compliance assessment for {len(scopes_list)} scope(s).", 55, file_id=file_info.get("file_id"), total_scopes=len(scopes_list))

        try:
            for scope_index, (scope_key, params) in enumerate(scopes_list, 1):
                scope_started = time.perf_counter()
                _emit_upload_progress(progress_cb, "assessment_scope", f"Analyzing scope {scope_index}/{len(scopes_list)}: {scope_key}.", 55 + 35 * ((scope_index - 1) / max(1, len(scopes_list))), file_id=file_info.get("file_id"), scope_key=scope_key, scope_index=scope_index, total_scopes=len(scopes_list))
                metrics_started = time.perf_counter()
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

                metrics = _prepare_metrics_for_retrieval(processor, metrics)
                system_components["current_metrics"] = metrics
                logger.info(
                    f"Loaded metrics for scope_key={scope_key} ({fw}), "
                    f"count={len(metrics.metrics)}, elapsed={time.perf_counter() - metrics_started:.2f}s"
                )

                retrieval_started = time.perf_counter()
                retrieval_results = retrieve_metric_collection(
                    report_content, metrics, config=system_components.get("config")
                )
                logger.info(
                    f"Metric retrieval scope={scope_key} took "
                    f"{time.perf_counter() - retrieval_started:.2f}s"
                )
                t_start = time.perf_counter()
                assessment = disclosure_engine.analyze_compliance(
                    retrieval_results,
                    report_content,
                    file_info["file_path"],
                    metrics,
                    framework=framework,
                    industry=industry,
                    semi_industry=semi_for_disclosure,
                )
                analysis_elapsed = time.perf_counter() - t_start
                logger.info(
                    f"Disclosure inference scope={scope_key} took "
                    f"{analysis_elapsed:.2f}s"
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
                sasb_metrics_filename = f"{sanitized_part}_{file_info['file_id']}_sasb_metrics.json"
                sasb_metrics_result_path = json_report_dir / sasb_metrics_filename

                assessment_json = _build_compliance_assessment_json(
                    assessment,
                    str(report_path),
                    _compliance_result_filename(filename or "", llm_model_name),
                )

                with open(json_report_path, "w", encoding="utf-8") as f:
                    json.dump(assessment_json, f, indent=2, ensure_ascii=False)

                # SASB final results are persisted in the original backend/data/sasb_metrics row shape.
                # backend/data/sasb_metric_profiles is retrieval-only and is not used for display/storage rows.
                if (framework or "").strip().upper() == "SASB" and assessment_json.get("sasb_metric_rows"):
                    with open(sasb_metrics_result_path, "w", encoding="utf-8") as f:
                        json.dump(assessment_json["sasb_metric_rows"], f, indent=2, ensure_ascii=False)

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
                        "sasb_metrics_filename": sasb_metrics_filename if (framework or "").strip().upper() == "SASB" else None,
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
                logger.info(
                    f"Compliance scope={scope_key} completed in "
                    f"{time.perf_counter() - scope_started:.2f}s"
                )
                performance["scopes"].append({
                    "scope_key": scope_key,
                    "total_seconds": round(time.perf_counter() - scope_started, 3),
                    "metrics_seconds": round(retrieval_started - metrics_started, 3),
                    "retrieval_seconds": round(t_start - retrieval_started, 3),
                    "analysis_seconds": round(analysis_elapsed, 3),
                    "metric_count": len(metrics.metrics),
                })
                _emit_upload_progress(progress_cb, "assessment_scope_done", f"Completed scope {scope_index}/{len(scopes_list)}: {scope_key}.", 55 + 35 * (scope_index / max(1, len(scopes_list))), file_id=file_info.get("file_id"), scope_key=scope_key, scope_index=scope_index, total_scopes=len(scopes_list))

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
            _patch_file_metadata(file_info["file_id"], status="processed", processing_stage="completed", processing_progress=100)
            _emit_upload_progress(progress_cb, "completed", "Report processing completed.", 100, file_id=file_info.get("file_id"))

            performance["total_seconds"] = round(time.perf_counter() - pipeline_started, 3)
            performance["resources_end"] = _runtime_resource_snapshot()
            logger.info(f"Report performance summary: {json.dumps(performance, ensure_ascii=False)}")
            return {
                "status": "success",
                "message": "Report uploaded and fully processed",
                "report_id": report_content.document_id,
                "file_id": file_info["file_id"],
                "summary": summary,
                "scopes": manifest_rows,
                "performance": performance,
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

            _patch_file_metadata(file_info["file_id"], status="processed", processing_stage="partial_success", processing_progress=100)
            _emit_upload_progress(progress_cb, "partial_success", error_message, 100, file_id=file_info.get("file_id"), error=str(assessment_error))

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
            _patch_file_metadata(file_info["file_id"], status="failed", processing_stage="failed", processing_progress=100)
            _emit_upload_progress(progress_cb, "failed", f"Report processing failed: {e}", 100, file_id=file_info.get("file_id"), error=str(e))
        raise



def _run_report_processing_job(
    job_id: str,
    file_info: dict,
    filename: str,
    industry: Optional[str],
    semiIndustry: Optional[str],
    framework: Optional[str],
    griSector: Optional[str],
    griTopic: Optional[str],
    scopeSlugs: Optional[str],
    user_id: int,
) -> None:
    """Background report processing entry point."""

    def progress_cb(*, stage: str, message: str, progress: Optional[float] = None, extra: Optional[dict] = None):
        update_report_job(
            job_id,
            status="processing",
            stage=stage,
            progress=progress,
            message=message,
            extra=extra,
        )
        if file_info.get("file_id"):
            metadata_updates = {
                "status": "processing",
                "processing_job_id": job_id,
                "processing_stage": stage,
                "processing_progress": progress,
            }
            metadata_updates.update(_ocr_progress_metadata_updates(extra))
            _patch_file_metadata(
                file_info["file_id"],
                **metadata_updates,
            )

    try:
        update_report_job(job_id, status="processing", stage="started", progress=1, message="Background processing started.")
        result = _sync_upload_report_body(
            None,
            filename,
            industry,
            semiIndustry,
            framework,
            griSector,
            griTopic,
            scopeSlugs,
            user_id,
            pre_saved_file_info=file_info,
            progress_cb=progress_cb,
        )
        final_status = str(result.get("status") or "success")
        update_report_job(
            job_id,
            status="partial_success" if final_status == "partial_success" else "success",
            stage="completed",
            progress=100,
            message=result.get("message") or "Report processing completed.",
            result=result,
            event_type="done",
        )
    except Exception as exc:
        logger.exception(f"Background report job failed: job_id={job_id}")
        update_report_job(
            job_id,
            status="failed",
            stage="failed",
            progress=100,
            message="Processing failed. Please try again.",
            error=str(exc),
            event_type="error",
        )


async def get_report_job_status(
    job_id: str,
    user_id: int = Depends(get_current_user),
):
    job = snapshot_report_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Report job not found")
    owner = get_report_job_owner(job_id)
    if owner is not None and owner != user_id:
        raise HTTPException(status_code=403, detail="Not allowed to access this report job")
    return {"status": "success", "job": job}


async def reprocess_report(
    file_id: str,
    user_id: int = Depends(get_current_user),
):
    """Explicitly enqueue the current visual parser for an existing report."""
    file_info = file_manager.get_file_info(file_id, user_id=user_id)
    if not file_info or file_info.get("file_type") != "report":
        raise HTTPException(status_code=404, detail="Report not found or access denied")
    source = Path(str(file_info.get("file_path") or ""))
    if not source.is_file():
        raise HTTPException(status_code=404, detail="Report PDF is missing")
    if str(file_info.get("status") or "").lower() == "processing":
        existing_job_id = str(file_info.get("processing_job_id") or "").strip()
        if existing_job_id and snapshot_report_job(existing_job_id):
            raise HTTPException(status_code=409, detail="Report is already being processed")
        _patch_file_metadata(
            file_id,
            status="failed",
            processing_stage="interrupted",
            processing_progress=100,
            processing_error="Processing was interrupted. A replacement job is being created.",
        )

    job = create_report_job(file_id=file_id, filename=source.name, user_id=user_id)
    _patch_file_metadata(
        file_id,
        status="processing",
        processing_job_id=job["job_id"],
        processing_stage="queued",
        processing_progress=0,
    )
    get_report_job_executor().submit(
        _run_report_processing_job,
        job["job_id"],
        dict(file_info),
        source.name,
        file_info.get("industry"),
        file_info.get("semi_industry"),
        file_info.get("framework"),
        file_info.get("gri_sector"),
        file_info.get("gri_topic"),
        json.dumps(file_info.get("scope_slugs") or []) if file_info.get("scope_slugs") else None,
        user_id,
    )
    return {
        "status": "accepted",
        "job_id": job["job_id"],
        "file_id": file_id,
        "report_id": file_id,
        "processing_status_url": f"/api/report-jobs/{job['job_id']}",
        "events_url": f"/api/report-jobs/{job['job_id']}/events",
    }


async def report_job_events(
    job_id: str,
    request: Request,
    token: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
):
    """SSE stream for report processing progress.

    EventSource cannot set custom Authorization headers. The frontend therefore
    passes the JWT token as a query parameter. Header auth is also accepted for
    non-browser clients.
    """
    auth_value = authorization
    if token and not auth_value:
        auth_value = f"Bearer {token}"
    if not auth_value:
        raise HTTPException(status_code=403, detail="Authentication token is required")
    try:
        user_id = get_user_id_from_authorization(auth_value)
    except Exception as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    job = snapshot_report_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Report job not found")
    owner = get_report_job_owner(job_id)
    if owner is not None and owner != user_id:
        raise HTTPException(status_code=403, detail="Not allowed to access this report job")

    async def event_generator():
        last_seq = 0
        snapshot = snapshot_report_job(job_id)
        if snapshot:
            last_seq = int(snapshot.get("seq", 0) or 0)
            yield _format_sse("snapshot", snapshot)
            if str(snapshot.get("status")) in TERMINAL_STATUSES:
                yield _format_sse("done" if snapshot.get("status") != "failed" else "error", snapshot)
                return
        while True:
            if await request.is_disconnected():
                break
            events = get_report_job_events_since(job_id, last_seq)
            for event in events:
                last_seq = int(event.get("seq", last_seq) or last_seq)
                event_name = str(event.get("event") or "progress")
                yield _format_sse(event_name, event)
                if str(event.get("status")) in TERMINAL_STATUSES:
                    return
            # Heartbeat keeps proxies and browsers from considering the stream idle.
            yield ": heartbeat\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def upload_report(
    file: UploadFile = File(...),
    industry: Optional[str] = Form(None),
    semiIndustry: Optional[str] = Form(None),
    framework: Optional[str] = Form(None),
    griSector: Optional[str] = Form(None),
    griTopic: Optional[str] = Form(None),
    scopeSlugs: Optional[str] = Form(None),
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
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    try:
        content = await file.read()

        # 保存上传文件后立即返回，真正的 OCR / embedding / compliance 分析在后台线程中执行。
        file_info = file_manager.save_uploaded_file(
            file_content=content,
            filename=file.filename or "",
            file_type="report",
            industry=industry,
            framework=framework,
            semi_industry=semiIndustry,
            gri_sector=griSector,
            gri_topic=griTopic,
            user_id=user_id,
        )
        job = create_report_job(file_id=file_info["file_id"], filename=file.filename or "", user_id=user_id)
        try:
            scope_count = max(1, len(_parse_scope_slugs_json(scopeSlugs, semiIndustry or griTopic)))
        except Exception:
            # Logging metadata must never make an otherwise valid upload fail.
            scope_count = 1
        logger.info(
            "Report upload accepted file_id={} job_id={} framework={} file_size={} scope_count={}",
            file_info["file_id"], job["job_id"], framework or "", len(content), scope_count,
        )
        _patch_file_metadata(
            file_info["file_id"],
            status="processing",
            processing_job_id=job["job_id"],
            processing_stage="queued",
            processing_progress=0,
        )
        get_report_job_executor().submit(
            _run_report_processing_job,
            job["job_id"],
            file_info,
            file.filename or "",
            industry,
            semiIndustry,
            framework,
            griSector,
            griTopic,
            scopeSlugs,
            user_id,
        )

        return {
            "status": "accepted",
            "message": "Report uploaded. Processing has started in the background.",
            "job_id": job["job_id"],
            "file_id": file_info["file_id"],
            "report_id": file_info["file_id"],
            "processing_status_url": f"/api/report-jobs/{job['job_id']}",
            "events_url": f"/api/report-jobs/{job['job_id']}/events",
        }
    except Exception as e:
        logger.error(f"Error processing report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@with_backend_model_task("upload_metrics")
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


async def get_report_by_file_id(
    file_id: str,
    scope: Optional[str] = Query(None),
    user_id: int = Depends(get_current_user),
):
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
        canonical_dir = Path(file_manager.compliance_outputs)
        scope_key = str(scope or "").strip()
        json_file = _json_path_from_manifest(canonical_dir, file_id, scope_key)
        if (json_file is None or not json_file.exists()) and scope_key:
            fw = str(file_info.get("framework") or "").strip()
            json_file = _compliance_json_path_for_scope(
                canonical_dir, file_id, fw, file_info, scope_key
            )
            if json_file is None or not json_file.exists():
                safe_scope = _sanitize_compliance_filename_part(scope_key)
                scoped_matches = list(
                    canonical_dir.glob(f"*{file_id}*{safe_scope}*compliance*.json")
                )
                scoped_matches = [p for p in scoped_matches if p.is_file()]
                if scoped_matches:
                    scoped_matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    json_file = scoped_matches[0]
        if (json_file is None or not json_file.exists()) and not scope_key:
            json_file = _find_assessment_json_path(file_id, file_info)
        if not json_file or not json_file.exists():
            detail = (
                f"No assessment found for file {file_id} and scope {scope_key}"
                if scope_key
                else f"No assessment found for file {file_id}"
            )
            raise HTTPException(status_code=404, detail=detail)

        # Read JSON to get report_id
        with open(json_file, 'r', encoding='utf-8') as f:
            assessment_data = json.load(f)

        report_id = assessment_data.get('report_id')
        if not report_id:
            logger.warning(f"Report ID not found in assessment data for file_id={file_id}")
            raise HTTPException(status_code=404, detail="Report ID not found in assessment data")

        # Now load the markdown report using scope-aware paths first, then legacy names.
        md_dirs = [
            Path(file_manager.markdown_outputs),
            Path(file_manager.compliance_outputs),
            legacy_outputs_dir,
        ]
        report_file = None

        md_candidates: List[Path] = []
        if scope_key:
            try:
                _, _, scoped_md = _paths_for_scope_compliance_bundle(
                    file_manager, file_id, file_info, scope_key
                )
                md_candidates.append(scoped_md)
            except Exception as exc:
                logger.debug(f"Scope markdown path resolution skipped for {file_id}/{scope_key}: {exc}")

        json_stem = Path(json_file).stem
        json_suffix = f"_{file_id}_compliance"
        if json_stem.endswith(json_suffix):
            scoped_part = json_stem[: -len(json_suffix)]
            if scoped_part:
                md_candidates.append(
                    Path(file_manager.markdown_outputs)
                    / f"compliance_report_{file_id}_{_sanitize_compliance_filename_part(scoped_part)}.md"
                )

        for d in md_dirs:
            md_candidates.append(d / f"compliance_report_{report_id}.md")

        for p in md_candidates:
            if p.exists():
                report_file = p
                break

        # Fallback: try to find any markdown report containing the report_id.
        if not report_file:
            for d in [x for x in md_dirs if x.exists()]:
                patterns = [f"*{report_id}*.md"]
                if scope_key:
                    safe_scope = _sanitize_compliance_filename_part(scope_key)
                    patterns.insert(0, f"*{report_id}*{safe_scope}*.md")
                matches: List[Path] = []
                for pattern in patterns:
                    matches.extend(list(d.glob(pattern)))
                matches = [m for m in matches if m.is_file()]
                if matches:
                    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
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
            "scope": scope_key or None,
            "report_file": report_file.name,
            "content": content,
            "created_at": datetime.fromtimestamp(report_file.stat().st_mtime).isoformat()
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching report for file {file_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
