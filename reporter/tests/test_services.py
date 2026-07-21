from datetime import datetime, time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.utils import timezone

from reporter.models import Dashboard, GrafanaConnection, ReportArtifact, ReportRun, Schedule, User
from reporter.services import create_report_run, manual_periods
from reporter.tasks import _due_time, dispatch_due_schedules


TEST_KEY = Fernet.generate_key().decode("ascii")


@override_settings(APP_ENCRYPTION_KEY=TEST_KEY)
class ReportServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("operator@example.com", "A-strong-password-123", role=User.Role.OPERATOR)
        self.connection = GrafanaConnection.objects.create(base_url="https://grafana.example.com")
        self.connection.set_username("service")
        self.connection.set_password("password")
        self.connection.save()
        self.dashboard = Dashboard.objects.create(
            name="server-01", site="Alibaba", group="Centralized", category="VM-Linux", url="/d/uid/linux"
        )

    def test_create_run_snapshots_artifacts_and_paths(self):
        periods = manual_periods(mode="all", year=2026, month=7, timezone_name="Asia/Jakarta")
        run = create_report_run(
            periods=periods,
            dashboards=[self.dashboard],
            source=ReportRun.Source.MANUAL,
            preset="all",
            requested_by=self.user,
        )
        self.assertEqual(run.total_artifacts, 5)
        self.assertEqual(run.artifacts.count(), 5)
        monthly = run.artifacts.get(filename="monthly.png")
        self.assertEqual(
            monthly.relative_path,
            "2026/7. Juli/Alibaba/Centralized/VM-Linux/server-01/monthly.png",
        )

    def test_due_time_uses_schedule_timezone(self):
        schedule = Schedule.objects.create(
            name="weekly",
            recurrence=Schedule.Recurrence.WEEKLY,
            preset=Schedule.Preset.PREVIOUS_7_DAYS,
            weekly_day=0,
            run_time=time(7, 0),
            created_by=self.user,
        )
        monday_jakarta = datetime(2026, 7, 20, 8, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
        due = _due_time(schedule, monday_jakarta.astimezone(ZoneInfo("UTC")))
        self.assertEqual(due.hour, 0)

    @patch("reporter.tasks.execute_report_run.delay")
    @patch("reporter.tasks.timezone.now")
    def test_dispatch_is_idempotent(self, mocked_now, mocked_delay):
        mocked_now.return_value = datetime(2026, 7, 20, 8, 0, tzinfo=ZoneInfo("Asia/Jakarta")).astimezone(ZoneInfo("UTC"))
        Schedule.objects.create(
            name="weekly",
            recurrence=Schedule.Recurrence.WEEKLY,
            preset=Schedule.Preset.PREVIOUS_7_DAYS,
            weekly_day=0,
            run_time=time(7, 0),
            created_by=self.user,
        )
        self.assertEqual(dispatch_due_schedules(), 1)
        self.assertEqual(dispatch_due_schedules(), 0)
        self.assertEqual(ReportRun.objects.filter(source=ReportRun.Source.SCHEDULED).count(), 1)
        mocked_delay.assert_called_once()
