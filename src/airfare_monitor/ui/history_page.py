"""Lightweight persisted CNY total-price history page."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models import LegConfig
from ..storage import SQLiteStore


class PriceChart(QWidget):
    def __init__(self):
        super().__init__()
        self.rows: list[dict[str, object]] = []
        self.threshold: Decimal | None = None
        self.setMinimumHeight(260)

    def set_series(self, rows: list[dict[str, object]], threshold: Decimal | None) -> None:
        self.rows = list(rows)
        self.threshold = threshold
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        area = self.rect().adjusted(62, 22, -24, -42)
        valid = [
            _decimal(row.get("minimum_total_price_cny"))
            for row in self.rows
            if str(row.get("status")) == "success"
            and _decimal(row.get("minimum_total_price_cny")) is not None
        ]
        values = [value for value in valid if value is not None]
        if not values:
            painter.setPen(QColor("#7287a6"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "完成查询后将在这里显示价格曲线")
            return
        if self.threshold is not None:
            values.append(self.threshold)

        low, high = min(values), max(values)
        padding = max(Decimal("20"), (high - low) * Decimal("0.12"))
        low -= padding
        high += padding
        span = high - low or Decimal("1")
        painter.setPen(QPen(QColor("#e8eef6"), 1))
        for step in range(5):
            y = area.top() + area.height() * step / 4
            painter.drawLine(area.left(), int(y), area.right(), int(y))
            price = high - span * Decimal(step) / Decimal(4)
            label = f"{price:,.0f}"
            painter.setPen(QColor("#7287a6"))
            painter.drawText(QRectF(0, y - 9, 55, 18), Qt.AlignmentFlag.AlignRight, label)
            painter.setPen(QPen(QColor("#e8eef6"), 1))

        def point(index: int, value: Decimal) -> QPointF:
            denominator = max(1, len(self.rows) - 1)
            x = area.left() + area.width() * index / denominator
            y = area.bottom() - float((value - low) / span) * area.height()
            return QPointF(x, y)

        if self.threshold is not None:
            threshold_y = point(0, self.threshold).y()
            painter.setPen(QPen(QColor("#ee8a15"), 1.5, Qt.PenStyle.DashLine))
            painter.drawLine(area.left(), int(threshold_y), area.right(), int(threshold_y))
            painter.drawText(
                QRectF(area.right() - 155, threshold_y - 23, 150, 20),
                Qt.AlignmentFlag.AlignRight,
                f"心理价位 ¥{self.threshold:,.0f}",
            )

        painter.setPen(QPen(QColor("#1673e6"), 2.5))
        for segment in price_segments(self.rows):
            path = QPainterPath()
            for position, (index, value) in enumerate(segment):
                current = point(index, value)
                if position == 0:
                    path.moveTo(current)
                else:
                    path.lineTo(current)
            painter.drawPath(path)
            for index, value in segment:
                current = point(index, value)
                painter.setBrush(QColor("#ffffff"))
                painter.drawEllipse(current, 3.5, 3.5)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e14c4c"))
        for index, row in enumerate(self.rows):
            if str(row.get("status")) != "success" or _decimal(row.get("minimum_total_price_cny")) is None:
                x = point(index, low).x()
                painter.drawEllipse(QPointF(x, area.bottom()), 4.5, 4.5)

        first_time = _datetime(self.rows[0].get("captured_at")) if self.rows else None
        last_time = _datetime(self.rows[-1].get("captured_at")) if self.rows else None
        painter.setPen(QColor("#7287a6"))
        metrics = QFontMetrics(painter.font())
        if first_time:
            painter.drawText(area.left(), area.bottom() + 27, first_time.strftime("%m-%d %H:%M"))
        if last_time:
            label = last_time.strftime("%m-%d %H:%M")
            painter.drawText(area.right() - metrics.horizontalAdvance(label), area.bottom() + 27, label)


class HistoryPage(QWidget):
    def __init__(
        self,
        store: SQLiteStore | None,
        *,
        open_latest_report: Callable[[], None],
        outputs_dir: Path | None,
    ):
        super().__init__()
        self.store = store
        self.open_latest_report = open_latest_report
        self.outputs_dir = outputs_dir
        self.routes: list[LegConfig] = []
        self.hours = 24

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 26, 30, 26)
        layout.setSpacing(14)
        title_row = QHBoxLayout()
        heading = QVBoxLayout()
        heading.addWidget(QLabel("历史价格", objectName="pageTitle"))
        heading.addWidget(QLabel("查看航程在不同时间的 CNY 含税总价变化", objectName="muted"))
        title_row.addLayout(heading)
        title_row.addStretch()
        report_button = QPushButton("打开最新 Excel")
        report_button.clicked.connect(open_latest_report)
        folder_button = QPushButton("打开报告目录")
        folder_button.clicked.connect(self.open_output_directory)
        title_row.addWidget(report_button)
        title_row.addWidget(folder_button)
        layout.addLayout(title_row)

        filters = QHBoxLayout()
        self.route_combo = QComboBox()
        self.route_combo.currentIndexChanged.connect(self.reload)
        filters.addWidget(self.route_combo, 1)
        self.period_group = QButtonGroup(self)
        for label, hours in (("最近 24 小时", 24), ("最近 7 天", 24 * 7)):
            button = QPushButton(label, checkable=True)
            button.setProperty("hours", hours)
            button.clicked.connect(lambda checked=False, value=hours: self.set_period(value))
            self.period_group.addButton(button)
            filters.addWidget(button)
            if hours == 24:
                button.setChecked(True)
        layout.addLayout(filters)

        metrics = QHBoxLayout()
        self.current_card = _history_metric("当前", "—")
        self.minimum_card = _history_metric("最低", "—")
        self.maximum_card = _history_metric("最高", "—")
        for card in (self.current_card, self.minimum_card, self.maximum_card):
            metrics.addWidget(card, 1)
        layout.addLayout(metrics)

        body = QHBoxLayout()
        chart_card = QFrame(objectName="card")
        chart_layout = QVBoxLayout(chart_card)
        chart_layout.addWidget(QLabel("含税总价（CNY）", objectName="sectionTitle"))
        self.chart = PriceChart()
        chart_layout.addWidget(self.chart, 1)
        self.chart_note = QLabel("蓝线为有效价格；红点表示查询失败或未取得有效价格。", objectName="muted")
        chart_layout.addWidget(self.chart_note)
        body.addWidget(chart_card, 3)

        records_card = QFrame(objectName="card")
        records_layout = QVBoxLayout(records_card)
        records_layout.addWidget(QLabel("最近查询记录", objectName="sectionTitle"))
        self.records = QTableWidget(0, 2)
        self.records.setHorizontalHeaderLabels(["时间", "结果"])
        self.records.horizontalHeader().setStretchLastSection(True)
        self.records.verticalHeader().setVisible(False)
        self.records.verticalHeader().setDefaultSectionSize(36)
        self.records.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        records_layout.addWidget(self.records)
        body.addWidget(records_card, 2)
        layout.addLayout(body, 1)

    def refresh(self, routes: list[LegConfig]) -> None:
        selected_id = self.route_combo.currentData()
        self.routes = list(routes)
        self.route_combo.blockSignals(True)
        self.route_combo.clear()
        for route in routes:
            label = f"{route.origin_name_zh or route.origin_airport_iata} {route.origin_airport_iata} → {route.destination_name_zh or route.destination_airport_iata} {route.destination_airport_iata}"
            self.route_combo.addItem(label, route.id)
        if selected_id:
            index = self.route_combo.findData(selected_id)
            self.route_combo.setCurrentIndex(max(0, index))
        self.route_combo.blockSignals(False)
        self.reload()

    def set_period(self, hours: int) -> None:
        self.hours = hours
        self.reload()

    def reload(self) -> None:
        leg_id = self.route_combo.currentData()
        route = next((item for item in self.routes if item.id == leg_id), None)
        if route is None:
            rows: list[dict[str, object]] = []
        else:
            rows = self.store.leg_price_series(
                route.id,
                since=datetime.now() - timedelta(hours=self.hours),
                max_points=500,
            ) if self.store is not None else []
        threshold = route.expected_total_price_cny if route else None
        self.chart.set_series(rows, threshold)
        valid = [
            value
            for row in rows
            if str(row.get("status")) == "success"
            and (value := _decimal(row.get("minimum_total_price_cny"))) is not None
        ]
        _set_history_metric(self.current_card, _price(valid[-1]) if valid else "—")
        _set_history_metric(self.minimum_card, _price(min(valid)) if valid else "—")
        _set_history_metric(self.maximum_card, _price(max(valid)) if valid else "—")
        recent = list(reversed(rows[-12:]))
        self.records.setRowCount(len(recent))
        for index, row in enumerate(recent):
            captured = _datetime(row.get("captured_at"))
            value = _decimal(row.get("minimum_total_price_cny"))
            status = str(row.get("status"))
            result = _price(value) if status == "success" and value is not None else _history_status(status)
            self.records.setItem(index, 0, QTableWidgetItem(captured.strftime("%m-%d %H:%M") if captured else "—"))
            self.records.setItem(index, 1, QTableWidgetItem(result))
        self.records.resizeColumnToContents(0)

    def open_output_directory(self) -> None:
        if self.outputs_dir is None or not self.outputs_dir.is_dir():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.outputs_dir.resolve())))


def price_segments(rows: list[dict[str, object]]) -> list[list[tuple[int, Decimal]]]:
    """Split valid points at failures so the graph never implies a valid bridge."""
    segments: list[list[tuple[int, Decimal]]] = []
    current: list[tuple[int, Decimal]] = []
    for index, row in enumerate(rows):
        value = _decimal(row.get("minimum_total_price_cny"))
        if str(row.get("status")) == "success" and value is not None:
            current.append((index, value))
        elif current:
            segments.append(current)
            current = []
    if current:
        segments.append(current)
    return segments


def _history_metric(title: str, value: str) -> QFrame:
    card = QFrame(objectName="metricCard")
    layout = QVBoxLayout(card)
    layout.addWidget(QLabel(title, objectName="muted"))
    layout.addWidget(QLabel(value, objectName="metricValue"))
    return card


def _set_history_metric(card: QFrame, value: str) -> None:
    card.findChild(QLabel, "metricValue").setText(value)


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def _datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _price(value: Decimal) -> str:
    return f"¥{value:,.0f}"


def _history_status(status: str) -> str:
    return {
        "failed": "查询失败",
        "manual_attention": "需要人工处理",
        "success": "无符合条件航班",
    }.get(status, "未取得价格")
