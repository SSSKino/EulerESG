import tempfile
import unittest

from esg_encoding.file_manager import FileManager
from esg_encoding.services.report_service import _report_progress_metadata_updates


class InterruptedReportRecoveryTests(unittest.TestCase):
    def test_processing_rows_are_recoverable_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = FileManager(directory)
            manager.metadata = {"files": {
                "stuck": {
                    "file_type": "report", "status": "processing",
                    "processing_stage": "extracted", "processing_job_id": "old-job",
                },
                "done": {
                    "file_type": "report", "status": "processing",
                    "processing_stage": "completed", "processing_job_id": "done-job",
                },
                "partial": {
                    "file_type": "report", "status": "processing",
                    "processing_stage": "partial_success", "processing_job_id": "partial-job",
                    "processing_error": "assessment failed",
                },
                "legacy-partial": {
                    "file_type": "report", "status": "failed",
                    "processing_stage": "interrupted",
                    "interrupted_job_id": "legacy-job",
                    "processing_error": "Processing was interrupted by a backend restart.",
                    "processing_history": [{
                        "stage": "interrupted_recovery",
                        "previous_stage": "partial_success",
                    }],
                },
            }, "sessions": {}}

            result = manager.recover_interrupted_reports()

            self.assertEqual(result, {"completed": 3, "interrupted": 1})
            self.assertEqual(manager.metadata["files"]["stuck"]["status"], "failed")
            self.assertEqual(manager.metadata["files"]["stuck"]["processing_stage"], "interrupted")
            self.assertNotIn("processing_job_id", manager.metadata["files"]["stuck"])
            self.assertEqual(manager.metadata["files"]["done"]["status"], "processed")
            self.assertNotIn("interrupted_job_id", manager.metadata["files"]["done"])
            self.assertEqual(manager.metadata["files"]["partial"]["status"], "processed")
            self.assertEqual(manager.metadata["files"]["partial"]["processing_stage"], "partial_success")
            self.assertEqual(manager.metadata["files"]["partial"]["processing_error"], "assessment failed")
            self.assertNotIn("processing_job_id", manager.metadata["files"]["partial"])
            self.assertNotIn("interrupted_job_id", manager.metadata["files"]["partial"])
            self.assertEqual(manager.metadata["files"]["legacy-partial"]["status"], "processed")
            self.assertEqual(manager.metadata["files"]["legacy-partial"]["processing_stage"], "partial_success")
            self.assertNotIn("interrupted_job_id", manager.metadata["files"]["legacy-partial"])
            self.assertIn("completed with warnings", manager.metadata["files"]["legacy-partial"]["processing_error"])
            self.assertEqual(manager.recover_interrupted_reports(), {"completed": 0, "interrupted": 0})


class ReportProgressMetadataTests(unittest.TestCase):
    def test_terminal_progress_does_not_restore_processing_state(self):
        completed = _report_progress_metadata_updates(
            stage="completed",
            message="done",
            progress=100,
            job_id="job-1",
            extra=None,
        )
        partial = _report_progress_metadata_updates(
            stage="partial_success",
            message="warning",
            progress=100,
            job_id="job-2",
            extra={"error": "assessment failed"},
        )
        failed = _report_progress_metadata_updates(
            stage="failed",
            message="failed",
            progress=100,
            job_id="job-3",
            extra={"error": "pipeline failed"},
        )

        self.assertEqual(completed["status"], "processed")
        self.assertIsNone(completed["processing_job_id"])
        self.assertIsNone(completed["processing_error"])
        self.assertEqual(partial["status"], "processed")
        self.assertEqual(partial["processing_stage"], "partial_success")
        self.assertEqual(partial["processing_error"], "assessment failed")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["processing_error"], "pipeline failed")

    def test_non_terminal_progress_remains_processing(self):
        updates = _report_progress_metadata_updates(
            stage="ocr_batch_processing",
            message="working",
            progress=42,
            job_id="job-active",
            extra={"pages_done": 10},
        )

        self.assertEqual(updates["status"], "processing")
        self.assertEqual(updates["processing_job_id"], "job-active")
        self.assertEqual(updates["processing_progress"], 42)
        self.assertEqual(updates["processing_pages_done"], 10)
        self.assertNotIn("processing_error", updates)
