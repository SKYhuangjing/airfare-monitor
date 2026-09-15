from __future__ import annotations

import logging
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from airfare_monitor.desktop_app.diagnostics import export_diagnostic_zip
from airfare_monitor.desktop_app.safe_logging import RedactingFormatter, redact_text, write_startup_failure
from airfare_monitor.desktop_app.session_state import DesktopSessionState
from airfare_monitor.desktop_app.settings_repository import DesktopSettings
from airfare_monitor.storage import SCHEMA_VERSION, SQLiteStore


ROOT = Path(__file__).resolve().parents[1]


class DesktopR4Tests(unittest.TestCase):
    def test_log_formatter_masks_addresses_urls_and_secrets(self):
        raw = (
            "mail=user@example.com password=abc123 Bearer abc123 "
            "https://example.com/path?token=abc123 cookie: c123"
        )
        masked = redact_text(raw)
        self.assertNotIn("user@example.com", masked)
        self.assertNotIn("abc123", masked)
        self.assertNotIn("c123", masked)
        self.assertNotIn("https://", masked)
        record = logging.LogRecord("test", logging.INFO, __file__, 1, raw, (), None)
        self.assertEqual(RedactingFormatter("%(message)s").format(record), masked)

    def test_startup_traceback_is_bounded_and_redacted(self):
        with TemporaryDirectory() as temp:
            class Paths:
                logs_dir = Path(temp)

            try:
                raise RuntimeError("password=abc123 user@example.com")
            except RuntimeError as error:
                path = write_startup_failure(Paths(), error)
            content = path.read_text(encoding="utf-8")
            self.assertIn("RuntimeError", content)
            self.assertNotIn("abc123", content)
            self.assertNotIn("user@example.com", content)
            self.assertLess(path.stat().st_size, 2_000_000)

    def test_session_state_detects_interrupted_run_and_clears_clean_run(self):
        with TemporaryDirectory() as temp:
            first = DesktopSessionState(Path(temp))
            self.assertFalse(first.begin())
            self.assertTrue(first.path.is_file())
            restarted = DesktopSessionState(Path(temp))
            self.assertTrue(restarted.begin())
            restarted.finish()
            self.assertFalse(restarted.path.exists())
            self.assertFalse(DesktopSessionState(Path(temp)).begin())

    def test_schema_version_reopen_preserves_existing_data(self):
        with TemporaryDirectory() as temp:
            store = SQLiteStore(Path(temp) / "prices.sqlite3")
            store.initialize()
            with closing(store.connect()) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
                connection.execute(
                    "INSERT INTO app_events (occurred_at,event_type,severity,message) "
                    "VALUES ('2026-09-15','test','info','keep me')"
                )
                connection.commit()
            store.initialize()
            with closing(store.connect()) as connection:
                self.assertEqual(connection.execute("SELECT message FROM app_events").fetchone()[0], "keep me")
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
                connection.commit()
            with self.assertRaises(RuntimeError):
                store.initialize()

    def test_installer_preserves_user_root_and_uses_stable_identity(self):
        script = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
        self.assertIn("B2D23D48-88C7-4C64-94C0-B827A397537E", script)
        self.assertIn('Source: "{#BuildRoot}\\*"', script)
        self.assertIn("CloseApplications=yes", script)
        self.assertNotIn("[UninstallDelete]", script)
        self.assertNotIn("DelTree(", script)

    def test_diagnostic_zip_only_contains_redacted_snippets(self):
        with TemporaryDirectory() as temp:
            logs = Path(temp) / "logs"
            logs.mkdir()
            (logs / "app.log").write_text(
                "error user@example.com password=abc123 C:\\Users\\Alice\\secret\n",
                encoding="utf-8",
            )
            archive_path = Path(temp) / "diagnostics.zip"
            export_diagnostic_zip(
                archive_path, DesktopSettings(), None,
                enabled_routes=2, logs_dir=logs, catalog_version=1,
            )
            with ZipFile(archive_path) as archive:
                self.assertEqual(set(archive.namelist()), {"diagnostics.json", "logs/app.log"})
                content = archive.read("logs/app.log").decode("utf-8")
                self.assertNotIn("abc123", content)
                self.assertNotIn("user@example.com", content)
                self.assertNotIn("Alice", content)


if __name__ == "__main__":
    unittest.main()
