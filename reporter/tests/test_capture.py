from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from reporting.capture import (
    LOADING_INDICATOR_SELECTORS,
    GrafanaCaptureError,
    GrafanaCaptureSession,
    _expand_dashboard_rows,
    _dashboard_capture_height,
    _dashboard_is_ready,
    _navigate_with_retries,
    _wait_for_dashboard_ready,
    _wait_for_first_visible,
)
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

    @patch("reporting.capture.time.monotonic", side_effect=[0, 0, 1])
    @patch("reporting.capture._first_visible", side_effect=[None, object()])
    def test_waits_for_delayed_login_field(self, first_visible, monotonic):
        page = MagicMock()

        result = _wait_for_first_visible(page, ["input[name='user']"], timeout_seconds=30)

        self.assertIsNotNone(result)
        page.wait_for_timeout.assert_called_once_with(250)


class DashboardReadinessTests(SimpleTestCase):
    def test_does_not_treat_arbitrary_loading_css_classes_as_a_loader(self):
        self.assertNotIn("[class*='spinner' i], [class*='loading' i]", LOADING_INDICATOR_SELECTORS)

    @patch("reporting.capture._first_visible")
    def test_ignores_a_global_grafana_loading_control(self, first_visible):
        page = MagicMock()
        ready = object()
        toolbar_loader = MagicMock()
        toolbar_loader.evaluate.return_value = False
        first_visible.side_effect = [ready, ready, toolbar_loader, None, None, None, None, None]

        self.assertTrue(_dashboard_is_ready(page, ".panel-container"))

    @patch("reporting.capture._first_visible")
    def test_blocks_a_loader_inside_a_panel(self, first_visible):
        page = MagicMock()
        ready = object()
        panel_loader = MagicMock()
        panel_loader.evaluate.return_value = True
        first_visible.side_effect = [ready, ready, panel_loader]

        self.assertFalse(_dashboard_is_ready(page, ".panel-container"))

    @patch("reporting.capture._has_visible_panel_loader", return_value=True)
    @patch("reporting.capture._first_visible", return_value=object())
    def test_can_capture_a_dashboard_with_persistent_background_loading(self, first_visible, has_loader):
        self.assertTrue(
            _dashboard_is_ready(
                MagicMock(),
                ".panel-container",
                wait_for_panel_loaders=False,
            )
        )
        has_loader.assert_not_called()

    @patch("reporting.capture.time.monotonic", side_effect=[0, 0])
    @patch("reporting.capture._first_visible")
    def test_waits_for_ready_panels_to_remain_stable(self, first_visible, monotonic):
        page = MagicMock()
        ready = object()
        first_visible.side_effect = [ready, ready, *([None] * 6), ready, ready, *([None] * 6)]

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


class DashboardRowTests(SimpleTestCase):
    def test_expands_configured_collapsed_rows(self):
        page = MagicMock()
        row = page.get_by_text.return_value.first
        row.is_visible.return_value = True

        _expand_dashboard_rows(page, ["Bandwidth Overview"])

        page.get_by_text.assert_called_once_with("Bandwidth Overview", exact=False)
        row.click.assert_called_once_with()
        page.wait_for_timeout.assert_called_once_with(500)


class DashboardCaptureHeightTests(SimpleTestCase):
    def test_uses_the_last_rendered_panel_bottom(self):
        page = MagicMock()
        page.locator.return_value.evaluate_all.return_value = 1210

        height = _dashboard_capture_height(page, fallback_height=705)

        self.assertEqual(height, 1234)

    def test_falls_back_to_configured_height_when_panel_measurement_fails(self):
        page = MagicMock()
        page.locator.return_value.evaluate_all.side_effect = RuntimeError("measurement failed")

        height = _dashboard_capture_height(page, fallback_height=705)

        self.assertEqual(height, 705)


class CaptureErrorTests(SimpleTestCase):
    @patch("reporting.capture._wait_for_dashboard_ready")
    @patch("reporting.capture._wait_for_quiet")
    @patch("reporting.capture._is_login_page", return_value=False)
    def test_capture_can_use_a_dashboard_specific_settle_delay(self, login_page, wait_for_quiet, wait_for_dashboard_ready):
        session = GrafanaCaptureSession(
            {
                "grafana": {
                    "base_url": "https://grafana.example.com",
                    "navigation_timeout_seconds": 90,
                    "panel_timeout_seconds": 60,
                    "wait_seconds": 8,
                    "viewport": {"width": 1920, "height": 1080},
                }
            }
        )
        session.page = MagicMock()

        session.capture(
            {
                "name": "Bandwidth",
                "url": "/d/bandwidth",
                "query_params": {
                    "capture_options": {
                        "skip_panel_readiness": True,
                        "settle_seconds": 15,
                        "expand_rows": ["Bandwidth Overview"],
                        "full_page": True,
                    }
                },
            },
            Period(
                "weekly",
                "week2.png",
                datetime(2026, 9, 8, tzinfo=timezone.utc),
                datetime(2026, 9, 14, 23, 59, tzinfo=timezone.utc),
                2026,
                9,
            ),
            Path("unused.png"),
        )

        session.page.wait_for_timeout.assert_called_with(15_000)
        session.page.get_by_text.assert_called_once_with("Bandwidth Overview", exact=False)
        session.page.screenshot.assert_called_once_with(path="unused.png", full_page=True)
        wait_for_dashboard_ready.assert_not_called()

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
