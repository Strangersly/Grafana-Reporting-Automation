# Ubuntu 24.04 deployment

The commands below assume the repository is checked out at `/srv/grafana-reporter` and DNS already points `reports.example.com` to the VM.

## System packages

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv postgresql redis-server nginx certbot python3-certbot-nginx
sudo useradd --system --home /srv/grafana-reporter --shell /usr/sbin/nologin grafana-reporter
sudo mkdir -p /srv/grafana-reporter/reports /srv/grafana-reporter/staticfiles /srv/grafana-reporter/var
sudo chown -R grafana-reporter:grafana-reporter /srv/grafana-reporter
```

## Application

```bash
cd /srv/grafana-reporter
sudo -u grafana-reporter python3.12 -m venv .venv
sudo -u grafana-reporter .venv/bin/pip install -r requirements.txt
sudo PLAYWRIGHT_BROWSERS_PATH=/srv/grafana-reporter/.playwright .venv/bin/python -m playwright install --with-deps chromium
sudo chown -R grafana-reporter:grafana-reporter /srv/grafana-reporter/.playwright
```

Generate secrets before editing `/etc/grafana-reporter.env`:

```bash
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(64))"
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
sudo install -m 600 -o root -g root deploy/grafana-reporter.env.example /etc/grafana-reporter.env
```

Create the PostgreSQL database and user, set the same password in `DATABASE_URL`, then initialize the application:

```bash
sudo -u grafana-reporter /srv/grafana-reporter/.venv/bin/python manage.py migrate
sudo -u grafana-reporter /srv/grafana-reporter/.venv/bin/python manage.py collectstatic --noinput
sudo -u grafana-reporter /srv/grafana-reporter/.venv/bin/python manage.py seed_dashboards --config config.shared.json
sudo -u grafana-reporter /srv/grafana-reporter/.venv/bin/python manage.py createsuperuser
```

## Services and HTTPS

```bash
sudo install -m 644 deploy/grafana-reporter-web.service /etc/systemd/system/
sudo install -m 644 deploy/grafana-reporter-worker.service /etc/systemd/system/
sudo install -m 644 deploy/grafana-reporter-beat.service /etc/systemd/system/
sudo install -m 644 deploy/nginx.conf /etc/nginx/sites-available/grafana-reporter
sudo ln -s /etc/nginx/sites-available/grafana-reporter /etc/nginx/sites-enabled/grafana-reporter
sudo systemctl daemon-reload
sudo systemctl enable --now grafana-reporter-web grafana-reporter-worker grafana-reporter-beat
sudo certbot --nginx -d reports.example.com
sudo nginx -t
sudo systemctl reload nginx
```

Change every `reports.example.com` placeholder before enabling Nginx. PostgreSQL and Redis should remain bound to localhost.

## Verification

```bash
curl --fail https://reports.example.com/health/
sudo systemctl status grafana-reporter-web grafana-reporter-worker grafana-reporter-beat
sudo journalctl -u grafana-reporter-worker -n 100 --no-pager
```

After signing in, save the shared Grafana service account under **Grafana connection**, use **Test connections**, and run one dashboard before enabling the monthly schedule.

Back up PostgreSQL daily and store `/etc/grafana-reporter.env` in a separate restricted secret backup. A database backup cannot recover encrypted Grafana credentials without `APP_ENCRYPTION_KEY`.
