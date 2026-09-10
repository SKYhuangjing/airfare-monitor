from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..config import MAX_ENABLED_LEGS
from ..desktop_app.airport_catalog import AirportCatalog
from ..desktop_app.browser_detector import BrowserCandidate
from ..desktop_app.controller import DesktopController
from ..desktop_app.events import (
    CoordinatorStateChanged, CycleFinished, CycleStarted, FatalError, LegFinished, LegStarted,
    ManualAttentionRequested, NextRunScheduled,
)
from ..models import LegConfig
from ..storage import SQLiteStore
from .route_wizard import RouteWizard


class MainWindow(QMainWindow):
    runtime_status_changed = Signal(str)
    monitor_paused_changed = Signal(bool)

    def __init__(
        self,
        controller: DesktopController,
        catalog: AirportCatalog,
        browsers: list[BrowserCandidate],
        on_run_now: Callable[[], bool | None] | None = None,
        history_store: SQLiteStore | None = None,
        outputs_dir: Path | None = None,
    ):
        super().__init__()
        self.controller = controller
        self.catalog = catalog
        self.browsers = browsers
        self.history_store = history_store
        self.outputs_dir = outputs_dir
        self.latest_report_path: Path | None = None
        self._latest_prices: dict[str, Decimal] = {}
        self.setWindowTitle("航价守望")
        self.setMinimumSize(1050, 700)
        self.resize(1250, 800)
        self.on_run_now = on_run_now
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
        self.dashboard = DashboardPage(self._open_new_route, self._run_now)
        self.routes_page = RoutesPage(self.controller, self.catalog)
        self.history = PlaceholderPage("价格历史", "完成查询后，这里将展示按航程查看的含税最低价趋势和 Excel 报告。")
        self.notifications = PlaceholderPage("通知设置", "桌面通知与 SMTP 设置将在下一阶段接入 Windows 凭据安全存储。")
        self.system = SystemStatusPage(self.browsers)
        for page in (self.dashboard, self.routes_page, self.history, self.notifications, self.system):
            self.pages.addWidget(page)
        layout.addWidget(self.pages, 1)
        self.setCentralWidget(root)

    def _make_sidebar(self) -> QWidget:
        sidebar = QFrame(objectName="sidebar")
        sidebar.setFixedWidth(215)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 14)
        brand = QLabel("✈  航价守望", objectName="brand")
        tag = QLabel("看见更好的出行价格", objectName="tagline")
        layout.addWidget(brand)
        layout.addWidget(tag)
        self.nav_buttons: list[QPushButton] = []
        for index, text in enumerate(("首页", "航程管理", "价格历史", "降价提醒", "系统状态")):
            button = QPushButton(text, objectName="navButton", checkable=True)
            button.clicked.connect(lambda checked=False, i=index: self._switch_page(i))
            layout.addWidget(button)
            self.nav_buttons.append(button)
        self.nav_buttons[0].setChecked(True)
        layout.addStretch()
        layout.addWidget(QLabel("P0 内部试用版", objectName="muted", alignment=Qt.AlignmentFlag.AlignCenter))
        return sidebar

    def _switch_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for position, button in enumerate(self.nav_buttons):
            button.setChecked(position == index)

    def _open_new_route(self) -> None:
        wizard = RouteWizard(self.catalog, self.controller, parent=self)
        wizard.exec()

    def _run_now(self) -> None:
        if self.on_run_now is None:
            QMessageBox.information(self, "准备中", "监控服务正在初始化，请稍后重试。")
            return
        accepted = self.on_run_now()
        if accepted is False:
            return
        self.dashboard.set_runtime_message("已请求立即查询；航程会在一个隔离浏览器中严格串行执行。")

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
        if rows:
            self.dashboard.set_latest_prices(self._latest_prices.values())
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
            self.monitor_paused_changed.emit(event.state == "PAUSED")
            self._set_runtime(title, event.message)
        elif isinstance(event, CycleStarted):
            self.routes_page.mark_enabled_queued()
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
            self.dashboard.set_latest_prices(self._latest_prices.values())
            self.statusBar().showMessage(f"已完成 {event.index}/{event.total}：{route_status}")
        elif isinstance(event, CycleFinished):
            succeeded = sum(result.status.value == "success" for result in event.report.legs)
            workbook_name = Path(event.workbook_path).name
            self.latest_report_path = Path(event.workbook_path)
            self.refresh_from_history()
            self._set_runtime(
                "本轮完成",
                f"成功 {succeeded}/{len(event.report.legs)} · 报告：{workbook_name}",
            )
        elif isinstance(event, NextRunScheduled):
            due = event.due_at.strftime("%m-%d %H:%M")
            self._set_runtime("等待下轮", f"下次自动查询：{due}")
        elif isinstance(event, ManualAttentionRequested):
            self.routes_page.set_leg_status(event.leg_id, "需要人工处理")
            self._set_runtime("需要人工处理", event.message)
        elif isinstance(event, FatalError):
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


