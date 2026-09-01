from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from reporting.capture import GrafanaCaptureError, GrafanaCaptureSession, _navigate_with_retries, _wait_for_dashboard_ready
from reporting.core import Period


class NavigationRetryTests(SimpleTestCase):
    def test_navigation_timeout_is_retried(self):
        page = MagicMock()
        page.goto.side_effect = [PlaywrightTimeoutError("timed out"), None]

        _navigate_with_retries(page, "https://grafana.example.com/login", timeout_seconds=30, description="Login")

        self.assertEqual(page.goto.call_count, 2)
        page.wait_for_timeout.assert_called_once_with(2000)

    def test_navigation_fails_after_three_timeouts(self):
        page = MagicMock()
        page.goto.side_effect = PlaywrightTimeoutError("timed out")

        with self.assertRaisesMessage(GrafanaCaptureError, "Login timed out after 3 attempts"):
            _navigate_with_retries(page, "https://grafana.example.com/login", timeout_seconds=30, description="Login")

        self.assertEqual(page.goto.call_count, 3)
        self.assertEqual(page.wait_for_timeout.call_count, 2)


class DashboardReadinessTests(SimpleTestCase):
    @patch("reporting.capture.time.monotonic", side_effect=[0, 0])
    @patch("reporting.capture._first_visible")
    def test_waits_for_ready_panels_to_remain_stable(self, first_visible, monotonic):
        page = MagicMock()
        ready = object()
        first_visible.side_effect = [ready, ready, None, ready, ready, None]

        _wait_for_dashboard_ready(
            page,
            wait_selector=".panel-container",
            timeout_seconds=60,
            settle_seconds=8,
            dashboard_name="Load Balancer",
        )

        page.wait_for_timeout.assert_called_once_with(8000)

    @patch("reporting.capture.time.monotonic", side_effect=[0, 0, 60])
    @patch("reporting.capture._first_visible")
    def test_rejects_panels_while_grafana_is_still_loading(self, first_visible, monotonic):
        page = MagicMock()
        ready = object()
        loading = object()
        first_visible.side_effect = [ready, ready, loading]

        with self.assertRaisesMessage(GrafanaCaptureError, "Dashboard did not finish rendering"):
            _wait_for_dashboard_ready(
                page,
                wait_selector=".panel-container",
                timeout_seconds=60,
                settle_seconds=8,
                dashboard_name="Load Balancer",
            )

        page.wait_for_timeout.assert_called_once_with(500)


class CaptureErrorTests(SimpleTestCase):
    @patch("reporting.capture._wait_for_dashboard_ready")
    @patch("reporting.capture._wait_for_quiet")
    @patch("reporting.capture._is_login_page", return_value=False)
    def test_capture_keeps_readiness_error(self, login_page, wait_for_quiet, wait_for_dashboard_ready):
        session = GrafanaCaptureSession(
            {
                "grafana": {
                    "base_url": "https://grafana.example.com",
                    "navigation_timeout_seconds": 90,
                    "panel_timeout_seconds": 60,
                    "viewport": {"width": 1920, "height": 1080},
                }
            }
        )
        session.page = MagicMock()
        wait_for_dashboard_ready.side_effect = GrafanaCaptureError(
            "Dashboard did not finish rendering within 60 seconds: Load Balancer"
        )

        with self.assertRaisesMessage(GrafanaCaptureError, "Dashboard did not finish rendering"):
            session.capture(
                {"name": "Load Balancer", "url": "/d/load-balancer"},
                Period(
                    "monthly",
                    "monthly.png",
                    datetime(2026, 8, 1, tzinfo=timezone.utc),
                    datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc),
                    2026,
                    8,
                ),
                Path("unused.png"),
            )

    @patch("reporting.capture.time.monotonic", side_effect=[0, 0, 60])
    @patch("reporting.capture._first_visible")
    def test_rejects_dashboard_without_visible_panels(self, first_visible, monotonic):
        page = MagicMock()
        first_visible.return_value = None

        with self.assertRaisesMessage(GrafanaCaptureError, "Dashboard did not finish rendering"):
            _wait_for_dashboard_ready(
                page,
                wait_selector=".panel-container",
                timeout_seconds=60,
                settle_seconds=8,
                dashboard_name="Centralized RDS",
            )

        page.wait_for_timeout.assert_called_once_with(500)
