"""Fluent 实例侧栏。"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QAbstractItemView, QFrame, QListWidgetItem, QVBoxLayout
from qfluentwidgets import BodyLabel, CaptionLabel, CardWidget, FluentIcon, ListWidget, PrimaryPushButton, PushButton


class InstanceSidebar(CardWidget):
    """实例列表 + 新增/删除/克隆/重命名。"""

    instance_selected = pyqtSignal(str)
    create_requested = pyqtSignal()
    delete_requested = pyqtSignal(str)
    clone_requested = pyqtSignal(str)
    rename_requested = pyqtSignal(str)

    ROLE_INSTANCE_ID = 0x0100
    ROLE_INSTANCE_NAME = 0x0101

    def __init__(self, parent=None):
        super().__init__(parent)
        self._id_to_state: dict[str, str] = {}
        self._id_to_name: dict[str, str] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        title = BodyLabel('实例')
        root.addWidget(title)
        root.addWidget(CaptionLabel('新增 / 删除 / 克隆 / 重命名'))

        self._list = ListWidget(self)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        root.addWidget(self._list, 1)

        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(line)

        self._btn_create = PrimaryPushButton('新增', self)
        self._btn_delete = PushButton('删除', self)
        self._btn_clone = PushButton('克隆', self)
        self._btn_rename = PushButton('重命名', self)
        self._btn_create.setIcon(FluentIcon.ADD)
        self._btn_delete.setIcon(FluentIcon.DELETE)
        self._btn_clone.setIcon(FluentIcon.COPY)
        self._btn_rename.setIcon(FluentIcon.EDIT)
        self._btn_create.clicked.connect(self.create_requested.emit)
        self._btn_delete.clicked.connect(self._emit_delete)
        self._btn_clone.clicked.connect(self._emit_clone)
        self._btn_rename.clicked.connect(self._emit_rename)
        for btn in (self._btn_create, self._btn_delete, self._btn_clone, self._btn_rename):
            btn.setFixedHeight(32)
            root.addWidget(btn)

    def _current_instance_id(self) -> str:
        item = self._list.currentItem()
        if item is None:
            return ''
        return str(item.data(self.ROLE_INSTANCE_ID) or '')

    def _emit_delete(self) -> None:
        iid = self._current_instance_id()
        if iid:
            self.delete_requested.emit(iid)

    def _emit_clone(self) -> None:
        iid = self._current_instance_id()
        if iid:
            self.clone_requested.emit(iid)

    def _emit_rename(self) -> None:
        iid = self._current_instance_id()
        if iid:
            self.rename_requested.emit(iid)

    def _on_selection_changed(self) -> None:
        iid = self._current_instance_id()
        if iid:
            self.instance_selected.emit(iid)

    @staticmethod
    def _state_tip(state: str) -> str:
        return {
            'running': '运行中',
            'paused': '已暂停',
            'idle': '空闲',
            'degraded': '降级运行',
        }.get(str(state or 'idle').lower(), '未知状态')

    def set_instances(self, instances: list[dict[str, Any]]) -> None:
        current = self._current_instance_id()
        self._list.blockSignals(True)
        self._list.clear()
        self._id_to_state.clear()
        self._id_to_name.clear()

        for data in instances:
            iid = str(data.get('id') or '')
            if not iid:
                continue
            name = str(data.get('name') or iid)
            state = str(data.get('state') or 'idle')
            self._id_to_state[iid] = state
            self._id_to_name[iid] = name
            item = QListWidgetItem(name)
            item.setData(self.ROLE_INSTANCE_ID, iid)
            item.setData(self.ROLE_INSTANCE_NAME, name)
            item.setToolTip(f'{name} - {self._state_tip(state)}')
            item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            self._list.addItem(item)
            if iid == current:
                self._list.setCurrentItem(item)

        self._list.blockSignals(False)

    def set_active_instance(self, instance_id: str) -> None:
        iid = str(instance_id or '')
        if not iid:
            return
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            item = self._list.item(i)
            if str(item.data(self.ROLE_INSTANCE_ID) or '') == iid:
                self._list.setCurrentItem(item)
                break
        self._list.blockSignals(False)

    def update_instance_state(self, instance_id: str, state: str, name: str | None = None) -> None:
        iid = str(instance_id or '')
        if not iid:
            return
        self._id_to_state[iid] = str(state or 'idle')
        if name:
            self._id_to_name[iid] = str(name)
        for i in range(self._list.count()):
            item = self._list.item(i)
            if str(item.data(self.ROLE_INSTANCE_ID) or '') != iid:
                continue
            display_name = str(self._id_to_name.get(iid) or item.data(self.ROLE_INSTANCE_NAME) or iid)
            item.setData(self.ROLE_INSTANCE_NAME, display_name)
            item.setText(display_name)
            item.setToolTip(f'{display_name} - {self._state_tip(self._id_to_state[iid])}')
            break
