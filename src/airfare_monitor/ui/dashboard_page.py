"""Daily overview page driven by persisted prices and coordinator state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
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
        open_results: Callable[[LegConfig], None],
    ):
        super().__init__()
        self._routes: list[LegConfig] = []
        self._data: DashboardData | None = None
        self._runtime_title = "等待配置"
        self._runtime_detail = "设置航程后可启动监控"
        self._next_run: datetime | None = None
        self._paused = False
        self._running = False
        self._open_results = open_results

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(30, 28, 30, 28)
        layout.setSpacing(20)
        title_row = QHBoxLayout()
        heading = QVBoxLayout()
        heading.addWidget(QLabel(_greeting(), objectName="pageTitle"))
        heading.addWidget(QLabel("航价守望与您一起，发现更好的出行时机。", objectName="muted"))
        heading.setSpacing(4)
        title_row.addLayout(heading)
        title_row.addStretch()
        self.runtime_label = QLabel("●  等待配置", objectName="runtimeStatus")
        self.runtime_label.setMaximumWidth(150)
        title_row.addWidget(self.runtime_label)
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
        self.runtime_detail_label = QLabel("设置航程后可启动监控", objectName="muted", wordWrap=True)
        layout.addWidget(self.runtime_detail_label)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.today_card = _metric_card("◆", "今日最低价", "暂无数据", "启用航程的 CNY 含税总价", "green")
        self.count_card = _metric_card("✈", "已启用航程", "0 / 10", "严格使用单浏览器串行查询", "blue")
        self.success_card = _metric_card("◷", "最近成功", "暂无记录", "尚未取得有效完整结果", "violet")
        self.attention_card = _metric_card("!", "需要处理", "0", "当前没有待处理航程", "amber")
        for card in (self.today_card, self.count_card, self.success_card, self.attention_card):
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)

        body = QHBoxLayout()
        body.setSpacing(16)
        routes_section = QVBoxLayout()
        route_header = QHBoxLayout()
        route_header.addWidget(QLabel("航程监控", objectName="sectionTitle"))
        route_header.addStretch()
        all_routes = QPushButton("查看全部航程  →", objectName="linkButton")
        all_routes.clicked.connect(open_routes)
        route_header.addWidget(all_routes)
        routes_section.addLayout(route_header)
        routes_section.addWidget(QLabel("关注含税价格，也能随时查看本轮全部航班候选。", objectName="muted"))
        self.route_cards = QVBoxLayout()
        self.route_cards.setSpacing(12)
        routes_section.addLayout(self.route_cards)
        routes_section.addStretch()
        body.addLayout(routes_section, 3)

        events_section = QVBoxLayout()
        events_section.addWidget(QLabel("最近动态", objectName="sectionTitle"))
        events_section.addWidget(QLabel("每一次查询和需要处理的事件", objectName="muted"))
        self.event_cards = QVBoxLayout()
        self.event_cards.setSpacing(9)
        events_section.addLayout(self.event_cards)
        events_section.addStretch()
        body.addLayout(events_section, 2)
        layout.addLayout(body, 1)
        scroll.setWidget(host)
        outer.addWidget(scroll)
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
        self.runtime_label.setText(f"●  {title}")
        self.runtime_detail_label.setText(f"{detail}{suffix}")

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
        _clear_layout(self.route_cards)
        overviews = self._data.routes if self._data else {}
        if not self._routes:
            empty = QLabel("还没有航程。点击“添加航程”，开始关注你的第一段旅程。", objectName="emptyState", wordWrap=True)
            self.route_cards.addWidget(empty)
            return
        for route in self._routes[:5]:
            overview = overviews.get(route.id)
            card = QFrame(objectName="routeCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(17, 13, 17, 12)
            card_layout.setSpacing(8)
            top = QHBoxLayout()
            route_text = f"{route.origin_airport_iata}  →  {route.destination_airport_iata}"
            top.addWidget(QLabel(route_text, objectName="routeCode"))
            top.addStretch()
            status = _status_text(overview.status) if overview else ("等待查询" if route.enabled else "已暂停")
            if not route.enabled:
                status = "已暂停"
            top.addWidget(QLabel(status, objectName="activePill" if status == "查询成功" else "pausedPill"))
            card_layout.addLayout(top)
            dates = route.departure_date.isoformat()
            if route.return_date:
                dates += f" / {route.return_date.isoformat()}"
            source = "国内 · 同程" if _market(route) == "domestic" else "国际/跨境 · 去哪儿"
            card_layout.addWidget(QLabel(f"{route.origin_name_zh or route.origin_airport_iata} → {route.destination_name_zh or route.destination_airport_iata}  ·  {dates}  ·  {source}", objectName="muted", wordWrap=True))
            bottom = QHBoxLayout()
            bottom.addWidget(QLabel("最低含税总价", objectName="detailLabel"))
            price = _price_text(overview.minimum_total_cny) if overview and overview.minimum_total_cny is not None else "—"
            bottom.addWidget(QLabel(price, objectName="metricValue"))
            bottom.addStretch()
            action = QPushButton("查看航班候选  →", objectName="linkButton")
            action.clicked.connect(lambda checked=False, item=route: self._open_results(item))
            bottom.addWidget(action)
            card_layout.addLayout(bottom)
            threshold = _price_text(route.expected_total_price_cny) if route.expected_total_price_cny is not None else "仅观察"
            updated = _friendly_time(overview.captured_at) if overview and overview.captured_at else "—"
            card_layout.addWidget(QLabel(f"{_delta_text(overview.change_cny if overview else None)}  ·  心理价位 {threshold}  ·  更新 {updated}", objectName="muted", wordWrap=True))
            self.route_cards.addWidget(card)
        if len(self._routes) > 5:
            self.route_cards.addWidget(QLabel(f"还有 {len(self._routes) - 5} 条航程，可在“我的航程”中查看。", objectName="muted"))

    def _render_events(self) -> None:
        _clear_layout(self.event_cards)
        events = self._data.recent_events if self._data else ()
        if not events:
            self.event_cards.addWidget(QLabel("暂无动态。完成查询后，运行记录会显示在这里。", objectName="emptyState", wordWrap=True))
            return
        for event in events[:8]:
            occurred = _parse_time(event.get("occurred_at"))
            entry = QFrame(objectName="card")
            entry_layout = QVBoxLayout(entry)
            entry_layout.setContentsMargins(13, 10, 13, 10)
            entry_layout.addWidget(QLabel(_friendly_time(occurred) if occurred else "—", objectName="detailLabel"))
            entry_layout.addWidget(QLabel(str(event.get("message", "")), wordWrap=True))
            self.event_cards.addWidget(entry)


def _metric_card(icon: str, title: str, value: str, detail: str, accent: str) -> QFrame:
    card = QFrame(objectName="metricCard")
    card.setProperty("accent", accent)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 13, 16, 13)
    icon_row = QHBoxLayout()
    icon_row.addWidget(QLabel(icon, objectName="metricIcon"))
    icon_row.addWidget(QLabel(title, objectName="muted"))
    icon_row.addStretch()
    layout.addLayout(icon_row)
    value_label = QLabel(value, objectName="metricValue")
    layout.addWidget(value_label)
    layout.addWidget(QLabel(detail, objectName="detail", wordWrap=True))
    return card


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if widget := item.widget():
            widget.deleteLater()


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


def _greeting(now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    if hour < 6:
        return "夜深了，旅行家"
    if hour < 12:
        return "早上好，旅行家"
    if hour < 18:
        return "下午好，旅行家"
    return "晚上好，旅行家"


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
