"""通用活动任务：按配置资源依次点击执行。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

from core.engine.task.registry import TaskResult
from core.ui.assets import ASSET_NAME_TO_CONST
from core.ui.page import page_main
from tasks.base import TaskBase

if TYPE_CHECKING:
    from core.base.button import Button


# 当期活动默认配置
DEFAULT_ACTIVITY_NAME = '荷风十里蝉初鸣'
DEFAULT_DAILY_TIMES = ['00:01']
DEFAULT_RESOURCES = [
    'btn_hefeng_100:threshold=0.74',  # 兼容微信的字体
    'btn_hefeng_101',
    #'btn_hefeng_102:threshold=0.95',
    'btn_hefeng_103:4.0',
    'EVENT_TOP_TAP',
    'btn_hefeng_104_s:1.2',
    'btn_hefeng_105_s:4.0',
    'EVENT_TOP_TAP',
    'btn_hefeng_106',
    'btn_hefeng_107:2',
    'btn_hefeng_108',
]
DEFAULT_END_TIME = '2026-07-21 00:00:00'
CLICK_ANIMATION_DELAY = 0.8
# 顶部偏上位置的点击坐标，用于关闭某些活动弹窗。
TOP_TAP_POINT = (270, 80)
_END_TIME_FORMATS = ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d')


class TaskEvent(TaskBase):
    """封装 `TaskEvent` 任务的执行入口与步骤。"""

    def __init__(self, engine, ui):
        """初始化对象并准备运行所需状态。"""
        super().__init__(engine, ui)

    def run(self, rect: tuple[int, int, int, int]) -> TaskResult:
        _ = rect
        logger.info('活动: 开始 | 名称={}', DEFAULT_ACTIVITY_NAME)
        self.ui.ui_ensure(page_main)

        view = self.task.event
        self._sync_event_config(view.feature)
        if self._is_event_ended(view.feature.end_time):
            logger.info('活动: 已到达结束时间，自动关闭任务')
            return self.ok()

        buttons = self._resolve_buttons(view.feature)
        if not buttons:
            logger.warning('活动: 没有可执行资源')
            return self.ok()

        for name, button, delay, top_click, threshold in buttons:
            self.ui.device.screenshot()
            if name == 'EVENT_TOP_TAP':
                logger.info('活动: 点击顶部区域 | name={} delay={}s', name, delay)
                self._click_top_area()
                self.ui.device.sleep(delay)
                continue
            if self.ui.appear_then_click(button, offset=30, interval=1, threshold=threshold):
                logger.info(
                    '活动: 点击资源 | name={} delay={}s top_click={} threshold={}', name, delay, top_click, threshold
                )
                if top_click:
                    self.ui.device.sleep(delay)
                    self._click_top_area()
                    self.ui.device.sleep(CLICK_ANIMATION_DELAY)
                else:
                    self.ui.device.sleep(delay)
            else:
                logger.debug('活动: 资源未出现 | name={}', name)

        logger.info('活动: 结束')
        return self.ok()

    @staticmethod
    def sync_event_config(config, emit: Callable[[], None] | None = None) -> None:
        """当活动默认配置变化时，将默认配置同步到本地配置。

        该逻辑抽离为静态方法，便于在任务未启用时由执行器主动调用，
        确保默认活动配置及时落盘。
        """
        cfg = config.tasks.get('event')
        if cfg is None:
            return

        features = cfg.features if isinstance(cfg.features, dict) else {}
        current_resources = list(features.get('resources', [])) if isinstance(features.get('resources'), list) else []
        current_daily_times = list(cfg.daily_times) if isinstance(cfg.daily_times, list) else []
        current_activity_name = str(features.get('activity_name', '') or '')
        current_end_time = str(features.get('end_time', '') or '')

        needs_sync = (
            current_activity_name != DEFAULT_ACTIVITY_NAME
            or current_resources != list(DEFAULT_RESOURCES)
            or current_end_time != DEFAULT_END_TIME
            or current_daily_times != list(DEFAULT_DAILY_TIMES)
        )
        if not needs_sync:
            return

        new_features = dict(features)
        new_features['activity_name'] = DEFAULT_ACTIVITY_NAME
        new_features['resources'] = list(DEFAULT_RESOURCES)
        new_features['end_time'] = DEFAULT_END_TIME
        # 保留用户已有的点券开关，避免覆盖用户偏好。
        new_features.setdefault('use_coupon', bool(new_features.get('use_coupon', False)))
        cfg.features = new_features
        cfg.daily_times = list(DEFAULT_DAILY_TIMES)
        try:
            config.save()
            if callable(emit):
                try:
                    emit()
                except Exception:
                    pass
            logger.info('活动: 已同步默认配置 | name={}', DEFAULT_ACTIVITY_NAME)
        except Exception as exc:
            logger.warning('活动: 同步默认配置失败 | error={}', exc)

    def _sync_event_config(self, feature) -> None:
        """实例入口：复用类级同步逻辑。"""
        _ = feature
        self.sync_event_config(self.config, self._emit_config_if_available)

    def _is_event_ended(self, end_time_text: str) -> bool:
        """判断活动是否已到达结束时间；到达时自动关闭任务开关。"""
        end_time = self._parse_end_time(str(end_time_text or '').strip())
        if end_time is None:
            return False
        if datetime.now() < end_time:
            return False

        cfg = self.config.tasks.get('event')
        if cfg is not None and cfg.enabled:
            cfg.enabled = False
            executor_tasks = getattr(self.engine, '_executor_tasks', None)
            if isinstance(executor_tasks, dict):
                item = executor_tasks.get('event')
                if item is not None:
                    item.enabled = False
            try:
                self.config.save()
                self._emit_config_if_available()
            except Exception as exc:
                logger.warning('活动: 关闭任务开关失败 | error={}', exc)
        return True

    @staticmethod
    def _parse_end_time(text: str) -> datetime | None:
        """解析结束时间文本。"""
        if not text:
            return None
        for fmt in _END_TIME_FORMATS:
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                continue
        return None

    DEFAULT_THRESHOLD = 0.86

    def _resolve_buttons(self, feature) -> list[tuple[str, Button | None, float, bool, float]]:
        """根据配置解析出需要点击的资源按钮列表、点击后延迟、是否点击顶部及置信度阈值。"""
        use_coupon = bool(feature.use_coupon)
        raw_resources = list(feature.resources) if isinstance(feature.resources, list) else []
        out: list[tuple[str, Button | None, float, bool, float]] = []
        for entry in raw_resources:
            name, delay, top_click, threshold = self._parse_resource_entry(str(entry or '').strip())
            if not name:
                continue
            if name == 'EVENT_TOP_TAP':
                out.append((name, None, delay, False, threshold))
                continue
            if name.endswith('_s') and not use_coupon:
                logger.debug('活动: 跳过点券资源 | name={}', name)
                continue
            button = ASSET_NAME_TO_CONST.get(name)
            if button is None:
                logger.warning('活动: 未知资源 | name={}', name)
                continue
            out.append((name, button, delay, top_click, threshold))
        return out

    @staticmethod
    def _parse_resource_entry(entry: str) -> tuple[str, float, bool, float]:
        """解析资源项，支持 `btn_name:delay:top:threshold=0.9` 格式；省略项使用默认。"""
        if not entry:
            return '', CLICK_ANIMATION_DELAY, False, TaskEvent.DEFAULT_THRESHOLD
        if ':' not in entry:
            return entry, CLICK_ANIMATION_DELAY, False, TaskEvent.DEFAULT_THRESHOLD
        parts = [part.strip() for part in str(entry).split(':')]
        name = parts[0]
        delay = CLICK_ANIMATION_DELAY
        top_click = False
        threshold = TaskEvent.DEFAULT_THRESHOLD
        for part in parts[1:]:
            if part == 'top':
                top_click = True
                continue
            if part.lower().startswith('threshold='):
                try:
                    threshold = max(0.0, min(1.0, float(part.split('=', 1)[1])))
                except Exception:
                    pass
                continue
            if not part:
                continue
            try:
                delay = max(0.0, float(part))
            except Exception:
                # 无法识别时直接跳过该段。
                continue
        return name, delay, top_click, threshold

    def _click_top_area(self) -> None:
        """点击设备顶部偏上位置，用于关闭活动后的特定弹窗。"""
        x, y = TOP_TAP_POINT
        try:
            self.ui.device.click_point(int(x), int(y), desc='event_top_tap')
            logger.debug('活动: 点击顶部区域 | point=({}, {})', x, y)
        except Exception as exc:
            logger.warning('活动: 点击顶部区域失败 | error={}', exc)

    def _emit_config_if_available(self) -> None:
        """如果引擎支持，立即推送一次配置变更事件。"""
        emit = getattr(self.engine, '_emit_config_now', None)
        if callable(emit):
            try:
                emit()
            except Exception:
                pass
