from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Dashboard, GrafanaConnection, Invitation, ReportArtifact, ReportRun, Schedule, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ["email"]
    list_display = ["email", "first_name", "last_name", "role", "is_active", "is_staff"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("first_name", "last_name", "role")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "date_joined", "invited_at", "accepted_at")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2", "role")}),)
    search_fields = ["email", "first_name", "last_name"]


@admin.register(Dashboard)
class DashboardAdmin(admin.ModelAdmin):
    list_display = ["name", "site", "group", "category", "enabled"]
    list_filter = ["enabled", "site", "group", "category"]
    search_fields = ["name", "site", "group", "category"]


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ["name", "recurrence", "preset", "run_time", "timezone", "enabled"]
    list_filter = ["enabled", "recurrence", "preset"]


@admin.register(ReportRun)
class ReportRunAdmin(admin.ModelAdmin):
    list_display = ["id", "source", "status", "total_artifacts", "completed_artifacts", "failed_artifacts", "created_at"]
    list_filter = ["source", "status"]
    readonly_fields = ["id", "created_at", "started_at", "finished_at"]


@admin.register(ReportArtifact)
class ReportArtifactAdmin(admin.ModelAdmin):
    list_display = ["dashboard_name", "filename", "status", "size_bytes", "superseded", "created_at"]
    list_filter = ["status", "superseded", "site", "category"]
    search_fields = ["dashboard_name", "relative_path"]


admin.site.register(GrafanaConnection)
admin.site.register(Invitation)
