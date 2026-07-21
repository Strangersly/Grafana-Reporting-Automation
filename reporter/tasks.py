from __future__ import annotations

import logging
import time
from datetime import datetime, timezone as datetime_timezone
from zoneinfo import ZoneInfo

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from reporting.capture import GrafanaAuthenticationError, GrafanaCaptureError, GrafanaCaptureSession
from reporting.core import Period

from .models import GrafanaConnection, ReportArtifact, ReportRun, Schedule, User
from .services import (
    artifact_dashboard_config,
    connection_config,
    create_report_run,
    dashboards_for_schedule,
    safe_artifact_path,
    scheduled_periods,
)

logger = logging.getLogger(__name__)


def _refresh_counts(run: ReportRun) -> None:
    counts = {row["status"]: row["count"] for row in run.artifacts.values("status").annotate(count=Count("id"))}
    run.completed_artifacts = counts.get(ReportArtifact.Status.SUCCEEDED, 0)
    run.failed_artifacts = counts.get(ReportArtifact.Status.FAILED, 0)
    run.save(update_fields=["completed_artifacts", "failed_artifacts"])


def _notify_failure(run: ReportRun) -> None:
    recipients = list(
        User.objects.filter(
            is_active=True,
        ).filter(Q(role__in=[User.Role.ADMIN, User.Role.OPERATOR]) | Q(is_superuser=True)).values_list("email", flat=True)
    )
    if not recipients:
        return
    send_mail(
        f"Grafana report {run.get_status_display().lower()}",
        f"Report run {run.id} finished with status {run.get_status_display()}.\n\n{run.summary}",
        settings.DEFAULT_FROM_EMAIL,
        recipients,
        fail_silently=True,
    )


@shared_task(name="reporter.tasks.execute_report_run")
def execute_report_run(run_id: str) -> None:
    run = ReportRun.objects.get(pk=run_id)
    if run.status in {ReportRun.Status.SUCCEEDED, ReportRun.Status.CANCELLED}:
        return
    ReportArtifact.objects.filter(run=run, status=ReportArtifact.Status.CAPTURING).update(
        status=ReportArtifact.Status.PENDING, error=""
    )
    run.status = ReportRun.Status.RUNNING
    run.started_at = run.started_at or timezone.now()
    run.summary = ""
    run.save(update_fields=["status", "started_at", "summary"])

    connection = GrafanaConnection.get_solo()
    if not connection or not connection.credentials_configured:
        message = "Grafana connection or credentials are not configured."
        run.artifacts.filter(status=ReportArtifact.Status.PENDING).update(
            status=ReportArtifact.Status.FAILED, error=message, finished_at=timezone.now()
        )
        run.status = ReportRun.Status.FAILED
        run.summary = message
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "summary", "finished_at"])
        _refresh_counts(run)
        _notify_failure(run)
        return

    authentication_error = ""
    try:
        with GrafanaCaptureSession(connection_config(connection, include_credentials=True)) as session:
            for artifact in run.artifacts.filter(status=ReportArtifact.Status.PENDING).iterator():
                run.refresh_from_db(fields=["cancel_requested"])
                if run.cancel_requested:
                    break
                artifact.status = ReportArtifact.Status.CAPTURING
                artifact.started_at = timezone.now()
                artifact.error = ""
                artifact.save(update_fields=["status", "started_at", "error"])
                period = Period(
                    artifact.report_type,
                    artifact.filename,
                    artifact.period_start,
                    artifact.period_end,
                    artifact.folder_year,
                    artifact.folder_month,
                )
                for attempt in range(1, 4):
                    artifact.attempts += 1
                    artifact.save(update_fields=["attempts"])
                    try:
                        output_path = safe_artifact_path(artifact.relative_path)
                        session.capture(artifact_dashboard_config(artifact), period, output_path)
                        ReportArtifact.objects.filter(
                            relative_path=artifact.relative_path,
                            status=ReportArtifact.Status.SUCCEEDED,
                            superseded=False,
                        ).exclude(pk=artifact.pk).update(superseded=True)
                        artifact.status = ReportArtifact.Status.SUCCEEDED
                        artifact.size_bytes = output_path.stat().st_size
                        artifact.error = ""
                        break
                    except GrafanaAuthenticationError:
                        raise
                    except GrafanaCaptureError as exc:
                        artifact.error = str(exc)
                        if attempt < 3:
                            time.sleep(2 ** attempt)
                else:
                    artifact.status = ReportArtifact.Status.FAILED
                artifact.finished_at = timezone.now()
                artifact.save(update_fields=["status", "size_bytes", "error", "finished_at"])
                _refresh_counts(run)
    except GrafanaAuthenticationError as exc:
        authentication_error = str(exc)
        run.artifacts.filter(status__in=[ReportArtifact.Status.PENDING, ReportArtifact.Status.CAPTURING]).update(
            status=ReportArtifact.Status.FAILED,
            error=authentication_error,
            finished_at=timezone.now(),
        )
    except Exception as exc:
        logger.exception("Report run %s crashed", run.id)
        run.artifacts.filter(status__in=[ReportArtifact.Status.PENDING, ReportArtifact.Status.CAPTURING]).update(
            status=ReportArtifact.Status.FAILED,
            error=f"Worker error: {type(exc).__name__}",
            finished_at=timezone.now(),
        )

    run.refresh_from_db(fields=["cancel_requested"])
    if run.cancel_requested:
        run.artifacts.filter(status=ReportArtifact.Status.PENDING).update(
            status=ReportArtifact.Status.CANCELLED, finished_at=timezone.now()
        )
        run.status = ReportRun.Status.CANCELLED
        run.summary = "Cancelled by a user."
    else:
        _refresh_counts(run)
        run.refresh_from_db(fields=["completed_artifacts", "failed_artifacts", "total_artifacts"])
        if run.failed_artifacts == 0:
            run.status = ReportRun.Status.SUCCEEDED
            run.summary = f"Captured {run.completed_artifacts} screenshots."
        elif run.completed_artifacts:
            run.status = ReportRun.Status.PARTIAL
            run.summary = f"Captured {run.completed_artifacts}; failed {run.failed_artifacts}."
        else:
            run.status = ReportRun.Status.FAILED
            run.summary = authentication_error or f"All {run.failed_artifacts} screenshots failed."
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "summary", "finished_at"])
    if run.status in {ReportRun.Status.PARTIAL, ReportRun.Status.FAILED}:
        _notify_failure(run)


