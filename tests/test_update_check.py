from __future__ import annotations

import configparser
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from timiniprint.update_check import (
    ReleaseInfo,
    UpdateChecker,
    check_for_updates,
    is_newer_version,
    release_url_for_version,
)
from timiniprint.app_settings import AppSettings


class UpdateCheckTests(unittest.TestCase):
    def test_version_compare_handles_v_prefix(self) -> None:
        self.assertTrue(is_newer_version("v0.6", "0.5"))
        self.assertTrue(is_newer_version("0.5.1", "v0.5"))
        self.assertFalse(is_newer_version("v0.5", "0.5"))
        self.assertFalse(is_newer_version("v0.4.9", "0.5"))

    def test_release_url_is_derived_from_version(self) -> None:
        self.assertEqual(
            release_url_for_version("v0.6"),
            "https://github.com/Dejniel/TiMini-Print/releases/tag/v0.6",
        )

    def test_check_fetches_release_and_writes_app_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.ini"
            calls = []

            def fetch(timeout_sec: float) -> ReleaseInfo:
                calls.append(timeout_sec)
                return ReleaseInfo("v0.6")

            result = check_for_updates(
                current_version="0.5",
                settings_path=settings_path,
                now=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
                fetch_latest_release=fetch,
            )

            self.assertIsNotNone(result)
            self.assertEqual(result.latest_version, "v0.6")
            self.assertEqual(
                result.release_url,
                "https://github.com/Dejniel/TiMini-Print/releases/tag/v0.6",
            )
            self.assertFalse(result.from_cache)
            self.assertEqual(calls, [2.0])

            parser = configparser.ConfigParser()
            parser.read(settings_path, encoding="utf-8")
            self.assertEqual(parser.get("update", "last_checked_at"), "2026-06-05T12:00:00Z")
            self.assertEqual(parser.get("update", "latest_version"), "v0.6")
            self.assertFalse(parser.has_option("update", "release_url"))

    def test_update_checker_uses_injected_settings_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings = AppSettings(Path(tmpdir) / "settings.ini")

            checker = UpdateChecker(
                settings=settings,
                current_version="0.5",
                fetch_latest_release=lambda _timeout: ReleaseInfo("v0.6"),
            )

            result = checker.check(now=datetime(2026, 6, 5, 12, 0, tzinfo=UTC))

            self.assertIsNotNone(result)
            self.assertEqual(result.latest_version, "v0.6")
            self.assertTrue(settings.path.exists())

    def test_recent_cached_update_skips_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.ini"
            settings_path.write_text(
                "[update]\n"
                "last_checked_at = 2026-06-05T11:00:00Z\n"
                "latest_version = v0.6\n",
                encoding="utf-8",
            )

            def fetch(_timeout_sec: float) -> ReleaseInfo:
                raise AssertionError("network fetch should not run")

            result = check_for_updates(
                current_version="0.5",
                settings_path=settings_path,
                now=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
                fetch_latest_release=fetch,
            )

            self.assertIsNotNone(result)
            self.assertTrue(result.from_cache)
            self.assertEqual(result.latest_version, "v0.6")
            self.assertEqual(
                result.release_url,
                "https://github.com/Dejniel/TiMini-Print/releases/tag/v0.6",
            )

    def test_stale_cache_refreshes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.ini"
            settings_path.write_text(
                "[update]\n"
                "last_checked_at = 2026-06-03T11:00:00Z\n"
                "latest_version = v0.6\n",
                encoding="utf-8",
            )
            calls = []

            def fetch(_timeout_sec: float) -> ReleaseInfo:
                calls.append(True)
                return ReleaseInfo("v0.7")

            result = check_for_updates(
                current_version="0.5",
                settings_path=settings_path,
                now=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
                check_interval=timedelta(hours=24),
                fetch_latest_release=fetch,
            )

            self.assertEqual(calls, [True])
            self.assertIsNotNone(result)
            self.assertEqual(result.latest_version, "v0.7")

    def test_incomplete_cache_refreshes_even_when_recent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.ini"
            settings_path.write_text(
                "[update]\n"
                "last_checked_at = 2026-06-05T11:00:00Z\n",
                encoding="utf-8",
            )
            calls = []

            def fetch(_timeout_sec: float) -> ReleaseInfo:
                calls.append(True)
                return ReleaseInfo("v0.6")

            result = check_for_updates(
                current_version="0.5",
                settings_path=settings_path,
                now=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
                fetch_latest_release=fetch,
            )

            self.assertEqual(calls, [True])
            self.assertIsNotNone(result)
            self.assertEqual(result.latest_version, "v0.6")

    def test_failed_fetch_updates_last_checked_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.ini"

            def fetch(_timeout_sec: float) -> ReleaseInfo:
                raise RuntimeError("offline")

            result = check_for_updates(
                current_version="0.5",
                settings_path=settings_path,
                now=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
                fetch_latest_release=fetch,
            )

            self.assertIsNone(result)
            parser = configparser.ConfigParser()
            parser.read(settings_path, encoding="utf-8")
            self.assertEqual(parser.get("update", "last_checked_at"), "2026-06-05T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
