import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from core.session import evaluate_session, next_quarter_close


class SessionLogicTest(unittest.TestCase):
    def test_london_kill_zone(self):
        result = evaluate_session(datetime(2026, 7, 10, 10, 15, tzinfo=ZoneInfo("Europe/Chisinau")))

        self.assertTrue(result.in_kill_zone)
        self.assertEqual(result.session_name, "London")
        self.assertEqual(result.local_time, "10:15")
        self.assertEqual(result.minutes_to_session_end, 105)

    def test_new_york_kill_zone(self):
        result = evaluate_session(datetime(2026, 7, 10, 16, 15, tzinfo=ZoneInfo("Europe/Chisinau")))

        self.assertTrue(result.in_kill_zone)
        self.assertEqual(result.session_name, "New York")
        self.assertEqual(result.minutes_to_session_end, 105)

    def test_kill_zone_end_is_exclusive(self):
        result = evaluate_session(datetime(2026, 7, 10, 12, 0, tzinfo=ZoneInfo("Europe/Chisinau")))

        self.assertFalse(result.in_kill_zone)
        self.assertEqual(result.session_name, "Outside KZ")

    def test_outside_kill_zone_reports_minutes_to_next_session(self):
        result = evaluate_session(datetime(2026, 7, 10, 9, 30, tzinfo=ZoneInfo("Europe/Chisinau")))

        self.assertFalse(result.in_kill_zone)
        self.assertEqual(result.minutes_to_next_session, 30)

    def test_next_quarter_close_is_timezone_aware(self):
        result = next_quarter_close(datetime(2026, 7, 10, 9, 44, 30, tzinfo=ZoneInfo("Europe/Chisinau")))

        self.assertEqual(result.strftime("%H:%M:%S"), "09:45:05")
        self.assertEqual(result.tzinfo.key, "Europe/Chisinau")


if __name__ == "__main__":
    unittest.main()
