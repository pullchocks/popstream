"""Face checks for the condensed Cursor and Grok usage keys. No credentials."""

from __future__ import annotations

import unittest
from datetime import datetime

from popstream.plugins.claude_usage import reset_face
from popstream.plugins.usage import UsagePlugin, UsageSnapshot, meter_face


NOW = 1_763_000_000


def _local(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    """Unix time for a clock reading in the machine's local timezone."""
    return datetime(year, month, day, hour, minute).timestamp()


class MeterFaceTest(unittest.TestCase):
    def test_remaining_over_reset(self) -> None:
        snap = UsageSnapshot(remaining_pct=58, used_pct=42, plan="Pro", reset_at=NOW + 3 * 86400)
        face = meter_face(snap, "remaining", "Cur", NOW)
        self.assertEqual(face.label, "Cur")
        self.assertEqual(face.amount, "58%")
        self.assertEqual(face.reset, reset_face(snap.reset_at, NOW))
        self.assertLessEqual(len(face.reset), 12)
        self.assertNotIn("\n", face.reset)

    def test_used_and_grok_label(self) -> None:
        snap = UsageSnapshot(remaining_pct=58, used_pct=42, plan="Grok Bot", reset_at=NOW + 40 * 60)
        face = meter_face(snap, "used", "Grok", NOW)
        self.assertEqual((face.label, face.amount), ("Grok", "42%"))
        self.assertEqual(face.reset, "40m")

    def test_old_reset_display_falls_back_to_remaining(self) -> None:
        snap = UsageSnapshot(remaining_pct=10, used_pct=90, reset_at=NOW + 86400)
        face = meter_face(snap, "countdown", "Cur", NOW)
        self.assertEqual(face.amount, "10%")

    def test_unlimited_and_unknown(self) -> None:
        unlimited = UsageSnapshot(remaining_pct=100, used_pct=0, unlimited=True, reset_at=NOW + 10 * 86400)
        self.assertEqual(meter_face(unlimited, "remaining", "Cur", NOW).amount, "∞")
        self.assertEqual(meter_face(unlimited, "used", "Cur", NOW).amount, "0%")
        loading = meter_face(UsageSnapshot(error="Loading"), "remaining", "Cur", NOW)
        self.assertEqual((loading.amount, loading.reset), ("…", "…"))
        missing = meter_face(UsageSnapshot(error="Sign in to Cursor"), "remaining", "Grok", NOW)
        self.assertEqual((missing.label, missing.amount, missing.reset), ("Grok", "—", "—"))
        numbers_gone = UsageSnapshot(error="No usage numbers", reset_at=NOW + 2 * 86400)
        face = meter_face(numbers_gone, "remaining", "Cur", NOW)
        self.assertEqual(face.amount, "—")
        self.assertEqual(face.reset, reset_face(numbers_gone.reset_at, NOW))
        self.assertNotEqual(face.reset, "—")


class ResetCountdownTest(unittest.TestCase):
    """Calendar days until a later day. Hours only on that day. Minutes in the last hour."""

    def test_forty_minutes_left_is_minutes(self) -> None:
        now = _local(2026, 11, 12, 14, 0)
        snap = UsageSnapshot(remaining_pct=58, used_pct=42, reset_at=now + 40 * 60)
        self.assertEqual(meter_face(snap, "remaining", "Cur", now).reset, "40m")
        self.assertEqual(meter_face(snap, "remaining", "Grok", now).reset, "40m")

    def test_five_hours_on_the_same_local_day_is_hours(self) -> None:
        now = _local(2026, 11, 12, 10, 0)
        reset = _local(2026, 11, 12, 15, 0)
        face = meter_face(UsageSnapshot(remaining_pct=58, used_pct=42, reset_at=reset), "remaining", "Cur", now)
        self.assertEqual(face.reset, "5h")

    def test_tomorrow_is_one_calendar_day_even_ten_hours_away(self) -> None:
        now = _local(2026, 11, 12, 22, 0)
        reset = _local(2026, 11, 13, 8, 0)
        face = meter_face(UsageSnapshot(remaining_pct=10, used_pct=90, reset_at=reset), "remaining", "Grok", now)
        self.assertEqual(face.reset, "1d")

    def test_six_calendar_days_out_is_six_days(self) -> None:
        now = _local(2026, 11, 12, 9, 30)
        reset = _local(2026, 11, 18, 6, 0)
        face = meter_face(UsageSnapshot(remaining_pct=10, used_pct=90, reset_at=reset), "remaining", "Cur", now)
        self.assertEqual(face.reset, "6d")

    def test_passed_or_missing_reset_is_a_dash(self) -> None:
        now = _local(2026, 11, 12, 12, 0)
        passed = meter_face(UsageSnapshot(remaining_pct=10, used_pct=90, reset_at=now - 60), "remaining", "Cur", now)
        missing = meter_face(UsageSnapshot(remaining_pct=10, used_pct=90), "remaining", "Grok", now)
        self.assertEqual(passed.reset, "—")
        self.assertEqual(missing.reset, "—")
        self.assertEqual(reset_face(None, now), "")
        self.assertEqual(reset_face(now - 1, now), "")


class ClaudeCatalogTest(unittest.TestCase):
    def test_old_id_is_five_hour_and_week_is_separate(self) -> None:
        plugin = UsagePlugin()
        names = {info.id: info.name for info in plugin.actions()}
        self.assertEqual(names["claude"], "Claude 5 Hour Usage")
        self.assertEqual(names["claude-week"], "Claude Weekly Usage")
        five = plugin.create_action("claude")
        week = plugin.create_action("claude-week")
        self.assertEqual((five.window_id, five.label), ("five_hour", "Claude 5h"))
        self.assertEqual((week.window_id, week.label), ("seven_day", "Claude Wk"))
        self.assertIsNone(plugin.create_action("missing"))
        cursor = plugin.create_action("cursor")
        grok = plugin.create_action("grok-bot")
        cursor_reset = plugin.create_action("cursor-reset")
        grok_reset = plugin.create_action("grok-bot-reset")
        self.assertEqual(cursor_reset.label, cursor.label)
        self.assertEqual(grok_reset.label, grok.label)
        self.assertIs(type(cursor_reset)._paint, type(cursor)._paint)
        self.assertIs(type(grok_reset)._paint, type(grok)._paint)


if __name__ == "__main__":
    unittest.main()
