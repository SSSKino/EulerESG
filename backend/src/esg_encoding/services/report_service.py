"""Report upload and retrieval service functions."""

from .common import *  # noqa: F401,F403


def _sync_upload_report_body(
    content: bytes,
    filename: str,
    industry: Optional[str],
    semiIndustry: Optional[str],
    framework: Optional[str],
    griSector: Optional[str],
    griTopic: Optional[str],
    scopeSlugs: Optional[str],
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

                metrics = _prepare_metrics_for_retrieval(processor, metrics)
                system_components["current_metrics"] = metrics
                logger.info(f"Loaded metrics for scope_key={scope_key} ({fw})")

                retrieval_results = retrieve_metric_collection(
                    report_content, metrics, config=system_components.get("config")
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
            user_id,
        )
    except Exception as e:
        logger.error(f"Error processing report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
