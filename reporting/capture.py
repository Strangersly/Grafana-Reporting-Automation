from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from .core import Period, build_dashboard_url


class GrafanaAuthenticationError(RuntimeError):
    pass


class GrafanaCaptureError(RuntimeError):
    pass


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
        self.page.goto(login_url, wait_until="domcontentloaded", timeout=timeout * 1000)
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
            selector = dashboard.get("wait_for_selector") or grafana.get("wait_for_selector")
            if selector:
                try:
                    self.page.locator(selector).first.wait_for(state="visible", timeout=panel_timeout * 1000)
                except Exception:
                    logging.warning("Dashboard panels did not become visible for %s", dashboard["name"])
            self.page.wait_for_timeout(int(float(grafana.get("wait_seconds", 8)) * 1000))
            if _is_login_page(self.page):
                raise GrafanaAuthenticationError("Grafana session expired before the screenshot was saved.")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(output_path), full_page=bool(grafana.get("full_page", True)))
        except GrafanaAuthenticationError:
            raise
        except Exception as exc:
            raise GrafanaCaptureError(f"Capture failed for {dashboard['name']}: {type(exc).__name__}") from exc
