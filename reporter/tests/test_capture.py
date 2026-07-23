from unittest.mock import MagicMock

from django.test import SimpleTestCase
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from reporting.capture import GrafanaCaptureError, _navigate_with_retries


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