def _due_time(schedule: Schedule, now: datetime) -> datetime | None:
    tz = ZoneInfo(schedule.timezone)
    local_now = now.astimezone(tz)
    if schedule.recurrence == Schedule.Recurrence.MONTHLY:
        if local_now.day != schedule.monthly_day:
            return None
    elif local_now.weekday() != schedule.weekly_day:
        return None
    local_due = datetime.combine(local_now.date(), schedule.run_time, tzinfo=tz)
    if local_now < local_due:
        return None
    return local_due.astimezone(datetime_timezone.utc)


@shared_task(name="reporter.tasks.dispatch_due_schedules")
def dispatch_due_schedules() -> int:
    now = timezone.now()
    dispatched = 0
    for schedule in Schedule.objects.filter(enabled=True).prefetch_related("dashboards"):
        due = _due_time(schedule, now)
        if not due:
            continue
        try:
            with transaction.atomic():
                locked = Schedule.objects.select_for_update().get(pk=schedule.pk)
                if ReportRun.objects.filter(schedule=locked, scheduled_for=due).exists():
                    continue
                local_now = now.astimezone(ZoneInfo(locked.timezone))
                run = create_report_run(
                    periods=scheduled_periods(locked, local_now),
                    dashboards=dashboards_for_schedule(locked),
                    source=ReportRun.Source.SCHEDULED,
                    preset=locked.preset,
                    schedule=locked,
                    scheduled_for=due,
                )
            execute_report_run.delay(str(run.id))
            dispatched += 1
        except (IntegrityError, ValueError):
            logger.exception("Could not dispatch schedule %s", schedule.pk)
    return dispatched


@shared_task(name="reporter.tasks.record_worker_heartbeat")
def record_worker_heartbeat() -> None:
    cache.set("reporter:worker-heartbeat", timezone.now().isoformat(), timeout=180)