class DashboardPage(QWidget):
    def __init__(self, open_new_route: Callable[[], None], run_now: Callable[[], None]):
        super().__init__()
        self.open_new_route = open_new_route
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 30)
        title_row = QHBoxLayout()
        title = QLabel("你好，旅行家", objectName="pageTitle")
        add = QPushButton("添加航程", objectName="primary")
        add.clicked.connect(open_new_route)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(add)
        layout.addLayout(title_row)
        layout.addWidget(QLabel("关注航价变化，出发更从容", objectName="muted"))
        run = QPushButton("立即查询")
        run.clicked.connect(run_now)
        layout.addWidget(run, alignment=Qt.AlignmentFlag.AlignLeft)
        cards = QHBoxLayout()
        self.count_card = _metric_card("启用航程", "0 / 10", "每台设备严格串行运行")
        self.price_card = _metric_card("最新含税最低价", "暂无数据", "完成首次查询后显示 CNY 总价")
        self.status_card = _metric_card("运行状态", "等待配置", "设置航程后可启动监控")
        for card in (self.count_card, self.price_card, self.status_card):
            cards.addWidget(card)
        layout.addLayout(cards)
        info = QFrame(objectName="card")
        info_layout = QVBoxLayout(info)
        info_layout.addWidget(QLabel("开始使用", objectName="pageTitle"))
        info_layout.addWidget(QLabel("1. 添加出发地、目的地和日期\n2. 设置含税心理价位和监控偏好\n3. 由应用自动选择同程或去哪儿来源"))
        layout.addWidget(info)
        layout.addStretch()

    def refresh(self, routes: list[LegConfig]) -> None:
        enabled = sum(route.enabled for route in routes)
        self.count_card.findChild(QLabel, "value").setText(f"{enabled} / {MAX_ENABLED_LEGS}")
        current = self.status_card.findChild(QLabel, "value").text()
        if not enabled:
            self.status_card.findChild(QLabel, "value").setText("等待配置")
        elif current == "等待配置":
            self.status_card.findChild(QLabel, "value").setText("等待首次查询")

    def set_runtime_message(self, message: str) -> None:
        self.set_runtime("正在准备", message)

    def set_runtime(self, title: str, detail: str) -> None:
        self.status_card.findChild(QLabel, "value").setText(title)
        self.status_card.findChild(QLabel, "detail").setText(detail)

    def set_latest_prices(self, prices: object) -> None:
        values = list(prices)
        if not values:
            self.price_card.findChild(QLabel, "value").setText("暂无符合条件价格")
            self.price_card.findChild(QLabel, "detail").setText("查询成功但没有航班符合当前机场、日期和筛选条件")
            return
        minimum = min(values)
        self.price_card.findChild(QLabel, "value").setText(_price_text(minimum))
        self.price_card.findChild(QLabel, "detail").setText("当前启用航程最近一次 CNY 含税总价")


