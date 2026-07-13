from fastapi import APIRouter

from ...services import report_service

router = APIRouter()
router.add_api_route("/api/upload-report", report_service.upload_report, methods=["POST"])
router.add_api_route("/api/upload-metrics", report_service.upload_metrics, methods=["POST"])
router.add_api_route("/api/reports/latest", report_service.get_latest_report, methods=["GET"])
router.add_api_route("/api/reports/{file_id}", report_service.get_report_by_file_id, methods=["GET"])

router.add_api_route("/api/report-jobs/{job_id}", report_service.get_report_job_status, methods=["GET"])
router.add_api_route("/api/report-jobs/{job_id}/events", report_service.report_job_events, methods=["GET"])
