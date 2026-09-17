from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse


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
INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class Period:
    report_type: str
    filename: str
    start: datetime
    end: datetime
    folder_year: int
    folder_month: int


def month_period(year: int, month: int, tz: tzinfo) -> Period:
    last_day = calendar.monthrange(year, month)[1]
    return Period(
        "monthly",
        "monthly.png",
        datetime(year, month, 1, tzinfo=tz),
        datetime(year, month, last_day, 23, 59, 59, 999000, tzinfo=tz),
        year,
        month,
    )


def week_block_periods(year: int, month: int, tz: tzinfo) -> list[Period]:
    last_day = calendar.monthrange(year, month)[1]
    ranges = [(1, 7, "week1.png"), (8, 14, "week2.png"), (15, 21, "week3.png"), (22, last_day, "week4.png")]
    return [
        Period(
            "weekly",
            filename,
            datetime(year, month, start_day, tzinfo=tz),
            datetime(year, month, min(end_day, last_day), 23, 59, 59, 999000, tzinfo=tz),
            year,
            month,
        )
        for start_day, end_day, filename in ranges
        if start_day <= last_day
    ]


def previous_7_days_period(now: datetime) -> Period:
    end = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(milliseconds=1)
    start = (end - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
    block = min(((start.day - 1) // 7) + 1, 4)
    return Period("weekly", f"week{block}.png", start, end, start.year, start.month)


def custom_date_period(
    report_type: str,
    start_date: date,
    end_date: date,
    tz: tzinfo,
    filename: str = "",
) -> Period:
    if end_date < start_date:
        raise ValueError("The end date must be the same as or later than the start date.")
    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=tz)
    end = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59, 999000, tzinfo=tz)
    if filename:
        output_filename = filename
    elif report_type == "monthly":
        output_filename = "monthly.png"
    else:
        output_filename = f"week{min(((start.day - 1) // 7) + 1, 4)}.png"
    if not output_filename.lower().endswith(".png"):
        output_filename += ".png"
    return Period(report_type, safe_path_part(output_filename), start, end, start.year, start.month)


def build_periods(report: str, year: int, month: int, tz: tzinfo) -> list[Period]:
    if not 1 <= month <= 12:
        raise ValueError("Month must be between 1 and 12.")
    periods: list[Period] = []
    if report in {"weekly", "all"}:
        periods.extend(week_block_periods(year, month, tz))
    if report in {"monthly", "all"}:
        periods.append(month_period(year, month, tz))
    return periods


def safe_path_part(value: Any) -> str:
    text = INVALID_PATH_CHARS.sub("_", str(value).strip()).rstrip(" .")
    return text or "_"


def dashboard_field(dashboard: dict[str, Any], key: str, default: str) -> str:
    folder = dashboard.get("folder", {})
    return str(dashboard.get(key) or folder.get(key) or default)


def output_relative_path_for(config: dict[str, Any], dashboard: dict[str, Any], period: Period) -> Path:
    output = config["output"]
    fields = {
        "year": period.folder_year,
        "month": period.folder_month,
        "month_number": period.folder_month,
        "month_number_2": f"{period.folder_month:02d}",
        "month_name": INDONESIAN_MONTHS[period.folder_month],
        "site": dashboard_field(dashboard, "site", "default"),
        "group": dashboard_field(dashboard, "group", "default"),
        "category": dashboard_field(dashboard, "category", "default"),
        "name": dashboard_field(dashboard, "name", dashboard["name"]),
        "dashboard_name": dashboard["name"],
    }
    rendered = output["folder_template"].format(**fields)
    parts = [safe_path_part(part) for part in re.split(r"[\\/]+", rendered) if part.strip()]
    return Path(*parts, safe_path_part(period.filename))


def output_path_for(config: dict[str, Any], dashboard: dict[str, Any], period: Period) -> Path:
    return Path(config["output"]["root"]).expanduser() / output_relative_path_for(config, dashboard, period)


def _set_query_param(pairs: list[tuple[str, str]], key: str, value: Any) -> list[tuple[str, str]]:
    return [(k, v) for k, v in pairs if k != key] + [(key, str(value))]


def _report_query_params(dashboard: dict[str, Any], report_type: str) -> dict[str, Any]:
    """Return dashboard URL parameters, including a report-type-specific override."""
    configured = dashboard.get("query_params", {})
    if not isinstance(configured, dict):
        return {}
    params = {key: value for key, value in configured.items() if key != "report_overrides"}
    overrides = configured.get("report_overrides", {})
    if isinstance(overrides, dict) and isinstance(overrides.get(report_type), dict):
        params.update(overrides[report_type])
    return params


def build_dashboard_url(config: dict[str, Any], dashboard: dict[str, Any], period: Period) -> str:
    grafana = config["grafana"]
    raw_url = dashboard["url"]
    dashboard_url = raw_url if urlparse(raw_url).scheme else urljoin(
        grafana["base_url"].rstrip("/") + "/", raw_url.lstrip("/")
    )
    parsed = urlparse(dashboard_url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    pairs = _set_query_param(pairs, "from", int(period.start.timestamp() * 1000))
    pairs = _set_query_param(pairs, "to", int(period.end.timestamp() * 1000))
    if grafana.get("theme"):
        pairs = _set_query_param(pairs, "theme", grafana["theme"])
    if grafana.get("org_id") is not None:
        pairs = _set_query_param(pairs, "orgId", grafana["org_id"])
    if grafana.get("kiosk"):
        pairs = _set_query_param(pairs, "kiosk", "tv")
    for key, value in _report_query_params(dashboard, period.report_type).items():
        pairs = _set_query_param(pairs, key, value)
    return urlunparse(parsed._replace(query=urlencode(pairs)))
