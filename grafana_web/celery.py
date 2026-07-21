import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "grafana_web.settings")

app = Celery("grafana_web")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
app.conf.beat_schedule = {
    "dispatch-due-report-schedules": {
        "task": "reporter.tasks.dispatch_due_schedules",
        "schedule": crontab(minute="*"),
    },
    "record-worker-heartbeat": {
        "task": "reporter.tasks.record_worker_heartbeat",
        "schedule": crontab(minute="*"),
    },
}
