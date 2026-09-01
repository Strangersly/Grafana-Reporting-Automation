from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from .core import Period, build_dashboard_url


class GrafanaAuthenticationError(RuntimeError):
    pass


class GrafanaCaptureError(RuntimeError):
    pass


PANEL_SURFACE_SELECTOR = (
    ".panel-container, [data-testid='panel-container'], "
    "[data-testid='data-testid panel content'], [data-testid^='data-testid Panel header ']"
)
LOADING_INDICATOR_SELECTORS = [
    "button:has-text('Cancel')",
    "[data-testid*='loading' i]",
    "[data-testid*='spinner' i]",
]


def _first_visible(page: Any, selectors: list[str]) -> Any | None:
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() and locator.first.is_visible():
                return locator.first
        except Exception:
            continue
    return None


def _wait_for_quiet(page: Any, timeout_seconds: int) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_seconds, 10) * 1000)
    except Exception:
        page.wait_for_timeout(1500)


def _dashboard_is_ready(page: Any, wait_selector: str) -> bool:
    """Grafana renders panel shells before its queries have completed."""
    if _first_visible(page, [wait_selector]) is None:
        return False
    if _first_visible(page, [PANEL_SURFACE_SELECTOR]) is None:
        return False
    return _first_visible(page, LOADING_INDICATOR_SELECTORS) is None


def _wait_for_dashboard_ready(
    page: Any,
    *,
    wait_selector: str,
    timeout_seconds: int,
    settle_seconds: int,
    dashboard_name: str,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _dashboard_is_ready(page, wait_selector):
            if settle_seconds:
                page.wait_for_timeout(settle_seconds * 1000)
            if _dashboard_is_ready(page, wait_selector):
                return
        page.wait_for_timeout(500)
    raise GrafanaCaptureError(
        f"Dashboard did not finish rendering within {timeout_seconds} seconds: {dashboard_name}"
    )


def _navigate_with_retries(page: Any, url: str, *, timeout_seconds: int, description: str) -> None:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise GrafanaCaptureError("Playwright is not installed.") from exc

    for attempt in range(1, 4):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
            return
        except PlaywrightTimeoutError as exc:
            if attempt == 3:
                raise GrafanaCaptureError(f"{description} timed out after 3 attempts.") from exc
            logging.warning("%s timed out; retrying attempt %s of 3", description, attempt + 1)
            page.wait_for_timeout((2**attempt) * 1000)


def _is_login_page(page: Any) -> bool:
    try:
        if urlparse(page.url).path.rstrip("/").endswith("/login"):
            return True
    except Exception:
        pass
    username = _first_visible(
        page,
        [
            "input[name='user']",
            "input[name='username']",
            "input[autocomplete='username']",
            "input[placeholder='email or username']",
            "input[type='email']",
        ],
    )
    password = _first_visible(
        page,
        [
            "input[name='password']",
            "input[autocomplete='current-password']",
            "input[placeholder='password']",
            "input[type='password']",
        ],
    )
    return username is not None and password is not None


class GrafanaCaptureSession:
    def __init__(self, config: dict[str, Any], *, headless: bool = True):
        self.config = config
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._context = None
        self.page = None

    def __enter__(self) -> "GrafanaCaptureSession":
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise GrafanaCaptureError("Playwright is not installed.") from exc
        grafana = self.config["grafana"]
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            viewport={
                "width": int(grafana.get("viewport", {}).get("width", 1920)),
                "height": int(grafana.get("viewport", {}).get("height", 1080)),
            },
            ignore_https_errors=bool(grafana.get("ignore_https_errors", False)),
        )
        self.page = self._context.new_page()
        timeout = int(grafana.get("navigation_timeout_seconds", 90)) * 1000
        self.page.set_default_timeout(timeout)
        self.page.set_default_navigation_timeout(timeout)
        self._login()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def _login(self) -> None:
        grafana = self.config["grafana"]
        username = grafana.get("username")
        password = grafana.get("password")
        if not username or not password:
            raise GrafanaAuthenticationError("Grafana credentials are not configured.")
        timeout = int(grafana.get("navigation_timeout_seconds", 90))
        login_url = urljoin(grafana["base_url"].rstrip("/") + "/", grafana.get("login_path", "/login").lstrip("/"))
        _navigate_with_retries(
            self.page,
            login_url,
            timeout_seconds=timeout,
            description="Grafana login page",
        )
        _wait_for_quiet(self.page, timeout)
        username_input = _first_visible(
            self.page,
            ["input[name='user']", "input[name='username']", "input[autocomplete='username']", "input[type='email']"],
        )
        password_input = _first_visible(
            self.page,
            ["input[name='password']", "input[autocomplete='current-password']", "input[type='password']"],
        )
        if not username_input or not password_input:
            if _is_login_page(self.page):
                raise GrafanaAuthenticationError("Grafana login form could not be used.")
            return
        username_input.fill(username)
        password_input.fill(password)
        submit = _first_visible(self.page, ["button[type='submit']", "button:has-text('Log in')", "button:has-text('Sign in')"])
        submit.click() if submit else password_input.press("Enter")
        _wait_for_quiet(self.page, timeout)
        self.page.wait_for_timeout(1000)
        if _is_login_page(self.page):
            raise GrafanaAuthenticationError("Grafana rejected the configured credentials.")

    def capture(self, dashboard: dict[str, Any], period: Period, output_path: Path) -> None:
        grafana = self.config["grafana"]
        timeout = int(grafana.get("navigation_timeout_seconds", 90))
        panel_timeout = int(grafana.get("panel_timeout_seconds", 60))
        width = int(dashboard.get("viewport", {}).get("width") or grafana.get("viewport", {}).get("width", 1920))
        height = int(dashboard.get("viewport", {}).get("height") or grafana.get("viewport", {}).get("height", 1080))
        self.page.set_viewport_size({"width": width, "height": height})
        try:
            self.page.goto(build_dashboard_url(self.config, dashboard, period), wait_until="domcontentloaded", timeout=timeout * 1000)
            _wait_for_quiet(self.page, timeout)
            if _is_login_page(self.page):
                raise GrafanaAuthenticationError("Grafana redirected the report to the login page.")
            selector = dashboard.get("wait_for_selector") or grafana.get("wait_for_selector") or PANEL_SURFACE_SELECTOR
            _wait_for_dashboard_ready(
                self.page,
                wait_selector=selector,
                timeout_seconds=panel_timeout,
                settle_seconds=int(float(grafana.get("wait_seconds", 8))),
                dashboard_name=dashboard["name"],
            )
            if _is_login_page(self.page):
                raise GrafanaAuthenticationError("Grafana session expired before the screenshot was saved.")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(output_path), full_page=bool(grafana.get("full_page", True)))
        except GrafanaAuthenticationError:
            raise
        except GrafanaCaptureError:
            raise
        except Exception as exc:
            raise GrafanaCaptureError(f"Capture failed for {dashboard['name']}: {type(exc).__name__}") from exc
