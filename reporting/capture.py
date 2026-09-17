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
    "[role='progressbar']",
    "[aria-label*='loading' i]",
    ".fa-spinner, .fa-spin",
]
LOGIN_USERNAME_SELECTORS = [
    "input[name='user']",
    "input[name='username']",
    "input[autocomplete='username']",
    "input[placeholder='email or username']",
    "input[type='email']",
]
LOGIN_PASSWORD_SELECTORS = [
    "input[name='password']",
    "input[autocomplete='current-password']",
    "input[placeholder='password']",
    "input[type='password']",
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


def _wait_for_first_visible(page: Any, selectors: list[str], timeout_seconds: int) -> Any | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        locator = _first_visible(page, selectors)
        if locator is not None:
            return locator
        page.wait_for_timeout(250)
    return None


def _wait_for_quiet(page: Any, timeout_seconds: int) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout_seconds, 10) * 1000)
    except Exception:
        page.wait_for_timeout(1500)


def _expand_dashboard_rows(page: Any, row_names: list[str]) -> None:
    """Open known collapsed Grafana rows before measuring or capturing a dashboard."""
    for row_name in row_names:
        try:
            row = page.get_by_text(row_name, exact=False).first
            if row.is_visible():
                row.click()
                page.wait_for_timeout(500)
        except Exception:
            logging.warning("Could not expand Grafana row %r", row_name)


def _has_visible_panel_loader(page: Any) -> bool:
    """Ignore Grafana toolbar activity; only panel-local loaders block capture."""
    for selector in LOADING_INDICATOR_SELECTORS:
        loader = _first_visible(page, [selector])
        if loader is None:
            continue
        try:
            if loader.evaluate(
                "(element, panelSelector) => Boolean(element.closest(panelSelector))",
                PANEL_SURFACE_SELECTOR,
            ):
                return True
        except Exception:
            # A detected loader that cannot be inspected is safer treated as active.
            return True
    return False


def _dashboard_is_ready(page: Any, wait_selector: str, *, wait_for_panel_loaders: bool = True) -> bool:
    """Grafana renders panel shells before its queries have completed."""
    if _first_visible(page, [wait_selector]) is None:
        return False
    if _first_visible(page, [PANEL_SURFACE_SELECTOR]) is None:
        return False
    return not wait_for_panel_loaders or not _has_visible_panel_loader(page)


def _wait_for_dashboard_ready(
    page: Any,
    *,
    wait_selector: str,
    timeout_seconds: int,
    settle_seconds: int,
    dashboard_name: str,
    wait_for_panel_loaders: bool = True,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _dashboard_is_ready(page, wait_selector, wait_for_panel_loaders=wait_for_panel_loaders):
            if settle_seconds:
                page.wait_for_timeout(settle_seconds * 1000)
            if _dashboard_is_ready(page, wait_selector, wait_for_panel_loaders=wait_for_panel_loaders):
                return
        page.wait_for_timeout(500)
    raise GrafanaCaptureError(
        f"Dashboard did not finish rendering within {timeout_seconds} seconds: {dashboard_name}"
    )


def _dashboard_capture_height(page: Any, fallback_height: int) -> int:
    """Capture through the bottom of rendered panels, not Grafana's inflated document height."""
    try:
        panel_bottom = page.locator(PANEL_SURFACE_SELECTOR).evaluate_all(
            """(panels) => Math.ceil(Math.max(0, ...panels.map((panel) => {
                const box = panel.getBoundingClientRect();
                return box.width > 0 && box.height > 0 ? box.bottom + window.scrollY : 0;
            })))"""
        )
        return max(fallback_height, int(panel_bottom) + 24)
    except Exception:
        return fallback_height


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
    username = _first_visible(page, LOGIN_USERNAME_SELECTORS)
    password = _first_visible(page, LOGIN_PASSWORD_SELECTORS)
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
        # Grafana is reachable directly; bypass any stale Windows proxy configured for Chromium.
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=["--no-proxy-server"],
        )
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
        username_input = _wait_for_first_visible(self.page, LOGIN_USERNAME_SELECTORS, timeout)
        password_input = _wait_for_first_visible(self.page, LOGIN_PASSWORD_SELECTORS, timeout)
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
            raw_capture_options = dashboard.get("query_params", {}).get("capture_options", {})
            capture_options = raw_capture_options if isinstance(raw_capture_options, dict) else {}
            rows_to_expand = capture_options.get("expand_rows", [])
            if isinstance(rows_to_expand, list):
                _expand_dashboard_rows(self.page, [str(row) for row in rows_to_expand if str(row).strip()])
            settle_seconds = int(
                float(capture_options.get("settle_seconds", grafana.get("wait_seconds", 8)))
            )
            if capture_options.get("skip_panel_readiness", False):
                self.page.wait_for_timeout(settle_seconds * 1000)
            else:
                _wait_for_dashboard_ready(
                    self.page,
                    wait_selector=selector,
                    timeout_seconds=panel_timeout,
                    settle_seconds=settle_seconds,
                    dashboard_name=dashboard["name"],
                    wait_for_panel_loaders=bool(capture_options.get("wait_for_panel_loaders", True)),
                )
            if _is_login_page(self.page):
                raise GrafanaAuthenticationError("Grafana session expired before the screenshot was saved.")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            full_page = bool(capture_options.get("full_page", grafana.get("full_page", True)))
            if full_page:
                self.page.screenshot(path=str(output_path), full_page=True)
            else:
                self.page.screenshot(
                    path=str(output_path),
                    clip={
                        "x": 0,
                        "y": 0,
                        "width": width,
                        "height": _dashboard_capture_height(self.page, height),
                    },
                )
        except GrafanaAuthenticationError:
            raise
        except GrafanaCaptureError:
            raise
        except Exception as exc:
            raise GrafanaCaptureError(f"Capture failed for {dashboard['name']}: {type(exc).__name__}") from exc