class RoutesPage(QWidget):
    def __init__(self, controller: DesktopController, catalog: AirportCatalog):
        super().__init__()
        self.controller = controller
        self.catalog = catalog
        self.routes: list[LegConfig] = []
        self._runtime_status: dict[str, str] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 30)
        row = QHBoxLayout()
        row.addWidget(QLabel("航程管理", objectName="pageTitle"))
        row.addStretch()
        self.capacity = QLabel(objectName="muted")
        row.addWidget(self.capacity)
        add = QPushButton("添加航程", objectName="primary")
        add.clicked.connect(self._new)
        row.addWidget(add)
        layout.addLayout(row)
        layout.addWidget(QLabel("最多同时启用 10 条航程；暂停的航程会保留配置与历史。", objectName="muted"))
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["航程", "日期", "来源", "偏好", "状态", "操作", ""])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

    def refresh(self, routes: list[LegConfig]) -> None:
        self.routes = routes
        enabled = sum(route.enabled for route in routes)
        self.capacity.setText(f"已启用 {enabled} / {MAX_ENABLED_LEGS}")
        self.table.setRowCount(len(routes))
        for row, route in enumerate(routes):
            source = "同程（国内）" if _market_name(route) == "domestic" else "去哪儿（国际/跨境）"
            values = [
                f"{route.origin_name_zh or route.origin_airport_iata} {route.origin_airport_iata} → {route.destination_name_zh or route.destination_airport_iata} {route.destination_airport_iata}",
                route.departure_date.isoformat() + (f" / {route.return_date.isoformat()}" if route.return_date else ""),
                source,
                "直达" if route.direct_only else f"中转 ≤ {route.max_layover_minutes} 分钟",
                self._runtime_status.get(route.id, "启用" if route.enabled else "暂停"),
            ]
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))
            self.table.setCellWidget(row, 5, _actions(
                ("编辑", lambda checked=False, item=route: self._edit(item)),
                ("暂停" if route.enabled else "启用", lambda checked=False, item=route: self._toggle(item)),
                ("复制", lambda checked=False, item=route: self._copy(item)),
                ("删除", lambda checked=False, item=route: self._delete(item)),
            ))
        self.table.resizeColumnsToContents()

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
    def __init__(self, browsers: list[BrowserCandidate]):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 30, 34, 30)
        layout.addWidget(QLabel("系统状态", objectName="pageTitle"))
        self.routes_label = QLabel("启用航程：0 / 10")
        browser_text = "\n".join(
            f"{candidate.kind.title()} · {candidate.path}" + (f" · {candidate.version}" if candidate.version else "")
            for candidate in browsers
        ) or "未检测到 Chrome 或 Edge；安装后重新打开应用即可检测。"
        for heading, text in (
            ("浏览器", browser_text),
            ("隔离 Profile", "将在首次运行时创建于当前用户应用数据目录。"),
            ("运行说明", "浏览器始终使用独立 Profile；不会复用日常浏览器数据。"),
        ):
            card = QFrame(objectName="card")
            card_layout = QVBoxLayout(card)
            card_layout.addWidget(QLabel(heading))
            card_layout.addWidget(QLabel(text, objectName="muted", wordWrap=True))
            layout.addWidget(card)
        layout.addWidget(self.routes_label)
        self.runtime_label = QLabel("运行状态：等待启动", objectName="muted")
        layout.addWidget(self.runtime_label)
        layout.addStretch()

    def set_route_count(self, routes: list[LegConfig]) -> None:
        self.routes_label.setText(f"启用航程：{sum(route.enabled for route in routes)} / {MAX_ENABLED_LEGS}")

    def set_runtime(self, title: str, detail: str) -> None:
        self.runtime_label.setText(f"运行状态：{title}\n{detail}")


def _metric_card(title: str, value: str, detail: str) -> QFrame:
    card = QFrame(objectName="card")
    layout = QVBoxLayout(card)
    layout.addWidget(QLabel(title, objectName="muted"))
    label = QLabel(value, objectName="value")
    label.setStyleSheet("font-size: 25px; font-weight: 700;")
    layout.addWidget(label)
    layout.addWidget(QLabel(detail, objectName="detail", wordWrap=True))
    return card


def _actions(*actions: tuple[str, Callable[[], None]]) -> QWidget:
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(4, 2, 4, 2)
    for label, callback in actions:
        button = QPushButton(label)
        button.setMaximumWidth(52)
        button.clicked.connect(callback)
        layout.addWidget(button)
    layout.addStretch()
    return widget


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
