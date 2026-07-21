import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from reporter.models import Dashboard, GrafanaConnection


class Command(BaseCommand):
    help = "Seed or update dashboards from config.shared.json or another CLI configuration file."

    def add_arguments(self, parser):
        parser.add_argument("--config", default="config.shared.json")

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options["config"]).expanduser()
        if not path.exists():
            raise CommandError(f"Configuration file not found: {path}")
        try:
            config = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Could not read {path}: {exc}") from exc
        grafana = config.get("grafana", {})
        base_url = grafana.get("base_url", "").strip().rstrip("/")
        if not base_url:
            raise CommandError("grafana.base_url is required in the source configuration.")
        connection, _ = GrafanaConnection.objects.get_or_create(
            pk=1,
            defaults={
                "base_url": base_url,
                "login_path": grafana.get("login_path", "/login"),
                "org_id": grafana.get("org_id"),
                "theme": grafana.get("theme", "dark"),
                "kiosk": grafana.get("kiosk", True),
                "timezone": grafana.get("timezone", "Asia/Jakarta"),
                "viewport_width": grafana.get("viewport", {}).get("width", 3440),
                "viewport_height": grafana.get("viewport", {}).get("height", 1220),
                "full_page": grafana.get("full_page", True),
                "wait_seconds": grafana.get("wait_seconds", 8),
                "ignore_https_errors": grafana.get("ignore_https_errors", False),
            },
        )
        if connection.base_url != base_url:
            self.stdout.write(self.style.WARNING("Existing Grafana connection was kept unchanged."))
        created = updated = 0
        for row in config.get("dashboards", []):
            if not row.get("name") or not row.get("url"):
                continue
            _, was_created = Dashboard.objects.update_or_create(
                name=str(row["name"]).strip(),
                defaults={
                    "site": str(row.get("site") or row.get("folder", {}).get("site") or "default"),
                    "group": str(row.get("group") or row.get("folder", {}).get("group") or "default"),
                    "category": str(row.get("category") or row.get("folder", {}).get("category") or "default"),
                    "url": str(row["url"]).strip(),
                    "query_params": row.get("query_params", {}),
                    "viewport_width": row.get("viewport", {}).get("width"),
                    "viewport_height": row.get("viewport", {}).get("height"),
                    "wait_for_selector": row.get("wait_for_selector", ""),
                    "enabled": row.get("enabled", True),
                },
            )
            created += int(was_created)
            updated += int(not was_created)
        self.stdout.write(self.style.SUCCESS(f"Dashboards seeded: {created} created, {updated} updated."))
