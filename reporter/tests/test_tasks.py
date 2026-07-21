from pathlib import Path
from tempfile import TemporaryDirectory
from threading import get_ident
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings

from reporting.capture import GrafanaAuthenticationError, GrafanaCaptureError
from reporter.models import Dashboard, GrafanaConnection, ReportArtifact, ReportRun, User
from reporter.services import create_report_run, manual_periods
from reporter.tasks import execute_report_run


TEST_KEY = Fernet.generate_key().decode("ascii")


@override_settings(APP_ENCRYPTION_KEY=TEST_KEY, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ReportTaskTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("operator@example.com", "A-strong-password-123", role=User.Role.OPERATOR)
        self.connection = GrafanaConnection.objects.create(base_url="https://grafana.example.com")
        self.connection.set_username("service")
        self.connection.set_password("password")
        self.connection.save()
        self.dashboard = Dashboard.objects.create(name="server-01", url="/d/uid/linux")

    def make_run(self):
        return create_report_run(
            periods=manual_periods(mode="monthly", year=2026, month=7),
            dashboards=[self.dashboard],
            source=ReportRun.Source.MANUAL,
            preset="monthly",
            requested_by=self.user,
        )

    @patch("reporter.tasks.GrafanaCaptureSession")
    def test_successful_capture_updates_run_and_artifact(self, session_class):
        with TemporaryDirectory() as temp_dir, override_settings(REPORT_ROOT=Path(temp_dir)):
            run = self.make_run()
            session = session_class.return_value.__enter__.return_value

            def write_png(dashboard, period, output_path):
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"png-data")

            session.capture.side_effect = write_png
            execute_report_run(str(run.pk))
            run.refresh_from_db()
            artifact = run.artifacts.get()
            self.assertEqual(run.status, ReportRun.Status.SUCCEEDED)
            self.assertEqual(artifact.status, ReportArtifact.Status.SUCCEEDED)
            self.assertEqual(artifact.size_bytes, 8)
            self.assertEqual(artifact.attempts, 1)

    @patch("reporter.tasks.GrafanaCaptureSession")
    def test_playwright_calls_run_on_one_dedicated_thread(self, session_class):
        with TemporaryDirectory() as temp_dir, override_settings(REPORT_ROOT=Path(temp_dir)):
            run = self.make_run()
            manager = session_class.return_value
            session = manager.__enter__.return_value
            capture_thread_ids = []

            def record_enter_thread():
                capture_thread_ids.append(get_ident())
                return session

            def write_png(dashboard, period, output_path):
                capture_thread_ids.append(get_ident())
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"png-data")

            def record_exit_thread(*args):
                capture_thread_ids.append(get_ident())

            manager.__enter__.side_effect = record_enter_thread
            manager.__exit__.side_effect = record_exit_thread
            session.capture.side_effect = write_png
            orm_thread_id = get_ident()

            execute_report_run(str(run.pk))

            self.assertEqual(len(set(capture_thread_ids)), 1)
            self.assertNotEqual(capture_thread_ids[0], orm_thread_id)

    @patch("reporter.tasks.GrafanaCaptureSession")
    def test_transient_capture_failure_retries_three_times(self, session_class):
        with TemporaryDirectory() as temp_dir, override_settings(REPORT_ROOT=Path(temp_dir)):
            run = self.make_run()
            session = session_class.return_value.__enter__.return_value
            session.capture.side_effect = GrafanaCaptureError("Timed out")
            with patch("reporter.tasks.time.sleep"):
                execute_report_run(str(run.pk))
            run.refresh_from_db()
            artifact = run.artifacts.get()
            self.assertEqual(run.status, ReportRun.Status.FAILED)
            self.assertEqual(artifact.attempts, 3)
            self.assertEqual(session.capture.call_count, 3)

    @patch("reporter.tasks.GrafanaCaptureSession")
    def test_authentication_failure_does_not_write_an_image(self, session_class):
        with TemporaryDirectory() as temp_dir, override_settings(REPORT_ROOT=Path(temp_dir)):
            run = self.make_run()
            session = session_class.return_value.__enter__.return_value
            session.capture.side_effect = GrafanaAuthenticationError("Credentials rejected")
            execute_report_run(str(run.pk))
            run.refresh_from_db()
            artifact = run.artifacts.get()
            self.assertEqual(run.status, ReportRun.Status.FAILED)
            self.assertEqual(artifact.attempts, 1)
            self.assertFalse((Path(temp_dir) / artifact.relative_path).exists())
