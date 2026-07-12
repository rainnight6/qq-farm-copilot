"""TaskMain 一键动作相关逻辑。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from loguru import logger

from core.base.timer import Timer
from core.ui.assets import *
from core.ui.page import GOTO_MAIN, page_main, page_mall
from models.farm_state import ActionType
from utils.app_paths import instance_screenshots_dir
from utils.land_grid import get_lands_from_land_anchor
from utils.ocr_utils import OCRItem

if TYPE_CHECKING:
    from core.engine.bot.local_engine import LocalBotEngine
    from core.ui.ui import UI

# 肥料库存 OCR 兜底区域（未识别到肥料按钮时使用）。
FERTILIZE_HOURS_OCR_REGION = (0, 650, 540, 900)
# 肥料库存文本提取模式（优先匹配“数字+小时/h”，支持小数）。
FERTILIZE_HOURS_PATTERN = re.compile(r'(\d+(?:\.\d+)?)\s*(?:小时|h|H)')
# 文本中的数字模式（兜底）。
FERTILIZE_NUMBER_PATTERN = re.compile(r'\d+(?:\.\d+)?')
# 成熟倒计时 `HH:MM:SS` 模式。
FERTILIZE_COUNTDOWN_PATTERN = re.compile(r'^(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})$')
# 商店列表上滑的起点/终点坐标（用于翻页查找肥料）。
# 实际为从底部向上滑动，使列表内容向下滚动，露出下方商品。
FERTILIZE_SHOP_LIST_SWIPE_START = (270, 860)
FERTILIZE_SHOP_LIST_SWIPE_END = (270, 300)
# 自动补货最多尝试轮次（每轮=购买一次+复检一次）。
FERTILIZE_BUY_MAX_ROUNDS = 6
# 商店 OCR 翻页上限。
FERTILIZE_SHOP_OCR_MAX_PAGES = 8
# 商城购买弹窗数量 "+" 按钮坐标（默认数量 1，每点一次 +1）。
FERTILIZE_SHOP_PLUS_BUTTON = (345, 525)
# 单次购买数量上限。
FERTILIZE_SHOP_MAX_BUY_COUNT = 99

# 施肥地块点击时的横向滑动修正阈值与点位（复用地块巡查逻辑）。
# x<160 时画面左移（手指从左向右滑 P2->P1），x>330 时画面右移（手指从右向左滑 P1->P2），
# 使边缘地块进入可点击区域。
_FERTILIZE_EDGE_SWIPE_LEFT_THRESHOLD = 160
_FERTILIZE_EDGE_SWIPE_RIGHT_THRESHOLD = 330
_FERTILIZE_SWIPE_H_P1 = (250, 190)
_FERTILIZE_SWIPE_H_P2 = (200, 190)
_FERTILIZE_SWIPE_STEP_DELAY = 0.2
_FERTILIZE_SWIPE_X_INTERVAL = abs(_FERTILIZE_SWIPE_H_P1[0] - _FERTILIZE_SWIPE_H_P2[0])
# 屏幕宽度，用于判断后续地块是否仍在屏幕内。
_FERTILIZE_SCREEN_WIDTH = 540
# 9 列布局下最左 2 列与最右 2 列的地块编号，用于优先从中间地块开始施肥，减少滑动。
_FERTILIZE_EDGE_PLOT_REFS: frozenset[str] = frozenset(
    {
        '1-4',  # physical col 1
        '1-3',  # physical col 2
        '2-4',  # physical col 2
        '5-1',  # physical col 8
        '6-2',  # physical col 8
        '6-1',  # physical col 9
    }
)


class TaskMainActionsMixin:
    """提供一键收获/务农/施肥能力。"""

    engine: 'LocalBotEngine'
    ui: 'UI'

    def _run_feature_harvest(self) -> str | None:
        """一键收获"""
        self.ui.device.screenshot()
        if not self.ui.appear(BTN_HARVEST, offset=30, static=False) and not self.ui.appear(
            BTN_MATURE, offset=30, static=False
        ):
            return None

        confirm_timer = Timer(0.2, count=1)
        while 1:
            self.ui.device.screenshot()

            if self.ui.appear_then_click(BTN_HARVEST, offset=30, interval=1, static=False):
                self.engine._record_stat(ActionType.HARVEST)
                continue
            # if self.ui.appear_then_click(BTN_MATURE, offset=30, interval=1, static=False):
            #     self.engine._record_stat(ActionType.HARVEST)
            #     continue
            if not self.ui.appear(BTN_HARVEST, offset=30, static=False):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    result = '一键收获'
                    break
            else:
                confirm_timer.clear()

        return result

    def _run_feature_maintain_actions(
        self,
        *,
        enable_farming: bool,
    ) -> str | None:
        """统一执行一键务农，共用确认计时器。"""
        action_specs = []
        if enable_farming:
            action_specs.append((BTN_FARMING, ActionType.FARMING))
        if not action_specs:
            return None
        action_buttons = [button for button, _ in action_specs]

        logger.info(
            '一键维护流程: 开始 | 务农={}',
            enable_farming,
        )

        self.ui.device.screenshot()
        if not self.ui.appear_any(action_buttons, offset=30, static=False):
            return None

        confirm_timer = Timer(0.5, count=2)
        while 1:
            self.ui.device.screenshot()

            clicked_action: str | None = None
            for button, stat_action in action_specs:
                if self.ui.appear(button, offset=30, static=False):
                    clicked_action = stat_action
                    break
            if self.ui.appear_then_click_any(action_buttons, offset=30, interval=0.3, static=False):
                if clicked_action is not None:
                    self.engine._record_stat(clicked_action)
                confirm_timer.clear()
                continue

            if not self.ui.appear_any(action_buttons, offset=30, static=False):
                if not confirm_timer.started():
                    confirm_timer.start()
                if confirm_timer.reached():
                    return '一键维护'
            else:
                confirm_timer.clear()

    def _save_merchant_screenshot(self) -> None:
        """保存神秘商人购买前的截图到实例截图目录，文件名带时间戳。"""
        try:
            instance_id = getattr(self.engine, 'instance_id', 'default') or 'default'
            save_dir = instance_screenshots_dir(instance_id) / 'merchant'
            save_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]
            path = save_dir / f'merchant_{timestamp}.png'
            preview = self.ui.device.preview_image
            if preview is not None:
                preview.save(path, format='PNG')
                logger.info(f'神秘商人: 截图已保存 {path}')
            else:
                logger.warning('神秘商人: 预览图为空，无法保存截图')
        except Exception as exc:
            logger.warning(f'神秘商人: 保存截图失败 {exc}')

    def _run_feature_merchant(self) -> None:
        """神秘商人：主界面出现商人图标时点击购买。"""
        logger.info('神秘商人: 开始')
        self.ui.device.screenshot()
        if not self.ui.appear(BTN_MERCHANT, offset=30, static=False, threshold=0.8):
            logger.info('神秘商人: 当前未出现')
            return

        if not self.ui.appear_then_click(BTN_MERCHANT, offset=30, interval=1, static=False, threshold=0.8):
            logger.warning('神秘商人: 点击图标失败')
            return

        popup_timer = Timer(3.0).start()
        while 1:
            self.ui.device.screenshot()
            if self.ui.appear(BTN_MERCHANT_CONFIRM, offset=30, static=False, threshold=0.8):
                break
            if popup_timer.reached():
                logger.warning('神秘商人: 弹窗加载超时')
                return
            self.ui.device.sleep(0.2)

        self._save_merchant_screenshot()

        confirm_timer = Timer(3.0).start()
        confirmed = False
        while 1:
            self.ui.device.screenshot()
            if self.ui.appear_then_click(BTN_MERCHANT_CONFIRM, offset=30, interval=1, static=False, threshold=0.8):
                confirmed = True
                logger.info('神秘商人: 已点击购买')
                self.engine._record_stat(ActionType.MERCHANT)
                self.ui.device.sleep(0.5)
                break
            if confirm_timer.reached():
                logger.warning('神秘商人: 购买确认失败')
                break
            self.ui.device.sleep(0.2)

        if not confirmed:
            return

        close_timer = Timer(3.0).start()
        while 1:
            self.ui.device.screenshot()
            if not self.ui.appear(BTN_MERCHANT_CONFIRM, offset=30, static=False, threshold=0.8):
                logger.info('神秘商人: 弹窗已关闭')
                break
            if close_timer.reached():
                logger.warning('神秘商人: 弹窗关闭超时，尝试返回主页')
                self.ui.device.click_button(GOTO_MAIN)
                break
            self.ui.device.sleep(0.2)

    def _run_feature_fertilize(self) -> str | None:
        """自动施肥：按土地巡查数据筛选地块并执行施肥。"""
        features = self.task.main.feature
        if not features.auto_fertilize:
            return None

        land_scan_cfg = self.config.tasks.get('land_scan')
        if land_scan_cfg is None or not land_scan_cfg.enabled:
            logger.info('自动施肥: 地块巡查未开启，跳过施肥')
            return None

        skip_rounds = int(self.config.planting.skip_fertilize_after_seed_rounds)
        if skip_rounds > 0:
            self.config.planting.skip_fertilize_after_seed_rounds = max(0, skip_rounds - 1)
            try:
                self.config.save()
            except Exception as exc:
                logger.warning('自动施肥: 保存跳过轮数失败 | error={}', exc)
            logger.info('自动施肥: 刚播种新种子，跳过本轮施肥 | 剩余跳过轮数={}', skip_rounds - 1)
            return None

        threshold_seconds = max(1, int(features.maturity_threshold_seconds))
        auto_buy = bool(features.auto_buy_fertilizer)
        buy_threshold_seconds = max(1, int(features.fertilizer_purchase_threshold_seconds))
        buy_threshold_hours = self._seconds_to_hours_ceil(buy_threshold_seconds)

        logger.info(
            '自动施肥: 开始 | 成熟阈值={}s 自动买肥={} 购买阈值={}h',
            threshold_seconds,
            auto_buy,
            buy_threshold_hours,
        )

        self.ui.ui_ensure(page_main)
        self.ui.device.click_button(GOTO_MAIN)
        self.align_view_by_background_tree(log_prefix='自动施肥')

        use_organic = bool(features.use_organic_fertilizer)
        target_plot_refs = self._collect_fertilize_plot_refs(
            threshold_seconds=threshold_seconds,
            use_organic=use_organic,
        )
        if not target_plot_refs:
            logger.info('自动施肥: 没有命中成熟阈值的地块，结束本轮')
            return None

        all_targets = self._collect_fertilize_targets_for_refs(target_plot_refs)
        if not all_targets:
            logger.warning('自动施肥: 地块坐标映射失败，跳过本轮')
            return None

        # 维护横向视图偏移，处理边缘地块点击前的滑动修正。
        view_offset_x = 0

        # 找一个非空地块来探测肥料库存；如果第一个地块是空地，继续尝试后面的
        probe_result: tuple[int, tuple[int, int]] | None = None
        probe_ref: str = ''
        probe_point: tuple[int, int] = (0, 0)
        for ref, point in all_targets:
            adjusted_point, view_offset_x = self._adjust_fertilize_view_offset(point, view_offset_x)
            probe_result = self._probe_fertilizer_hours(plot_ref=ref, point=adjusted_point)
            if probe_result is not None:
                probe_ref = ref
                probe_point = adjusted_point
                break
        available_hours = probe_result[0] if probe_result is not None else 0
        quantity_point = probe_result[1] if probe_result is not None else (0, 0)

        required_hours = len(target_plot_refs)

        # 库存不足时，先打开地块弹窗，再点击化肥数量使用背包中的化肥
        if available_hours < required_hours and quantity_point != (0, 0):
            logger.info(
                '自动施肥: 当前库存不足，尝试使用背包化肥 | available={}h required={}h',
                available_hours,
                required_hours,
            )
            popup_opened = self._open_plot_popup_for_fertilize(plot_ref=probe_ref, point=probe_point)
            if popup_opened and self._use_backpack_fertilizer(quantity_point):
                self.ui.device.click_button(GOTO_MAIN)
                self.ui.device.sleep(0.3)
                probe_result = self._probe_fertilizer_hours(plot_ref=probe_ref, point=probe_point)
                if probe_result is not None:
                    available_hours, quantity_point = probe_result
            elif popup_opened:
                self.ui.device.click_button(GOTO_MAIN)
                self.ui.device.sleep(0.3)

        # 若开启自动购买且库存低于购买阈值，触发补货
        if auto_buy and available_hours < buy_threshold_hours:
            logger.info(
                '自动施肥: 当前库存低于购买阈值，触发补货 | available={}h threshold={}h',
                available_hours,
                buy_threshold_hours,
            )
            probe_ref_for_buy = probe_ref if probe_ref else all_targets[0][0]
            available_hours, quantity_point, _, view_offset_x = self._ensure_fertilizer_hours(
                target_hours=buy_threshold_hours,
                probe_ref=probe_ref_for_buy,
                view_offset_x=view_offset_x,
            )

        if available_hours < required_hours:
            logger.warning(
                '自动施肥: 肥料不足 | available={}h required={}h',
                available_hours,
                required_hours,
            )
            if not auto_buy:
                logger.warning('自动施肥: 未开启自动购买肥料，结束本轮')
            else:
                logger.warning(
                    '自动施肥: 按阈值补货后仍不足本轮需求 | threshold={}h',
                    buy_threshold_hours,
                )
            return None

        use_organic = bool(features.use_organic_fertilizer)

        # 分批拖拽施肥：每轮先对第一个未施肥地块循环滑动到 [160, 330] 可点击区域，
        # 再拖拽到当前 view_offset_x 下所有仍在屏幕内（0 <= x <= 540）的地块；
        # 超出屏幕的跳过，留到下一轮重新判断并滑动修正。
        fertilized_flags = [False] * len(all_targets)
        while not all(fertilized_flags):
            first_idx = next((i for i, done in enumerate(fertilized_flags) if not done), -1)
            if first_idx < 0:
                break
            first_ref, first_point = all_targets[first_idx]
            adjusted_first, view_offset_x = self._adjust_fertilize_view_offset(first_point, view_offset_x)

            if not self._open_plot_popup_for_fertilize(plot_ref=first_ref, point=adjusted_first):
                logger.warning('自动施肥: 无法打开地块弹窗，跳过该地块 | plot={}', first_ref)
                fertilized_flags[first_idx] = True
                self.ui.device.click_button(GOTO_MAIN)
                self.ui.device.sleep(0.2)
                continue

            if use_organic:
                fertilizer_loc = self.ui.appear_location(BTN_ORGANIC_FERTILIZER, offset=30, static=False)
            else:
                fertilizer_loc = self.ui.appear_location(BTN_ORDINARY_FERTILIZER, offset=30, static=False)
                if fertilizer_loc is None:
                    fertilizer_loc = self.ui.appear_location(BTN_ORGANIC_FERTILIZER, offset=30, static=False)
            if fertilizer_loc is None:
                logger.warning('自动施肥: 分批施肥未识别到肥料按钮 | use_organic={}', use_organic)
                fertilized_flags[first_idx] = True
                self.ui.device.click_button(GOTO_MAIN)
                self.ui.device.sleep(0.2)
                continue

            drag_x, drag_y = int(fertilizer_loc[0]), int(fertilizer_loc[1])
            dragging = False
            try:
                self.ui.device.drag_down_point(drag_x, drag_y, duration=0.1)
                dragging = True
                self.ui.device.sleep(0.1)

                # 第一个地块：已循环滑动到范围内
                self.ui.device.drag_move_point(int(adjusted_first[0]), int(adjusted_first[1]), duration=0.1)
                self.ui.device.sleep(0.15)
                fertilized_flags[first_idx] = True

                # 后续地块：仅处理当前 view_offset_x 下仍在屏幕内的地块；
                # 第一个地块已循环滑动到 [160, 330] 精确区域，后续地块只要没超出屏幕即可拖拽。
                for i in range(first_idx + 1, len(all_targets)):
                    if fertilized_flags[i]:
                        continue
                    ref, point = all_targets[i]
                    effective_x = int(point[0]) + view_offset_x
                    if not (0 <= effective_x <= _FERTILIZE_SCREEN_WIDTH):
                        logger.debug(
                            '自动施肥: 地块超出屏幕范围，跳过 | plot={} effective_x={}',
                            ref,
                            effective_x,
                        )
                        continue
                    self.ui.device.drag_move_point(int(effective_x), int(point[1]), duration=0.1)
                    self.ui.device.sleep(0.15)
                    fertilized_flags[i] = True
            finally:
                if dragging:
                    self.ui.device.drag_up()

            self.ui.device.click_button(GOTO_MAIN)
            self.ui.device.sleep(0.2)

        self.ui.device.click_button(GOTO_MAIN)
        self.ui.device.sleep(0.2)
        self.ui.ui_ensure(page_main)

        fertilized_count = sum(fertilized_flags)
        fertilized_refs = [str(all_targets[i][0]) for i, done in enumerate(fertilized_flags) if done]
        for _ in range(fertilized_count):
            self.engine._record_stat(ActionType.FERTILIZE)

        if fertilized_refs and not use_organic:
            self._record_fertilize_time(fertilized_refs, threshold_seconds=threshold_seconds)

        logger.info('自动施肥: 结束 | fertilized={}/{}', fertilized_count, required_hours)
        return '自动施肥' if fertilized_count > 0 else None

    def _record_fertilize_time(self, plot_refs: list[str], *, threshold_seconds: int) -> None:
        """记录地块本次施肥时间与真实剩余成熟时间，用于普通化肥冷却防重施。"""
        if not plot_refs:
            return
        plots = self.config.land.plots
        if not isinstance(plots, list):
            return
        now = datetime.now()
        now_text = now.replace(microsecond=0).strftime('%Y-%m-%d %H:%M:%S')
        changed = False
        for plot_ref in plot_refs:
            target = str(plot_ref or '').strip()
            if not target:
                continue
            for item in plots:
                if not isinstance(item, dict):
                    continue
                if str(item.get('plot_id', '')).strip() != target:
                    continue
                countdown_seconds = self._parse_fertilize_countdown_seconds(item.get('maturity_countdown'))
                sync_time_text = str(item.get('countdown_sync_time') or '').strip().replace('T', ' ')
                real_remaining_seconds: int | None = None
                if countdown_seconds is not None and sync_time_text:
                    try:
                        sync_time = datetime.strptime(sync_time_text, '%Y-%m-%d %H:%M:%S')
                        real_remaining_seconds = int(
                            (sync_time + timedelta(seconds=countdown_seconds) - now).total_seconds()
                        )
                        real_remaining_seconds = max(0, real_remaining_seconds)
                    except Exception:
                        pass
                if (
                    real_remaining_seconds is None
                    or real_remaining_seconds <= 0
                    or real_remaining_seconds > int(threshold_seconds)
                ):
                    real_remaining_seconds = int(threshold_seconds)

                old_time = str(item.get('last_fertilize_time') or '').strip()
                old_remaining = str(item.get('last_real_remaining_seconds') or '').strip()
                if old_time != now_text:
                    item['last_fertilize_time'] = now_text
                    changed = True
                if old_remaining != str(real_remaining_seconds):
                    item['last_real_remaining_seconds'] = real_remaining_seconds
                    changed = True
                break
        if not changed:
            return
        try:
            self.config.save()
        except Exception as exc:
            logger.warning('自动施肥: 保存施肥时间失败 | refs={} error={}', plot_refs, exc)
            return
        logger.info('自动施肥: 已记录普通化肥施肥时间 | refs={} time={}', plot_refs, now_text)

    def _trigger_feature_harvest_after_fertilize(self) -> str | None:
        """施肥后立即调用一次一键收获，将刚施肥成熟的作物收掉。"""
        self.ui.ui_ensure(page_main)
        self.ui.device.click_button(GOTO_MAIN)
        self.ui.device.sleep(0.3)
        self.align_view_by_background_tree(log_prefix='一键收获-施肥后回正')
        logger.info('自动施肥: 触发一次一键收获')
        return self._run_feature_harvest()

    def _trigger_land_scan_after_plant(self) -> None:
        """播种后若地块巡查已启用，则立即触发一次巡查更新地块状态。"""
        if not self.is_task_enabled('land_scan'):
            logger.info('自动播种: 地块巡查未启用，跳过触发')
            return
        logger.info('自动播种: 触发一次地块巡查')
        # 播后画面可能仍有惯性/弹窗残留，先清理并回正，避免地块巡查初始锚点识别偏移
        self.ui.ui_ensure(page_main)
        self.ui.device.click_button(GOTO_MAIN)
        self.ui.device.sleep(0.3)
        self.align_view_by_background_tree(log_prefix='自动播种-播后回正')
        self.task.land_scan.call(force_call=True)

    def _collect_fertilize_plot_refs(self, *, threshold_seconds: int, use_organic: bool) -> list[str]:
        """从土地详情里筛选真实剩余成熟时间小于阈值的地块；普通化肥冷却期为该地块真实剩余成熟时间。"""
        now = datetime.now()
        candidates: list[tuple[int, str]] = []
        for item in self.parse_land_detail_plots():
            plot_ref = str(item.get('plot_id', '') or '').strip()
            countdown_seconds = self._parse_fertilize_countdown_seconds(item.get('maturity_countdown'))
            sync_time_text = str(item.get('countdown_sync_time') or '').strip().replace('T', ' ')
            if not plot_ref or countdown_seconds is None or not sync_time_text:
                continue
            try:
                sync_time = datetime.strptime(sync_time_text, '%Y-%m-%d %H:%M:%S')
            except Exception:
                continue
            real_remaining_seconds = int((sync_time + timedelta(seconds=countdown_seconds) - now).total_seconds())
            real_remaining_seconds = max(0, real_remaining_seconds)
            if real_remaining_seconds <= 0 or real_remaining_seconds > int(threshold_seconds):
                continue
            if not use_organic:
                last_fertilize_text = str(item.get('last_fertilize_time') or '').strip().replace('T', ' ')
                last_real_remaining_text = str(item.get('last_real_remaining_seconds') or '').strip()
                if last_fertilize_text and last_real_remaining_text:
                    try:
                        last_fertilize_time = datetime.strptime(last_fertilize_text, '%Y-%m-%d %H:%M:%S')
                        last_real_remaining_seconds = int(last_real_remaining_text)
                        elapsed_seconds = int((now - last_fertilize_time).total_seconds())
                        cooldown_seconds = max(0, last_real_remaining_seconds)
                        if 0 <= elapsed_seconds < cooldown_seconds:
                            logger.debug(
                                '自动施肥: 普通化肥冷却中，跳过 | plot={} elapsed={}s cooldown={}s',
                                plot_ref,
                                elapsed_seconds,
                                cooldown_seconds,
                            )
                            continue
                    except Exception:
                        pass
            candidates.append((real_remaining_seconds, plot_ref))

        candidates.sort(key=lambda row: (row[0], row[1]))
        refs = [plot_ref for _, plot_ref in candidates]
        # 若首个地块位于最左/最右 2 列，则将其后置，优先从中间地块开始拖拽；
        # 若全部命中地块都在边缘列，则不做调整。
        if refs and refs[0] in _FERTILIZE_EDGE_PLOT_REFS:
            if not all(ref in _FERTILIZE_EDGE_PLOT_REFS for ref in refs):
                refs = refs[1:] + [refs[0]]
        logger.info('自动施肥: 倒计时命中地块={}', refs)
        return refs

    @staticmethod
    def _parse_fertilize_countdown_seconds(value: Any) -> int | None:
        """解析成熟倒计时为秒。"""
        text = str(value or '').strip()
        if not text:
            return None
        match = FERTILIZE_COUNTDOWN_PATTERN.match(text)
        if not match:
            return None
        hour = int(match.group('h'))
        minute = int(match.group('m'))
        second = int(match.group('s'))
        if minute < 0 or minute > 59 or second < 0 or second > 59:
            return None
        return hour * 3600 + minute * 60 + second

    def _collect_fertilize_targets_for_refs(
        self,
        refs: list[str],
        *,
        anchor_threshold: float = 0.85,
        log_prefix: str = '自动施肥',
    ) -> list[tuple[str, tuple[int, int]]]:
        """将地块编号映射为当前画面中心点坐标。

        锚点识别阈值默认 0.85；若两个锚点均未识别到，会尝试一次截图重试。
        """
        if not refs:
            return []

        def _try_collect():
            self.ui.device.screenshot()
            right_anchor = self.appear_land_right(offset=30, threshold=float(anchor_threshold), static=False)
            left_anchor = self.ui.appear_location(
                BTN_LAND_LEFT, offset=30, threshold=float(anchor_threshold), static=False
            )
            return right_anchor, left_anchor

        land_right_anchor, land_left_anchor = _try_collect()
        need_retry = land_right_anchor is None or land_left_anchor is None
        if need_retry:
            logger.warning(
                '{}: 地块锚点识别不全（right={} left={}），先重置缩放再重试 | refs={}',
                log_prefix,
                land_right_anchor is not None,
                land_left_anchor is not None,
                refs,
            )
            self.align_view_by_background_tree(log_prefix=f'{log_prefix}: 重置缩放')
            land_right_anchor, land_left_anchor = _try_collect()
            if land_right_anchor is None and land_left_anchor is None:
                logger.warning('{}: 重试后仍未识别到地块锚点 | refs={}', log_prefix, refs)
                return []

        all_lands = get_lands_from_land_anchor(
            (int(land_right_anchor[0]), int(land_right_anchor[1])) if land_right_anchor is not None else None,
            (int(land_left_anchor[0]), int(land_left_anchor[1])) if land_left_anchor is not None else None,
        )
        if not all_lands:
            logger.warning('{}: 网格生成失败 | refs={}', log_prefix, refs)
            return []

        center_by_plot_id = {str(cell.label): (int(cell.center[0]), int(cell.center[1])) for cell in all_lands}
        targets: list[tuple[str, tuple[int, int]]] = []
        for ref in refs:
            point = center_by_plot_id.get(str(ref))
            if point is None:
                logger.warning('{}: 当前画面缺失地块坐标 | plot={}', log_prefix, ref)
                continue
            targets.append((str(ref), point))
        return targets

    def _adjust_fertilize_view_offset(
        self,
        point: tuple[int, int],
        view_offset_x: int,
    ) -> tuple[tuple[int, int], int]:
        """根据当前横向偏移循环滑动修正，直到坐标进入可点击区域。

        复用地块巡查的滑动修正逻辑：
        - 修正后 x < 160 时画面左移（手指从左向右滑 P2->P1），偏移量增加一个滑动间隔；
        - 修正后 x > 330 时画面右移（手指从右向左滑 P1->P2），偏移量减少一个滑动间隔。
        循环执行，直到坐标落在 [160, 330] 范围内，避免边缘/超屏地块无法点击。
        """
        x, y = int(point[0]), int(point[1])
        while True:
            effective_x = x + view_offset_x
            if effective_x < _FERTILIZE_EDGE_SWIPE_LEFT_THRESHOLD:
                # 目标偏左，需要画面左移：手指从左向右滑（P2 -> P1）。
                self.ui.device.swipe(_FERTILIZE_SWIPE_H_P2, _FERTILIZE_SWIPE_H_P1, speed=30)
                self.ui.device.sleep(float(_FERTILIZE_SWIPE_STEP_DELAY))
                view_offset_x += _FERTILIZE_SWIPE_X_INTERVAL
                new_effective_x = x + view_offset_x
                logger.info(
                    '自动施肥: 左移画面修正 | 原x={} 修正后x={} 新偏移={}',
                    effective_x,
                    new_effective_x,
                    view_offset_x,
                )
                continue
            if effective_x > _FERTILIZE_EDGE_SWIPE_RIGHT_THRESHOLD:
                # 目标偏右，需要画面右移：手指从右向左滑（P1 -> P2）。
                self.ui.device.swipe(_FERTILIZE_SWIPE_H_P1, _FERTILIZE_SWIPE_H_P2, speed=30)
                self.ui.device.sleep(float(_FERTILIZE_SWIPE_STEP_DELAY))
                view_offset_x -= _FERTILIZE_SWIPE_X_INTERVAL
                new_effective_x = x + view_offset_x
                logger.info(
                    '自动施肥: 右移画面修正 | 原x={} 修正后x={} 新偏移={}',
                    effective_x,
                    new_effective_x,
                    view_offset_x,
                )
                continue
            break
        return (effective_x, y), view_offset_x

    def _probe_fertilizer_hours(
        self,
        *,
        plot_ref: str,
        point: tuple[int, int],
    ) -> tuple[int, tuple[int, int]] | None:
        """打开一个地块弹窗并识别当前肥料库存（小时）及数量文本中心点。"""
        opened = self._open_plot_popup_for_fertilize(plot_ref=plot_ref, point=point)
        if not opened:
            self.ui.device.click_button(GOTO_MAIN)
            self.ui.device.sleep(0.2)
            return None
        hours, quantity_point = self._read_fertilizer_hours_from_popup()
        self.ui.device.click_button(GOTO_MAIN)
        self.ui.device.sleep(0.2)
        return hours, quantity_point

    def _open_plot_popup_for_fertilize(self, *, plot_ref: str, point: tuple[int, int]) -> bool:
        """点击地块坐标打开弹窗，空地或超时返回 False。"""
        self.ui.device.click_point(int(point[0]), int(point[1]), desc=f'点击施肥地块 {plot_ref}')
        self.ui.device.sleep(0.2)
        wait_timer = Timer(1.0, count=1).start()
        empty_hits = 0
        while 1:
            self.ui.device.screenshot()
            # 优先判断已播种弹窗，避免空地块误识别
            if self.ui.appear(BTN_CROP_REMOVAL, offset=30, static=False):
                return True
            if self.ui.appear(BTN_LAND_POP_EMPTY, offset=(-160, -180, 280, 280), threshold=0.75):
                empty_hits += 1
                if empty_hits >= 2:
                    logger.info('自动施肥: 地块为空地，跳过 | plot={}', plot_ref)
                    return False
            else:
                empty_hits = 0
            if wait_timer.reached():
                logger.warning('自动施肥: 地块弹窗识别超时 | plot={}', plot_ref)
                return False
            self.ui.device.sleep(0.1)

    def _read_fertilizer_hours_from_popup(self) -> tuple[int, tuple[int, int]]:
        """从地块弹窗 OCR 提取肥料库存小时数及数量文本中心点，数量坐标始终对应要使用的化肥类型。"""
        cv_img = self.ui.device.screenshot()
        if cv_img is None:
            return 0, (0, 0)

        use_organic = bool(self.task.main.feature.use_organic_fertilizer)
        ordinary_loc = self.ui.appear_location(BTN_ORDINARY_FERTILIZER, offset=30, static=False)
        organic_loc = self.ui.appear_location(BTN_ORGANIC_FERTILIZER, offset=30, static=False)

        # 根据开关确定要使用的化肥类型为主目标，数量坐标始终返回该类型的位置
        if use_organic:
            target_loc, target_name = organic_loc, '有机化肥'
            other_loc, other_name = ordinary_loc, '普通化肥'
        else:
            target_loc, target_name = ordinary_loc, '普通化肥'
            other_loc, other_name = organic_loc, '有机化肥'

        target_quantity_point = self._estimate_quantity_point_from_button(target_loc)

        if target_loc is not None:
            result = self._read_hours_at_button(cv_img, target_loc)
            if result is not None:
                hours, ocr_point = result
                logger.info('自动施肥: 识别{}库存={}h ocr_point={}', target_name, hours, ocr_point)
                # 点击坐标直接使用 OCR 识别到的数量文本中心，不加额外偏移
                return hours, ocr_point
            logger.warning('自动施肥: 未识别到{}库存文本，尝试{}兜底读数', target_name, other_name)

        if other_loc is not None:
            result = self._read_hours_at_button(cv_img, other_loc)
            if result is not None:
                hours, _ = result
                logger.info(
                    '自动施肥: 识别{}兜底库存={}h，但数量坐标仍使用{}估算点 point={}',
                    other_name,
                    hours,
                    target_name,
                    target_quantity_point,
                )
                return hours, target_quantity_point

        # 两个按钮都没定位到时，读整个兜底区域，数量坐标不可用
        result = self._read_hours_in_region(cv_img, FERTILIZE_HOURS_OCR_REGION, log_prefix='兜底')
        if result is not None:
            return result[0], (0, 0)
        return 0, (0, 0)

    @staticmethod
    def _estimate_quantity_point_from_button(button_loc: tuple[int, int] | None) -> tuple[int, int]:
        """根据肥料按钮中心估算其下方数量文本的点击坐标；未定位到按钮返回 (0,0)。"""
        if button_loc is None:
            return (0, 0)
        return (int(button_loc[0]), int(button_loc[1] + 75))

    def _read_hours_at_button(
        self,
        cv_img,
        button_loc: tuple[int, int],
    ) -> tuple[int, tuple[int, int]] | None:
        """读取按钮下方区域的肥料小时数及文本中心点，未识别返回 None。"""
        region = (
            max(0, button_loc[0] - 80),
            max(0, button_loc[1] + 10),
            min(540, button_loc[0] + 80),
            min(960, button_loc[1] + 90),
        )
        return self._read_hours_in_region(cv_img, region)

    def _read_hours_in_region(
        self,
        cv_img,
        region: tuple[int, int, int, int],
        log_prefix: str = '',
    ) -> tuple[int, tuple[int, int]] | None:
        """在指定区域内 OCR 提取肥料小时数，返回 (小时数, 文本中心点)，未识别返回 None。"""
        items = self.engine._get_ocr_tool().detect(
            cv_img,
            region=region,
            scale=1.3,
            alpha=1.15,
            beta=0.0,
        )
        prefix = f'{log_prefix}:' if log_prefix else ''
        if not items:
            logger.debug('自动施肥: {}肥料库存OCR无结果 | region={}', prefix, region)
            return None

        # 按 text 拼接并记录每个字符来自哪个 item，用于定位匹配数字的位置
        merged_items: list[tuple[str, OCRItem]] = []
        for item in items:
            text = str(item.text or '').strip().replace(' ', '')
            if text:
                merged_items.append((text, item))
        merged = ''.join(text for text, _ in merged_items)
        logger.debug('自动施肥: {}肥料库存OCR原始文本 | region={} raw={}', prefix, region, merged or '<empty>')

        # 优先匹配 "数字+小时/h/H"
        for match in FERTILIZE_HOURS_PATTERN.finditer(merged):
            item = self._find_ocr_item_covering_position(merged_items, match.start(), match.end())
            if item is not None:
                center = self._ocr_item_center(item)
                return int(float(match.group(1))), (int(center[0]), int(center[1]))

        # 兜底匹配纯数字
        for match in FERTILIZE_NUMBER_PATTERN.finditer(merged):
            item = self._find_ocr_item_covering_position(merged_items, match.start(), match.end())
            if item is not None:
                center = self._ocr_item_center(item)
                return int(float(match.group())), (int(center[0]), int(center[1]))

        return None

    @staticmethod
    def _find_ocr_item_covering_position(
        merged_items: list[tuple[str, OCRItem]],
        start: int,
        end: int,
    ) -> OCRItem | None:
        """根据合并字符串中的 [start, end) 区间，找到覆盖该区间主要部分的 OCRItem。"""
        pos = 0
        best_item: OCRItem | None = None
        best_overlap = 0
        for text, item in merged_items:
            item_start = pos
            item_end = pos + len(text)
            overlap_start = max(start, item_start)
            overlap_end = min(end, item_end)
            overlap = max(0, overlap_end - overlap_start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_item = item
            pos = item_end
        return best_item

    @staticmethod
    def _ocr_item_center(item: OCRItem) -> tuple[float, float]:
        """计算 OCRItem 中心点。"""
        xs = [point[0] for point in item.box]
        ys = [point[1] for point in item.box]
        return float(sum(xs) / len(xs)), float(sum(ys) / len(ys))

    def _use_backpack_fertilizer(self, quantity_point: tuple[int, int]) -> bool:
        """点击化肥数量坐标打开背包弹窗，并点击确认使用背包中的化肥。"""
        if not quantity_point or quantity_point == (0, 0):
            logger.warning('自动施肥: 无化肥数量坐标，无法打开背包')
            return False
        logger.info('自动施肥: 点击化肥数量打开背包 | point={}', quantity_point)
        self.ui.device.click_point(
            int(quantity_point[0]),
            int(quantity_point[1]),
            desc='点击化肥数量打开背包',
        )
        self.ui.device.sleep(0.5)

        wait_timer = Timer(5.0, count=1).start()
        while 1:
            self.ui.device.screenshot()
            if self.ui.appear_then_click(BTN_FERTILIZER_USE_CONFIRM, offset=30, interval=1):
                # 等待确认按钮消失，确保弹窗已关闭
                close_timer = Timer(3.0, count=1).start()
                while not close_timer.reached():
                    self.ui.device.screenshot()
                    if not self.ui.appear(BTN_FERTILIZER_USE_CONFIRM, offset=30):
                        logger.info('自动施肥: 背包化肥使用完成')
                        return True
                    self.ui.device.sleep(0.1)
                logger.info('自动施肥: 背包确认按钮点击完成')
                return True
            if wait_timer.reached():
                logger.warning('自动施肥: 背包确认按钮未出现/点击超时')
                return False
            self.ui.device.sleep(0.1)

    def _ensure_fertilizer_hours(
        self,
        *,
        target_hours: int,
        probe_ref: str,
        view_offset_x: int,
    ) -> tuple[int, tuple[int, int], bool, int]:
        """自动购买并使用背包化肥，复检库存直到达到目标阈值或达到上限。

        返回 (库存小时, 数量坐标, 是否达标, 新的横向视图偏移)。
        每轮重新获取探测点坐标并做滑动修正，以应对购买流程返回主页面后画面回正的情况。
        """
        last_hours = 0
        last_quantity_point: tuple[int, int] = (0, 0)
        for round_index in range(1, FERTILIZE_BUY_MAX_ROUNDS + 1):
            probe_targets = self._collect_fertilize_targets_for_refs([probe_ref])
            if not probe_targets:
                logger.warning('自动施肥: 补货复检缺失地块坐标 | plot={}', probe_ref)
                break
            _, raw_probe_point = probe_targets[0]
            probe_point, view_offset_x = self._adjust_fertilize_view_offset(raw_probe_point, view_offset_x)

            probed = self._probe_fertilizer_hours(plot_ref=probe_ref, point=probe_point)
            if probed is not None:
                last_hours, last_quantity_point = probed
                last_hours = max(0, int(last_hours))
            if last_hours >= int(target_hours):
                return last_hours, last_quantity_point, True, view_offset_x
            needed_hours = max(1, int(target_hours) - last_hours)
            # 当前候选均为 10 小时化肥，按缺口计算购买数量
            buy_count = min(FERTILIZE_SHOP_MAX_BUY_COUNT, max(1, (needed_hours + 9) // 10))
            logger.info(
                '自动施肥: 自动补货第{}轮 | current={}h target={}h needed={}h count={}',
                round_index,
                last_hours,
                target_hours,
                needed_hours,
                buy_count,
            )
            if not self._buy_fertilizer_once(buy_count=buy_count):
                break
            # 购买后的化肥进入背包，需要重新打开地块弹窗并点击数量确认使用
            if not self._open_plot_popup_for_fertilize(plot_ref=probe_ref, point=probe_point):
                logger.warning('自动施肥: 购买后无法打开地块弹窗使用背包化肥')
                break
            _, quantity_point = self._read_fertilizer_hours_from_popup()
            if quantity_point == (0, 0):
                logger.warning('自动施肥: 购买后未识别到化肥数量坐标，无法使用背包化肥')
                self.ui.device.click_button(GOTO_MAIN)
                self.ui.device.sleep(0.3)
                break
            if not self._use_backpack_fertilizer(quantity_point):
                logger.warning('自动施肥: 购买后使用背包化肥失败，停止补货')
                self.ui.device.click_button(GOTO_MAIN)
                self.ui.device.sleep(0.3)
                break
            self.ui.device.click_button(GOTO_MAIN)
            self.ui.device.sleep(0.3)
        return last_hours, last_quantity_point, last_hours >= int(target_hours), view_offset_x

    def _buy_fertilizer_once(self, *, buy_count: int = 1) -> bool:
        """执行一次肥料购买流程：进商店 → OCR 定位肥料 → 点击 → 调整数量 → 确认购买。"""
        buy_count = max(1, min(FERTILIZE_SHOP_MAX_BUY_COUNT, int(buy_count)))
        logger.info('自动施肥: 开始购买肥料 | count={}', buy_count)
        self.ui.ui_ensure(page_mall, confirm_wait=0.5)
        target_item = self._locate_fertilizer_item()
        if target_item is None:
            logger.warning('自动施肥: 商店未识别到肥料商品')
            self.ui.ui_ensure(page_main)
            return False

        # 直接点击 OCR 定位到的肥料商品，不依赖 SHOP_CHECK 模板是否匹配
        logger.info(
            '自动施肥: 点击肥料商品 | name={} center=({},{})',
            target_item.name,
            target_item.center_x,
            target_item.center_y,
        )
        self.ui.device.click_point(
            int(target_item.center_x),
            int(target_item.center_y),
            desc=f'选择{target_item.name}',
        )
        self.ui.device.sleep(0.5)

        click_buy = False
        quantity_set = False
        wait_timer = Timer(10.0, count=1).start()
        while 1:
            self.ui.device.screenshot()
            # 购买弹窗已消失，视为成功
            if click_buy and not self.ui.appear(BTN_SHOP_BUY_CHECK, offset=30):
                self.ui.ui_ensure(page_main)
                logger.info('自动施肥: 肥料购买成功 | count={}', buy_count)
                return True
            # 设置购买数量
            if (
                buy_count > 1
                and not quantity_set
                and self.ui.appear(BTN_SHOP_BUY_CHECK, offset=30)
                and self.ui.appear(BTN_SHOP_BUY_CONFIRM, offset=30)
            ):
                logger.info('自动施肥: 设置购买数量 | count={}', buy_count)
                for _ in range(buy_count - 1):
                    self.ui.device.click_point(
                        *FERTILIZE_SHOP_PLUS_BUTTON,
                        desc='购买数量+1',
                    )
                    self.ui.device.sleep(0.1)
                quantity_set = True
                self.ui.device.sleep(0.3)
                continue
            # 点击确认购买
            if self.ui.appear(BTN_SHOP_BUY_CHECK, offset=30) and self.ui.appear_then_click(
                BTN_SHOP_BUY_CONFIRM,
                offset=30,
                interval=1,
            ):
                click_buy = True
                continue
            if wait_timer.reached():
                logger.warning('自动施肥: 购买流程超时')
                self.ui.ui_ensure(page_main)
                return False

    def _locate_fertilizer_item(self):
        """OCR 定位肥料商品，必要时上滑翻页。"""
        use_organic = bool(self.task.main.feature.use_organic_fertilizer)
        if use_organic:
            candidates = ('10小时有机化肥', '10小时化肥')
        else:
            candidates = ('10小时化肥', '10小时有机化肥')
        seen_items: set[str] = set()
        for _ in range(FERTILIZE_SHOP_OCR_MAX_PAGES):
            cv_img = self.ui.device.screenshot()
            if cv_img is None:
                return None
            best_item = None
            for name in candidates:
                match = self.fertilizer_ocr.find_item(cv_img, name, min_similarity=0.75)
                if match.target is not None:
                    return match.target
                if match.best is not None and (best_item is None or match.best_similarity > best_item[1]):
                    best_item = (match.best, match.best_similarity)
                for parsed in match.parsed_items:
                    seen_items.add(str(parsed.name))
            if best_item is not None and best_item[1] >= 0.85:
                return best_item[0]
            self.ui.device.swipe(
                FERTILIZE_SHOP_LIST_SWIPE_START,
                FERTILIZE_SHOP_LIST_SWIPE_END,
                speed=30,
                delay=1,
                hold=0.1,
            )
        logger.debug('自动施肥: 肥料 OCR 候选={}', sorted(seen_items))
        return None

    @staticmethod
    def _seconds_to_hours_ceil(seconds: int) -> int:
        """将秒数向上取整为小时。"""
        value = max(0, int(seconds))
        if value <= 0:
            return 0
        return (value + 3600 - 1) // 3600
