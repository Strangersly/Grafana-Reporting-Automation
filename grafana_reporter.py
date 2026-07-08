#!/usr/bin/env python3
"""Capture scheduled weekly and monthly Grafana dashboard screenshots."""

from __future__ import annotations

import argparse
import calendar
import json
import logging
import os
import platform
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


INDONESIAN_MONTHS = [
    "",
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
]

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


DEFAULT_CONFIG: dict[str, Any] = {
    "grafana": {
        "base_url": "",
        "login_path": "/login",
        "username_env": "GRAFANA_USERNAME",
        "password_env": "GRAFANA_PASSWORD",
        "username": "",
        "password": "",
        "org_id": None,
        "theme": "dark",
        "kiosk": True,
        "timezone": "Asia/Jakarta",
        "viewport": {"width": 3440, "height": 1220},
        "full_page": True,
        "wait_seconds": 8,
        "navigation_timeout_seconds": 90,
        "panel_timeout_seconds": 60,
        "ignore_https_errors": False,
        "storage_state": ".grafana-auth-state.json",
        "wait_for_selector": ".panel-container, [data-testid='panel-container'], [data-testid='data-testid Panel header']",
    },
    "output": {
        "root": "reports",
        "folder_template": "{year}/{month_number}. {month_name}/{site}/{group}/{category}/{name}",
        "overwrite": True,
    },
    "periods": {
        "target_month": "previous",
        "weekly_mode": "four_blocks",
    },
    "schedule": {
        "enabled": True,
        "poll_seconds": 60,
        "monthly": {
            "enabled": True,
            "day": 1,
            "time": "07:30",
            "capture": "previous_month",
            "include_weeklies": True,
        },
        "weekly": {
            "enabled": False,
            "day": "monday",
            "time": "07:00",
            "capture": "previous_7_days",
        },
    },
    "dashboards": [],
}


@dataclass(frozen=True)
class Period:
    report_type: str
    filename: str
    start: datetime
    end: datetime
    folder_year: int
    folder_month: int


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. Copy config.example.json to config.json and edit it."
        )
    with path.open("r", encoding="utf-8") as handle:
        user_config = json.load(handle)
    config = deep_merge(DEFAULT_CONFIG, user_config)
    validate_config(config)
    return config


def load_dotenv_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def validate_config(config: dict[str, Any]) -> None:
    if not config["grafana"].get("base_url"):
        raise ValueError("grafana.base_url is required.")
    if not config.get("dashboards"):
        raise ValueError("At least one dashboard entry is required.")
    for index, dashboard in enumerate(config["dashboards"], start=1):
        if not dashboard.get("name"):
            raise ValueError(f"dashboards[{index}].name is required.")
        if not dashboard.get("url"):
            raise ValueError(f"dashboards[{index}].url is required.")


def get_timezone(config: dict[str, Any]) -> tzinfo:
    timezone_name = config["grafana"].get("timezone") or "Asia/Jakarta"
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        if timezone_name == "Asia/Jakarta":
            return timezone(timedelta(hours=7), "Asia/Jakarta")
        raise


def month_name(month: int) -> str:
    return INDONESIAN_MONTHS[month]


def resolve_target_month(
    args: argparse.Namespace, config: dict[str, Any], now: datetime
) -> tuple[int, int]:
    if args.year or args.month:
        if not args.year or not args.month:
            raise ValueError("--year and --month must be used together.")
        if args.month < 1 or args.month > 12:
            raise ValueError("--month must be between 1 and 12.")
        return args.year, args.month

    mode = config.get("periods", {}).get("target_month", "previous")
    if mode == "current":
        return now.year, now.month
    if mode != "previous":
        raise ValueError("periods.target_month must be 'previous' or 'current'.")

    first_of_current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_month = first_of_current_month - timedelta(days=1)
    return previous_month.year, previous_month.month


def month_period(year: int, month: int, tz: tzinfo) -> Period:
    last_day = calendar.monthrange(year, month)[1]
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=tz)
    end = datetime(year, month, last_day, 23, 59, 59, 999000, tzinfo=tz)
    return Period("monthly", "monthly.png", start, end, year, month)


