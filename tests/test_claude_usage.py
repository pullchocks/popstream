"""Parser checks for the Claude usage payload. No credentials."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from popstream.plugins.claude_usage import (
    ClaudeSnapshot,
    UsageWindow,
    _libcrypto,
    apply_reading,
    cache_is_fresh,
    _history_needs_session,
    decrypt_cookie_value,
    load_desktop_history,
    parse_desktop_history,
    parse_desktop_usage,
    parse_usage,
    prefer_reading,
    reset_face,
    window_face,
)


def _local(year: int, month: int, day: int, hour: int, minute: int = 0) -> float:
    """Unix time for a clock reading in the machine's local timezone."""
    return datetime(year, month, day, hour, minute).timestamp()


def both_windows(snap, mode: str = "remaining", now: float | None = None):
    """Each Claude window is its own key face."""
    return (
        window_face(snap, "five_hour", mode, now),
        window_face(snap, "seven_day", mode, now),
    )

# Shape from Claude Code's /api/oauth/usage schema: utilization is a fraction,
# resets_at is an ISO-8601 timestamp. Extra windows are ignored.
SAMPLE = {
    "five_hour": {
        "utilization": 0.42,
        "resets_at": "2026-11-12T20:30:00Z",
    },
    "seven_day": {
        "utilization": 0.155,
        "resets_at": "2026-11-18T04:00:00+00:00",
    },
    "seven_day_opus": {"utilization": 0.05, "resets_at": "2026-11-18T04:00:00Z"},
    "seven_day_sonnet": {"utilization": None, "resets_at": None},
}


