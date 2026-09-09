from __future__ import annotations

import uuid
from datetime import date, time
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QDate, QTime, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QSpinBox, QStackedWidget, QTimeEdit, QVBoxLayout, QWidget,
)

from ..desktop_app.airport_catalog import AirportCatalog, AirportRecord
from ..desktop_app.controller import DesktopController
from ..market import resolve_market
from ..models import EtdWindow, LegConfig, PreferredSchedule
from .widgets.airport_picker import AirportPicker


class RouteWizard(QDialog):
    def __init__(
        self, catalog: AirportCatalog, controller: DesktopController, route: LegConfig | None = None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.catalog = catalog
        self.controller = controller
        self.route = route
        self.setWindowTitle("编辑航程" if route else "添加航程")
        self.setMinimumSize(800, 620)
        self.stack = QStackedWidget()
        self.step_label = QLabel()
        self.back_button = QPushButton("上一步")
        self.next_button = QPushButton("下一步")
        self.next_button.setObjectName("primary")
        self.cancel_button = QPushButton("取消")
        self._build_fields()
        self.stack.addWidget(self._route_page())
        self.stack.addWidget(self._preferences_page())
        self.stack.addWidget(self._confirm_page())
        buttons = QHBoxLayout()
        buttons.addWidget(self.cancel_button)
        buttons.addStretch()
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("航程设置", objectName="pageTitle"))
        layout.addWidget(self.step_label)
        layout.addWidget(self.stack, 1)
        layout.addLayout(buttons)
        self.cancel_button.clicked.connect(self.reject)
        self.back_button.clicked.connect(self._back)
        self.next_button.clicked.connect(self._next)
        self.origin_picker.selected_changed.connect(self._update_capability)
        self.destination_picker.selected_changed.connect(self._update_capability)
        self.trip_type.currentIndexChanged.connect(self._update_capability)
        self.direct_only.toggled.connect(self._update_layover)
        self._load_route(route)
        self._update_step()

    def _build_fields(self) -> None:
        self.origin_picker = AirportPicker(self.catalog)
        self.destination_picker = AirportPicker(self.catalog)
        self.trip_type = QComboBox()
        self.trip_type.addItems(["单程", "往返"])
        self.departure_date = QDateEdit(calendarPopup=True)
        self.departure_date.setDate(QDate.currentDate().addDays(1))
        self.return_date = QDateEdit(calendarPopup=True)
        self.return_date.setDate(QDate.currentDate().addDays(8))
        self.start_time = QTimeEdit(QTime(0, 0))
        self.end_time = QTimeEdit(QTime(23, 59))
        self.return_start_time = QTimeEdit(QTime(0, 0))
        self.return_end_time = QTimeEdit(QTime(23, 59))
        self.direct_only = QCheckBox("只看直达航班")
        self.direct_only.setChecked(True)
        self.max_layover = QSpinBox()
        self.max_layover.setRange(30, 1440)
        self.max_layover.setValue(240)
        self.price = QLineEdit(placeholderText="留空表示只观察，不触发低价提醒")
        self.enabled = QCheckBox("保存后立即启用监控")
        self.enabled.setChecked(True)
        self.focus_enabled = QCheckBox("添加重点班次（可选）")
        self.focus_label = QLineEdit(placeholderText="例如：早班直飞")
        self.focus_departure = QTimeEdit(QTime(8, 0))
        self.focus_arrival = QTimeEdit(QTime(12, 0))
        self.focus_tolerance = QSpinBox()
        self.focus_tolerance.setRange(0, 360)
        self.focus_tolerance.setValue(30)

    def _route_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        form.addRow("从哪里出发", self.origin_picker)
        swap = QPushButton("交换起终点")
        swap.clicked.connect(self._swap)
        form.addRow("", swap)
        form.addRow("到哪里", self.destination_picker)
        form.addRow("航程类型", self.trip_type)
        form.addRow("出发日期", self.departure_date)
        window = QHBoxLayout()
        window.addWidget(self.start_time)
        window.addWidget(QLabel("至"))
        window.addWidget(self.end_time)
        form.addRow("出发时间段", _layout_widget(window))
        self.return_date_label = QLabel("返程日期")
        self.return_window_label = QLabel("返程时间段")
        self.return_window_widget = _layout_widget(_time_layout(self.return_start_time, self.return_end_time))
        form.addRow(self.return_date_label, self.return_date)
        form.addRow(self.return_window_label, self.return_window_widget)
        layout.addLayout(form)
        layout.addStretch()
        return page

    def _preferences_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        form.addRow("筛选", self.direct_only)
        self.layover_label = QLabel("最长总中转等待")
        form.addRow(self.layover_label, self.max_layover)
        form.addRow("含税心理价位（CNY）", self.price)
        form.addRow("启用状态", self.enabled)
        form.addRow("重点班次", self.focus_enabled)
        form.addRow("重点班次名称", self.focus_label)
        form.addRow("重点起飞时间", self.focus_departure)
        form.addRow("重点到达时间", self.focus_arrival)
        form.addRow("时间容差（分钟）", self.focus_tolerance)
        layout.addLayout(form)
        note = QLabel("价格比较、历史和提醒均使用解析后的 CNY 含税总价。")
        note.setObjectName("muted")
        layout.addWidget(note)
        layout.addStretch()
        return page

    def _confirm_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.summary = QLabel(wordWrap=True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        card = QFrame(objectName="card")
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(self.summary)
        layout.addWidget(card)
        layout.addStretch()
        return page

    def _load_route(self, route: LegConfig | None) -> None:
        if route is None:
            self._update_capability()
            return
        self.origin_picker.set_record(self.catalog.by_iata(route.origin_airport_iata))
        self.destination_picker.set_record(self.catalog.by_iata(route.destination_airport_iata))
        self.trip_type.setCurrentIndex(1 if route.return_date else 0)
        self.departure_date.setDate(_qdate(route.departure_date))
        self.start_time.setTime(_qtime(route.etd_window.start))
        self.end_time.setTime(_qtime(route.etd_window.end))
        self.direct_only.setChecked(route.direct_only)
        self.max_layover.setValue(route.max_layover_minutes or 240)
        self.price.setText(str(route.expected_total_price_cny or ""))
        self.enabled.setChecked(route.enabled)
        if route.return_date and route.return_etd_window:
            self.return_date.setDate(_qdate(route.return_date))
            self.return_start_time.setTime(_qtime(route.return_etd_window.start))
            self.return_end_time.setTime(_qtime(route.return_etd_window.end))
        if route.preferred_schedules:
            focus = route.preferred_schedules[0]
            self.focus_enabled.setChecked(True)
            self.focus_label.setText(focus.label)
            self.focus_departure.setTime(_qtime(focus.departure_time))
            self.focus_arrival.setTime(_qtime(focus.arrival_time))
            self.focus_tolerance.setValue(focus.departure_tolerance_minutes)
        self._update_capability()

    def _update_capability(self) -> None:
        origin = self.origin_picker.selected
        destination = self.destination_picker.selected
        domestic = False
        if origin and destination:
            try:
                domestic = resolve_market(_draft_leg(origin, destination)) == "domestic"
            except ValueError:
                domestic = False
        self.trip_type.model().item(1).setEnabled(not domestic)
        if domestic:
            self.trip_type.setCurrentIndex(0)
            self.direct_only.setChecked(True)
            self.direct_only.setEnabled(False)
        else:
            self.direct_only.setEnabled(True)
        roundtrip = self.trip_type.currentIndex() == 1
        self.return_date_label.setVisible(roundtrip)
        self.return_date.setVisible(roundtrip)
        self.return_window_label.setVisible(roundtrip)
        self.return_window_widget.setVisible(roundtrip)
        self._update_layover()

    def _update_layover(self) -> None:
        show = not self.direct_only.isChecked() and self.direct_only.isEnabled()
        self.layover_label.setVisible(show)
        self.max_layover.setVisible(show)

    def _swap(self) -> None:
        origin, destination = self.origin_picker.selected, self.destination_picker.selected
        self.origin_picker.set_record(destination)
        self.destination_picker.set_record(origin)
        self._update_capability()

    def _back(self) -> None:
        self.stack.setCurrentIndex(max(0, self.stack.currentIndex() - 1))
        self._update_step()

    def _next(self) -> None:
        index = self.stack.currentIndex()
        if index == 0 and not self._validate_route_page():
            return
        if index == 1 and not self._validate_preferences():
            return
        if index < 2:
            if index == 1:
                self._refresh_summary()
            self.stack.setCurrentIndex(index + 1)
            self._update_step()
            return
        self._save()

    def _update_step(self) -> None:
        index = self.stack.currentIndex()
        self.step_label.setText(f"第 {index + 1} 步，共 3 步 · {'航程信息' if index == 0 else '偏好设置' if index == 1 else '确认保存'}")
        self.back_button.setVisible(index > 0)
        self.next_button.setText("保存航程" if index == 2 else "下一步")

    def _validate_route_page(self) -> bool:
        if not self.origin_picker.selected or not self.destination_picker.selected:
            QMessageBox.warning(self, "请选择机场", "起运地和目的地都必须从内置机场目录中选择。")
            return False
        if self.origin_picker.selected.airport_iata == self.destination_picker.selected.airport_iata:
            QMessageBox.warning(self, "航程无效", "出发和到达机场不能相同。")
            return False
        if self.trip_type.currentIndex() == 1 and self.return_date.date() <= self.departure_date.date():
            QMessageBox.warning(self, "返程日期无效", "返程日期必须晚于出发日期。")
            return False
        return True

    def _validate_preferences(self) -> bool:
        if self.price.text().strip():
            try:
                if Decimal(self.price.text().strip()) <= 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                QMessageBox.warning(self, "心理价位无效", "请输入大于 0 的 CNY 金额，或留空只观察。")
                return False
        if self.enabled.isChecked() and self.controller.enabled_capacity_remaining(excluding_id=self.route.id if self.route else None) <= 0:
            self.enabled.setChecked(False)
            QMessageBox.information(self, "已达到上限", "当前设备最多同时启用 10 个航程；此航程将保存为暂停。")
        if self.focus_enabled.isChecked() and not self.focus_label.text().strip():
            QMessageBox.warning(self, "重点班次缺少名称", "请输入重点班次名称，或取消重点班次。")
            return False
        return True

    def _refresh_summary(self) -> None:
        origin = self.origin_picker.selected
        destination = self.destination_picker.selected
        assert origin and destination
        market = resolve_market(_draft_leg(origin, destination))
        source = "同程（国内）" if market == "domestic" else "去哪儿（国际/跨境）"
        kind = "往返" if self.trip_type.currentIndex() else "单程"
        self.summary.setText(
            f"<h2>{origin.display_text} → {destination.display_text}</h2>"
            f"<p>{origin.city_name_zh} → {destination.city_name_zh} · {kind}</p>"
            f"<p>出发：{self.departure_date.date().toString('yyyy-MM-dd')} · "
            f"{self.start_time.time().toString('HH:mm')}–{self.end_time.time().toString('HH:mm')}</p>"
            f"<p>来源自动匹配：<b>{source}</b></p>"
            f"<p>状态：<b>{'启用监控' if self.enabled.isChecked() else '保存为暂停'}</b></p>"
        )

    def _save(self) -> None:
        try:
            self.controller.save_route(self._build_route())
        except (ValueError, OSError) as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.accept()

    def _build_route(self) -> LegConfig:
        origin = self.origin_picker.selected
        destination = self.destination_picker.selected
        assert origin and destination
        return_date = self.return_date.date().toPython() if self.trip_type.currentIndex() else None
        direct = self.direct_only.isChecked()
        preferred: tuple[PreferredSchedule, ...] = ()
        if self.focus_enabled.isChecked():
            preferred = (
                PreferredSchedule(
                    label=self.focus_label.text().strip(),
                    departure_time=self.focus_departure.time().toPython(),
                    arrival_time=self.focus_arrival.time().toPython(),
                    arrival_day_offset=0,
                    departure_tolerance_minutes=self.focus_tolerance.value(),
                    arrival_tolerance_minutes=self.focus_tolerance.value(),
                    origin_airport_iata=origin.airport_iata,
                    destination_airport_iata=destination.airport_iata,
                ),
            )
        return LegConfig(
            id=self.route.id if self.route else f"route-{uuid.uuid4().hex[:8]}",
            enabled=self.enabled.isChecked(),
            origin_airport_iata=origin.airport_iata,
            destination_airport_iata=destination.airport_iata,
            departure_date=self.departure_date.date().toPython(),
            etd_window=EtdWindow(self.start_time.time().toPython(), self.end_time.time().toPython()),
            direct_only=direct,
            expected_total_price_cny=Decimal(self.price.text().strip()) if self.price.text().strip() else None,
            top_n=10,
            adult_count=1,
            child_count=0,
            cabin_class="economy",
            origin_name_zh=origin.city_name_zh,
            destination_name_zh=destination.city_name_zh,
            preferred_schedules=preferred,
            market="auto",
            max_layover_minutes=None if direct else self.max_layover.value(),
            return_date=return_date,
            return_etd_window=EtdWindow(self.return_start_time.time().toPython(), self.return_end_time.time().toPython()) if return_date else None,
            return_direct_only=direct if return_date else None,
            return_max_layover_minutes=(None if direct else self.max_layover.value()) if return_date else None,
        )


def _draft_leg(origin: AirportRecord, destination: AirportRecord) -> LegConfig:
    return LegConfig(
        id="draft", enabled=False, origin_airport_iata=origin.airport_iata, destination_airport_iata=destination.airport_iata,
        departure_date=date.today(), etd_window=EtdWindow(time(0, 0), time(23, 59)), direct_only=True,
        expected_total_price_cny=None, top_n=10, adult_count=1, child_count=0, cabin_class="economy",
    )


def _layout_widget(layout: QHBoxLayout) -> QWidget:
    widget = QWidget()
    widget.setLayout(layout)
    return widget


def _time_layout(start: QTimeEdit, end: QTimeEdit) -> QHBoxLayout:
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(start)
    layout.addWidget(QLabel("至"))
    layout.addWidget(end)
    return layout


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _qtime(value: time) -> QTime:
    return QTime(value.hour, value.minute)
