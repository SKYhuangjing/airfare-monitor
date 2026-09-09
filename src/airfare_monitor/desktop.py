"""Windows desktop entry point for 航价守望."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from .app_paths import AppPaths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="airfare-monitor-gui")
    parser.add_argument("--user-root", help="Desktop runtime directory; intended for diagnostics and tests")
    parser.add_argument("--background", action="store_true", help="Start hidden; used only by optional autostart")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Initialize and validate local resources without opening the UI or accessing flight websites",
    )
    parser.add_argument(
        "--ui-smoke-test",
        action="store_true",
        help="Validate the packaged Qt UI runtime without starting monitoring or accessing flight websites",
    )
    parser.add_argument("--smoke-report", help="Optional JSON result path for automated package verification")
    args = parser.parse_args(argv)
    paths = AppPaths.discover(user_root=args.user_root)
    try:
        if args.smoke_test:
            from .desktop_app.startup import initialize_desktop

            result = initialize_desktop(paths)
            payload = {
                "status": "ok",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
                "user_root": str(paths.user_root.resolve()),
                "resource_root": str(paths.resource_root.resolve()),
                "airport_count": result.airport_count,
                "enabled_route_count": result.enabled_route_count,
                "detected_browsers": list(result.detected_browsers),
            }
            _write_smoke_report(paths, args.smoke_report, payload)
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        # Create the user-owned state before importing Qt and collection modules.
        # If a frozen dependency import fails, the exception is now recorded under
        # this deterministic directory instead of disappearing in a windowed EXE.
        paths.initialize()
        from .desktop_app.application import run_desktop

        if args.ui_smoke_test:
            from .desktop_app.application import validate_ui_runtime

            result = validate_ui_runtime(paths)
            payload = {
                "status": "ok",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
                "user_root": str(paths.user_root.resolve()),
                "qt_platform": result,
            }
            _write_smoke_report(paths, args.smoke_report, payload)
            print(json.dumps(payload, ensure_ascii=False))
            return 0

        return run_desktop(paths, start_hidden=args.background)
    except Exception as exc:
        log_path = _write_startup_error(paths, exc)
        if args.smoke_test or args.ui_smoke_test:
            payload = {
                "status": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "startup_log": str(log_path),
            }
            _write_smoke_report(paths, args.smoke_report, payload)
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        else:
            _show_startup_error(log_path)
        return 1


def _write_smoke_report(paths: AppPaths, requested_path: str | None, payload: dict[str, object]) -> Path:
    report_path = Path(requested_path) if requested_path else paths.logs_dir / "startup-smoke.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report_path


def _write_startup_error(paths: AppPaths, exc: Exception) -> Path:
    log_path = paths.logs_dir / "startup.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now().isoformat(timespec='seconds')}] {type(exc).__name__}: {exc}\n")
            handle.write("".join(traceback.format_exception(exc)))
            handle.write("\n")
    except OSError:
        return log_path
    return log_path


def _show_startup_error(log_path: Path) -> None:
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        QMessageBox.critical(
            None,
            "航价守望无法启动",
            f"初始化失败，详细信息已写入：\n{log_path}",
        )
    except Exception:
        return


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