class ClaudeUsageParseTest(unittest.TestCase):
    def test_both_windows(self) -> None:
        snap = parse_usage(SAMPLE)
        self.assertEqual(snap.five_hour.used_pct, 42)
        self.assertEqual(snap.five_hour.remaining_pct, 58)
        self.assertIsNotNone(snap.five_hour.reset_at)
        self.assertEqual(snap.seven_day.used_pct, 16)
        self.assertEqual(snap.seven_day.remaining_pct, 84)
        self.assertGreater(snap.seven_day.reset_at, snap.five_hour.reset_at)

    def test_unix_reset(self) -> None:
        snap = parse_usage({"five_hour": {"utilization": 0.1, "resets_at": 1_763_000_000}})
        self.assertEqual(snap.five_hour.reset_at, 1_763_000_000)

    def test_missing_utilization_is_not_zero(self) -> None:
        snap = parse_usage(
            {
                "five_hour": {"utilization": None, "resets_at": "2026-11-12T20:30:00Z"},
                "seven_day": {},
            }
        )
        now = snap.five_hour.reset_at - 2 * 3600
        five, week = both_windows(snap, "remaining", now)
        self.assertIsNone(snap.five_hour.used_pct)
        self.assertIsNone(snap.seven_day)
        self.assertEqual(five.label, "Claude 5h")
        self.assertEqual(five.amount, "—")
        self.assertNotEqual(five.reset, "0%")
        self.assertNotIn("%", five.amount)
        self.assertEqual(week.label, "Claude Wk")
        self.assertEqual(week.amount, "—")
        self.assertEqual(week.reset, "—")

    def test_each_window_is_its_own_face(self) -> None:
        snap = parse_usage(SAMPLE)
        now = snap.five_hour.reset_at - 2 * 3600
        five, week = both_windows(snap, "remaining", now)
        self.assertEqual((five.label, five.amount), ("Claude 5h", "58%"))
        self.assertEqual((week.label, week.amount), ("Claude Wk", "84%"))
        self.assertEqual(five.reset, reset_face(snap.five_hour.reset_at, now))
        self.assertEqual(week.reset, reset_face(snap.seven_day.reset_at, now))
        self.assertLessEqual(len(five.reset), 12)
        self.assertLessEqual(len(week.reset), 12)
        self.assertNotIn("\n", five.amount)
        self.assertNotIn("\n", week.amount)
        self.assertNotIn("\n", five.reset)
        self.assertNotIn("\n", week.reset)
        self.assertNotIn("2026", five.reset)
        self.assertNotIn("2026", week.reset)
        used_five, used_week = both_windows(snap, "used", now)
        self.assertEqual(used_five.amount, "42%")
        self.assertEqual(used_week.amount, "16%")

    def test_full_and_empty_are_real_readings(self) -> None:
        snap = parse_usage(
            {
                "five_hour": {"utilization": 0, "resets_at": "2026-11-12T20:30:00Z"},
                "seven_day": {"utilization": 1, "resets_at": "2026-11-18T04:00:00Z"},
            }
        )
        now = snap.five_hour.reset_at - 2 * 3600
        five, week = both_windows(snap, "remaining", now)
        self.assertEqual(five.amount, "100%")
        self.assertEqual(week.amount, "0%")
        used_five, used_week = both_windows(snap, "used", now)
        self.assertEqual(used_five.amount, "0%")
        self.assertEqual(used_week.amount, "100%")

    def test_countdown_is_calendar_days_then_hours_then_minutes(self) -> None:
        """Later calendar days stay Nd. The reset's own day is Nh. The last hour is Nm."""
        now = _local(2026, 11, 12, 14, 0)
        self.assertEqual(reset_face(now + 40 * 60, now), "40m")
        same_day = ClaudeSnapshot(
            five_hour=UsageWindow(used_pct=42, remaining_pct=58, reset_at=_local(2026, 11, 12, 19, 0)),
            seven_day=UsageWindow(used_pct=10, remaining_pct=90, reset_at=_local(2026, 11, 18, 6, 0)),
        )
        five, week = both_windows(same_day, "remaining", now)
        self.assertEqual(five.reset, "5h")
        self.assertEqual(week.reset, "6d")
        late = _local(2026, 11, 12, 22, 0)
        tomorrow = ClaudeSnapshot(
            five_hour=UsageWindow(used_pct=42, remaining_pct=58, reset_at=_local(2026, 11, 13, 8, 0)),
            seven_day=UsageWindow(used_pct=10, remaining_pct=90, reset_at=late + 40 * 60),
        )
        five, week = both_windows(tomorrow, "remaining", late)
        self.assertEqual(five.reset, "1d")
        self.assertEqual(week.reset, "40m")
        passed = window_face(
            ClaudeSnapshot(five_hour=UsageWindow(used_pct=1, remaining_pct=99, reset_at=now - 30)),
            "five_hour",
            "remaining",
            now,
        )
        self.assertEqual(passed.reset, "—")
        self.assertEqual(reset_face(None, now), "")

    def test_loading_and_failed_fetch(self) -> None:
        loading = ClaudeSnapshot(error="Loading")
        five, week = both_windows(loading)
        self.assertEqual((five.label, five.amount, five.reset), ("Claude 5h", "…", "…"))
        self.assertEqual((week.label, week.amount, week.reset), ("Claude Wk", "…", "…"))
        good = ClaudeSnapshot(
            five_hour=UsageWindow(used_pct=42, remaining_pct=58, reset_at=1_763_000_000),
            seven_day=UsageWindow(used_pct=10, remaining_pct=90, reset_at=1_763_500_000),
            fetched_at=1_762_000_000,
        )
        failed = ClaudeSnapshot(error="Claude usage unavailable", keep_previous=True)
        kept = apply_reading(good, failed, now=1_762_000_100)
        self.assertEqual(kept.five_hour.used_pct, 42)
        self.assertEqual(kept.seven_day.remaining_pct, 90)
        first = apply_reading(loading, failed, now=1_762_000_100)
        self.assertEqual(first.error, "Claude usage unavailable")
        self.assertIsNone(first.five_hour)
        idle_five, idle_week = both_windows(first)
        self.assertEqual(idle_five.amount, "—")
        self.assertEqual(idle_week.amount, "—")


# Desktop history stores percent points, not the OAuth 0-1 fraction.
# The org id is a fixture, not an account on this machine.
HISTORY_NOW = 1_794_500_000
HISTORY = {
    "version": 2,
    "samples": [
        {
            "t": (HISTORY_NOW - 3600) * 1000,
            "org": "00000000-0000-4000-8000-000000000001",
            "u": {"fh": 5, "sd": 1},
        },
        {
            "t": (HISTORY_NOW - 600) * 1000,
            "org": "00000000-0000-4000-8000-000000000001",
            "u": {},
        },
        {
            "t": (HISTORY_NOW - 600) * 1000,
            "org": "00000000-0000-4000-8000-000000000001",
            "u": {"fh": 100, "sd": 46},
        },
    ],
}
# Desktop GET /api/organizations/{org}/usage. Utilization is a percent point.
# Resets are synthetic. No account id, cookie, or token.
DESKTOP_USAGE = {
    "five_hour": {
        "utilization": 100.0,
        "resets_at": "2026-11-12T22:00:00+00:00",
    },
    "seven_day": {
        "utilization": 46.0,
        "resets_at": "2026-11-18T04:00:00+00:00",
    },
    "extra_usage": {"is_enabled": False, "utilization": None},
}
# v11 ciphertext of SHA-256(".claude.ai") + "sk-ant-sid-example",
# AES-128-CBC, PBKDF2-HMAC-SHA1("test-secret", "saltysalt", 1, 16).
COOKIE_BLOB = bytes.fromhex(
    "763131"
    "f2ec976a4541ea6c2b13958c1fcf8cf08beb8b478fed208c2d2b9f5db4580793"
    "8a84308d9e31bd11259a64dea5e71b1d8debbb0c05dded673bb59395b01cff61"
)


