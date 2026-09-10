"""Desktop composition root: paths, controllers, UI and tray."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from ..app_paths import AppPaths
from ..storage import SQLiteStore
from ..ui.main_window import MainWindow
from .airport_catalog import AirportCatalog
from .browser_detector import BrowserDetector
from .controller import DesktopController
from .event_bridge import CoordinatorEventBridge
from .monitor_coordinator import MonitorCoordinator
from .route_repository import RouteRepository
from .single_instance import SingleInstance
from .startup import initialize_desktop


def validate_ui_runtime(paths: AppPaths) -> str:
    """Construct the main window without starting monitoring or the event loop."""
    initialize_desktop(paths)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("航价守望")
    _apply_style(app, paths.resource_root)
    catalog = AirportCatalog.load(paths.resource_root / "airports.zh.json")
    controller = DesktopController(RouteRepository(paths.routes_path))
    window = MainWindow(
        controller,
        catalog,
        BrowserDetector().detect(),
        on_run_now=lambda: None,
        history_store=SQLiteStore(paths.database_path),
        outputs_dir=paths.outputs_dir,
    )
    window.close()
    return QGuiApplication.platformName()


def run_desktop(paths: AppPaths, *, start_hidden: bool = False) -> int:
    initialize_desktop(paths)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("航价守望")
    app.setOrganizationName("AirfareMonitor")
    app.setQuitOnLastWindowClosed(False)
    _apply_style(app, paths.resource_root)
    catalog = AirportCatalog.load(paths.resource_root / "airports.zh.json")
    controller = DesktopController(RouteRepository(paths.routes_path))
    instance = SingleInstance()
    if not instance.acquire():
        return 0
    coordinator = MonitorCoordinator(paths)
    controller.on_routes_changed(lambda routes: coordinator.run_now() if any(route.enabled for route in routes) else coordinator.pause())
    window = MainWindow(
        controller,
        catalog,
        BrowserDetector().detect(),
        on_run_now=coordinator.run_now,
        history_store=SQLiteStore(paths.database_path),
        outputs_dir=paths.outputs_dir,
    )
    bridge = CoordinatorEventBridge(app)
    bridge.event_received.connect(window.handle_monitor_event, Qt.ConnectionType.QueuedConnection)
    coordinator.subscribe(bridge.publish)
    instance.set_activation_handler(window.activate)
    tray = _create_tray(app, window, coordinator)
    window.runtime_status_changed.connect(lambda text: tray.setToolTip(f"航价守望 · {text}"))
    app.aboutToQuit.connect(coordinator.shutdown)
    app.aboutToQuit.connect(instance.close)
    if not start_hidden:
        window.show()
    tray.show()
    coordinator.start()
    return app.exec()


def _apply_style(app: QApplication, resource_root: Path) -> None:
    stylesheet = resource_root / "styles.qss"
    if not stylesheet.is_file():
        stylesheet = Path(__file__).resolve().parents[1] / "ui" / "resources" / "styles.qss"
    if stylesheet.is_file():
        app.setStyleSheet(stylesheet.read_text(encoding="utf-8"))


def _create_tray(app: QApplication, window: MainWindow, coordinator: MonitorCoordinator) -> QSystemTrayIcon:
    icon = app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("航价守望 · 等待监控")
    menu = QMenu()
    open_action = menu.addAction("打开航价守望")
    open_action.triggered.connect(window.showNormal)
    run_action = menu.addAction("立即查询")
    run_action.triggered.connect(window._run_now)
    pause_action = menu.addAction("暂停监控")
    pause_action.setCheckable(True)

    def toggle_pause(paused: bool) -> None:
        if paused:
            coordinator.pause()
        else:
            coordinator.resume()
        pause_action.setText("继续监控" if paused else "暂停监控")

    def sync_pause(paused: bool) -> None:
        pause_action.blockSignals(True)
        pause_action.setChecked(paused)
        pause_action.setText("继续监控" if paused else "暂停监控")
        pause_action.blockSignals(False)

    pause_action.toggled.connect(toggle_pause)
    window.monitor_paused_changed.connect(sync_pause)
    report_action = menu.addAction("打开最新报告")
    report_action.triggered.connect(window.open_latest_report)
    menu.addSeparator()
    quit_action = menu.addAction("退出并停止监控")
    quit_action.triggered.connect(app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: window.showNormal() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    return tray
