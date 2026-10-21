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
            "agent_performance": [{"name": "<img src=x>", "count": 4}],
            "daily_agent_report": [{
                "name": "A&B", "created_today": 1, "under_1h": 2,
                "one_to_two_hours": 3, "two_to_24_hours": 4,
                "resolved_today": 5, "created_this_month": 6,
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


if __name__ == "__main__":
    unittest.main()
