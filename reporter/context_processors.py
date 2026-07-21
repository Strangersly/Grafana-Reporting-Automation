def navigation_counts(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    from .models import ReportRun

    return {
        "nav_active_runs": ReportRun.objects.filter(
            status__in=[ReportRun.Status.QUEUED, ReportRun.Status.RUNNING]
        ).count()
    }
