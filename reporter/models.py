from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .managers import UserManager
from .security import decrypt_secret, encrypt_secret


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        OPERATOR = "operator", "Operator"
        VIEWER = "viewer", "Viewer"

    username = None
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.VIEWER)
    invited_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []
    objects = UserManager()

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        if self.is_superuser:
            self.role = self.Role.ADMIN
        super().save(*args, **kwargs)

    @property
    def is_admin(self) -> bool:
        return self.is_superuser or self.role == self.Role.ADMIN

    @property
    def can_operate(self) -> bool:
        return self.is_admin or self.role == self.Role.OPERATOR

    def __str__(self) -> str:
        return self.get_full_name() or self.email


class Invitation(models.Model):
    email = models.EmailField()
    role = models.CharField(max_length=16, choices=User.Role.choices, default=User.Role.VIEWER)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def issue(cls, *, email: str, role: str, invited_by: User) -> tuple["Invitation", str]:
        raw_token = secrets.token_urlsafe(32)
        invitation = cls.objects.create(
            email=email.strip().lower(),
            role=role,
            token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
            expires_at=timezone.now() + timedelta(hours=settings.INVITATION_TTL_HOURS),
            invited_by=invited_by,
        )
        return invitation, raw_token

    @classmethod
    def find_valid(cls, raw_token: str) -> "Invitation":
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        try:
            invitation = cls.objects.get(token_hash=digest, accepted_at__isnull=True)
        except cls.DoesNotExist as exc:
            raise ValidationError("This invitation is invalid or has already been used.") from exc
        if invitation.expires_at <= timezone.now():
            raise ValidationError("This invitation has expired.")
        return invitation

    def __str__(self) -> str:
        return self.email


class GrafanaConnection(models.Model):
    base_url = models.URLField()
    login_path = models.CharField(max_length=255, default="/login")
    username_ciphertext = models.TextField(blank=True)
    password_ciphertext = models.TextField(blank=True)
    org_id = models.PositiveIntegerField(null=True, blank=True, default=1)
    theme = models.CharField(max_length=16, default="dark")
    kiosk = models.BooleanField(default=True)
    timezone = models.CharField(max_length=64, default="Asia/Jakarta")
    viewport_width = models.PositiveIntegerField(default=3440)
    viewport_height = models.PositiveIntegerField(default=1220)
    full_page = models.BooleanField(default=True)
    wait_seconds = models.PositiveIntegerField(default=8)
    navigation_timeout_seconds = models.PositiveIntegerField(default=90)
    panel_timeout_seconds = models.PositiveIntegerField(default=60)
    ignore_https_errors = models.BooleanField(default=False)
    wait_for_selector = models.CharField(
        max_length=500,
        blank=True,
        default=".panel-container, [data-testid='panel-container'], [data-testid='data-testid panel content'], [data-testid^='data-testid Panel header ']",
    )
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1
        self.base_url = self.base_url.rstrip("/")
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls) -> "GrafanaConnection | None":
        return cls.objects.filter(pk=1).first()

    def set_username(self, value: str) -> None:
        self.username_ciphertext = encrypt_secret(value)

    def set_password(self, value: str) -> None:
        self.password_ciphertext = encrypt_secret(value)

    def get_username(self) -> str:
        return decrypt_secret(self.username_ciphertext)

    def get_password(self) -> str:
        return decrypt_secret(self.password_ciphertext)

    @property
    def credentials_configured(self) -> bool:
        return bool(self.username_ciphertext and self.password_ciphertext)

    def __str__(self) -> str:
        return self.base_url


