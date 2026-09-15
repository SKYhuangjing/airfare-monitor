"""Desktop composition root: paths, controllers, UI and tray."""

from __future__ import annotations

import sys
import logging
from pathlib import Path

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox, QSystemTrayIcon

from ..app_paths import AppPaths
from ..storage import SQLiteStore
from ..ui.app_icon import application_icon
from ..ui.main_window import MainWindow
from ..ui.onboarding import OnboardingDialog
from .airport_catalog import AirportCatalog
from .credential_store import CredentialStore
from .controller import DesktopController
from .event_bridge import CoordinatorEventBridge
from .event_journal import AppEventJournal
from .events import CoordinatorStateChanged
from .monitor_coordinator import MonitorCoordinator
from .notification_policy import alert_for_event
from .preferences import PreferencesManager
from .route_repository import RouteRepository
from .settings_repository import SettingsRepository
from .single_instance import SingleInstance
from .session_state import DesktopSessionState
from .startup import initialize_desktop


logger = logging.getLogger(__name__)


def validate_ui_runtime(paths: AppPaths) -> str:
    """Construct the main window without starting monitoring or the event loop."""
    initialize_desktop(paths)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("航价守望")
    app.setWindowIcon(application_icon())
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
    # Read-only check catches missing dynamic keyring backends in frozen builds.
    CredentialStore._backend()
    return QGuiApplication.platformName()


def run_desktop(paths: AppPaths, *, start_hidden: bool = False) -> int:
    initialize_desktop(paths)
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("航价守望")
    app.setOrganizationName("AirfareMonitor")
    app.setWindowIcon(application_icon())
    app.setQuitOnLastWindowClosed(False)
    _apply_style(app, paths.resource_root)
    catalog = AirportCatalog.load(paths.resource_root / "airports.zh.json")
    controller = DesktopController(RouteRepository(paths.routes_path))
    instance = SingleInstance()
    if not instance.acquire():
        return 0
    session_state = DesktopSessionState(paths.logs_dir)
    previous_unclean = session_state.begin()
    preferences = PreferencesManager(SettingsRepository(paths.settings_path, user_root=paths.user_root))
    desktop_settings = preferences.load()
    browsers = preferences.refresh_browsers()
    add_first_route = False
    if not desktop_settings.onboarding_completed:
        onboarding = OnboardingDialog(preferences)
        if onboarding.exec() != QDialog.DialogCode.Accepted:
            session_state.finish()
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
    if previous_unclean:
        journal.store.record_app_event(
            event_type="previous_unclean_exit",
            severity="warning",
            message="上次运行未正常结束；请检查浏览器与最近查询状态",
        )
        logger.warning("检测到上次桌面会话未正常结束")

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

    def open_verification_if_ready(leg_id: str) -> bool:
        accepted = coordinator.open_verification(leg_id)
        if not accepted:
            QMessageBox.information(
                window, "暂时无法打开", "请等待当前查询结束，或先完成已打开的人工确认页面。"
            )
        return accepted

    window = MainWindow(
        controller,
        catalog,
        browsers,
        preferences,
        on_run_now=run_if_ready,
        on_pause=coordinator.pause,
        on_resume=coordinator.resume,
        on_retry_leg=retry_if_ready,
        on_open_verification=open_verification_if_ready,
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
    alert_target = [0]

    def notify_desktop(event: object) -> None:
        alert = alert_for_event(event)
        if alert is None:
            return
        alert_target[0] = alert.target_page
        if preferences.load().desktop_notifications and tray.isVisible():
            tray.showMessage(alert.title, alert.message, QSystemTrayIcon.MessageIcon.Information, 8000)

    bridge.event_received.connect(notify_desktop, Qt.ConnectionType.QueuedConnection)
    tray.messageClicked.connect(lambda: (window.activate(), window._switch_page(alert_target[0])))
    window.runtime_status_changed.connect(lambda text: tray.setToolTip(f"航价守望 · {text}"))
    def finish_desktop() -> None:
        try:
            monitor_stopped = coordinator.shutdown(timeout=20)
            mail_stopped = window.notifications.finish_pending_test()
            if monitor_stopped and mail_stopped:
                session_state.finish()
            else:
                logger.error("桌面退出等待超时，保留异常会话标记以便下次启动提示")
        finally:
            instance.close()

    app.aboutToQuit.connect(finish_desktop)
    selected_browser = preferences.selected_browser(desktop_settings)
    if not start_hidden or selected_browser is None:
        window.show()
    tray.show()
    if previous_unclean:
        if start_hidden:
            tray.showMessage(
                "上次运行未正常结束",
                "请打开系统状态检查浏览器和最近查询记录。",
                QSystemTrayIcon.MessageIcon.Warning,
                8000,
            )
        else:
            QTimer.singleShot(0, lambda: QMessageBox.warning(
                window,
                "上次运行未正常结束",
                "航价守望检测到上次运行未正常退出。请在系统状态中检查浏览器与最近查询记录。",
            ))
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
    icon = application_icon()
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("航价守望 · 等待监控（右键打开菜单）")
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