def week_block_periods(year: int, month: int, tz: tzinfo) -> list[Period]:
    last_day = calendar.monthrange(year, month)[1]
    ranges = [
        (1, min(7, last_day), "week1.png"),
        (8, min(14, last_day), "week2.png"),
        (15, min(21, last_day), "week3.png"),
        (22, last_day, "week4.png"),
    ]
    periods: list[Period] = []
    for start_day, end_day, filename in ranges:
        if start_day > last_day:
            continue
        periods.append(
            Period(
                "weekly",
                filename,
                datetime(year, month, start_day, 0, 0, 0, tzinfo=tz),
                datetime(year, month, end_day, 23, 59, 59, 999000, tzinfo=tz),
                year,
                month,
            )
        )
    return periods


def previous_7_days_period(now: datetime) -> Period:
    end = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(milliseconds=1)
    start = (end - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    block_number = min(((start.day - 1) // 7) + 1, 4)
    return Period(
        "weekly",
        f"week{block_number}.png",
        start,
        end,
        start.year,
        start.month,
    )


def custom_date_period(
    report: str,
    from_date: str,
    to_date: str,
    tz: tzinfo,
    filename: str | None = None,
) -> Period:
    try:
        start_date = datetime.strptime(from_date, "%Y-%m-%d")
        end_date = datetime.strptime(to_date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("--from-date and --to-date must use YYYY-MM-DD format.") from exc

    start = datetime(start_date.year, start_date.month, start_date.day, 0, 0, 0, tzinfo=tz)
    end = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, 999000, tzinfo=tz)
    if end < start:
        raise ValueError("--to-date must be the same as or later than --from-date.")

    if filename:
        output_filename = filename
    elif report == "monthly":
        output_filename = "monthly.png"
    else:
        block_number = min(((start.day - 1) // 7) + 1, 4)
        output_filename = f"week{block_number}.png"

    return Period(report, output_filename, start, end, start.year, start.month)


def build_periods(
    report: str, year: int, month: int, tz: tzinfo
) -> list[Period]:
    periods: list[Period] = []
    if report in {"weekly", "all"}:
        periods.extend(week_block_periods(year, month, tz))
    if report in {"monthly", "all"}:
        periods.append(month_period(year, month, tz))
    return periods


def safe_path_part(value: Any) -> str:
    text = str(value).strip()
    text = INVALID_PATH_CHARS.sub("_", text)
    text = text.rstrip(" .")
    return text or "_"


def dashboard_field(dashboard: dict[str, Any], key: str, default: str) -> str:
    folder = dashboard.get("folder", {})
    return str(dashboard.get(key) or folder.get(key) or default)


def output_path_for(
    config: dict[str, Any], dashboard: dict[str, Any], period: Period
) -> Path:
    output_config = config["output"]
    root = Path(output_config["root"]).expanduser()
    month = period.folder_month
    fields = {
        "year": period.folder_year,
        "month": month,
        "month_number": month,
        "month_number_2": f"{month:02d}",
        "month_name": month_name(month),
        "site": dashboard_field(dashboard, "site", "default"),
        "group": dashboard_field(dashboard, "group", "default"),
        "category": dashboard_field(dashboard, "category", "default"),
        "name": dashboard_field(dashboard, "name", dashboard["name"]),
        "dashboard_name": dashboard["name"],
    }
    rendered = output_config["folder_template"].format(**fields)
    folder_parts = [
        safe_path_part(part)
        for part in re.split(r"[\\/]+", rendered)
        if part.strip()
    ]
    return root.joinpath(*folder_parts, period.filename)


def epoch_millis(value: datetime) -> str:
    return str(int(value.timestamp() * 1000))


def set_query_param(pairs: list[tuple[str, str]], key: str, value: Any) -> list[tuple[str, str]]:
    return [(current_key, current_value) for current_key, current_value in pairs if current_key != key] + [
        (key, str(value))
    ]


def build_dashboard_url(
    config: dict[str, Any], dashboard: dict[str, Any], period: Period
) -> str:
    grafana = config["grafana"]
    raw_url = dashboard["url"]
    if urlparse(raw_url).scheme:
        dashboard_url = raw_url
    else:
        dashboard_url = urljoin(grafana["base_url"].rstrip("/") + "/", raw_url.lstrip("/"))

    parsed = urlparse(dashboard_url)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query_pairs = set_query_param(query_pairs, "from", epoch_millis(period.start))
    query_pairs = set_query_param(query_pairs, "to", epoch_millis(period.end))

    if grafana.get("theme"):
        query_pairs = set_query_param(query_pairs, "theme", grafana["theme"])
    if grafana.get("org_id") is not None:
        query_pairs = set_query_param(query_pairs, "orgId", grafana["org_id"])
    if grafana.get("kiosk"):
        query_pairs = set_query_param(query_pairs, "kiosk", "tv")

    for key, value in dashboard.get("query_params", {}).items():
        query_pairs = set_query_param(query_pairs, key, value)

    return urlunparse(parsed._replace(query=urlencode(query_pairs)))


def resolve_relative_path(raw_path: str | None, base_dir: Path) -> Path | None:
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return base_dir / path


def get_credentials(config: dict[str, Any]) -> tuple[str | None, str | None]:
    grafana = config["grafana"]
    username_env = grafana.get("username_env", "GRAFANA_USERNAME")
    password_env = grafana.get("password_env", "GRAFANA_PASSWORD")
    username = grafana.get("username") or os.getenv(username_env) or read_windows_user_env(username_env)
    password = grafana.get("password") or os.getenv(password_env) or read_windows_user_env(password_env)
    return username, password


def read_windows_user_env(name: str) -> str | None:
    if platform.system() != "Windows":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value)
    except FileNotFoundError:
        return None
    except OSError:
        return None


def viewport_for_dashboard(config: dict[str, Any], dashboard: dict[str, Any]) -> dict[str, int]:
    viewport = config["grafana"].get("viewport", {})
    size = {
        "width": int(viewport.get("width", 1920)),
        "height": int(viewport.get("height", 1080)),
    }
    dashboard_viewport = dashboard.get("viewport", {})
    if isinstance(dashboard_viewport, dict):
        if dashboard_viewport.get("width"):
            size["width"] = int(dashboard_viewport["width"])
        if dashboard_viewport.get("height"):
            size["height"] = int(dashboard_viewport["height"])
    return size


def first_visible_locator(page: Any, selectors: list[str]) -> Any | None:
    for selector in selectors:
        locator = page.locator(selector)
        try:
            if locator.count() > 0 and locator.first.is_visible():
                return locator.first
        except Exception:
            continue
    return None


def wait_for_quiet_page(page: Any, timeout_seconds: int) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_seconds * 1000)
    except Exception:
        page.wait_for_timeout(1500)


def is_grafana_login_page(page: Any) -> bool:
    try:
        if urlparse(page.url).path.rstrip("/").endswith("/login"):
            return True
    except Exception:
        pass
    username_input = first_visible_locator(
        page,
        [
            "input[name='user']",
            "input[name='username']",
            "input[autocomplete='username']",
            "input[placeholder='email or username']",
            "input[aria-label='Username']",
            "input[type='email']",
            "input[type='text']",
        ],
    )
    password_input = first_visible_locator(
        page,
        [
            "input[name='password']",
            "input[autocomplete='current-password']",
            "input[placeholder='password']",
            "input[aria-label='Password']",
            "input[type='password']",
        ],
    )
    return username_input is not None and password_input is not None


def visible_login_error(page: Any) -> str:
    for selector in ["[role='alert']", "[data-testid='data-testid Alert error']", ".alert-error"]:
        locator = first_visible_locator(page, [selector])
        if locator is not None:
            try:
                return locator.inner_text().strip()
            except Exception:
                return ""
    return ""


def assert_not_login_page(page: Any, context: str) -> None:
    if not is_grafana_login_page(page):
        return
    error_text = visible_login_error(page)
    detail = f" Grafana message: {error_text}" if error_text else ""
    raise RuntimeError(
        f"Grafana is still showing the login page while {context}.{detail} "
        "Check GRAFANA_USERNAME/GRAFANA_PASSWORD, or run with --show-browser to complete any manual login/SSO step."
    )


def wait_for_manual_login(page: Any, timeout_seconds: int) -> None:
    logging.info("Waiting up to %s seconds for manual login in the visible browser.", timeout_seconds)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not is_grafana_login_page(page):
            wait_for_quiet_page(page, 10)
            return
        page.wait_for_timeout(1000)


def maybe_login(page: Any, config: dict[str, Any], allow_manual_login: bool = False) -> None:
    username, password = get_credentials(config)
    if not username or not password:
        raise RuntimeError(
            "Grafana credentials were not found. Set GRAFANA_USERNAME and GRAFANA_PASSWORD, "
            "then reopen PowerShell or rerun the script."
        )

    grafana = config["grafana"]
    login_url = urljoin(
        grafana["base_url"].rstrip("/") + "/", grafana.get("login_path", "/login").lstrip("/")
    )
    timeout_seconds = int(grafana.get("navigation_timeout_seconds", 90))
    logging.info("Opening Grafana login page.")
    page.goto(login_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
    wait_for_quiet_page(page, timeout_seconds)

    username_input = first_visible_locator(
        page,
        [
            "input[name='user']",
            "input[name='username']",
            "input[autocomplete='username']",
            "input[placeholder='email or username']",
            "input[aria-label='Username']",
            "input[type='email']",
            "input[type='text']",
        ],
    )
    password_input = first_visible_locator(
        page,
        [
            "input[name='password']",
            "input[autocomplete='current-password']",
            "input[placeholder='password']",
            "input[aria-label='Password']",
            "input[type='password']",
        ],
    )

    if not username_input or not password_input:
        if allow_manual_login and is_grafana_login_page(page):
            wait_for_manual_login(page, int(grafana.get("manual_login_timeout_seconds", 180)))
            assert_not_login_page(page, "waiting for manual login")
            return
        logging.info("Login form was not visible. Reusing existing session if available.")
        return

    username_input.fill(username)
    password_input.fill(password)
    submit = first_visible_locator(
        page,
        [
            "button[type='submit']",
            "button:has-text('Log in')",
            "button:has-text('Sign in')",
            "[data-testid='data-testid Login button']",
        ],
    )
    if submit:
        submit.click()
    else:
        password_input.press("Enter")
    wait_for_quiet_page(page, timeout_seconds)
    page.wait_for_timeout(1000)
    if allow_manual_login and is_grafana_login_page(page):
        wait_for_manual_login(page, int(grafana.get("manual_login_timeout_seconds", 180)))
    assert_not_login_page(page, "logging in")

    for selector in ["button:has-text('Skip')", "button:has-text('No thanks')"]:
        try:
            button = page.locator(selector)
            if button.count() > 0 and button.first.is_visible():
                button.first.click()
                wait_for_quiet_page(page, timeout_seconds)
                break
        except Exception:
            continue


def capture_periods(
    config: dict[str, Any],
    periods: list[Period],
    config_dir: Path,
    show_browser: bool = False,
    dry_run: bool = False,
) -> None:
    if not periods:
        logging.info("No periods selected.")
        return

    for dashboard in config["dashboards"]:
        for period in periods:
            output_path = output_path_for(config, dashboard, period)
            url = build_dashboard_url(config, dashboard, period)
            logging.info("%s -> %s", url, output_path)

    if dry_run:
        return

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install -r requirements.txt"
        ) from exc

    grafana = config["grafana"]
    viewport = grafana.get("viewport", {})
    timeout_seconds = int(grafana.get("navigation_timeout_seconds", 90))
    panel_timeout_seconds = int(grafana.get("panel_timeout_seconds", 60))
    storage_state_path = resolve_relative_path(grafana.get("storage_state"), config_dir)
    context_options: dict[str, Any] = {
        "viewport": {
            "width": int(viewport.get("width", 1920)),
            "height": int(viewport.get("height", 1080)),
        },
        "ignore_https_errors": bool(grafana.get("ignore_https_errors", False)),
    }
    if storage_state_path and storage_state_path.exists():
        context_options["storage_state"] = str(storage_state_path)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not show_browser)
        context = browser.new_context(**context_options)
        page = context.new_page()
        page.set_default_timeout(timeout_seconds * 1000)
        page.set_default_navigation_timeout(timeout_seconds * 1000)

        maybe_login(page, config, allow_manual_login=show_browser)
        if storage_state_path:
            storage_state_path.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(storage_state_path))

        for dashboard in config["dashboards"]:
            page.set_viewport_size(viewport_for_dashboard(config, dashboard))
            for period in periods:
                output_path = output_path_for(config, dashboard, period)
                if output_path.exists() and not config["output"].get("overwrite", True):
                    logging.info("Skipping existing file: %s", output_path)
                    continue

                output_path.parent.mkdir(parents=True, exist_ok=True)
                url = build_dashboard_url(config, dashboard, period)
                logging.info("Capturing %s for %s", period.filename, dashboard["name"])
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
                wait_for_quiet_page(page, timeout_seconds)
                if is_grafana_login_page(page):
                    logging.info("Dashboard redirected to login. Signing in again.")
                    maybe_login(page, config, allow_manual_login=show_browser)
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)
                    wait_for_quiet_page(page, timeout_seconds)
                assert_not_login_page(page, f"capturing {dashboard['name']}")

                wait_selector = dashboard.get("wait_for_selector") or grafana.get("wait_for_selector")
                if wait_selector:
                    try:
                        page.locator(wait_selector).first.wait_for(
                            state="visible", timeout=panel_timeout_seconds * 1000
                        )
                    except PlaywrightTimeoutError:
                        logging.warning("Timed out waiting for dashboard panels: %s", dashboard["name"])

                page.wait_for_timeout(int(float(grafana.get("wait_seconds", 8)) * 1000))
                assert_not_login_page(page, f"saving {output_path}")
                page.screenshot(path=str(output_path), full_page=bool(grafana.get("full_page", True)))
                logging.info("Saved %s", output_path)

        browser.close()


