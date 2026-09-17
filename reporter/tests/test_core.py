from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from reporting.core import build_dashboard_url, build_periods, custom_date_period, output_relative_path_for, safe_path_part


class ReportingCoreTests(SimpleTestCase):
    def setUp(self):
        self.tz = ZoneInfo("Asia/Jakarta")
        self.config = {
            "grafana": {
                "base_url": "https://grafana.example.com",
                "org_id": 1,
                "theme": "dark",
                "kiosk": True,
            },
            "output": {
                "root": "reports",
                "folder_template": "{year}/{month_number}. {month_name}/{site}/{group}/{category}/{name}",
            },
        }
        self.dashboard = {
            "name": "server-01",
            "site": "Alibaba",
            "group": "Centralized",
            "category": "VM-Linux",
            "url": "/d/uid/linux?var-Hostname=server-01",
        }

    def test_monthly_and_weekly_periods_cover_leap_february(self):
        periods = build_periods("all", 2028, 2, self.tz)
        self.assertEqual([period.filename for period in periods], ["week1.png", "week2.png", "week3.png", "week4.png", "monthly.png"])
        self.assertEqual(periods[3].end.day, 29)
        self.assertEqual(periods[4].end.day, 29)

    def test_custom_period_validates_order_and_filename(self):
        period = custom_date_period("weekly", date(2026, 7, 1), date(2026, 7, 5), self.tz, "special")
        self.assertEqual(period.filename, "special.png")
        with self.assertRaises(ValueError):
            custom_date_period("weekly", date(2026, 7, 5), date(2026, 7, 1), self.tz)

    def test_url_and_output_path_preserve_expected_contract(self):
        period = build_periods("monthly", 2026, 7, self.tz)[0]
        url = build_dashboard_url(self.config, self.dashboard, period)
        self.assertIn("https://grafana.example.com/d/uid/linux", url)
        self.assertIn("from=", url)
        self.assertIn("to=", url)
        self.assertIn("kiosk=tv", url)
        path = output_relative_path_for(self.config, self.dashboard, period)
        self.assertEqual(path.as_posix(), "2026/7. Juli/Alibaba/Centralized/VM-Linux/server-01/monthly.png")

    def test_monthly_url_can_override_a_dashboard_interval(self):
        dashboard = {
            **self.dashboard,
            "url": "/d/uid/linux?var-interval=1m",
            "query_params": {"report_overrides": {"monthly": {"var-interval": "1h"}}},
        }
        monthly, weekly = build_periods("all", 2026, 8, self.tz)[-1], build_periods("all", 2026, 8, self.tz)[0]

        self.assertIn("var-interval=1h", build_dashboard_url(self.config, dashboard, monthly))
        self.assertIn("var-interval=1m", build_dashboard_url(self.config, dashboard, weekly))

    def test_safe_path_part_removes_reserved_characters(self):
        self.assertEqual(safe_path_part('bad:name? '), "bad_name_")
