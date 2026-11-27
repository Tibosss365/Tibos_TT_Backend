import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.report_sections import (
    _selected_ids,
    render_report_sections,
    report_subject,
    report_time_bounds,
)
from app.schemas.admin import AlertSettingsOut, AlertSettingsUpdate


class ReportSectionTests(unittest.TestCase):
    def test_new_sections_render_when_enabled_and_escape_dynamic_text(self):
        template = {
            "includeAgentPerformance": True,
            "includeDailyAgentReport": True,
            "includeCategorySpecialists": True,
        }
        data = {
            "agent_performance": [
                {"name": "<img src=x>", "count": 4},
                {"name": "A&B", "count": 3},
            ],
            "daily_agent_report": [{
                "name": "A&B", "created_today": 1, "under_1h": 2,
                "one_to_two_hours": 3, "two_to_24_hours": 4, "resolved_today": 5,
                "resolved_under_1h": 1, "resolved_1_to_2h": 2,
                "resolved_2_to_24h": 2, "created_this_month": 6,
            }],
            "category_specialists": [{"category": "<script>", "agent": "A&B", "count": 7}],
        }

        html = render_report_sections(data, template)

        self.assertIn("Agent Performance", html)
        self.assertIn("Daily Report", html)
        self.assertIn("Category Specialists", html)
        self.assertIn("&lt;img src=x&gt;", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("A&amp;B", html)
        self.assertNotIn("<script>", html)
        self.assertIn("Today created", html)
        self.assertIn("Resolved 2–24h", html)
        self.assertIn("Open 2–24h", html)

    def test_disabled_or_missing_new_sections_are_omitted(self):
        data = {
            "agent_performance": [{"name": "A", "count": 1}],
            "daily_agent_report": [],
            "category_specialists": [],
        }
        self.assertEqual(render_report_sections(data, {}), "")
        self.assertEqual(render_report_sections(data, {
            "includeAgentPerformance": False,
            "includeDailyAgentReport": False,
            "includeCategorySpecialists": False,
        }), "")

    def test_local_day_and_month_boundaries_convert_to_utc(self):
        now = datetime(2026, 3, 1, 0, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
        day_start, next_day, month_start = report_time_bounds(now)
        self.assertEqual(day_start, datetime(2026, 2, 28, 18, 30, tzinfo=ZoneInfo("UTC")))
        self.assertEqual(next_day, datetime(2026, 3, 1, 18, 30, tzinfo=ZoneInfo("UTC")))
        self.assertEqual(month_start, datetime(2026, 2, 28, 18, 30, tzinfo=ZoneInfo("UTC")))

    def test_subject_uses_saved_template_and_replaces_date_variables(self):
        now = datetime(2026, 10, 21, 8, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertEqual(
            report_subject({"subject": "{system_name} — {date} — {month} {year}"}, now),
            "Tibos Helpdesk — 21 Oct 2026 — October 2026",
        )

    def test_selected_agent_ids_are_used_when_available_otherwise_fallback(self):
        self.assertIsNone(_selected_ids({}))
        self.assertEqual(_selected_ids({"selectedAgentIds": ["one", "two"]}), {"one", "two"})
        self.assertEqual(_selected_ids({"selectedAgents": [{"id": "one"}]}), {"one"})
        self.assertEqual(_selected_ids({"selectedAgentIds": []}), set())

    def test_alert_settings_schema_round_trips_new_template_options(self):
        reports = {"timezone": "Asia/Kolkata", "daily": {"enabled": True, "template": {
            "includeAgentPerformance": True,
            "includeDailyAgentReport": False,
            "includeCategorySpecialists": True,
        }}}
        update = AlertSettingsUpdate(
            conditions={}, reports=reports, recipients={}, alertEmailConfig=None,
        )
        out = AlertSettingsOut(
            conditions=update.conditions, reports=update.reports,
            recipients=update.recipients, alertEmailConfig=update.alert_email_config,
            updated_at=datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC")),
        )
        self.assertEqual(out.reports["daily"]["template"], reports["daily"]["template"])

    def test_is_due_for_545_pm(self):
        from app.services.report_scheduler import _is_due

        ist = ZoneInfo("Asia/Kolkata")
        before = datetime(2026, 11, 27, 17, 44, tzinfo=ist)
        at_545 = datetime(2026, 11, 27, 17, 45, tzinfo=ist)
        rep = {"enabled": True, "time": "17:45"}
        self.assertFalse(_is_due("daily", rep, {}, before, before.date()))
        self.assertTrue(_is_due("daily", rep, {}, at_545, at_545.date()))
        self.assertFalse(_is_due("daily", rep, {"daily": at_545.isoformat()}, at_545, at_545.date()))

    def test_build_daily_monthly_email_keeps_previous_template_with_accurate_counts(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from app.services.report_scheduler import build_daily_monthly_email

        def _run_sync_coro(coro):
            try:
                coro.send(None)
            except StopIteration as stop:
                return stop.value
            raise AssertionError("Expected mocked coroutine to complete synchronously")

        ist = ZoneInfo("Asia/Kolkata")
        now_tz = datetime(2026, 11, 27, 17, 45, tzinfo=ist)
        alert_cfg = SimpleNamespace(reports={
            "timezone": "Asia/Kolkata",
            "daily": {
                "enabled": True,
                "time": "17:45",
                "template": {
                    "includeAgentPerformance": True,
                    "includeDailyAgentReport": True,
                    "includeDailyMonthlyReport": True,
                    "includeCategorySpecialists": True,
                },
            },
        })
        fake_counts = {
            "unassigned": 2, "sla_breach": 1, "on_hold": 0,
            "open_today": 5, "created_today": 3, "resolved_today": 4,
        }
        fake_sections = {
            "agent_performance": [{"name": "Alice", "count": 2}],
            "daily_agent_report": [{
                "name": "Alice", "created_today": 3, "under_1h": 1, "one_to_two_hours": 1,
                "two_to_24_hours": 0, "resolved_today": 2, "resolved_under_1h": 1,
                "resolved_1_to_2h": 1, "resolved_2_to_24h": 0, "created_this_month": 10,
            }],
            "monthly_agent_report": [{
                "name": "Alice", "created_this_month": 10, "under_1h": 1, "one_to_two_hours": 1,
                "two_to_24_hours": 0, "resolved_this_month": 8, "resolved_under_1h": 3,
                "resolved_1_to_2h": 2, "resolved_2_to_24h": 3,
            }],
            "category_specialists": [],
        }

        async def _fake_sections(_db, _now, _tmpl, report_type="daily"):
            return fake_sections

        with (
            patch("app.services.report_scheduler._gather_data", new=AsyncMock(return_value=(fake_counts, []))),
            patch("app.services.report_scheduler.gather_report_sections", side_effect=_fake_sections),
        ):
            subject, html = _run_sync_coro(build_daily_monthly_email(None, alert_cfg, now_tz))

        self.assertEqual(subject, "Daily + Monthly Ticket Health Report — November 27, 2026")
        self.assertIn("5:45 PM DIGEST", html)
        self.assertIn("Daily Report", html)
        self.assertIn("Monthly Report &nbsp;·&nbsp; Month to Date", html)
        self.assertIn("Monthly Agent Metrics", html)
        self.assertIn("Category Specialists", html)


if __name__ == "__main__":
    unittest.main()