def parse_hhmm(value: str) -> datetime_time:
    try:
        hour_text, minute_text = value.split(":", 1)
        return datetime_time(int(hour_text), int(minute_text))
    except Exception as exc:
        raise ValueError(f"Invalid schedule time '{value}'. Use HH:MM.") from exc


def load_scheduler_state(output_root: Path) -> dict[str, str]:
    path = output_root / ".grafana_reporter_state.json"
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        logging.warning("Could not read scheduler state. Starting fresh.")
        return {}


def save_scheduler_state(output_root: Path, state: dict[str, str]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / ".grafana_reporter_state.json"
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def scheduled_time_reached(entry: dict[str, Any], now: datetime) -> bool:
    scheduled = parse_hhmm(entry.get("time", "00:00"))
    return now.time() >= scheduled


def should_run_monthly(entry: dict[str, Any], now: datetime) -> bool:
    if not entry.get("enabled", False) or not scheduled_time_reached(entry, now):
        return False
    configured_day = int(entry.get("day", 1))
    return now.day == configured_day


def should_run_weekly(entry: dict[str, Any], now: datetime) -> bool:
    if not entry.get("enabled", False) or not scheduled_time_reached(entry, now):
        return False
    day = str(entry.get("day", "monday")).lower()
    if day not in WEEKDAYS:
        raise ValueError("schedule.weekly.day must be a weekday name, such as 'monday'.")
    return now.weekday() == WEEKDAYS[day]


def previous_month_for(now: datetime) -> tuple[int, int]:
    first_of_current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous = first_of_current_month - timedelta(days=1)
    return previous.year, previous.month


def run_scheduler(
    config: dict[str, Any],
    config_dir: Path,
    show_browser: bool,
    dry_run: bool,
) -> None:
    tz = get_timezone(config)
    output_root = Path(config["output"]["root"]).expanduser()
    state = load_scheduler_state(output_root)
    poll_seconds = int(config.get("schedule", {}).get("poll_seconds", 60))
    logging.info("Scheduler started. Polling every %s seconds.", poll_seconds)

    while True:
        now = datetime.now(tz)
        schedule = config.get("schedule", {})

        monthly = schedule.get("monthly", {})
        if should_run_monthly(monthly, now):
            state_key = f"monthly:{now.date().isoformat()}"
            if state.get("last_monthly") != state_key:
                year, month = previous_month_for(now)
                report = "all" if monthly.get("include_weeklies", True) else "monthly"
                periods = build_periods(report, year, month, tz)
                capture_periods(config, periods, config_dir, show_browser, dry_run)
                state["last_monthly"] = state_key
                save_scheduler_state(output_root, state)

        weekly = schedule.get("weekly", {})
        if should_run_weekly(weekly, now):
            state_key = f"weekly:{now.date().isoformat()}"
            if state.get("last_weekly") != state_key:
                if weekly.get("capture", "previous_7_days") != "previous_7_days":
                    raise ValueError("Only schedule.weekly.capture='previous_7_days' is supported.")
                capture_periods(config, [previous_7_days_period(now)], config_dir, show_browser, dry_run)
                state["last_weekly"] = state_key
                save_scheduler_state(output_root, state)

        time.sleep(poll_seconds)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Grafana weekly and monthly screenshot reports.")
    parser.add_argument("--config", default="config.json", help="Path to JSON config file.")
    parser.add_argument("--env-file", default=".env", help="Path to local dotenv credentials file.")
    parser.add_argument("--mode", choices=["once", "schedule"], default="once")
    parser.add_argument("--report", choices=["weekly", "monthly", "all"], default="all")
    parser.add_argument("--year", type=int, help="Target report year. Must be used with --month.")
    parser.add_argument("--month", type=int, help="Target report month number. Must be used with --year.")
    parser.add_argument("--from-date", help="Custom report start date in YYYY-MM-DD format.")
    parser.add_argument("--to-date", help="Custom report end date in YYYY-MM-DD format.")
    parser.add_argument("--filename", help="Output filename for a custom date range, such as week1.png.")
    parser.add_argument("--show-browser", action="store_true", help="Run Chromium visibly for debugging.")
    parser.add_argument("--limit", type=int, help="Only process the first N dashboards. Useful for testing.")
    parser.add_argument("--dry-run", action="store_true", help="Print target URLs and files without screenshots.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    config_path = Path(args.config).expanduser()
    config_dir = config_path.resolve().parent
    env_path = Path(args.env_file).expanduser()
    if not env_path.is_absolute():
        env_path = config_dir / env_path
    load_dotenv_file(env_path)
    config = load_config(config_path)
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("--limit must be 1 or greater.")
        config = dict(config)
        config["dashboards"] = config["dashboards"][: args.limit]
    config_dir = config_path.resolve().parent

    if args.mode == "schedule":
        run_scheduler(config, config_dir, args.show_browser, args.dry_run)
        return 0

    tz = get_timezone(config)
    now = datetime.now(tz)
    has_custom_range = bool(args.from_date or args.to_date)
    if has_custom_range:
        if not args.from_date or not args.to_date:
            raise ValueError("Use --from-date and --to-date together.")
        if args.report == "all":
            raise ValueError("Custom date ranges require --report weekly or --report monthly, not --report all.")
        periods = [custom_date_period(args.report, args.from_date, args.to_date, tz, args.filename)]
    else:
        if args.filename:
            raise ValueError("--filename is only valid with --from-date and --to-date.")
        year, month = resolve_target_month(args, config, now)
        periods = build_periods(args.report, year, month, tz)
    capture_periods(config, periods, config_dir, args.show_browser, args.dry_run)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        logging.info("Stopped.")
        raise SystemExit(130)
    except Exception as error:
        logging.error("%s", error)
        raise SystemExit(1)











