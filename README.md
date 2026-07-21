# Grafana Reporting Automation

Grafana Reporting Automation is a Django web application and compatible local CLI for generating weekly and monthly Grafana dashboard screenshots. Reports keep the existing folder contract:

```text
reports/2026/7. Juli/Alibaba/Centralized/VM-Linux/vm-talend-adm-center/
  week1.png
  week2.png
  week3.png
  week4.png
  monthly.png
```

The shared `config.shared.json` contains 78 dashboards. Real credentials, local configuration, database files, browser sessions, and generated reports are ignored by Git.

## Web app features

- Invitation-only email/password accounts with admin, operator, and viewer roles.
- Encrypted shared Grafana service credentials.
- Dashboard editing and Excel/JSON import with column mapping.
- Manual monthly, weekly, previous-seven-day, and custom-range report runs.
- Autonomous weekly and monthly schedules.
- Background screenshot jobs with retries, cancellation, progress, and failure history.
- Authenticated PNG previews and ZIP downloads.
- Health checks for the database, cache, worker, report storage, Grafana, and SMTP.

## Local web setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .\.env.example .\.env
python manage.py migrate
python manage.py seed_dashboards --config .\config.shared.json
python manage.py createsuperuser
python manage.py runserver
```

Open `http://127.0.0.1:8000`, sign in as the superuser, and save the Grafana service account under **Grafana connection**.

The local development settings use SQLite, Redis, and console email. Production uses PostgreSQL, Redis, and SMTP through environment variables.

### One-command local start

This checkout includes PowerShell scripts that run the web app, Redis, the screenshot worker, and the scheduler in the background. Redis must be installed in the WSL `Ubuntu` distribution:

```powershell
wsl -d Ubuntu -u root -- apt-get update
wsl -d Ubuntu -u root -- apt-get install -y redis-server
.\scripts\start-local.ps1
```

Open `http://127.0.0.1:8000`. Runtime logs are written to `.runtime/`. Stop every local service cleanly with:

```powershell
.\scripts\stop-local.ps1
```

After a Windows restart, run `start-local.ps1` again. The script is idempotent, so running it while the services are already active does not start duplicates.

To start the application automatically whenever the current Windows user signs in, register the included Scheduled Task once:

```powershell
.\scripts\register-local-autostart.ps1
```

Remove automatic startup with `.\scripts\register-local-autostart.ps1 -Remove`.

Run Redis, the screenshot worker, and scheduler in separate terminals when testing background jobs:

```powershell
celery -A grafana_web worker --loglevel=INFO --pool=solo --concurrency=1
celery -A grafana_web beat --loglevel=INFO
```

On Linux, omit `--pool=solo`. The production worker service is fixed at concurrency `1` so only one browser capture sequence reaches Grafana at a time.

## Initial dashboard import

Seed or update the included dashboards from the command line:

```powershell
python manage.py seed_dashboards --config .\config.shared.json
```

Operators can also upload `.xlsx` or `.json` files from **Dashboards > Import**. The preview maps source columns to `name`, `url`, `site`, `group`, and `category`. Full dashboard URLs are accepted only when they use the configured Grafana hostname.

## Local CLI

The original CLI remains available. Copy the shared config and local credential template first:

```powershell
Copy-Item .\config.shared.json .\config.json
Copy-Item .\.env.example .\.env
```

Set `GRAFANA_USERNAME` and `GRAFANA_PASSWORD` in `.env`, then run:

```powershell
python .\grafana_reporter.py --config .\config.json --report all --year 2026 --month 7
python .\grafana_reporter.py --config .\config.json --report weekly --from-date 2026-07-01 --to-date 2026-07-05 --filename week1.png
python .\grafana_reporter.py --config .\config.json --mode schedule
```

Use `--dry-run` to inspect target paths without opening Chromium and `--limit 1` to test one dashboard.

## Tests

```powershell
python manage.py check
python manage.py test
```

Tests cover date boundaries, path and URL generation, encryption, host restrictions, invitations, roles, imports, scheduling deduplication, run creation, and protected artifacts.

## Production deployment

Ubuntu 24.04 service files, Nginx configuration, environment template, and installation commands are in [deploy/README.md](deploy/README.md). The production layout uses:

- `/srv/grafana-reporter` for the application.
- `/srv/grafana-reporter/reports` for generated PNG files.
- PostgreSQL and Redis bound to localhost.
- Gunicorn, Celery worker, and Celery Beat as separate `systemd` services.
- Nginx HTTPS and internal authenticated report delivery.

No cloud-drive integration is included. Users download report folders as ZIP files and upload them manually.

## Security notes

- Generate a unique `DJANGO_SECRET_KEY` and Fernet `APP_ENCRYPTION_KEY` before production deployment.
- Never commit `.env`, `config.json`, `db.sqlite3`, `.grafana-auth-state.json`, or report files.
- Use a dedicated read-only Grafana service account without MFA.
- Keep `APP_ENCRYPTION_KEY` in a restricted backup separate from PostgreSQL backups.
- The public login endpoint is rate-limited, but v1 does not include MFA. Prefer an IP allowlist or company VPN when possible.
