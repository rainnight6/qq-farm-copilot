"""gui 主窗口（全新 Fluent 实现）。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from html import escape, unescape
from pathlib import Path
from typing import Any

import keyboard
from PIL import Image
from PyQt6.QtCore import QEvent, QObject, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QGuiApplication, QIcon, QImage, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    ElevatedCardWidget,
    FluentIcon,
    FluentWindow,
    IconWidget,
    IndeterminateProgressRing,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    MessageBox,
    MessageBoxBase,
    NavigationItemPosition,
    PushButton,
    SplashScreen,
    SubtitleLabel,
    TabWidget,
    Theme,
    ToolButton,
    isDarkTheme,
    qconfig,
    setTheme,
    setThemeColor,
)

from core.engine.bot import BotEngine
from core.instance.manager import InstanceManager, InstanceSession
from core.update_checker import UpdateCheckResult, check_github_latest_release
from gui.steal_chart_panel import StealChartPanel
from gui.widgets.feature_panel import FeaturePanel
from gui.widgets.global_settings_panel import GlobalSettingsPanel
from gui.widgets.instance_manage_panel import InstanceManagePanel
from gui.widgets.land_detail_panel import LandDetailPanel
from gui.widgets.log_panel import LogPanel
from gui.widgets.settings_panel import SettingsPanel
from gui.widgets.status_panel import StatusPanel
from gui.widgets.task_panel import TaskPanel
from models.config import AppConfig
from utils.app_paths import resolve_runtime_path, user_app_dir
from utils.logger import (
    DEFAULT_LOG_RETENTION_DAYS,
    cleanup_expired_logs,
    normalize_log_retention_days,
    switch_log_directory,
)
from utils.version import APP_GITHUB_REPO, APP_RELEASES_URL, APP_VERSION

GUI_API_VERSION = 2
BOOT_PROTOCOL = 1
BOOT_CALLER = 'gui.window_loader'
BOOT_SIGNATURE = 'qqfarm-gui-boot-v1'
ALLOWED_EXE_BASENAMES: set[str] = {'QQFarmCopilot.exe'}
ALLOWED_PROCESS_NAMES: set[str] = {'QQFarmCopilot'}
APP_WINDOW_TITLE = 'QQ Farm Copilot（免费软件，谨防倒卖）'
FREE_NOTICE_TITLE = '免费提示'
FREE_NOTICE_TEXT = (
    '本软件完全免费，若付费购买请立即退款。请通过项目主页获取最新版与公告，谨防二次售卖、捆绑分发或虚假收费。'
)
FREE_NOTICE_TOAST = '本软件完全免费，谨防倒卖。'
FREE_HINT_TEXT = '免费软件，谨防倒卖'
FREE_HINT_COLORS = ('#ef4444', '#f97316', '#eab308', '#22c55e', '#06b6d4', '#3b82f6')
FREE_HINT_PUNCT = {'，', '。', '！', '？', '、', '：', '；', ',', '.', '!', '?', ' '}
FREE_HINT_INTERVAL_MS = 700
FREE_WATERMARK_TEXT = '免费软件 谨防倒卖'
INSTANCE_ICON_SIZE = 18
PROJECT_HOME_URL = 'https://github.com/490720818/qq-farm-copilot'
APP_SETTINGS_FILENAME = 'app_settings.json'
DEFAULT_WINDOW_WIDTH = 1747
DEFAULT_WINDOW_HEIGHT = 1080
MIN_WINDOW_WIDTH = 970
MIN_WINDOW_HEIGHT = 600
PREVIEW_TARGET_WIDTH = 540
PREVIEW_TARGET_HEIGHT = 960
APP_THEME_COLOR = '#8FD19E'
UPDATE_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000
UPDATE_CHECK_TIMEOUT_SECONDS = 8.0


@dataclass
class Workspace:
    instance_id: str
    name: str
    session: InstanceSession
    container: QWidget
    engine: BotEngine
    status_panel: StatusPanel
    log_panel: LogPanel
    task_panel: TaskPanel
    feature_panel: FeaturePanel
    settings_panel: SettingsPanel
    land_panel: LandDetailPanel
    btn_start: PushButton
    btn_pause: PushButton
    btn_stop: PushButton
    free_hint_label: QLabel
    state: str = 'idle'
    last_preview: Image.Image | None = None
    start_in_progress: bool = False


class _NameDialog(MessageBoxBase):
    def __init__(self, title: str, default_text: str = '', parent=None):
        super().__init__(parent)
        self.title_label = SubtitleLabel(title, self)
        self.value_edit = LineEdit(self)
        self.value_edit.setText(str(default_text))
        self.value_edit.selectAll()
        self.value_edit.returnPressed.connect(self.accept)
        self.value_edit.setPlaceholderText('仅支持英文和数字')
        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(BodyLabel('实例名称:'))
        self.viewLayout.addWidget(self.value_edit)
        self.widget.setMinimumWidth(420)
        self.yesButton.setText('确定')
        self.cancelButton.setText('取消')

    def value(self) -> str:
        return str(self.value_edit.text() or '').strip()


class _FreeNoticeDialog(MessageBoxBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        title = SubtitleLabel(FREE_NOTICE_TITLE, self)
        content = QLabel(self)
        content.setWordWrap(True)
        content.setTextFormat(Qt.TextFormat.RichText)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        content.setOpenExternalLinks(True)
        content.setText(
            (
                '<div style="line-height:1.65;">'
                '<p>本软件<span style="color:#d13438;font-weight:700;">完全免费</span>，'
                '若<span style="color:#d13438;font-weight:700;">付费购买请立即退款</span>。</p>'
                '<p>请通过项目主页获取最新版与公告，'
                '<span style="color:#d13438;font-weight:700;">谨防二次售卖、捆绑分发或虚假收费</span>。</p>'
                f'<p>项目地址：<a href="{PROJECT_HOME_URL}">{PROJECT_HOME_URL}</a></p>'
                '</div>'
            )
        )
        self.skip_next_box = CheckBox('下次不再提醒', self)
        self.viewLayout.addWidget(title)
        self.viewLayout.addWidget(content)
        self.viewLayout.addWidget(self.skip_next_box)
        self.widget.setMinimumWidth(460)
        self.yesButton.setText('我知道了')
        self.cancelButton.hide()

    def skip_next(self) -> bool:
        return bool(self.skip_next_box.isChecked())


class _StaticElevatedCardWidget(ElevatedCardWidget):
    """仅保留阴影效果，禁用上移动画，避免在高频刷新区域出现回落抖动。"""

    def _startElevateAni(self, start, end):
        return


class _NavDotOverlay(QObject):
    """在导航按钮右上角叠加红点，绕过 drawIcon 的自动着色。"""

    def __init__(self, widget: QObject, parent: QObject):
        super().__init__(parent)
        self._widget = widget
        widget.installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._widget and event.type() == QEvent.Type.Paint:
            result = super().eventFilter(obj, event)
            painter = QPainter(obj)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            r = 4
            x = obj.width() - r - 3
            y = r + 3
            painter.setPen(QColor('#ffffff'))
            painter.setBrush(QColor('#ef4444'))
            painter.drawEllipse(x - r, y - r, r * 2, r * 2)
            painter.end()
            return result
        return super().eventFilter(obj, event)

    def remove(self) -> None:
        self._widget.removeEventFilter(self)


class _FreeWatermarkOverlay(QWidget):
    """主窗口免费信息缺失时的全窗口斜向小字水印。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._text = str(text or '').strip() or FREE_WATERMARK_TEXT
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.hide()

    def paintEvent(self, event) -> None:
        if not self._text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor('#94a3b8') if isDarkTheme() else QColor('#64748b')
        color.setAlpha(55 if isDarkTheme() else 40)
        painter.setPen(color)
        font = QFont(self.font())
        font.setPointSize(9)
        painter.setFont(font)

        width = max(1, self.width())
        height = max(1, self.height())
        step_x = 220
        step_y = 110
        text_w = 200
        text_h = 44

        painter.translate(width / 2.0, height / 2.0)
        painter.rotate(-30)
        painter.translate(-width / 2.0, -height / 2.0)
        for y in range(-height, height * 2, step_y):
            for x in range(-width, width * 2, step_x):
                painter.drawText(x, y, text_w, text_h, Qt.AlignmentFlag.AlignCenter, self._text)
        painter.end()