class Dashboard(models.Model):
    name = models.CharField(max_length=255, unique=True)
    site = models.CharField(max_length=255, default="default")
    group = models.CharField(max_length=255, default="default")
    category = models.CharField(max_length=255, default="default")
    url = models.TextField()
    query_params = models.JSONField(default=dict, blank=True)
    viewport_width = models.PositiveIntegerField(null=True, blank=True)
    viewport_height = models.PositiveIntegerField(null=True, blank=True)
    wait_for_selector = models.CharField(max_length=500, blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["site", "group", "category", "name"]

    def clean(self):
        raw = self.url.strip()
        parsed = urlparse(raw)
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            raise ValidationError({"url": "Dashboard URLs must use HTTP or HTTPS."})
        if parsed.scheme:
            connection = GrafanaConnection.get_solo()
            if connection:
                allowed = urlparse(connection.base_url)
                if parsed.hostname != allowed.hostname or parsed.port != allowed.port:
                    raise ValidationError({"url": "The dashboard URL must use the configured Grafana host."})
        elif not raw.startswith("/"):
            raise ValidationError({"url": "Use a relative Grafana path beginning with / or a full Grafana URL."})

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        self.url = self.url.strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class Schedule(models.Model):
    WEEKDAY_CHOICES = [
        (0, "Monday"),
        (1, "Tuesday"),
        (2, "Wednesday"),
        (3, "Thursday"),
        (4, "Friday"),
        (5, "Saturday"),
        (6, "Sunday"),
    ]

    class Recurrence(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        WEEKLY = "weekly", "Weekly"

    class Preset(models.TextChoices):
        PREVIOUS_MONTH_ALL = "previous_month_all", "Previous month: weekly blocks and monthly"
        PREVIOUS_MONTH = "previous_month", "Previous month: monthly only"
        PREVIOUS_7_DAYS = "previous_7_days", "Previous 7 days"

    name = models.CharField(max_length=255, unique=True)
    enabled = models.BooleanField(default=True)
    recurrence = models.CharField(max_length=16, choices=Recurrence.choices)
    preset = models.CharField(max_length=32, choices=Preset.choices)
    monthly_day = models.PositiveSmallIntegerField(default=1)
    weekly_day = models.PositiveSmallIntegerField(choices=WEEKDAY_CHOICES, default=0)
    run_time = models.TimeField()
    timezone = models.CharField(max_length=64, default="Asia/Jakarta")
    all_dashboards = models.BooleanField(default=True)
    dashboards = models.ManyToManyField(Dashboard, blank=True, related_name="schedules")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def clean(self):
        if not 1 <= self.monthly_day <= 28:
            raise ValidationError({"monthly_day": "Monthly day must be between 1 and 28."})
        if not 0 <= self.weekly_day <= 6:
            raise ValidationError({"weekly_day": "Weekly day must be between Monday and Sunday."})
        if self.recurrence == self.Recurrence.MONTHLY and self.preset == self.Preset.PREVIOUS_7_DAYS:
            raise ValidationError({"preset": "Monthly schedules cannot use the previous-seven-days preset."})
        if self.recurrence == self.Recurrence.WEEKLY and self.preset != self.Preset.PREVIOUS_7_DAYS:
            raise ValidationError({"preset": "Weekly schedules must use the previous-seven-days preset."})

    def __str__(self) -> str:
        return self.name


class ReportRun(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manual", "Manual"
        SCHEDULED = "scheduled", "Scheduled"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(max_length=16, choices=Source.choices)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    preset = models.CharField(max_length=32)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    folder_year = models.PositiveIntegerField()
    folder_month = models.PositiveSmallIntegerField()
    custom_filename = models.CharField(max_length=255, blank=True)
    total_artifacts = models.PositiveIntegerField(default=0)
    completed_artifacts = models.PositiveIntegerField(default=0)
    failed_artifacts = models.PositiveIntegerField(default=0)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="report_runs"
    )
    schedule = models.ForeignKey(Schedule, null=True, blank=True, on_delete=models.SET_NULL, related_name="runs")
    scheduled_for = models.DateTimeField(null=True, blank=True)
    cancel_requested = models.BooleanField(default=False)
    summary = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["schedule", "scheduled_for"],
                condition=Q(schedule__isnull=False, scheduled_for__isnull=False),
                name="unique_schedule_dispatch",
            )
        ]

    @property
    def progress_percent(self) -> int:
        if not self.total_artifacts:
            return 0
        return min(100, int(((self.completed_artifacts + self.failed_artifacts) / self.total_artifacts) * 100))

    def __str__(self) -> str:
        return f"{self.get_source_display()} {self.created_at:%Y-%m-%d %H:%M}"


class ReportArtifact(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CAPTURING = "capturing", "Capturing"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    run = models.ForeignKey(ReportRun, on_delete=models.CASCADE, related_name="artifacts")
    dashboard = models.ForeignKey(Dashboard, null=True, blank=True, on_delete=models.SET_NULL)
    dashboard_name = models.CharField(max_length=255)
    site = models.CharField(max_length=255)
    group = models.CharField(max_length=255)
    category = models.CharField(max_length=255)
    dashboard_url = models.TextField()
    dashboard_query_params = models.JSONField(default=dict, blank=True)
    viewport_width = models.PositiveIntegerField(null=True, blank=True)
    viewport_height = models.PositiveIntegerField(null=True, blank=True)
    wait_for_selector = models.CharField(max_length=500, blank=True)
    report_type = models.CharField(max_length=16)
    filename = models.CharField(max_length=255)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    folder_year = models.PositiveIntegerField()
    folder_month = models.PositiveSmallIntegerField()
    relative_path = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    superseded = models.BooleanField(default=False)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["site", "group", "category", "dashboard_name", "period_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "dashboard_name", "filename", "period_start", "period_end"],
                name="unique_run_artifact",
            )
        ]

    def __str__(self) -> str:
        return f"{self.dashboard_name}/{self.filename}"
