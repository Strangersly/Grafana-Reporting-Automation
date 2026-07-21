import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.fernet import Fernet
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook

from reporter.models import Dashboard, GrafanaConnection, ReportArtifact, ReportRun, User
from reporter.services import create_report_run, manual_periods


TEST_KEY = Fernet.generate_key().decode("ascii")


@override_settings(APP_ENCRYPTION_KEY=TEST_KEY, EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class WebViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin@example.com", "A-strong-password-123")
        self.operator = User.objects.create_user("operator@example.com", "A-strong-password-123", role=User.Role.OPERATOR)
        self.viewer = User.objects.create_user("viewer@example.com", "A-strong-password-123", role=User.Role.VIEWER)
        self.connection = GrafanaConnection.objects.create(base_url="https://grafana.example.com")
        self.connection.set_username("service")
        self.connection.set_password("password")
        self.connection.save()
        self.dashboard = Dashboard.objects.create(name="server-01", url="/d/uid/linux")

    def test_viewer_cannot_manage_dashboards(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("dashboard_list"))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get(reverse("report_library")).status_code, 200)

    def test_operator_imports_json_with_column_mapping(self):
        self.client.force_login(self.operator)
        payload = [{"dashboard_name": "server-02", "dashboard_link": "/d/uid/linux2", "site": "Alibaba"}]
        upload = SimpleUploadedFile("dashboards.json", json.dumps(payload).encode(), content_type="application/json")
        response = self.client.post(reverse("dashboard_import"), {"step": "upload", "file": upload})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("dashboard_import"),
            {
                "step": "apply",
                "name_column": "dashboard_name",
                "url_column": "dashboard_link",
                "site_column": "site",
                "group_column": "",
                "category_column": "",
            },
        )
        self.assertRedirects(response, reverse("dashboard_list"))
        imported = Dashboard.objects.get(name="server-02")
        self.assertEqual(imported.site, "Alibaba")

    def test_operator_imports_excel_with_column_mapping(self):
        self.client.force_login(self.operator)
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Name", "Link", "Site", "Group", "Category"])
        sheet.append(["server-03", "/d/uid/linux3", "Alibaba", "Centralized", "VM-Linux"])
        payload = BytesIO()
        workbook.save(payload)
        upload = SimpleUploadedFile(
            "dashboards.xlsx",
            payload.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response = self.client.post(reverse("dashboard_import"), {"step": "upload", "file": upload})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("dashboard_import"),
            {
                "step": "apply",
                "name_column": "Name",
                "url_column": "Link",
                "site_column": "Site",
                "group_column": "Group",
                "category_column": "Category",
            },
        )
        self.assertRedirects(response, reverse("dashboard_list"))
        self.assertTrue(Dashboard.objects.filter(name="server-03", category="VM-Linux").exists())

    def test_admin_invites_user(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("invite_user"), {"email": "new@example.com", "role": User.Role.VIEWER})
        self.assertRedirects(response, reverse("user_list"))
        invited = User.objects.get(email="new@example.com")
        self.assertFalse(invited.is_active)
        self.assertFalse(invited.has_usable_password())

    def test_run_status_requires_login(self):
        periods = manual_periods(mode="monthly", year=2026, month=7)
        run = create_report_run(
            periods=periods,
            dashboards=[self.dashboard],
            source=ReportRun.Source.MANUAL,
            preset="monthly",
            requested_by=self.operator,
        )
        response = self.client.get(reverse("run_status", args=[run.pk]))
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("run_status", args=[run.pk]))
        self.assertEqual(response.json()["total"], 1)

    def test_authenticated_pages_render(self):
        periods = manual_periods(mode="monthly", year=2026, month=7)
        run = create_report_run(
            periods=periods,
            dashboards=[self.dashboard],
            source=ReportRun.Source.MANUAL,
            preset="monthly",
            requested_by=self.operator,
        )
        self.client.force_login(self.admin)
        urls = [
            reverse("home"),
            reverse("dashboard_list"),
            reverse("dashboard_create"),
            reverse("dashboard_import"),
            reverse("schedule_list"),
            reverse("schedule_create"),
            reverse("run_list"),
            reverse("run_create"),
            reverse("run_detail", args=[run.pk]),
            reverse("report_library"),
            reverse("grafana_settings"),
            reverse("user_list"),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_artifact_download_stays_inside_report_root(self):
        with TemporaryDirectory() as temp_dir, override_settings(REPORT_ROOT=Path(temp_dir)):
            periods = manual_periods(mode="monthly", year=2026, month=7)
            run = create_report_run(
                periods=periods,
                dashboards=[self.dashboard],
                source=ReportRun.Source.MANUAL,
                preset="monthly",
                requested_by=self.operator,
            )
            artifact = run.artifacts.get()
            path = Path(temp_dir) / artifact.relative_path
            path.parent.mkdir(parents=True)
            path.write_bytes(b"png")
            artifact.status = ReportArtifact.Status.SUCCEEDED
            artifact.save(update_fields=["status"])
            self.client.force_login(self.viewer)
            response = self.client.get(reverse("artifact_image", args=[artifact.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "image/png")
            response.close()
