"""Cross-analysis service functions."""

from .common import *  # noqa: F401,F403


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


async def cross_analysis_excel_metrics(req: ExcelMetricsRequest):
    raise HTTPException(
        status_code=410,
        detail=(
            "Excel metrics extraction has been removed from this backend build. "
            "Use cross-analysis topic extraction or the standard compliance assessment pipeline instead."
        ),
    )


async def cross_analysis_excel_metrics_cache(ids: str):
    raise HTTPException(
        status_code=410,
        detail=(
            "Excel metrics cache has been removed together with the legacy Excel metrics extractor."
        ),
    )
