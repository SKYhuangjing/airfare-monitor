"""Desktop composition root: paths, controllers, UI and tray."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox, QStyle, QSystemTrayIcon

from ..app_paths import AppPaths
from ..storage import SQLiteStore
from ..ui.main_window import MainWindow
from ..ui.onboarding import OnboardingDialog
from .airport_catalog import AirportCatalog
from .controller import DesktopController
from .event_bridge import CoordinatorEventBridge
from .event_journal import AppEventJournal
from .events import CoordinatorStateChanged
from .monitor_coordinator import MonitorCoordinator
from .preferences import PreferencesManager
from .route_repository import RouteRepository
from .settings_repository import SettingsRepository
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
    preferences = PreferencesManager(SettingsRepository(paths.settings_path, user_root=paths.user_root))
    browsers = preferences.refresh_browsers()
    onboarding = OnboardingDialog(preferences)
    onboarding.close()
    window = MainWindow(
        controller,
        catalog,
        browsers,
        preferences,
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
    preferences = PreferencesManager(SettingsRepository(paths.settings_path, user_root=paths.user_root))
    desktop_settings = preferences.load()
    browsers = preferences.refresh_browsers()
    add_first_route = False
    if not desktop_settings.onboarding_completed:
        onboarding = OnboardingDialog(preferences)
        if onboarding.exec() != QDialog.DialogCode.Accepted:
            instance.close()
            return 0
        assert onboarding.saved_settings is not None
        desktop_settings = onboarding.saved_settings
        browsers = preferences.browsers
        add_first_route = not controller.current_routes()

    coordinator = MonitorCoordinator(paths)
    history_store = SQLiteStore(paths.database_path)
    journal = AppEventJournal(history_store)
    journal.initialize()

    window: MainWindow

    def run_if_ready() -> bool:
        current = preferences.load()
        if preferences.selected_browser(current) is None:
            message = "没有找到已选择的 Chrome 或 Edge，请在系统状态中重新检测并保存。"
            window.show_browser_settings(message)
            QMessageBox.warning(window, "需要浏览器", message)
            return False
        return coordinator.run_now()

    def retry_if_ready(leg_id: str) -> bool:
        current = preferences.load()
        if preferences.selected_browser(current) is None:
            message = "没有找到已选择的 Chrome 或 Edge，请先重新检测并保存。"
            window.show_browser_settings(message)
            QMessageBox.warning(window, "需要浏览器", message)
            return False
        return coordinator.retry_leg(leg_id)

    window = MainWindow(
        controller,
        catalog,
        browsers,
        preferences,
        on_run_now=run_if_ready,
        on_pause=coordinator.pause,
        on_resume=coordinator.resume,
        on_retry_leg=retry_if_ready,
        history_store=history_store,
        outputs_dir=paths.outputs_dir,
    )

    paused_for_no_routes = False

    def routes_changed(routes: list[object]) -> None:
        nonlocal paused_for_no_routes
        enabled = sum(bool(getattr(route, "enabled", False)) for route in routes)
        journal.record_routes_changed(enabled)
        if enabled:
            if paused_for_no_routes:
                paused_for_no_routes = False
                coordinator.resume()
            else:
                run_if_ready()
        else:
            was_paused = coordinator.snapshot().paused
            coordinator.pause()
            paused_for_no_routes = not was_paused

    controller.on_routes_changed(routes_changed)

    def apply_runtime_settings(saved: object) -> None:
        journal.record_settings_changed()
        coordinator.apply_settings()
        window.refresh_from_history()

    window.runtime_settings_saved.connect(apply_runtime_settings)
    bridge = CoordinatorEventBridge(app)
    bridge.event_received.connect(window.handle_monitor_event, Qt.ConnectionType.QueuedConnection)
    coordinator.subscribe(journal.record)
    coordinator.subscribe(bridge.publish)
    instance.set_activation_handler(window.activate)
    tray = _create_tray(app, window, coordinator)
    window.runtime_status_changed.connect(lambda text: tray.setToolTip(f"航价守望 · {text}"))
    app.aboutToQuit.connect(coordinator.shutdown)
    app.aboutToQuit.connect(instance.close)
    selected_browser = preferences.selected_browser(desktop_settings)
    if not start_hidden or selected_browser is None:
        window.show()
    tray.show()
    coordinator.start(run_immediately=selected_browser is not None)
    if selected_browser is None:
        window.handle_monitor_event(
            CoordinatorStateChanged("ERROR", "未检测到可用浏览器；完成浏览器设置前不会开始查询")
        )
        window.show_browser_settings("安装或重新选择 Chrome/Edge 后即可开始查询。")
    if add_first_route:
        QTimer.singleShot(0, window.begin_first_route)
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
