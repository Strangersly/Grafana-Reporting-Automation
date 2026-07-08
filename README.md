# Grafana Screenshot Reports

This local script signs in to Grafana, opens configured dashboards, captures PNG screenshots, and writes them into a report folder structure like:

```text
D:/GrafanaReports/2026/1. Januari/Alibaba/Centralized/VM-Linux/vm-talend-adm-center/
  monthly.png
  week1.png
  week2.png
  week3.png
  week4.png
```

Weekly reports use four monthly blocks by default:

```text
week1.png = day 1-7
week2.png = day 8-14
week3.png = day 15-21
week4.png = day 22-last day of month
```

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
Copy-Item .\config.shared.json .\config.json
Copy-Item .\.env.example .\.env
```

Edit `config.json`:

- Set `grafana.base_url`.
- Set `output.root`.
- The shared dashboard URLs are already included in `config.shared.json`.

Edit `.env` with your own Grafana login:

```text
GRAFANA_USERNAME=your-user
GRAFANA_PASSWORD=your-password
```

The script automatically reads `.env` from the same folder as `config.json`. Do not share the real `.env` file.

`config.shared.json` contains the shared 78-dashboard list. Copy it to `config.json` for local use, then adjust only local settings such as `output.root` if needed. Keep `config.json` uncommitted for personal changes.

## Run Once

Dry-run first to confirm URLs and output paths:

```powershell
python .\grafana_reporter.py --config .\config.json --report all --year 2026 --month 1 --dry-run
```

Capture the screenshots:

```powershell
python .\grafana_reporter.py --config .\config.json --report all --year 2026 --month 1
```

Without `--year` and `--month`, the script uses `periods.target_month` from the config. The example config uses the previous full month.

## Custom Date Range

Use `--from-date` and `--to-date` when a weekly report needs a manual date range. For example, July 1-5, 2026 saved as `week1.png`:

```powershell
python .\grafana_reporter.py --config .\config.json --report weekly --from-date 2026-07-01 --to-date 2026-07-05 --filename week1.png
```

If `--filename` is omitted for a weekly custom range, the script chooses `week1.png` through `week4.png` from the start date.

## Run on Schedule

Start the local scheduler:

```powershell
python .\grafana_reporter.py --config .\config.json --mode schedule
```

The example config runs on day `1` of each month at `07:30` Asia/Jakarta time and captures the previous month, including `week1.png` through `week4.png` and `monthly.png`.

For unattended operation on Windows, create a Task Scheduler task that runs at login or system startup with:

```text
Program: D:\Project ICS\.venv\Scripts\python.exe
Arguments: D:\Project ICS\grafana_reporter.py --config D:\Project ICS\config.json --mode schedule
Start in: D:\Project ICS
```

## Dashboard URL

Use the normal Grafana dashboard URL path. The script automatically adds `from`, `to`, `theme`, `orgId`, and `kiosk` query parameters based on the config.

Example:

```json
{
  "name": "vm-talend-adm-center",
  "site": "Alibaba",
  "group": "Centralized",
  "category": "VM-Linux",
  "url": "/d/YOUR_DASHBOARD_UID/YOUR_DASHBOARD_SLUG?var-Hostname=vm-talend-adm-center"
}
```

If login uses SSO or MFA, run once with `--show-browser`, complete the login manually, and keep `grafana.storage_state` enabled so Playwright can reuse the saved browser session.

