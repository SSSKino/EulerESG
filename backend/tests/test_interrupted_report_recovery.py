import tempfile
import unittest

from esg_encoding.file_manager import FileManager


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
            }, "sessions": {}}

            result = manager.recover_interrupted_reports()

            self.assertEqual(result, {"completed": 1, "interrupted": 1})
            self.assertEqual(manager.metadata["files"]["stuck"]["status"], "failed")
            self.assertEqual(manager.metadata["files"]["stuck"]["processing_stage"], "interrupted")
            self.assertNotIn("processing_job_id", manager.metadata["files"]["stuck"])
            self.assertEqual(manager.metadata["files"]["done"]["status"], "processed")
