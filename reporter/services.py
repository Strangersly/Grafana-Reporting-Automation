from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from reporting.core import (
    Period,
    build_periods,
    custom_date_period,
    output_relative_path_for,
    previous_7_days_period,
)

from .models import Dashboard, GrafanaConnection, ReportArtifact, ReportRun, Schedule, User


FOLDER_TEMPLATE = "{year}/{month_number}. {month_name}/{site}/{group}/{category}/{name}"


def connection_config(connection: GrafanaConnection, *, include_credentials: bool = False) -> dict:
    grafana = {
        "base_url": connection.base_url,
        "login_path": connection.login_path,
        "org_id": connection.org_id,
        "theme": connection.theme,
        "kiosk": connection.kiosk,
        "timezone": connection.timezone,
        "viewport": {"width": connection.viewport_width, "height": connection.viewport_height},
        "full_page": connection.full_page,
        "wait_seconds": connection.wait_seconds,
        "navigation_timeout_seconds": connection.navigation_timeout_seconds,
        "panel_timeout_seconds": connection.panel_timeout_seconds,
        "ignore_https_errors": connection.ignore_https_errors,
        "wait_for_selector": connection.wait_for_selector,
    }
    if include_credentials:
        grafana["username"] = connection.get_username()
        grafana["password"] = connection.get_password()
    return {
        "grafana": grafana,
        "output": {"root": str(settings.REPORT_ROOT), "folder_template": FOLDER_TEMPLATE, "overwrite": True},
    }


def dashboard_config(dashboard: Dashboard) -> dict:
    data = {
        "name": dashboard.name,
        "site": dashboard.site,
        "group": dashboard.group,
        "category": dashboard.category,
        "url": dashboard.url,
        "query_params": dashboard.query_params,
    }
    if dashboard.viewport_width or dashboard.viewport_height:
        data["viewport"] = {
            "width": dashboard.viewport_width,
            "height": dashboard.viewport_height,
        }
    if dashboard.wait_for_selector:
        data["wait_for_selector"] = dashboard.wait_for_selector
    return data


def artifact_dashboard_config(artifact: ReportArtifact) -> dict:
    data = {
        "name": artifact.dashboard_name,
        "site": artifact.site,
        "group": artifact.group,
        "category": artifact.category,
        "url": artifact.dashboard_url,
        "query_params": artifact.dashboard_query_params,
    }
    if artifact.viewport_width or artifact.viewport_height:
        data["viewport"] = {"width": artifact.viewport_width, "height": artifact.viewport_height}
    if artifact.wait_for_selector:
        data["wait_for_selector"] = artifact.wait_for_selector
    return data


def manual_periods(
    *,
    mode: str,
    year: int | None = None,
    month: int | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    filename: str = "",
    timezone_name: str = "Asia/Jakarta",
) -> list[Period]:
    tz = ZoneInfo(timezone_name)
    if mode == "previous_7_days":
        return [previous_7_days_period(timezone.now().astimezone(tz))]
    if mode == "custom":
        if not from_date or not to_date:
            raise ValueError("Custom reports require a start and end date.")
        report_type = "monthly" if filename.lower() == "monthly.png" else "weekly"
        return [custom_date_period(report_type, from_date, to_date, tz, filename)]
    if year is None or month is None:
        raise ValueError("Month-based reports require a year and month.")
    return build_periods(mode, year, month, tz)


@transaction.atomic
def create_report_run(
    *,
    periods: list[Period],
    dashboards: Iterable[Dashboard],
    source: str,
    preset: str,
    requested_by: User | None = None,
    schedule: Schedule | None = None,
    scheduled_for: datetime | None = None,
    custom_filename: str = "",
) -> ReportRun:
    periods = list(periods)
    dashboards = list(dashboards)
    if not periods:
        raise ValueError("At least one report period is required.")
    if not dashboards:
        raise ValueError("At least one dashboard is required.")
    if schedule and scheduled_for:
        existing = ReportRun.objects.filter(schedule=schedule, scheduled_for=scheduled_for).first()
        if existing:
            return existing
    run = ReportRun.objects.create(
        source=source,
        preset=preset,
        period_start=min(period.start for period in periods),
        period_end=max(period.end for period in periods),
        folder_year=periods[0].folder_year,
        folder_month=periods[0].folder_month,
        custom_filename=custom_filename,
        total_artifacts=len(periods) * len(dashboards),
        requested_by=requested_by,
        schedule=schedule,
        scheduled_for=scheduled_for,
    )
    connection = GrafanaConnection.get_solo()
    if not connection:
        raise ValueError("Configure the Grafana connection before creating report runs.")
    config = connection_config(connection)
    artifacts: list[ReportArtifact] = []
    for dashboard in dashboards:
        dashboard_data = dashboard_config(dashboard)
        for period in periods:
            relative_path = output_relative_path_for(config, dashboard_data, period).as_posix()
            artifacts.append(
                ReportArtifact(
                    run=run,
                    dashboard=dashboard,
                    dashboard_name=dashboard.name,
                    site=dashboard.site,
                    group=dashboard.group,
                    category=dashboard.category,
                    dashboard_url=dashboard.url,
                    dashboard_query_params=dashboard.query_params,
                    viewport_width=dashboard.viewport_width,
                    viewport_height=dashboard.viewport_height,
                    wait_for_selector=dashboard.wait_for_selector,
                    report_type=period.report_type,
                    filename=period.filename,
                    period_start=period.start,
                    period_end=period.end,
                    folder_year=period.folder_year,
                    folder_month=period.folder_month,
                    relative_path=relative_path,
                )
            )
    ReportArtifact.objects.bulk_create(artifacts)
    return run


def scheduled_periods(schedule: Schedule, local_now: datetime) -> list[Period]:
    tz = ZoneInfo(schedule.timezone)
    if schedule.preset == Schedule.Preset.PREVIOUS_7_DAYS:
        return [previous_7_days_period(local_now)]
    first_current = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous = first_current - timedelta(days=1)
    report_type = "all" if schedule.preset == Schedule.Preset.PREVIOUS_MONTH_ALL else "monthly"
    return build_periods(report_type, previous.year, previous.month, tz)


def dashboards_for_schedule(schedule: Schedule):
    if schedule.all_dashboards:
        return Dashboard.objects.filter(enabled=True)
    return schedule.dashboards.filter(enabled=True)


def safe_artifact_path(relative_path: str) -> Path:
    root = settings.REPORT_ROOT.resolve()
    candidate = (root / relative_path).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("Artifact path escaped the configured report root.")
    return candidate
