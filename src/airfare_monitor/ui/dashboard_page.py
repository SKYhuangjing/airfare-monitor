"""Daily overview page driven by persisted prices and coordinator state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..config import MAX_ENABLED_LEGS
from ..desktop_app.view_data import DashboardData
from ..market import resolve_market
from ..models import LegConfig


class DashboardPage(QWidget):
    def __init__(
        self,
        open_new_route: Callable[[], None],
        run_now: Callable[[], None],
        toggle_pause: Callable[[], None],
        open_routes: Callable[[], None],
    ):
        super().__init__()
        self._routes: list[LegConfig] = []
        self._data: DashboardData | None = None
        self._runtime_title = "等待配置"
        self._runtime_detail = "设置航程后可启动监控"
        self._next_run: datetime | None = None
        self._paused = False
        self._running = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(14)
        title_row = QHBoxLayout()
        heading = QVBoxLayout()
        heading.addWidget(QLabel("航价概览", objectName="pageTitle"))
        self.runtime_label = QLabel("等待配置", objectName="runtimeStatus")
        heading.addWidget(self.runtime_label)
        title_row.addLayout(heading)
        title_row.addStretch()
        self.pause_button = QPushButton("暂停监控")
        self.pause_button.clicked.connect(toggle_pause)
        self.run_button = QPushButton("立即查询")
        self.run_button.clicked.connect(run_now)
        add = QPushButton("添加航程", objectName="primary")
        add.clicked.connect(open_new_route)
        title_row.addWidget(self.pause_button)
        title_row.addWidget(self.run_button)
        title_row.addWidget(add)
        layout.addLayout(title_row)

        metrics = QHBoxLayout()
        self.today_card = _metric_card("今日最低价", "暂无数据", "启用航程的 CNY 含税总价")
        self.count_card = _metric_card("已启用航程", "0 / 10", "严格使用单浏览器串行查询")
        self.success_card = _metric_card("最近成功", "暂无记录", "尚未取得有效完整结果")
        self.attention_card = _metric_card("需要处理", "0", "当前没有待处理航程")
        for card in (self.today_card, self.count_card, self.success_card, self.attention_card):
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)

        route_header = QHBoxLayout()
        route_header.addWidget(QLabel("航程监控", objectName="sectionTitle"))
        route_header.addStretch()
        all_routes = QPushButton("查看全部航程")
        all_routes.clicked.connect(open_routes)
        route_header.addWidget(all_routes)
        layout.addLayout(route_header)
        self.route_table = QTableWidget(0, 7)
        self.route_table.setHorizontalHeaderLabels(
            ["航程", "日期与来源", "筛选", "最新含税价", "变化/心理价位", "状态", "更新时间"]
        )
        self.route_table.horizontalHeader().setStretchLastSection(True)
        self.route_table.verticalHeader().setVisible(False)
        self.route_table.verticalHeader().setDefaultSectionSize(50)
        self.route_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.route_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.route_table.setMinimumHeight(190)
        layout.addWidget(self.route_table, 1)

        layout.addWidget(QLabel("最近动态", objectName="sectionTitle"))
        self.event_table = QTableWidget(0, 2)
        self.event_table.setHorizontalHeaderLabels(["时间", "动态"])
        self.event_table.horizontalHeader().setStretchLastSection(True)
        self.event_table.verticalHeader().setVisible(False)
        self.event_table.verticalHeader().setDefaultSectionSize(36)
        self.event_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.event_table.setMaximumHeight(170)
        layout.addWidget(self.event_table)
        self._sync_controls()

    def refresh(self, routes: list[LegConfig]) -> None:
        self._routes = list(routes)
        enabled = sum(route.enabled for route in routes)
        _set_metric(self.count_card, f"{enabled} / {MAX_ENABLED_LEGS}", f"另有 {len(routes) - enabled} 条已暂停")
        self._render_routes()
        self._sync_controls()

    def set_data(self, data: DashboardData) -> None:
        self._data = data
        _set_metric(
            self.today_card,
            _price_text(data.today_minimum_cny) if data.today_minimum_cny is not None else "暂无数据",
            "今日已完成查询中的最低 CNY 含税总价",
        )
        _set_metric(
            self.success_card,
            _friendly_time(data.latest_success_at) if data.latest_success_at else "暂无记录",
            (
                f"上一轮耗时 {data.latest_run_duration_seconds} 秒"
                if data.latest_run_duration_seconds is not None
                else "尚未取得有效完整结果"
            ),
        )
        _set_metric(
            self.attention_card,
            str(data.attention_count),
            "请前往系统状态处理" if data.attention_count else "当前没有待处理航程",
        )
        self._render_routes()
        self._render_events()

    def set_runtime(self, title: str, detail: str) -> None:
        self._runtime_title = title
        self._runtime_detail = detail
        suffix = f" · 下次查询 {_friendly_time(self._next_run)}" if self._next_run else ""
        self.runtime_label.setText(f"{title} · {detail}{suffix}")

    def set_runtime_message(self, message: str) -> None:
        self.set_runtime("正在准备", message)

    def set_next_run(self, due_at: datetime | None) -> None:
        self._next_run = due_at
        self.set_runtime(self._runtime_title, self._runtime_detail)

    def set_running(self, running: bool) -> None:
        self._running = running
        self._sync_controls()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._sync_controls()

    def _sync_controls(self) -> None:
        enabled = any(route.enabled for route in self._routes)
        self.run_button.setEnabled(enabled and not self._running and not self._paused)
        self.run_button.setText("正在查询…" if self._running else "立即查询")
        self.pause_button.setEnabled(enabled or self._paused)
        self.pause_button.setText("继续监控" if self._paused else "暂停监控")

    def _render_routes(self) -> None:
        self.route_table.setRowCount(len(self._routes))
        overviews = self._data.routes if self._data else {}
        for row, route in enumerate(self._routes):
            overview = overviews.get(route.id)
            route_text = f"{route.origin_airport_iata} → {route.destination_airport_iata}"
            dates = route.departure_date.isoformat()
            if route.return_date:
                dates += f" / {route.return_date.isoformat()}"
            source = "国内 · 同程" if _market(route) == "domestic" else "国际/跨境 · 去哪儿"
            filter_text = "直达" if route.direct_only else f"中转≤{route.max_layover_minutes}分钟"
            price = _price_text(overview.minimum_total_cny) if overview and overview.minimum_total_cny is not None else "—"
            delta = _delta_text(overview.change_cny if overview else None)
            threshold = _price_text(route.expected_total_price_cny) if route.expected_total_price_cny is not None else "仅观察"
            change = f"{delta} / 心理 {threshold}"
            status = _status_text(overview.status) if overview else ("等待查询" if route.enabled else "已暂停")
            if not route.enabled:
                status = "已暂停"
            updated = _friendly_time(overview.captured_at) if overview and overview.captured_at else "—"
            values = [route_text, f"{dates}\n{source}", filter_text, price, change, status, updated]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.route_table.setItem(row, column, item)
        self.route_table.resizeColumnsToContents()

    def _render_events(self) -> None:
        events = self._data.recent_events if self._data else ()
        self.event_table.setRowCount(len(events))
        for row, event in enumerate(events):
            occurred = _parse_time(event.get("occurred_at"))
            self.event_table.setItem(row, 0, QTableWidgetItem(_friendly_time(occurred) if occurred else "—"))
            self.event_table.setItem(row, 1, QTableWidgetItem(str(event.get("message", ""))))
        self.event_table.resizeColumnToContents(0)


def _metric_card(title: str, value: str, detail: str) -> QFrame:
    card = QFrame(objectName="metricCard")
    layout = QVBoxLayout(card)
    layout.addWidget(QLabel(title, objectName="muted"))
    value_label = QLabel(value, objectName="metricValue")
    layout.addWidget(value_label)
    layout.addWidget(QLabel(detail, objectName="detail", wordWrap=True))
    return card


def _set_metric(card: QFrame, value: str, detail: str) -> None:
    card.findChild(QLabel, "metricValue").setText(value)
    card.findChild(QLabel, "detail").setText(detail)


def _price_text(value: Decimal | None) -> str:
    return "—" if value is None else f"¥{value:,.0f}"


def _delta_text(value: Decimal | None) -> str:
    if value is None:
        return "暂无对比"
    if value == 0:
        return "较上次持平"
    direction = "上涨" if value > 0 else "下降"
    return f"较上次{direction} ¥{abs(value):,.0f}"


def _friendly_time(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%m-%d %H:%M")


def _parse_time(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _status_text(status: str) -> str:
    return {
        "success": "查询成功",
        "failed": "查询失败",
        "manual_attention": "需要人工处理",
    }.get(status, status)


def _market(route: LegConfig) -> str:
    try:
        return resolve_market(route)
    except ValueError:
        return route.market