class DesktopHistoryTest(unittest.TestCase):
    def test_newest_sample_is_percent_points(self) -> None:
        snap, org = parse_desktop_history(HISTORY)
        self.assertEqual(org, "00000000-0000-4000-8000-000000000001")
        self.assertNotIn(org, str(snap))
        self.assertTrue(cache_is_fresh(snap, HISTORY_NOW))
        self.assertEqual(snap.five_hour.used_pct, 100)
        self.assertEqual(snap.five_hour.remaining_pct, 0)
        self.assertEqual(snap.seven_day.used_pct, 46)
        self.assertEqual(snap.seven_day.remaining_pct, 54)
        self.assertIsNone(snap.five_hour.reset_at)
        self.assertIsNone(snap.seven_day.reset_at)
        five, week = both_windows(snap, "remaining", HISTORY_NOW)
        self.assertEqual((five.label, five.amount, five.reset), ("Claude 5h", "0%", "—"))
        self.assertEqual((week.label, week.amount, week.reset), ("Claude Wk", "54%", "—"))
        used_five, used_week = both_windows(snap, "used", HISTORY_NOW)
        self.assertEqual(used_five.amount, "100%")
        self.assertEqual(used_week.amount, "46%")

    def test_empty_newest_sample_is_not_zero(self) -> None:
        stale_only = {
            "version": 2,
            "samples": [
                {"t": HISTORY_NOW * 1000, "org": "not-an-org", "u": {}},
                {"t": (HISTORY_NOW - 3600) * 1000, "org": None, "u": {"fh": 100, "sd": 46}},
            ],
        }
        snap, org = parse_desktop_history(stale_only)
        self.assertIsNone(snap)
        self.assertEqual(org, "")

    def test_stale_history_is_not_the_displayed_reading(self) -> None:
        snap, _org = parse_desktop_history(HISTORY)
        later = HISTORY_NOW + 60 * 60 + 5
        self.assertFalse(cache_is_fresh(snap, later))
        chosen = prefer_reading(None, snap, None, later)
        five, week = both_windows(chosen, "remaining", later)
        self.assertEqual(five.amount, "—")
        self.assertEqual(week.amount, "—")
        self.assertTrue(chosen.keep_previous)

    def test_fresh_cache_beats_a_session_without_a_reset(self) -> None:
        snap, _org = parse_desktop_history(HISTORY)
        session = ClaudeSnapshot(
            five_hour=UsageWindow(used_pct=1, remaining_pct=99),
            seven_day=UsageWindow(used_pct=2, remaining_pct=98),
            fetched_at=HISTORY_NOW,
        )
        chosen = prefer_reading(None, snap, session, HISTORY_NOW)
        self.assertEqual(chosen.five_hour.used_pct, 100)
        self.assertEqual(chosen.seven_day.used_pct, 46)
        self.assertIsNone(chosen.five_hour.reset_at)
        self.assertIsNone(chosen.seven_day.reset_at)
        failed = ClaudeSnapshot(error="Sign in to Claude", keep_previous=True)
        self.assertEqual(prefer_reading(failed, snap, session, HISTORY_NOW).five_hour.used_pct, 100)

    def test_usage_endpoint_reset_replaces_history_without_one(self) -> None:
        snap, _org = parse_desktop_history(HISTORY)
        self.assertTrue(_history_needs_session(None, snap, HISTORY_NOW))
        session = parse_desktop_usage(DESKTOP_USAGE)
        session.fetched_at = HISTORY_NOW
        self.assertFalse(_history_needs_session(None, session, HISTORY_NOW))
        chosen = prefer_reading(None, snap, session, HISTORY_NOW)
        self.assertEqual(chosen.five_hour.used_pct, 100)
        self.assertEqual(chosen.five_hour.remaining_pct, 0)
        self.assertEqual(chosen.seven_day.used_pct, 46)
        self.assertEqual(chosen.seven_day.remaining_pct, 54)
        self.assertEqual(chosen.five_hour.reset_at, session.five_hour.reset_at)
        self.assertEqual(chosen.seven_day.reset_at, session.seven_day.reset_at)
        now = chosen.five_hour.reset_at - 50 * 60
        five, week = both_windows(chosen, "remaining", now)
        self.assertEqual((five.label, five.amount), ("Claude 5h", "0%"))
        self.assertEqual((week.label, week.amount), ("Claude Wk", "54%"))
        self.assertEqual(five.reset, reset_face(chosen.five_hour.reset_at, now))
        self.assertEqual(week.reset, reset_face(chosen.seven_day.reset_at, now))
        self.assertEqual(five.reset, "50m")
        self.assertRegex(week.reset, r"^\d+d$")
        self.assertNotIn(" ", five.reset)
        self.assertNotIn(" ", week.reset)
        self.assertLessEqual(len(five.reset), 12)
        self.assertLessEqual(len(week.reset), 12)

    def test_one_known_reset_is_kept_on_the_history_percents(self) -> None:
        snap, _org = parse_desktop_history(HISTORY)
        session = ClaudeSnapshot(
            five_hour=UsageWindow(used_pct=100, remaining_pct=0, reset_at=HISTORY_NOW + 50 * 60),
        )
        chosen = prefer_reading(None, snap, session, HISTORY_NOW)
        self.assertEqual(chosen.five_hour.used_pct, 100)
        self.assertEqual(chosen.five_hour.reset_at, HISTORY_NOW + 50 * 60)
        self.assertEqual(chosen.seven_day.used_pct, 46)
        self.assertIsNone(chosen.seven_day.reset_at)
        five, week = both_windows(chosen, "remaining", HISTORY_NOW)
        self.assertEqual((five.label, five.amount), ("Claude 5h", "0%"))
        self.assertEqual((week.label, week.amount), ("Claude Wk", "54%"))
        self.assertEqual(five.reset, reset_face(HISTORY_NOW + 50 * 60, HISTORY_NOW))
        self.assertNotEqual(five.reset, "—")
        self.assertEqual(week.reset, "—")

    def test_stale_cache_uses_the_session(self) -> None:
        snap, _org = parse_desktop_history(HISTORY)
        later = HISTORY_NOW + 60 * 60 + 5
        session = parse_desktop_usage(
            {
                "five_hour": {"utilization": 12, "resets_at": "2026-11-12T22:00:00Z"},
                "seven_day": {"utilization": 46, "resets_at": "2026-11-18T04:00:00Z"},
            }
        )
        session.fetched_at = later
        chosen = prefer_reading(None, snap, session, later)
        self.assertEqual(chosen.five_hour.used_pct, 12)
        self.assertEqual(chosen.five_hour.remaining_pct, 88)
        self.assertIsNotNone(chosen.five_hour.reset_at)
        five, week = both_windows(chosen, "remaining", later - 2 * 3600)
        self.assertEqual(five.amount, "88%")
        self.assertEqual(week.amount, "54%")
        self.assertNotEqual(five.reset, "—")
        self.assertNotEqual(week.reset, "0%")

    def test_desktop_api_does_not_scale_percent_points(self) -> None:
        snap = parse_desktop_usage(
            {
                "five_hour": {"utilization": None, "resets_at": "2026-11-12T20:30:00Z"},
                "seven_day": {"utilization": 46, "resets_at": "2026-11-18T04:00:00Z"},
            }
        )
        five, week = both_windows(snap, "remaining", HISTORY_NOW)
        self.assertIsNone(snap.five_hour.used_pct)
        self.assertEqual(five.amount, "—")
        self.assertEqual(week.amount, "54%")
        oauth = parse_usage({"five_hour": {"utilization": 0.46, "resets_at": "2026-11-12T20:30:00Z"}})
        self.assertEqual(oauth.five_hour.used_pct, 46)

    def test_version1_history_file(self) -> None:
        payload = {
            "version": 1,
            "samples": [{"t": (HISTORY_NOW - 60) * 1000, "fh": 0, "sd": 15}],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan-usage-history.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            snap, org = load_desktop_history(path)
        self.assertEqual(org, "")
        self.assertEqual(snap.five_hour.remaining_pct, 100)
        self.assertEqual(snap.seven_day.used_pct, 15)
        five, _week = both_windows(snap, "remaining", HISTORY_NOW)
        self.assertEqual(five.amount, "100%")

    def test_cookie_decrypt_strips_domain_hash(self) -> None:
        if _libcrypto() is None:
            self.skipTest("libcrypto is not available")
        text = decrypt_cookie_value(COOKIE_BLOB, "test-secret", ".claude.ai")
        self.assertEqual(text, "sk-ant-sid-example")
        self.assertEqual(decrypt_cookie_value(COOKIE_BLOB, "wrong-secret", ".claude.ai"), "")
        self.assertEqual(decrypt_cookie_value(COOKIE_BLOB, "test-secret", ".example.com"), "")


if __name__ == "__main__":
    unittest.main()