class _UpdateCheckWorker(QObject):
    finished = pyqtSignal(object)

    def __init__(self, repo: str, current_version: str):
        super().__init__()
        self._repo = str(repo or '').strip()
        self._current_version = str(current_version or '').strip()

    def run(self) -> None:
        result = check_github_latest_release(
            repo=self._repo,
            current_version=self._current_version,
            timeout_seconds=UPDATE_CHECK_TIMEOUT_SECONDS,
        )
        self.finished.emit(result)


class _UpdateCheckingDialog(MessageBoxBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.title_label = SubtitleLabel('检查更新中', self)
        self.content_label = BodyLabel('正在从 GitHub Release 获取最新版本信息，请稍候…', self)
        self.content_label.setWordWrap(True)
        self.progress_ring = IndeterminateProgressRing(self)
        self.progress_ring.setFixedSize(28, 28)
        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(10)
        row_layout.addWidget(self.progress_ring, 0, Qt.AlignmentFlag.AlignTop)
        row_layout.addWidget(self.content_label, 1)
        self.viewLayout.addWidget(self.title_label)
        self.viewLayout.addWidget(row)
        self.widget.setMinimumWidth(460)
        self.yesButton.hide()
        self.cancelButton.hide()


class MainWindow(FluentWindow):
    def __init__(self, instance_manager: InstanceManager):
        # Qt 在父类初始化阶段可能触发 resizeEvent，关键状态需先初始化。
        self._last_shot: Image.Image | None = None
        self._last_shot_ts = 0.0
        self._shot_hidden = False
        self._preview_page_visible = True
        self._preview_layout_pending = False
        self._free_watermark_overlay: _FreeWatermarkOverlay | None = None
        super().__init__()
        self.instance_manager = instance_manager
        self._workspaces: dict[str, Workspace] = {}
        self._active_iid = ''
        self._name_re = re.compile(r'^[A-Za-z0-9]+$')
        self._theme_mode = 'auto'
        self._mica_enabled = True
        self._log_retention_days = DEFAULT_LOG_RETENTION_DAYS
        self._free_hint_tick = 0
        self._instance_nav_buttons: dict[str, object] = {}
        self._free_notice_shown = False
        self._free_notice_queued = False
        self._skip_free_notice = False
        self._current_version = str(APP_VERSION or '0.0.0-dev').strip() or '0.0.0-dev'
        self._latest_release_tag = ''
        self._latest_release_url = APP_RELEASES_URL
        self._has_update_available = False
        self._update_check_in_progress = False
        self._update_check_worker: _UpdateCheckWorker | None = None
        self._update_check_thread: QThread | None = None
        self._update_check_dialog: _UpdateCheckingDialog | None = None
        self._manual_check_requested = False
        self._update_notified_once = False
        self._update_check_retry_count = 0
        self._global_settings_nav_btn = None
        self._settings_dot_overlay: _NavDotOverlay | None = None
        self._app_settings_file = user_app_dir() / APP_SETTINGS_FILENAME
        self._load_app_settings()

        self._build_ui()
        qconfig.themeChangedFinished.connect(self._apply_preview_placeholder_style)
        qconfig.themeChangedFinished.connect(self._refresh_settings_nav_indicator)
        self._init_instances()
        self._apply_startup_window_size()
        self._free_hint_timer = QTimer(self)
        self._free_hint_timer.setInterval(FREE_HINT_INTERVAL_MS)
        self._free_hint_timer.timeout.connect(self._on_free_hint_tick)
        self._free_hint_timer.start()
        self._update_check_timer = QTimer(self)
        self._update_check_timer.setInterval(UPDATE_CHECK_INTERVAL_MS)
        self._update_check_timer.timeout.connect(self._check_updates_periodic)
        self._update_check_timer.start()
        QTimer.singleShot(0, self._check_updates_on_startup)

        keyboard.add_hotkey('F9', self._on_pause)
        keyboard.add_hotkey('F10', self._on_stop)

    def _build_ui(self) -> None:
        self.setWindowTitle(APP_WINDOW_TITLE)
        icon = resolve_runtime_path('gui', 'icons', 'app_icon.ico')
        self.setWindowIcon(QIcon(str(icon)))
        default_w, default_h = self._to_logical_size(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        min_w, min_h = self._to_logical_size(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.resize(default_w, default_h)
        self.setMinimumSize(min_w, min_h)
        self.setCustomBackgroundColor(QColor(244, 247, 252), QColor(32, 32, 32))
        self.setMicaEffectEnabled(self._mica_enabled)
        setTheme(self._resolve_theme(), save=False)
        setThemeColor(QColor(APP_THEME_COLOR), save=False)
        self.navigationInterface.setCollapsible(True)
        self.navigationInterface.setMenuButtonVisible(True)
        self.navigationInterface.setReturnButtonVisible(False)
        self.navigationInterface.setExpandWidth(200)
        self.navigationInterface.setAcrylicEnabled(True)
        # 强制进入悬浮菜单（MENU）模式，避免展开时挤占内容区域。
        self.navigationInterface.setMinimumExpandWidth(100000)
        # 保证折叠按钮固定在导航栏最上方。
        panel = self.navigationInterface.panel
        panel.topLayout.removeWidget(panel.menuButton)
        panel.topLayout.insertWidget(0, panel.menuButton, 0, Qt.AlignmentFlag.AlignTop)

        self.workbench = QWidget(self)
        self.workbench.setObjectName('workbenchInterface')
        root = QHBoxLayout(self.workbench)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self._build_preview_card(root)

        self.stack = QStackedWidget(self.workbench)
        root.addWidget(self.stack, 1)
        self._build_instance_manage_page()
        self._build_global_settings_page()

        self._workbench_nav_btn = self.addSubInterface(self.workbench, FluentIcon.HOME, '工作台')
        self._workbench_nav_btn.hide()
        self.navigationInterface.addItem(
            routeKey='instanceManageAction',
            icon=FluentIcon.TILES,
            text='多开',
            onClick=self._show_instance_manage_page,
            selectable=True,
            position=NavigationItemPosition.BOTTOM,
        )
        self.navigationInterface.addItem(
            routeKey='projectHomeAction',
            icon=FluentIcon.GITHUB,
            text='GitHub',
            onClick=self._open_project_home,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )
        self._global_settings_nav_btn = self.navigationInterface.addItem(
            routeKey='globalSettingsAction',
            icon=FluentIcon.SETTING,
            text='设置',
            onClick=self._show_global_settings_page,
            selectable=True,
            position=NavigationItemPosition.BOTTOM,
        )
        self._refresh_settings_nav_indicator()
        self.switchTo(self.workbench)

        self._free_watermark_overlay = _FreeWatermarkOverlay(FREE_WATERMARK_TEXT, self)
        self._free_watermark_overlay.setGeometry(self.rect())

        self.splash_screen = SplashScreen(self.windowIcon(), self)
        self.splash_screen.setIconSize(QSize(106, 106))
        self.splash_screen.raise_()
        self._layout_preview_toggle()
        self._refresh_free_watermark_visibility()

    def _apply_startup_window_size(self) -> None:
        min_w = max(1, int(self.minimumWidth()))
        min_h = max(1, int(self.minimumHeight()))
        default_w, default_h = self._to_logical_size(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        target_w = max(default_w, min_w)
        target_h = max(default_h, min_h)
        if self.width() < min_w or self.height() < min_h:
            self.resize(target_w, target_h)
        self._apply_startup_window_position()

    def _apply_startup_window_position(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.left() + max(0, (area.width() - self.width()) // 2)
        y = area.top() + max(0, (area.height() - self.height()) // 2)
        self.move(x, y)

    @staticmethod
    def _screen_scale_factor() -> float:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return 1.0
        try:
            scale = float(screen.devicePixelRatio())
        except Exception:
            scale = 1.0
        if not (scale > 0):
            scale = 1.0
        return max(1.0, min(scale, 4.0))

    @classmethod
    def _to_logical_size(cls, physical_w: int, physical_h: int) -> tuple[int, int]:
        scale = cls._screen_scale_factor()
        w = max(1, int(round(float(physical_w) / scale)))
        h = max(1, int(round(float(physical_h) / scale)))
        return w, h

    def _build_preview_card(self, root: QHBoxLayout) -> None:
        self.preview_card = _StaticElevatedCardWidget(self.workbench)
        self.preview_card.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.preview_card.setFixedWidth(PREVIEW_TARGET_WIDTH)
        pv = QVBoxLayout(self.preview_card)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(0)

        self.preview_viewport = QWidget(self.preview_card)
        viewport_layout = QVBoxLayout(self.preview_viewport)
        viewport_layout.setContentsMargins(0, 0, 0, 0)
        viewport_layout.setSpacing(0)
        viewport_layout.addStretch()

        self.preview_label = BodyLabel('启动后显示\n实时截图', self.preview_viewport)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setWordWrap(True)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._apply_preview_placeholder_style()
        viewport_layout.addWidget(self.preview_label, 0, Qt.AlignmentFlag.AlignHCenter)
        viewport_layout.addStretch()
        pv.addWidget(self.preview_viewport, 1)

        self.preview_toggle = ToolButton(FluentIcon.HIDE, self.preview_label)
        self.preview_toggle.setToolTip('隐藏截图预览')
        self.preview_toggle.setFixedSize(30, 30)
        self.preview_toggle.clicked.connect(self._toggle_preview)
        self._sync_preview_toggle_button()
        root.addWidget(self.preview_card, 0)
        self._update_preview_viewport_size()
        self._layout_preview_toggle()

    def _set_preview_page_visible(self, visible: bool) -> None:
        self._preview_page_visible = bool(visible)
        self._apply_window_size_constraints()
        self.preview_card.setVisible(self._preview_page_visible)
        if not self._preview_page_visible:
            return
        self._queue_preview_layout_sync()
        if self._shot_hidden:
            self._clear_preview_display('截图预览已隐藏')
            return
        active_preview = self._get_active_preview_image()
        if active_preview is not None:
            self._render_shot(active_preview, force=True)
        else:
            self._clear_preview_display('启动后显示\n实时截图')

    def _apply_preview_placeholder_style(self, *_args) -> None:
        if not hasattr(self, 'preview_label'):
            return
        color = '#CBD5E1' if isDarkTheme() else '#475569'
        self.preview_label.setStyleSheet(
            f'QLabel {{ color: {color}; background: transparent; font-size: 13px; font-weight: 400; }}'
        )

    def _apply_window_size_constraints(self) -> None:
        min_w, min_h = self._to_logical_size(MIN_WINDOW_WIDTH, MIN_WINDOW_HEIGHT)
        self.setMinimumSize(min_w, min_h)
        if self.width() < min_w or self.height() < min_h:
            self.resize(max(self.width(), min_w), max(self.height(), min_h))

    def _update_preview_viewport_size(self) -> None:
        if not hasattr(self, 'preview_card') or not hasattr(self, 'preview_label'):
            return
        if not self._preview_page_visible:
            return
        card_h = max(1, int(self.preview_card.height()))
        # 预览画布保持 540:960；当高度不足时按比例整体缩小（包含宽度）。
        scale = min(1.0, card_h / PREVIEW_TARGET_HEIGHT)
        target_w = max(1, int(PREVIEW_TARGET_WIDTH * scale))
        target_h = max(1, int(PREVIEW_TARGET_HEIGHT * scale))
        if self.preview_card.width() != target_w:
            self.preview_card.setFixedWidth(target_w)
        if self.preview_label.width() != target_w or self.preview_label.height() != target_h:
            self.preview_label.setFixedSize(target_w, target_h)

    def _queue_preview_layout_sync(self) -> None:
        if self._preview_layout_pending:
            return
        self._preview_layout_pending = True
        QTimer.singleShot(0, self._sync_preview_layout)

    def _sync_preview_layout(self) -> None:
        self._preview_layout_pending = False
        if not self._preview_page_visible:
            return
        self._update_preview_viewport_size()
        self._layout_preview_toggle()
        if self._shot_hidden:
            self._clear_preview_display('截图预览已隐藏')
            return
        active_preview = self._get_active_preview_image()
        if active_preview is None:
            self._clear_preview_display('启动后显示\n实时截图')
            return
        self._render_shot(active_preview, force=True)

    def _build_instance_manage_page(self) -> None:
        self.instance_manage_page = QWidget(self.stack)
        layout = QVBoxLayout(self.instance_manage_page)
        layout.setContentsMargins(10, 10, 10, 10)
        self.instance_manage_panel = InstanceManagePanel(self.instance_manage_page)
        self.instance_manage_panel.open_requested.connect(self._switch)
        self.instance_manage_panel.create_requested.connect(self._on_create)
        self.instance_manage_panel.delete_requested.connect(self._on_delete)
        self.instance_manage_panel.clone_requested.connect(self._on_clone)
        self.instance_manage_panel.rename_requested.connect(self._on_rename)
        self.instance_manage_panel.order_changed.connect(self._on_reorder_instances)
        layout.addWidget(self.instance_manage_panel, 1)
        self.stack.addWidget(self.instance_manage_page)

    def _show_instance_manage_page(self) -> None:
        self._set_preview_page_visible(False)
        self.stack.setCurrentWidget(self.instance_manage_page)
        self.navigationInterface.setCurrentItem('instanceManageAction')
        self._refresh_free_watermark_visibility()

    def _build_global_settings_page(self) -> None:
        self.global_settings_page = QWidget(self.stack)
        layout = QVBoxLayout(self.global_settings_page)
        layout.setContentsMargins(10, 10, 10, 10)
        self.global_settings_panel = GlobalSettingsPanel(self.global_settings_page)
        self.global_settings_panel.apply_requested.connect(self._apply_global_settings)
        self.global_settings_panel.check_update_requested.connect(self._check_updates_manual)
        self.global_settings_panel.set_values(self._theme_mode, self._mica_enabled, self._log_retention_days)
        self.global_settings_panel.set_version_text(self._current_version)
        layout.addWidget(self.global_settings_panel, 1)
        self.stack.addWidget(self.global_settings_page)

    def _show_global_settings_page(self) -> None:
        self._set_preview_page_visible(False)
        self.stack.setCurrentWidget(self.global_settings_page)
        self.navigationInterface.setCurrentItem('globalSettingsAction')
        self._refresh_free_watermark_visibility()

    def _open_project_home(self) -> None:
        if not QDesktopServices.openUrl(QUrl(PROJECT_HOME_URL)):
            self._toast('warning', '打开失败', f'无法打开项目地址：{PROJECT_HOME_URL}', 2400)

    def _check_updates_on_startup(self) -> None:
        self._start_update_check(manual=False)

    def _check_updates_periodic(self) -> None:
        self._start_update_check(manual=False)

    def _check_updates_manual(self) -> None:
        self._start_update_check(manual=True)

    def _start_update_check(self, manual: bool) -> None:
        if self._update_check_in_progress:
            if manual:
                self._toast('info', '检查中', '正在检查更新，请稍候。', 1800)
            return

        self._manual_check_requested = bool(manual)
        self._update_check_in_progress = True
        self._update_check_retry_count = 0
        if manual:
            self._show_update_check_dialog()
        self._launch_update_check_worker()

    def _launch_update_check_worker(self) -> None:
        worker = _UpdateCheckWorker(APP_GITHUB_REPO, self._current_version)
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_update_check_finished)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_update_check_thread_finished)
        self._update_check_worker = worker
        self._update_check_thread = thread
        thread.start()

    def _show_update_check_dialog(self) -> None:
        dialog = _UpdateCheckingDialog(self)
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.raise_()
        dialog.activateWindow()
        dialog.show()
        self._update_check_dialog = dialog

    def _close_update_check_dialog(self) -> None:
        dialog = self._update_check_dialog
        self._update_check_dialog = None
        if dialog is None:
            return
        dialog.close()
        dialog.deleteLater()

    def _on_update_check_thread_finished(self) -> None:
        self._update_check_worker = None
        self._update_check_thread = None

    def _on_update_check_finished(self, result_obj: object) -> None:
        self._update_check_in_progress = False
        manual = bool(self._manual_check_requested)
        self._close_update_check_dialog()

        if not isinstance(result_obj, UpdateCheckResult):
            result = UpdateCheckResult(
                ok=False,
                has_update=False,
                current_version=self._current_version,
                latest_version='',
                latest_tag='',
                release_url=self._latest_release_url or APP_RELEASES_URL,
                download_url='',
                message='检查更新失败：结果格式无效',
            )
        else:
            result = result_obj

        if not result.ok and not manual and self._update_check_retry_count < 3:
            self._update_check_retry_count += 1
            self._update_check_in_progress = True
            QTimer.singleShot(10 * 1000, self._launch_update_check_worker)
            return

        self._manual_check_requested = False
        self._update_check_retry_count = 0
        self._apply_update_check_result(result)
        if manual:
            self._show_update_result_dialog(result)
        elif result.ok and result.has_update and not self._update_notified_once:
            self._update_notified_once = True
            latest = str(result.latest_tag or '').strip() or f'v{result.latest_version}'
            self._toast('success', '发现新版本', f'检测到新版本 {latest}，可在设置页查看并更新。', -1)

    def _apply_update_check_result(self, result: UpdateCheckResult) -> None:
        if result.release_url:
            self._latest_release_url = str(result.release_url)
        if result.latest_tag:
            self._latest_release_tag = str(result.latest_tag)
        if result.ok:
            self._has_update_available = bool(result.has_update)

        detail = '检查失败'
        if result.ok and result.has_update:
            detail = f'发现 {result.latest_tag or ("v" + result.latest_version)}'
        elif result.ok:
            detail = '已是最新'
        if hasattr(self, 'global_settings_panel'):
            self.global_settings_panel.set_version_text(self._current_version, detail)
        self._refresh_settings_nav_indicator()

    def _show_update_result_dialog(self, result: UpdateCheckResult) -> None:
        target_url = self._resolve_update_target_url(result)
        if result.ok and result.has_update:
            latest = str(result.latest_tag or '').strip() or f'v{result.latest_version}'
            box = MessageBox(
                '发现新版本',
                f'当前版本: v{result.current_version}\n最新版本: {latest}',
                self,
            )
            box.yesButton.setText('打开发布页')
            box.cancelButton.setText('稍后')
            if box.exec():
                self._open_url(target_url, fail_title='打开失败')
            return

        if result.ok:
            box = MessageBox(
                '检查完成',
                f'当前版本: v{result.current_version}\n当前已是最新版本。\n发布地址:\n{target_url}',
                self,
            )
            box.yesButton.setText('打开发布页')
            box.cancelButton.setText('关闭')
            if box.exec():
                self._open_url(target_url, fail_title='打开失败')
            return

        box = MessageBox(
            '检查失败',
            f'{result.message}\n\n可手动查看发布地址:\n{target_url}',
            self,
        )
        box.yesButton.setText('打开发布页')
        box.cancelButton.setText('关闭')
        if box.exec():
            self._open_url(target_url, fail_title='打开失败')

    @staticmethod
    def _make_settings_icon_with_dot() -> QIcon:
        color = QColor('#e5e7eb') if isDarkTheme() else QColor('#475569')
        pixmap = FluentIcon.SETTING.icon(color=color).pixmap(QSize(18, 18))
        if pixmap.isNull():
            return FluentIcon.SETTING.icon(color=color)
        marked = QPixmap(pixmap)
        painter = QPainter(marked)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        dot_radius = 4
        center_x = marked.width() - 4
        center_y = 4
        painter.setPen(QColor('#ffffff'))
        painter.setBrush(QColor('#ef4444'))
        painter.drawEllipse(center_x - dot_radius, center_y - dot_radius, dot_radius * 2, dot_radius * 2)
        painter.end()
        return QIcon(marked)

    def _refresh_settings_nav_indicator(self, *_args) -> None:
        button = self._global_settings_nav_btn
        if button is None:
            return
        if self._has_update_available:
            if self._settings_dot_overlay is None:
                item_widget = getattr(button, 'itemWidget', button)
                self._settings_dot_overlay = _NavDotOverlay(item_widget, self)
            latest = str(self._latest_release_tag or '').strip()
            button.setToolTip('设置（发现新版本）' if not latest else f'设置（发现新版本 {latest}）')
        else:
            if self._settings_dot_overlay is not None:
                self._settings_dot_overlay.remove()
                self._settings_dot_overlay = None
            button.setToolTip('设置')

    @staticmethod
    def _resolve_update_target_url(result: UpdateCheckResult) -> str:
        release_url = str(result.release_url or '').strip()
        if release_url:
            return release_url
        return APP_RELEASES_URL

    def _open_url(self, url: str, fail_title: str = '打开失败') -> None:
        target = str(url or '').strip()
        if not target:
            self._toast('warning', fail_title, '地址为空', 1800)
            return
        if not QDesktopServices.openUrl(QUrl(target)):
            self._toast('warning', fail_title, f'无法打开地址：{target}', 2400)

    def _apply_global_settings(self, theme_mode: str, mica_enabled: bool, log_retention_days: int) -> None:
        self._theme_mode = self._normalize_theme_mode(theme_mode)
        self._mica_enabled = bool(mica_enabled)
        new_retention_days = normalize_log_retention_days(log_retention_days)
        retention_changed = new_retention_days != self._log_retention_days
        self._log_retention_days = new_retention_days
        setTheme(self._resolve_theme(), save=False)
        setThemeColor(QColor(APP_THEME_COLOR), save=False)
        self.setMicaEffectEnabled(self._mica_enabled)
        deleted_logs = 0
        failed_logs = 0
        if retention_changed:
            try:
                switch_log_directory(str((user_app_dir() / 'logs').resolve()), retention_days=self._log_retention_days)
            except Exception:
                pass
            for ws in self._workspaces.values():
                try:
                    ws.engine.apply_log_retention_days(self._log_retention_days)
                except Exception:
                    continue
            cleanup_stats = cleanup_expired_logs(user_app_dir(), retention_days=self._log_retention_days)
            deleted_logs = int(cleanup_stats.get('deleted', 0))
            failed_logs = int(cleanup_stats.get('failed', 0))
        self._refresh_settings_nav_indicator()
        self._save_app_settings()
        if retention_changed:
            if failed_logs > 0:
                detail = f'全局设置已更新；日志清理 {deleted_logs} 个，失败 {failed_logs} 个'
            else:
                detail = f'全局设置已更新；日志清理 {deleted_logs} 个'
        else:
            detail = '全局外观设置已更新'
        self._toast('success', '设置已应用', detail, 2200)

    @staticmethod
    def _normalize_theme_mode(theme_mode: str) -> str:
        mode = str(theme_mode or 'auto').strip().lower()
        if mode not in {'auto', 'light', 'dark'}:
            return 'auto'
        return mode

    def _resolve_theme(self) -> Theme:
        return {
            'light': Theme.LIGHT,
            'dark': Theme.DARK,
            'auto': Theme.AUTO,
        }.get(self._theme_mode, Theme.AUTO)

    def _load_app_settings(self) -> None:
        path = self._app_settings_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        appearance = data.get('appearance')
        if isinstance(appearance, dict):
            self._theme_mode = self._normalize_theme_mode(str(appearance.get('theme_mode', self._theme_mode)))
            self._mica_enabled = bool(appearance.get('mica_enabled', self._mica_enabled))
        logging = data.get('logging')
        if isinstance(logging, dict):
            self._log_retention_days = normalize_log_retention_days(
                logging.get('retention_days', self._log_retention_days)
            )
        free_notice = data.get('free_notice')
        if isinstance(free_notice, dict):
            self._skip_free_notice = bool(free_notice.get('skip', self._skip_free_notice))
        preview = data.get('preview')
        if isinstance(preview, dict):
            self._shot_hidden = bool(preview.get('hidden', self._shot_hidden))

    def _save_app_settings(self) -> None:
        path = self._app_settings_file
        payload = {
            'appearance': {
                'theme_mode': self._normalize_theme_mode(self._theme_mode),
                'mica_enabled': bool(self._mica_enabled),
            },
            'logging': {
                'retention_days': int(normalize_log_retention_days(self._log_retention_days)),
            },
            'free_notice': {
                'skip': bool(self._skip_free_notice),
            },
            'preview': {
                'hidden': bool(self._shot_hidden),
            },
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(path.suffix + '.tmp')
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp_path.replace(path)
        except Exception:
            return

    def _runtime_paths(self, s: InstanceSession) -> dict[str, str]:
        return {
            'config_path': str(s.paths.config_file),
            'logs_dir': str(s.paths.logs_dir),
            'screenshots_dir': str(s.paths.screenshots_dir),
            'error_dir': str(s.paths.error_dir),
            'log_retention_days': str(self._log_retention_days),
        }

    def _build_workspace(self, s: InstanceSession) -> Workspace:
        engine = BotEngine(
            s.config, runtime_paths=self._runtime_paths(s), instance_id=s.instance_id, allow_idle_prewarm=False
        )
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        bar = CardWidget(container)
        hb = QHBoxLayout(bar)
        hb.setContentsMargins(10, 8, 10, 8)
        start, pause, stop = (
            PushButton('开始', bar),
            PushButton('暂停', bar),
            PushButton('停止', bar),
        )
        start.setIcon(FluentIcon.PLAY.icon(color=QColor('#16a34a')))
        pause.setIcon(FluentIcon.PAUSE.icon(color=QColor('#f59e0b')))
        stop.setIcon(FluentIcon.CANCEL.icon(color=QColor('#ef4444')))
        pause.setEnabled(False)
        stop.setEnabled(False)
        for b in (start, pause, stop):
            hb.addWidget(b)
        hb.addStretch()
        free_hint = QLabel(bar)
        free_hint.setTextFormat(Qt.TextFormat.RichText)
        free_hint.setStyleSheet('QLabel { background: transparent; }')
        free_hint.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        free_hint.setMinimumWidth(180)
        free_hint.setToolTip(FREE_NOTICE_TEXT)
        self._set_free_hint_label(free_hint)
        hb.addWidget(free_hint)
        layout.addWidget(bar, 0)

        tabs = TabWidget(container)
        tabs.setTabsClosable(False)
        tabs.tabBar.setAddButtonVisible(False)
        status, log, task, feature, settings, land_detail = (
            StatusPanel(),
            LogPanel(),
            TaskPanel(s.config),
            FeaturePanel(s.config),
            SettingsPanel(s.config),
            LandDetailPanel(s.config),
        )

        status_page = QWidget(container)
        sv = QVBoxLayout(status_page)
        sv.setContentsMargins(10, 10, 10, 10)
        sv.addWidget(status)
        log_card = _StaticElevatedCardWidget(status_page)
        log_card.setObjectName('statusPageLogCard')
        log_card.setStyleSheet(
            'ElevatedCardWidget#statusPageLogCard { border-radius: 10px; border: 1px solid rgba(100, 116, 139, 0.22); }'
            'ElevatedCardWidget#statusPageLogCard:hover {'
            ' background-color: rgba(37, 99, 235, 0.06);'
            ' border: 1px solid rgba(59, 130, 246, 0.32);'
            ' }'
        )
        lv = QVBoxLayout(log_card)
        lv.setContentsMargins(10, 10, 10, 10)
        log_header = QWidget(log_card)
        log_header_layout = QHBoxLayout(log_header)
        log_header_layout.setContentsMargins(0, 0, 0, 0)
        log_header_layout.setSpacing(6)
        log_icon = IconWidget(FluentIcon.MESSAGE, log_header)
        log_icon.setFixedSize(14, 14)
        log_header_layout.addWidget(log_icon)
        log_title = BodyLabel('运行日志')
        log_title.setStyleSheet('font-weight: 700; font-size: 14px; color: #1e293b;')
        log_header_layout.addWidget(log_title)
        log_header_layout.addStretch()
        lv.addWidget(log_header)
        log_divider = QWidget(log_card)
        log_divider.setFixedHeight(1)
        log_divider.setStyleSheet('background-color: rgba(37, 99, 235, 0.10); border: none;')
        lv.addWidget(log_divider)
        lv.addWidget(log, 1)
        sv.addWidget(log_card, 1)

        tabs.addTab(status_page, '状态', FluentIcon.HOME)
        tabs.addTab(land_detail, '农场详情', FluentIcon.APPLICATION)
        tabs.addTab(task, '任务调度', FluentIcon.CALENDAR)
        tabs.addTab(feature, '任务设置', FluentIcon.BRUSH)
        tabs.addTab(StealChartPanel(s.instance_id), '数据统计', FluentIcon.PIE_SINGLE)
        tabs.addTab(settings, '设置', FluentIcon.SETTING)
        layout.addWidget(tabs, 1)

        start.clicked.connect(lambda: self._on_start(s.instance_id))
        pause.clicked.connect(lambda: self._on_pause(s.instance_id))
        stop.clicked.connect(lambda: self._on_stop(s.instance_id))

        ws = Workspace(
            s.instance_id,
            s.name,
            s,
            container,
            engine,
            status,
            log,
            task,
            feature,
            settings,
            land_detail,
            start,
            pause,
            stop,
            free_hint,
        )
        self._bind(ws)
        return ws

    def _bind(self, ws: Workspace) -> None:
        ws.engine.log_message.connect(lambda t, _ws=ws: _ws.log_panel.append_log(t))
        ws.engine.screenshot_updated.connect(lambda im, _ws=ws: self._on_shot(_ws.instance_id, im))
        ws.engine.detection_result.connect(lambda im, _ws=ws: self._on_shot(_ws.instance_id, im))
        ws.engine.state_changed.connect(lambda st, _ws=ws: self._on_state(_ws.instance_id, st))
        ws.engine.stats_updated.connect(ws.status_panel.update_stats)
        ws.engine.config_updated.connect(lambda _c, _ws=ws: self._reload_cfg(_ws.instance_id))
        ws.settings_panel.config_changed.connect(lambda c, _ws=ws: self._on_cfg(_ws.instance_id, c))
        ws.task_panel.config_changed.connect(lambda c, _ws=ws: self._on_cfg(_ws.instance_id, c))
        ws.feature_panel.config_changed.connect(lambda c, _ws=ws: self._on_cfg(_ws.instance_id, c))
        ws.land_panel.config_changed.connect(lambda c, _ws=ws: self._on_cfg(_ws.instance_id, c))

    def _init_instances(self) -> None:
        self.instance_manager.load()
        for s in self.instance_manager.iter_sessions():
            ws = self._build_workspace(s)
            self._workspaces[s.instance_id] = ws
            self.stack.addWidget(ws.container)

        self._refresh_lists()
        active = self.instance_manager.get_active()
        if active and active.instance_id in self._workspaces:
            self._switch(active.instance_id)
        elif self._workspaces:
            first_iid = next(iter(self._workspaces.keys()))
            self._switch(first_iid)

    def _build_free_hint_html(self) -> str:
        parts: list[str] = []
        tick = int(self._free_hint_tick)
        for idx, ch in enumerate(FREE_HINT_TEXT):
            text = escape(ch)
            if ch in FREE_HINT_PUNCT:
                parts.append(f'<span style="color:#94a3b8;">{text}</span>')
                continue
            color = FREE_HINT_COLORS[(idx + tick) % len(FREE_HINT_COLORS)]
            parts.append(f'<span style="color:{color};">{text}</span>')
        return '<span style="font-size:16px; font-weight:800; letter-spacing:0.2px;">' + ''.join(parts) + '</span>'

    def _set_free_hint_label(self, label: QLabel) -> None:
        label.setText(self._build_free_hint_html())

    @staticmethod
    def _normalize_hint_text(text: str) -> str:
        raw = str(text or '')
        plain = re.sub(r'<[^>]+>', '', raw)
        plain = unescape(plain)
        plain = re.sub(r'\s+', '', plain)
        return plain.strip()

    def _has_visible_free_info(self) -> bool:
        title = str(self.windowTitle() or '')
        if not title:
            return False
        return self._normalize_hint_text(FREE_HINT_TEXT) in self._normalize_hint_text(title)

    def _refresh_free_watermark_visibility(self) -> None:
        overlay = getattr(self, '_free_watermark_overlay', None)
        if overlay is None:
            return
        should_show = not self._has_visible_free_info()
        if should_show:
            overlay.setGeometry(self.rect())
            overlay.show()
            overlay.raise_()
            return
        overlay.hide()

    @staticmethod
    def _short_name(name: str, limit: int = 8) -> str:
        text = str(name or '')
        if len(text) <= limit:
            return text
        return f'{text[: max(1, limit - 1)]}…'

    @staticmethod
    def _route_for_instance(iid: str) -> str:
        return f'instance:{iid}'

    @staticmethod
    def _is_running_state(state: str) -> bool:
        normalized = str(state or 'idle').strip().lower()
        return normalized in {'starting', 'running', 'paused', 'analyzing', 'executing', 'waiting'}

    @staticmethod
    def _instance_state_icon(state: str) -> QIcon:
        normalized = str(state or 'idle').lower()
        if normalized in {'running', 'analyzing', 'executing', 'waiting'}:
            return FluentIcon.ROBOT.icon(color=QColor('#16a34a'))
        if normalized == 'paused':
            return FluentIcon.PAUSE.icon(color=QColor('#f59e0b'))
        if normalized == 'error':
            return FluentIcon.CANCEL.icon(color=QColor('#ef4444'))
        return FluentIcon.LEAF.icon(color=QColor('#22c55e'))

    def _sync_instance_nav_items(self, items: list[dict[str, str]]) -> None:
        normalized_items: list[tuple[str, str, str, str]] = []
        for item in items:
            iid = str(item.get('id') or '')
            if not iid:
                continue
            name = str(item.get('name') or iid)
            state = str(item.get('state') or 'idle')
            route = self._route_for_instance(iid)
            normalized_items.append((route, iid, name, state))

        desired_routes = [route for route, _, _, _ in normalized_items]
        current_routes = list(self._instance_nav_buttons.keys())

        # 实例发生重命名/增删后，按 manager 顺序重建导航项，避免顺序跳变。
        if current_routes != desired_routes:
            for route in current_routes:
                self.navigationInterface.removeWidget(route)
            self._instance_nav_buttons.clear()

            for idx, (route, iid, _name, state) in enumerate(normalized_items):
                button = self.navigationInterface.insertItem(
                    index=idx,
                    routeKey=route,
                    icon=self._instance_state_icon(state),
                    text='',
                    onClick=None,
                    selectable=True,
                    position=NavigationItemPosition.SCROLL,
                )
                button.clicked.connect(lambda _=False, x=iid: self._switch(x))
                self._instance_nav_buttons[route] = button

        for route, _iid, name, state in normalized_items:
            button = self._instance_nav_buttons.get(route)
            if button is None:
                continue
            button.show()
            if hasattr(button, 'setIconSize'):
                button.setIconSize(QSize(INSTANCE_ICON_SIZE, INSTANCE_ICON_SIZE))
            button.setIcon(self._instance_state_icon(state))
            button.setText(self._short_name(name))
            button.setToolTip(f'{name} [{state}]')

    def _refresh_lists(self) -> None:
        items: list[dict[str, str]] = []
        for session in self.instance_manager.iter_sessions():
            ws = self._workspaces.get(session.instance_id)
            state = ws.state if ws else 'idle'
            items.append({'id': session.instance_id, 'name': session.name, 'state': state})

        self._sync_instance_nav_items(items)
        self.instance_manage_panel.set_instances(items)
        if self._active_iid:
            self.instance_manage_panel.set_active_instance(self._active_iid)

    def _on_free_hint_tick(self) -> None:
        self._free_hint_tick = (self._free_hint_tick + 1) % 100_000
        ws = self._workspaces.get(self._active_iid)
        if ws is not None:
            self._set_free_hint_label(ws.free_hint_label)

    def _switch(self, iid: str) -> None:
        ws = self._workspaces.get(str(iid or ''))
        if ws is None:
            return
        self.instance_manager.switch_active(ws.instance_id)
        self._active_iid = ws.instance_id
        self._set_preview_page_visible(True)
        self.switchTo(self.workbench)
        self.stack.setCurrentWidget(ws.container)
        self.navigationInterface.setCurrentItem(self._route_for_instance(ws.instance_id))
        self.instance_manage_panel.set_active_instance(ws.instance_id)
        self._reload_cfg(ws.instance_id)
        ws.container.update()
        self.stack.update()
        QApplication.processEvents()
        self.setWindowTitle(f'{APP_WINDOW_TITLE} [当前实例: {ws.name}]')
        self._sync_btns(ws)
        if self._shot_hidden:
            self._clear_preview_display('截图预览已隐藏')
        elif ws.last_preview is None:
            self._clear_preview_display('启动后显示\n实时截图')
        elif ws.last_preview is not None:
            self._render_shot(ws.last_preview, force=True)
        self._refresh_lists()
        self._refresh_free_watermark_visibility()

    def _sync_btns(self, ws: Workspace) -> None:
        state_text = str(ws.state or 'idle').strip().lower()
        starting = ws.start_in_progress or state_text == 'starting'
        running = self._is_running_state(state_text) or starting
        ws.btn_start.setEnabled(not running)
        ws.btn_pause.setEnabled(running and not starting)
        ws.btn_stop.setEnabled(running)
        ws.btn_pause.setText('恢复' if ws.state == 'paused' else '暂停')

    def _on_start(self, iid: str) -> None:
        ws = self._workspaces.get(iid)
        if ws is None:
            return
        if ws.start_in_progress or self._is_running_state(ws.state):
            return

        ws.start_in_progress = True
        ws.state = 'starting'
        self._sync_btns(ws)
        self._refresh_lists()
        started = False
        start_error = ''
        try:
            started = bool(ws.engine.start())
        except Exception as exc:
            start_error = str(exc or '启动异常')
        finally:
            ws.start_in_progress = False

        if started:
            ws.state = 'running'
        else:
            ws.state = 'idle'
        self._sync_btns(ws)
        self._refresh_lists()
        if start_error:
            self._toast('error', '启动失败', start_error)

    def _on_pause(self, iid: str | None = None) -> None:
        ws = self._workspaces.get(str(iid or self._active_iid or ''))
        if ws is None or not self._is_running_state(ws.state):
            return
        if ws.btn_pause.text() == '暂停':
            ws.engine.pause()
            ws.state = 'paused'
        else:
            ws.engine.resume()
            ws.state = 'running'
        self._sync_btns(ws)
        self._refresh_lists()

    def _on_stop(self, iid: str | None = None) -> None:
        ws = self._workspaces.get(str(iid or self._active_iid or ''))
        if ws:
            ws.engine.stop()
            ws.state = 'idle'
            self._sync_btns(ws)
            self._refresh_lists()

    def _on_cfg(self, iid: str, config: AppConfig) -> None:
        ws = self._workspaces.get(iid)
        if ws:
            ws.session.config = config
            ws.engine.update_config(config)

    def _reload_cfg(self, iid: str) -> None:
        ws = self._workspaces.get(iid)
        if ws is None:
            return
        try:
            cfg = AppConfig.load(str(ws.session.paths.config_file))
        except Exception:
            return
        ws.session.config = cfg
        ws.engine.config = cfg
        ws.settings_panel.set_config(cfg)
        ws.task_panel.set_config(cfg)
        ws.feature_panel.set_config(cfg)
        ws.land_panel.set_config(cfg)

    def _on_shot(self, iid: str, image: Image.Image) -> None:
        ws = self._workspaces.get(iid)
        if ws is None:
            return
        ws.last_preview = image.copy()
        if iid == self._active_iid:
            self._render_shot(image)

    def _get_active_preview_image(self) -> Image.Image | None:
        ws = self._workspaces.get(str(self._active_iid or ''))
        if ws is None or ws.last_preview is None:
            return None
        return ws.last_preview

    def _clear_preview_display(self, text: str = '启动后显示\n实时截图') -> None:
        self._last_shot = None
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText(text)

    def _on_state(self, iid: str, state: str) -> None:
        ws = self._workspaces.get(iid)
        if ws:
            incoming = str(state or 'idle').strip().lower()
            # 启动握手期间 worker 可能短暂上报 idle，避免把“启动中”错误覆盖为可再次点击开始。
            if ws.start_in_progress and incoming == 'idle':
                self._sync_btns(ws)
                self._refresh_lists()
                return
            ws.state = incoming or 'idle'
            self.instance_manage_panel.update_instance_state(iid, ws.state, ws.name)
            self._sync_btns(ws)
            self._refresh_lists()

    def _render_shot(self, image: Image.Image, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_shot_ts < 0.35:
            return
        self._last_shot_ts = now
        self._last_shot = image.copy()
        if not self._preview_page_visible:
            return
        if self._shot_hidden:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText('截图预览已隐藏')
            return
        image = image.convert('RGB')
        qimg = QImage(
            image.tobytes('raw', 'RGB'), image.width, image.height, image.width * 3, QImage.Format.Format_RGB888
        )
        pix = QPixmap.fromImage(qimg)
        target = self.preview_label.size()
        if target.width() <= 0 or target.height() <= 0:
            return
        # 等比完整显示，不裁剪（宽高比不一致时会留边）。
        scaled = pix.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        rounded = QPixmap(target)
        rounded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(rounded)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, target.width(), target.height(), 10, 10)
        painter.setClipPath(path)
        dx = (target.width() - scaled.width()) // 2
        dy = (target.height() - scaled.height()) // 2
        painter.drawPixmap(dx, dy, scaled)
        painter.end()
        self.preview_label.setText('')
        self.preview_label.setPixmap(rounded)

    def _toggle_preview(self) -> None:
        self._shot_hidden = not self._shot_hidden
        self._sync_preview_toggle_button()
        if self._shot_hidden:
            self._clear_preview_display('截图预览已隐藏')
        else:
            active_preview = self._get_active_preview_image()
            if active_preview is None:
                self._clear_preview_display('启动后显示\n实时截图')
            else:
                self._render_shot(active_preview, force=True)
        self._save_app_settings()

    def _sync_preview_toggle_button(self) -> None:
        if self._shot_hidden:
            self.preview_toggle.setIcon(FluentIcon.VIEW)
            self.preview_toggle.setToolTip('显示截图预览')
            return
        self.preview_toggle.setIcon(FluentIcon.HIDE)
        self.preview_toggle.setToolTip('隐藏截图预览')

    def _layout_preview_toggle(self) -> None:
        if not hasattr(self, 'preview_label') or not hasattr(self, 'preview_toggle'):
            return
        x = max(8, self.preview_label.width() - self.preview_toggle.width() - 8)
        self.preview_toggle.move(x, 8)
        self.preview_toggle.raise_()

    def _prompt_name(self, title: str, default: str = '') -> tuple[str, bool]:
        dlg = _NameDialog(title, default, self)
        ok = bool(dlg.exec())
        if not ok:
            return '', False
        return dlg.value(), True

    def _toast(self, level: str, title: str, text: str, duration: int = 2600) -> None:
        method = {
            'success': InfoBar.success,
            'warning': InfoBar.warning,
            'error': InfoBar.error,
            'info': InfoBar.info,
        }.get(level, InfoBar.info)
        method(
            title=title,
            content=text,
            duration=duration,
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _show_free_notice_once(self) -> None:
        self._free_notice_queued = False
        if self._free_notice_shown or self._skip_free_notice:
            return
        self._free_notice_shown = True
        box = _FreeNoticeDialog(self)
        box.setWindowModality(Qt.WindowModality.ApplicationModal)
        box.raise_()
        box.activateWindow()
        box.exec()
        self._skip_free_notice = box.skip_next()
        self._save_app_settings()

    def _queue_free_notice_once(self) -> None:
        if self._free_notice_shown or self._free_notice_queued:
            return
        self._free_notice_queued = True
        QTimer.singleShot(0, self._show_free_notice_once)

    def _warn(self, title: str, text: str) -> None:
        self._toast('warning', title, text)

    def _confirm_delete(self, name: str) -> bool:
        box = MessageBox('确认删除', f'确认删除实例 `{name}` 吗？', self)
        box.yesButton.setText('删除')
        box.cancelButton.setText('取消')
        return bool(box.exec())

    def _on_create(self) -> None:
        name, ok = self._prompt_name('新增实例')
        if not ok or not name:
            return
        if not self._name_re.fullmatch(name):
            self._warn('新增失败', '实例名仅支持英文和数字。')
            return
        try:
            s = self.instance_manager.create_instance(name)
        except Exception as exc:
            self._toast('error', '新增失败', str(exc))
            return
        ws = self._build_workspace(s)
        self._workspaces[s.instance_id] = ws
        self.stack.addWidget(ws.container)
        self._switch(s.instance_id)
        self._toast('success', '新增成功', f'实例 {name} 已创建', 1800)

    def _on_clone(self, source_iid: str) -> None:
        src = self._workspaces.get(source_iid)
        if src is None:
            return
        name, ok = self._prompt_name('克隆实例', f'{src.name}Copy')
        if not ok or not name:
            return
        if not self._name_re.fullmatch(name):
            self._warn('克隆失败', '实例名仅支持英文和数字。')
            return
        try:
            s = self.instance_manager.clone_instance(source_iid, name)
        except Exception as exc:
            self._toast('error', '克隆失败', str(exc))
            return
        ws = self._build_workspace(s)
        self._workspaces[s.instance_id] = ws
        self.stack.addWidget(ws.container)
        self._switch(s.instance_id)
        self._toast('success', '克隆成功', f'实例 {name} 已创建', 1800)

    def _on_rename(self, iid: str) -> None:
        ws = self._workspaces.get(iid)
        if ws is None:
            return
        if self._is_running_state(ws.state):
            self._warn('重命名受限', '请先停止该实例再重命名。')
            return
        old_name = ws.name
        name, ok = self._prompt_name('重命名实例', ws.name)
        if not ok or not name:
            return
        if not self._name_re.fullmatch(name):
            self._warn('重命名失败', '实例名仅支持英文和数字。')
            return
        if str(name) == str(old_name):
            self._warn('重命名失败', '新名称与当前名称相同。')
            return
        old = ws.instance_id
        ws.engine.stop(keep_prewarm=False)
        try:
            s = self.instance_manager.rename_instance(old, name)
        except Exception as exc:
            self._toast('error', '重命名失败', str(exc))
            return
        if str(s.instance_id) == str(old) and str(s.name) == str(old_name):
            self._warn('重命名失败', '未检测到实例变更，请检查新名称是否有效。')
            return
        ws.instance_id, ws.name, ws.session = s.instance_id, s.name, s
        ws.engine.instance_id = s.instance_id
        ws.engine.runtime_paths = self._runtime_paths(s)
        ws.engine.update_config(s.config)
        self._workspaces.pop(old, None)
        self._workspaces[ws.instance_id] = ws
        self._switch(ws.instance_id)
        self._toast('success', '重命名成功', f'实例已重命名为 {s.name}', 1800)

    def _on_delete(self, iid: str) -> None:
        ws = self._workspaces.get(iid)
        if ws is None:
            return
        if self._is_running_state(ws.state):
            self._warn('删除受限', '请先停止该实例再删除。')
            return
        if len(self._workspaces) <= 1:
            self._warn('删除受限', '至少保留一个实例。')
            return
        if not self._confirm_delete(ws.name):
            return
        ws.engine.stop(keep_prewarm=False)
        try:
            self.instance_manager.delete_instance(iid)
        except Exception as exc:
            self._toast('error', '删除失败', str(exc))
            return
        self.stack.removeWidget(ws.container)
        ws.container.deleteLater()
        self._workspaces.pop(iid, None)
        active = self.instance_manager.get_active()
        if active and active.instance_id in self._workspaces:
            self._switch(active.instance_id)
        elif self._workspaces:
            self._switch(next(iter(self._workspaces.keys())))
        self._toast('success', '删除成功', f'实例 {ws.name} 已删除', 1800)

    def _on_reorder_instances(self, ordered_ids: list[str]) -> None:
        ids = [str(iid or '').strip() for iid in ordered_ids if str(iid or '').strip()]
        if not ids:
            return
        try:
            self.instance_manager.reorder_instances(ids)
        except Exception as exc:
            self._toast('error', '排序失败', str(exc))
            return
        self._refresh_lists()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_window_size_constraints()
        self._queue_preview_layout_sync()
        if hasattr(self, 'splash_screen') and self.splash_screen.isVisible():
            self.splash_screen.finish()
        self._queue_free_notice_once()
        self._refresh_free_watermark_visibility()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._queue_preview_layout_sync()
        if hasattr(self, 'splash_screen'):
            self.splash_screen.resize(self.size())
        self._refresh_free_watermark_visibility()

    def closeEvent(self, event) -> None:
        if hasattr(self, '_update_check_timer'):
            self._update_check_timer.stop()
        try:
            keyboard.unhook_all_hotkeys()
            keyboard.unhook_all()
        except Exception:
            pass
        self._close_update_check_dialog()
        thread = self._update_check_thread
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(int(UPDATE_CHECK_TIMEOUT_SECONDS * 1000) + 1000)
        for ws in self._workspaces.values():
            ws.engine.stop(keep_prewarm=False)
        super().closeEvent(event)


def _validate_boot_ctx(boot_ctx: dict[str, Any]) -> None:
    if not isinstance(boot_ctx, dict):
        raise RuntimeError('启动参数无效: boot_ctx')
    if int(boot_ctx.get('protocol', 0)) != BOOT_PROTOCOL:
        raise RuntimeError('启动协议不匹配')
    if str(boot_ctx.get('caller', '')) != BOOT_CALLER:
        raise RuntimeError('启动来源不匹配')
    if str(boot_ctx.get('signature', '')) != BOOT_SIGNATURE:
        raise RuntimeError('启动签名无效')


def _get_current_process_info(pid: int) -> dict[str, str]:
    ps_script = (
        '$targetPid={0};'
        '$proc=Get-Process -Id $targetPid -ErrorAction Stop;'
        '$wmi=Get-CimInstance Win32_Process -Filter "ProcessId=$targetPid";'
        '[pscustomobject]@{{'
        'ProcessName=$proc.ProcessName;'
        'Path=$proc.Path;'
        'CommandLine=$wmi.CommandLine;'
        '}} | ConvertTo-Json -Compress'
    ).format(int(pid))
    raw_output = subprocess.check_output(
        ['powershell', '-NoProfile', '-Command', ps_script],
        stderr=subprocess.STDOUT,
    )
    output = ''
    for encoding in ('utf-8', 'utf-8-sig', sys.getdefaultencoding(), 'mbcs', 'gbk'):
        try:
            output = raw_output.decode(encoding)
            break
        except Exception:
            continue
    if not output:
        output = raw_output.decode('utf-8', errors='replace')

    payload = output.strip()
    if payload:
        json_start = payload.find('{')
        json_end = payload.rfind('}')
        if json_start >= 0 and json_end >= json_start:
            payload = payload[json_start : json_end + 1]
    raw = json.loads(payload)
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _verify_host_process_or_raise() -> None:
    err_msg = '当前版本无法启动。'
    if not getattr(sys, 'frozen', False):
        return
    if not ALLOWED_EXE_BASENAMES or not ALLOWED_PROCESS_NAMES:
        raise RuntimeError(err_msg)

    exe_path = Path(sys.executable).resolve()
    exe_name = exe_path.name
    if exe_name not in ALLOWED_EXE_BASENAMES:
        raise RuntimeError(err_msg)
    try:
        proc_info = _get_current_process_info(os.getpid())
    except Exception as exc:
        raise RuntimeError(err_msg) from exc
    process_name = str(proc_info.get('ProcessName', '')).strip()
    process_path_raw = str(proc_info.get('Path', '')).strip()
    command_line = str(proc_info.get('CommandLine', '')).strip()

    if process_name not in ALLOWED_PROCESS_NAMES:
        raise RuntimeError(err_msg)
    if process_path_raw:
        try:
            process_path = Path(process_path_raw).resolve()
            if process_path != exe_path:
                raise RuntimeError(err_msg)
        except Exception as exc:
            raise RuntimeError(err_msg) from exc
    if not command_line or exe_name.lower() not in command_line.lower():
        raise RuntimeError(err_msg)


def _verify_main_window_title_or_raise(window: MainWindow) -> None:
    err_msg = '当前版本无法启动。'
    title = str(window.windowTitle() or '').strip()
    if not title.startswith(APP_WINDOW_TITLE):
        raise RuntimeError(err_msg)


def build_main_window(instance_manager: InstanceManager, boot_ctx: dict[str, Any]) -> MainWindow:
    _validate_boot_ctx(boot_ctx)
    _verify_host_process_or_raise()
    window = MainWindow(instance_manager)
    _verify_main_window_title_or_raise(window)
    return window
