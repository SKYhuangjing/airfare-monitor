from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..config import MAX_ENABLED_LEGS
from ..desktop_app.airport_catalog import AirportCatalog
from ..desktop_app.browser_detector import BrowserCandidate
from ..desktop_app.controller import DesktopController
from ..desktop_app.events import (
    CoordinatorStateChanged, CycleFinished, CycleStarted, FatalError, LegFinished, LegStarted,
    ManualAttentionRequested, NextRunScheduled,
)
from ..desktop_app.preferences import PreferencesManager
from ..desktop_app.view_data import load_dashboard_data
from ..models import LegConfig
from ..storage import SQLiteStore
from .dashboard_page import DashboardPage
from .flight_results_page import FlightResultsPage
from .history_page import HistoryPage
from .preferences import RuntimePreferencesForm, preference_card
from .route_wizard import RouteWizard
from .app_icon import application_icon
from .. import __version__


class MainWindow(QMainWindow):
    runtime_status_changed = Signal(str)
    monitor_paused_changed = Signal(bool)
    runtime_settings_saved = Signal(object)

    def __init__(
        self,
        controller: DesktopController,
        catalog: AirportCatalog,
        browsers: list[BrowserCandidate],
        preferences: PreferencesManager,
        on_run_now: Callable[[], bool | None] | None = None,
        on_pause: Callable[[], bool | None] | None = None,
        on_resume: Callable[[], bool | None] | None = None,
        on_retry_leg: Callable[[str], bool | None] | None = None,
        history_store: SQLiteStore | None = None,
        outputs_dir: Path | None = None,
    ):
        super().__init__()
        self.controller = controller
        self.catalog = catalog
        self.browsers = browsers
        self.preferences = preferences
        self.history_store = history_store
        self.outputs_dir = outputs_dir
        self.latest_report_path: Path | None = None
        self._latest_prices: dict[str, Decimal] = {}
        self.setWindowTitle("航价守望")
        self.setMinimumSize(1050, 700)
        self.resize(1250, 800)
        self.on_run_now = on_run_now
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.on_retry_leg = on_retry_leg
        self._paused = False
        self._build()
        self.controller.on_routes_changed(self.refresh_routes)
        self.refresh_routes(self.controller.current_routes())
        self.refresh_from_history()

    def _build(self) -> None:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        self.sidebar = self._make_sidebar()
        layout.addWidget(self.sidebar)
        self.pages = QStackedWidget()
        self.dashboard = DashboardPage(
            self._open_new_route,
            self._run_now,
            self._toggle_pause,
            lambda: self._switch_page(1),
            self._open_results,
        )
        self.routes_page = RoutesPage(self.controller, self.catalog, open_results=self._open_results)
        self.history = HistoryPage(
            self.history_store,
            open_latest_report=self.open_latest_report,
            outputs_dir=self.outputs_dir,
        )
        self.notifications = PlaceholderPage("通知设置", "桌面通知与 SMTP 设置将在下一阶段接入 Windows 凭据安全存储。")
        self.system = SystemStatusPage(
            self.browsers,
            self.preferences,
            history_store=self.history_store,
            outputs_dir=self.outputs_dir,
            retry_leg=self._retry_leg,
        )
        self.flight_results = FlightResultsPage(
            self.history_store,
            on_back=lambda: self._switch_page(1),
        )
        self.system.settings_saved.connect(self.runtime_settings_saved.emit)
        for page in (
            self.dashboard,
            self.routes_page,
            self.history,
            self.notifications,
            self.system,
            self.flight_results,
        ):
            self.pages.addWidget(page)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

    def _make_sidebar(self) -> QWidget:
        sidebar = QFrame(objectName="sidebar")
        sidebar.setFixedWidth(238)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 18, 12, 16)
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        brand_icon = QLabel()
        brand_icon.setPixmap(application_icon().pixmap(44, 44))
        brand_row.addWidget(brand_icon)
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(0)
        brand_copy.addWidget(QLabel("航价守望", objectName="brand"))
        brand_copy.addWidget(QLabel("看见更好的出行价格", objectName="tagline"))
        brand_row.addLayout(brand_copy, 1)
        layout.addLayout(brand_row)
        layout.addSpacing(18)
        self.nav_buttons: list[QPushButton] = []
        for index, text in enumerate(("概览", "我的航程", "历史价格", "通知设置", "系统状态")):
            button = QPushButton(text, objectName="navButton", checkable=True)
            button.clicked.connect(lambda checked=False, i=index: self._switch_page(i))
            layout.addWidget(button)
            self.nav_buttons.append(button)
        self.nav_buttons[0].setChecked(True)
        layout.addStretch()
        layout.addWidget(QLabel("个人工具 · 最多监控 10 条", objectName="sidebarNote", alignment=Qt.AlignmentFlag.AlignCenter))
        layout.addWidget(QLabel(f"v{__version__}", objectName="sidebarVersion", alignment=Qt.AlignmentFlag.AlignCenter))
        return sidebar

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for position, button in enumerate(self.nav_buttons):
            button.setChecked(position == index)

    def _open_new_route(self) -> None:
        wizard = RouteWizard(self.catalog, self.controller, parent=self)
        wizard.exec()

    def _open_results(self, route: LegConfig) -> None:
        self.flight_results.show_route(route)
        self._switch_page(5)

    def begin_first_route(self) -> None:
        self._switch_page(1)
        self._open_new_route()

    def show_browser_settings(self, message: str | None = None) -> None:
        self._switch_page(4)
        if message:
            self.system.set_browser_warning(message)

    def _run_now(self) -> None:
        if self.on_run_now is None:
            QMessageBox.information(self, "准备中", "监控服务正在初始化，请稍后重试。")
            return
        accepted = self.on_run_now()
        if accepted is False:
            return
        self.dashboard.set_running(True)
        self.dashboard.set_runtime_message("已请求立即查询；航程会在一个隔离浏览器中严格串行执行。")

    def _toggle_pause(self) -> None:
        callback = self.on_resume if self._paused else self.on_pause
        if callback is None:
            return
        callback()

    def _retry_leg(self, leg_id: str) -> None:
        if self.on_retry_leg is None:
            return
        accepted = self.on_retry_leg(leg_id)
        if accepted is not False:
            self.dashboard.set_running(True)
            self.system.clear_attention()

    def open_latest_report(self) -> None:
        report = self.latest_report_path
        if report is None and self.outputs_dir and self.outputs_dir.is_dir():
            candidates = sorted(self.outputs_dir.glob("airfare-monitor_*.xlsx"), key=lambda item: item.stat().st_mtime)
            report = candidates[-1] if candidates else None
        if report is None or not report.is_file():
            QMessageBox.information(self, "暂无报告", "完成至少一轮查询后即可打开最新 Excel 报告。")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(report.resolve())))

    def refresh_routes(self, routes: list[LegConfig]) -> None:
        self.dashboard.refresh(routes)
        self.routes_page.refresh(routes)
        self.history.refresh(routes)
        self.system.set_route_count(routes)

    def refresh_from_history(self) -> None:
        if self.history_store is None:
            return
        routes = self.controller.current_routes()
        rows = self.history_store.latest_leg_results([route.id for route in routes])
        self._latest_prices.clear()
        for row in rows:
            leg_id = str(row["leg_id"])
            price = _optional_decimal(row.get("minimum_total_price_cny"))
            if price is not None:
                self._latest_prices[leg_id] = price
            self.routes_page.set_leg_status(leg_id, _stored_result_status(row))
        latest_run = self.history_store.latest_run()
        self.dashboard.set_data(load_dashboard_data(self.history_store, routes))
        self.history.reload()
        if latest_run:
            finished = str(latest_run["finished_at"]).replace("T", " ")
            self.dashboard.set_runtime("最近完成", f"最近一轮：{finished} · {latest_run['status']}")
            self.statusBar().showMessage(f"最近一轮：{finished} · {latest_run['status']}")

    def handle_monitor_event(self, event: object) -> None:
        if isinstance(event, CoordinatorStateChanged):
            labels = {
                "IDLE": "空闲",
                "RUNNING": "正在查询",
                "PAUSED": "已暂停",
                "ATTENTION": "需要人工处理",
                "ERROR": "运行异常",
                "EXITING": "正在退出",
            }
            title = labels.get(event.state, event.state)
            self._paused = event.state == "PAUSED"
            self.monitor_paused_changed.emit(event.state == "PAUSED")
            self.dashboard.set_paused(self._paused)
            self.dashboard.set_running(event.state == "RUNNING")
            self._set_runtime(title, event.message)
        elif isinstance(event, CycleStarted):
            self.routes_page.mark_enabled_queued()
            self.dashboard.set_running(True)
            self._set_runtime("正在查询", f"本轮共 {event.total_legs} 条航程，浏览器将严格串行执行。")
        elif isinstance(event, LegStarted):
            route = f"{event.leg.origin_airport_iata} → {event.leg.destination_airport_iata}"
            self.routes_page.set_leg_status(event.leg.id, f"查询中 {event.index}/{event.total}")
            self._set_runtime("正在查询", f"{event.index}/{event.total} · {route}")
        elif isinstance(event, LegFinished):
            status = event.result.status.value
            if status == "success" and event.minimum_total_cny is not None:
                self._latest_prices[event.result.leg.id] = event.minimum_total_cny
                route_status = f"完成 · {_price_text(event.minimum_total_cny)}"
            elif status == "success":
                self._latest_prices.pop(event.result.leg.id, None)
                route_status = "完成 · 无符合条件航班"
            elif status == "manual_attention":
                route_status = "需要人工处理"
            else:
                route_status = "查询失败"
            self.routes_page.set_leg_status(event.result.leg.id, route_status)
            self.statusBar().showMessage(f"已完成 {event.index}/{event.total}：{route_status}")
        elif isinstance(event, CycleFinished):
            succeeded = sum(result.status.value == "success" for result in event.report.legs)
            total = event.total_legs or len(event.report.legs)
            workbook_name = Path(event.workbook_path).name
            self.latest_report_path = Path(event.workbook_path)
            self.refresh_from_history()
            self.dashboard.set_running(False)
            self._set_runtime(
                "本轮完成",
                f"成功 {succeeded}/{total} · 报告：{workbook_name}",
            )
        elif isinstance(event, NextRunScheduled):
            due = event.due_at.strftime("%m-%d %H:%M")
            self.dashboard.set_next_run(event.due_at)
            self._set_runtime("等待下轮", f"下次自动查询：{due}")
        elif isinstance(event, ManualAttentionRequested):
            self.routes_page.set_leg_status(event.leg_id, "需要人工处理")
            self.system.set_attention(event.leg_id, event.message)
            self._set_runtime("需要人工处理", event.message)
        elif isinstance(event, FatalError):
            self.dashboard.set_running(False)
            self._set_runtime("运行异常", f"{event.category}：{event.user_message}")

    def _set_runtime(self, title: str, detail: str) -> None:
        self.dashboard.set_runtime(title, detail)
        self.system.set_runtime(title, detail)
        self.statusBar().showMessage(f"{title} · {detail}")
        self.runtime_status_changed.emit(title)

    def activate(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self.isVisible():
            event.accept()
            return
        settings = self.preferences.load()
        if not settings.close_to_tray_confirmed:
            QMessageBox.information(
                self,
                "航价守望仍会继续运行",
                "关闭窗口后应用会收至系统托盘，监控不会停止。\n"
                "如需完全退出，请使用托盘菜单中的“退出并停止监控”。",
            )
            try:
                self.preferences.repository.save_desktop(
                    replace(settings, close_to_tray_confirmed=True)
                )
            except (OSError, ValueError):
                pass
        event.accept()


class RoutesPage(QWidget):
    def __init__(
        self,
        controller: DesktopController,
        catalog: AirportCatalog,
        *,
        open_results: Callable[[LegConfig], None] | None = None,
    ):
        super().__init__()
        self.controller = controller
        self.catalog = catalog
        self.open_results = open_results
        self.routes: list[LegConfig] = []
        self._runtime_status: dict[str, str] = {}
        self.cards: list[QFrame] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(14)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        heading.addWidget(QLabel("我的航程", objectName="pageTitle"))
        heading.addWidget(QLabel("管理需要持续关注的航程；复制的航程默认暂停。", objectName="muted"))
        header.addLayout(heading)
        header.addStretch()
        add = QPushButton("添加航程", objectName="primary")
        add.clicked.connect(self._new)
        header.addWidget(add)
        layout.addLayout(header)

        capacity_row = QHBoxLayout()
        self.capacity = QLabel(objectName="capacityText")
        capacity_row.addWidget(self.capacity)
        self.capacity_bar = QProgressBar()
        self.capacity_bar.setObjectName("capacityBar")
        self.capacity_bar.setRange(0, MAX_ENABLED_LEGS)
        self.capacity_bar.setTextVisible(False)
        self.capacity_bar.setMaximumWidth(360)
        capacity_row.addWidget(self.capacity_bar, 1)
        capacity_row.addStretch()
        layout.addLayout(capacity_row)

        self.empty_label = QLabel(
            "还没有航程。点击右上角“添加航程”，两分钟内即可开始监控。",
            objectName="emptyState",
            alignment=Qt.AlignmentFlag.AlignCenter,
            wordWrap=True,
        )
        self.scroll = QScrollArea()
        self.scroll.setObjectName("routeScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_host = QWidget()
        self.cards_grid = QGridLayout(self.cards_host)
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setHorizontalSpacing(14)
        self.cards_grid.setVerticalSpacing(14)
        self.cards_grid.setColumnStretch(0, 1)
        self.cards_grid.setColumnStretch(1, 1)
        self.cards_grid.setRowStretch(99, 1)
        self.scroll.setWidget(self.cards_host)
        layout.addWidget(self.empty_label, 1)
        layout.addWidget(self.scroll, 1)

    def refresh(self, routes: list[LegConfig]) -> None:
        self.routes = routes
        enabled = sum(route.enabled for route in routes)
        self.capacity.setText(f"已启用 {enabled} / {MAX_ENABLED_LEGS} 个航程")
        self.capacity_bar.setValue(enabled)
        for card in self.cards:
            self.cards_grid.removeWidget(card)
            card.deleteLater()
        self.cards.clear()
        self.empty_label.setVisible(not routes)
        self.scroll.setVisible(bool(routes))
        for index, route in enumerate(routes):
            card = self._route_card(route)
            self.cards.append(card)
            self.cards_grid.addWidget(card, index // 2, index % 2)

    def _route_card(self, route: LegConfig) -> QFrame:
        card = QFrame(objectName="routeCard")
        card.setMinimumHeight(300)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 17, 20, 15)
        layout.setSpacing(11)

        top = QHBoxLayout()
        state = QLabel("运行中" if route.enabled else "已暂停")
        state.setObjectName("activePill" if route.enabled else "pausedPill")
        top.addWidget(state)
        top.addStretch()
        toggle = QPushButton("暂停" if route.enabled else "启用", objectName="routeToggle")
        toggle.clicked.connect(lambda checked=False, item=route: self._toggle(item))
        top.addWidget(toggle)
        layout.addLayout(top)

        route_row = QHBoxLayout()
        route_row.addLayout(_airport_block(route.origin_name_zh, route.origin_airport_iata))
        route_row.addStretch()
        route_row.addWidget(QLabel("✈  →" if not route.return_date else "✈  ⇄", objectName="routeArrow"))
        route_row.addStretch()
        route_row.addLayout(_airport_block(route.destination_name_zh, route.destination_airport_iata))
        layout.addLayout(route_row)

        tags = QHBoxLayout()
        source = "国内 · 同程" if _market_name(route) == "domestic" else "国际/跨境 · 去哪儿"
        source_label = QLabel(source, objectName="sourcePill")
        trip_label = QLabel("往返" if route.return_date else "单程", objectName="neutralPill")
        tags.addWidget(source_label)
        tags.addWidget(trip_label)
        tags.addStretch()
        layout.addLayout(tags)

        summary = QFrame(objectName="routeSummary")
        details = QGridLayout(summary)
        details.setContentsMargins(13, 11, 13, 11)
        details.setHorizontalSpacing(14)
        details.setVerticalSpacing(7)
        date_text = route.departure_date.isoformat()
        if route.return_date:
            date_text += f" — {route.return_date.isoformat()}"
        time_text = f"{route.etd_window.start.strftime('%H:%M')} — {route.etd_window.end.strftime('%H:%M')}"
        preference = "仅直达" if route.direct_only else f"允许中转 · 最长 {route.max_layover_minutes or 0} 分钟"
        threshold = _price_text(route.expected_total_price_cny) if route.expected_total_price_cny is not None else "仅观察"
        _add_detail(details, 0, 0, "出行日期", date_text)
        _add_detail(details, 1, 0, "出发时段", time_text)
        _add_detail(details, 0, 1, "行程偏好", preference)
        _add_detail(details, 1, 1, "心理价位", threshold)
        layout.addWidget(summary)

        latest = self._runtime_status.get(route.id, "等待首次查询" if route.enabled else "监控已暂停")
        latest_row = QHBoxLayout()
        latest_row.addWidget(QLabel("最近状态", objectName="muted"))
        latest_value = QLabel(latest, objectName="routeLatest")
        latest_row.addWidget(latest_value)
        latest_row.addStretch()
        layout.addLayout(latest_row)

        footer = QHBoxLayout()
        edit = QPushButton("编辑", objectName="routeAction")
        edit.clicked.connect(lambda checked=False, item=route: self._edit(item))
        copy = QPushButton("复制", objectName="routeAction")
        copy.clicked.connect(lambda checked=False, item=route: self._copy(item))
        delete = QPushButton("删除", objectName="dangerAction")
        delete.clicked.connect(lambda checked=False, item=route: self._delete(item))
        footer.addWidget(edit)
        footer.addWidget(copy)
        footer.addStretch()
        footer.addWidget(delete)
        results = QPushButton("查看候选", objectName="routeResultAction")
        results.setEnabled(self.open_results is not None)
        if self.open_results is not None:
            results.clicked.connect(lambda checked=False, item=route: self.open_results(item))
        footer.addWidget(results)
        layout.addLayout(footer)
        return card

    def set_leg_status(self, leg_id: str, status: str) -> None:
        self._runtime_status[leg_id] = status
        self.refresh(self.routes)

    def mark_enabled_queued(self) -> None:
        for route in self.routes:
            if route.enabled:
                self._runtime_status[route.id] = "排队中"
        self.refresh(self.routes)

    def _new(self) -> None:
        RouteWizard(self.catalog, self.controller, parent=self).exec()

    def _edit(self, route: LegConfig) -> None:
        RouteWizard(self.catalog, self.controller, route=route, parent=self).exec()

    def _toggle(self, route: LegConfig) -> None:
        try:
            self.controller.toggle_route(route.id, not route.enabled)
        except ValueError as exc:
            QMessageBox.warning(self, "无法启用", str(exc))

    def _copy(self, route: LegConfig) -> None:
        copied = _copy_paused(route)
        self.controller.save_route(copied)

    def _delete(self, route: LegConfig) -> None:
        answer = QMessageBox.question(self, "删除航程", "只删除监控配置，不会删除已有历史记录。确定删除吗？")
        if answer == QMessageBox.StandardButton.Yes:
            self.controller.delete_route(route.id)


class PlaceholderPage(QWidget):
    def __init__(self, title: str, description: str):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 30)
        layout.addWidget(QLabel(title, objectName="pageTitle"))
        card = QFrame(objectName="card")
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(QLabel(description, wordWrap=True))
        layout.addWidget(card)
        layout.addStretch()


class SystemStatusPage(QWidget):
    settings_saved = Signal(object)

    def __init__(
        self,
        browsers: list[BrowserCandidate],
        preferences: PreferencesManager,
        *,
        history_store: SQLiteStore | None = None,
        outputs_dir: Path | None = None,
        retry_leg: Callable[[str], None] | None = None,
    ):
        super().__init__()
        self.preferences = preferences
        self.history_store = history_store
        self.outputs_dir = outputs_dir
        self.retry_leg = retry_leg
        self._attention_leg_id: str | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 30)
        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("系统状态与运行设置", objectName="pageTitle"))
        title_row.addStretch()
        title_row.addWidget(QLabel(f"v{__version__}", objectName="muted"))
        self.save_button = QPushButton("保存设置", objectName="primary")
        self.save_button.clicked.connect(self._save_settings)
        title_row.addWidget(self.save_button)
        layout.addLayout(title_row)
        layout.addWidget(QLabel("浏览器、查询间隔和通知偏好可随时调整。", objectName="muted"))
        health_row = QHBoxLayout()
        self.service_health = _health_card("监控服务", "等待启动")
        self.storage_health = _health_card(
            "数据存储",
            "正常" if history_store is not None else "不可用",
        )
        self.query_health = _health_card("航班查询", "等待首次查询")
        for card in (self.service_health, self.storage_health, self.query_health):
            health_row.addWidget(card, 1)
        layout.addLayout(health_row)
        self.routes_label = QLabel("启用航程：0 / 10")
        settings = self.preferences.load()
        self.form = RuntimePreferencesForm(browsers, settings)
        self.form.redetect_requested.connect(self._redetect)
        layout.addWidget(
            preference_card(
                "浏览器与自动查询",
                "设置会保存到当前 Windows 用户目录；浏览器显示方式从下一轮查询开始生效。",
                self.form,
            )
        )
        self.browser_warning = QLabel(objectName="warningText", wordWrap=True)
        self.browser_warning.hide()
        layout.addWidget(self.browser_warning)

        profile = QFrame(objectName="infoCard")
        profile_layout = QVBoxLayout(profile)
        profile_layout.addWidget(QLabel("独立浏览器空间", objectName="sectionTitle"))
        profile_layout.addWidget(
            QLabel(
                "航价守望使用自己的浏览器 Profile，不会读取或修改你日常 Chrome/Edge 的收藏、Cookie 和登录状态。",
                objectName="muted",
                wordWrap=True,
            )
        )
        layout.addWidget(profile)

        self.attention_card = QFrame(objectName="warningCard")
        attention_layout = QHBoxLayout(self.attention_card)
        self.attention_text = QLabel(wordWrap=True)
        attention_layout.addWidget(self.attention_text, 1)
        retry = QPushButton("重新查询此航程", objectName="primary")
        retry.clicked.connect(self._retry_attention)
        later = QPushButton("稍后处理")
        later.clicked.connect(self.attention_card.hide)
        attention_layout.addWidget(later)
        attention_layout.addWidget(retry)
        self.attention_card.hide()
        layout.addWidget(self.attention_card)

        paths_card = QFrame(objectName="card")
        paths_layout = QVBoxLayout(paths_card)
        paths_layout.addWidget(QLabel("本地数据位置", objectName="sectionTitle"))
        profile_path = self.preferences.repository.load_core().browser.user_data_path
        paths_layout.addWidget(QLabel(f"独立 Profile：{profile_path}", objectName="muted", wordWrap=True))
        if history_store is not None:
            paths_layout.addWidget(QLabel(f"价格数据库：{history_store.path}", objectName="muted", wordWrap=True))
        if outputs_dir is not None:
            open_outputs = QPushButton("打开报告目录")
            open_outputs.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(outputs_dir.resolve())))
            )
            paths_layout.addWidget(open_outputs, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(paths_card)
        layout.addWidget(self.routes_label)
        self.runtime_label = QLabel("运行状态：等待启动", objectName="muted")
        layout.addWidget(self.runtime_label)
        layout.addStretch()

    def _redetect(self) -> None:
        browsers = self.preferences.refresh_browsers()
        current = self.preferences.load()
        self.form.set_browsers(
            browsers,
            preferred_path=current.browser_path,
            preferred_kind=current.browser_kind,
        )
        if browsers:
            self.browser_warning.hide()

    def _save_settings(self) -> None:
        try:
            saved = self.preferences.save(self.form.values(onboarding_completed=True))
        except Exception as exc:
            QMessageBox.warning(self, "设置未保存", str(exc))
            return
        self.form.load(saved)
        self.browser_warning.hide()
        self.settings_saved.emit(saved)
        QMessageBox.information(self, "设置已保存", "新的运行设置将从下一轮查询开始生效。")

    def set_browser_warning(self, message: str) -> None:
        self.browser_warning.setText(message)
        self.browser_warning.show()

    def set_route_count(self, routes: list[LegConfig]) -> None:
        self.routes_label.setText(f"启用航程：{sum(route.enabled for route in routes)} / {MAX_ENABLED_LEGS}")

    def set_runtime(self, title: str, detail: str) -> None:
        self.runtime_label.setText(f"运行状态：{title}\n{detail}")
        _set_health(self.service_health, title)
        if title in {"本轮完成", "等待下轮", "最近完成"}:
            _set_health(self.query_health, "最近查询正常")
        elif title in {"运行异常", "需要人工处理"}:
            _set_health(self.query_health, title)

    def set_attention(self, leg_id: str, message: str) -> None:
        self._attention_leg_id = leg_id
        self.attention_text.setText(
            f"航程 {leg_id} 需要人工处理。{message}\n"
            "如当前为隐藏模式，请先开启“显示浏览器运行过程”，保存后再重新查询。"
        )
        self.attention_card.show()

    def clear_attention(self) -> None:
        self._attention_leg_id = None
        self.attention_card.hide()

    def _retry_attention(self) -> None:
        if self._attention_leg_id and self.retry_leg:
            self.retry_leg(self._attention_leg_id)


def _airport_block(name: str | None, iata: str) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(0)
    layout.addWidget(QLabel(name or iata, objectName="routeCity"))
    layout.addWidget(QLabel(iata, objectName="routeCode"))
    return layout


def _add_detail(layout: QGridLayout, row: int, column: int, title: str, value: str) -> None:
    block = QVBoxLayout()
    block.setSpacing(2)
    block.addWidget(QLabel(title, objectName="detailLabel"))
    block.addWidget(QLabel(value, objectName="detailValue"))
    layout.addLayout(block, row, column)


def _health_card(title: str, value: str) -> QFrame:
    card = QFrame(objectName="healthCard")
    layout = QVBoxLayout(card)
    layout.addWidget(QLabel(title, objectName="muted"))
    layout.addWidget(QLabel(value, objectName="healthValue"))
    return card


def _set_health(card: QFrame, value: str) -> None:
    card.findChild(QLabel, "healthValue").setText(value)


def _market_name(route: LegConfig) -> str:
    from ..market import resolve_market
    try:
        return resolve_market(route)
    except ValueError:
        return route.market


def _copy_paused(route: LegConfig) -> LegConfig:
    from dataclasses import replace
    import uuid
    return replace(route, id=f"route-{uuid.uuid4().hex[:8]}", enabled=False)


def _price_text(value: Decimal) -> str:
    return f"¥{value:,.0f}"


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _stored_result_status(row: dict[str, object]) -> str:
    status = str(row.get("status", ""))
    price = _optional_decimal(row.get("minimum_total_price_cny"))
    if status == "success" and price is not None:
        return f"最近完成 · {_price_text(price)}"
    if status == "success":
        return "最近完成 · 无符合条件航班"
    if status == "manual_attention":
        return "需要人工处理"
    return "最近查询失败"
