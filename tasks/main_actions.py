"""TaskMain 一键动作相关逻辑。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from loguru import logger

from core.base.timer import Timer
from core.ui.assets import *
from core.ui.page import GOTO_MAIN, page_main, page_shop
from models.farm_state import ActionType
from utils.land_grid import get_lands_from_land_anchor
from utils.shop_item_ocr import ShopItemOCR

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
FERTILIZE_SHOP_LIST_SWIPE_START = (270, 300)
FERTILIZE_SHOP_LIST_SWIPE_END = (270, 860)
# 自动补货最多尝试轮次（每轮=购买一次+复检一次）。
FERTILIZE_BUY_MAX_ROUNDS = 6
# 商店 OCR 翻页上限。
FERTILIZE_SHOP_OCR_MAX_PAGES = 8


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

    def _run_feature_fertilize(self) -> str | None:
        """自动施肥：按土地巡查数据筛选地块并执行施肥。"""
        features = self.task.main.feature
        if not features.auto_fertilize:
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

        # 找一个非空地块来探测肥料库存；如果第一个地块是空地，继续尝试后面的
        probe_hours: int | None = None
        probe_ref: str = ''
        probe_point: tuple[int, int] = (0, 0)
        for ref, point in all_targets:
            hours = self._probe_fertilizer_hours(plot_ref=ref, point=point)
            if hours is not None:
                probe_hours = hours
                probe_ref = ref
                probe_point = point
                break
        available_hours = probe_hours if probe_hours is not None else 0

        required_hours = len(target_plot_refs)
        if auto_buy and available_hours < buy_threshold_hours:
            logger.info(
                '自动施肥: 当前库存低于购买阈值，触发补货 | available={}h threshold={}h',
                available_hours,
                buy_threshold_hours,
            )
            probe_ref_for_buy = probe_ref if probe_ref else all_targets[0][0]
            available_hours, _ = self._ensure_fertilizer_hours(
                target_hours=buy_threshold_hours,
                probe_ref=probe_ref_for_buy,
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

        popup_ref, popup_point = (probe_ref, probe_point) if probe_ref else all_targets[0]
        if not self._open_plot_popup_for_fertilize(plot_ref=popup_ref, point=popup_point):
            self.ui.device.click_button(GOTO_MAIN)
            self.ui.device.sleep(0.2)
            return None

        use_organic = bool(features.use_organic_fertilizer)
        if use_organic:
            fertilizer_loc = self.ui.appear_location(BTN_ORGANIC_FERTILIZER, offset=30, static=False)
        else:
            fertilizer_loc = self.ui.appear_location(BTN_ORDINARY_FERTILIZER, offset=30, static=False)
            if fertilizer_loc is None:
                fertilizer_loc = self.ui.appear_location(BTN_ORGANIC_FERTILIZER, offset=30, static=False)
        if fertilizer_loc is None:
            logger.warning('自动施肥: 未识别到肥料按钮 | use_organic={}', use_organic)
            self.ui.device.click_button(GOTO_MAIN)
            self.ui.device.sleep(0.2)
            return None

        drag_x, drag_y = int(fertilizer_loc[0]), int(fertilizer_loc[1])
        dragging = False
        try:
            self.ui.device.drag_down_point(drag_x, drag_y, duration=0.1)
            dragging = True
            self.ui.device.sleep(0.1)
            for _, point in all_targets:
                self.ui.device.drag_move_point(int(point[0]), int(point[1]), duration=0.1)
                self.ui.device.sleep(0.15)
        finally:
            if dragging:
                self.ui.device.drag_up()

        self.ui.device.click_button(GOTO_MAIN)
        self.ui.device.sleep(0.2)
        self.ui.ui_ensure(page_main)

        fertilized_refs = [str(ref) for ref, _ in all_targets]
        for _ in all_targets:
            self.engine._record_stat(ActionType.FERTILIZE)

        fertilized_refs = [str(ref) for ref, _ in all_targets]
        if fertilized_refs and not use_organic:
            self._record_fertilize_time(fertilized_refs, threshold_seconds=threshold_seconds)

        logger.info('自动施肥: 结束 | fertilized={}/{}', len(all_targets), required_hours)
        return '自动施肥'

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

    def _collect_fertilize_targets_for_refs(self, refs: list[str]) -> list[tuple[str, tuple[int, int]]]:
        """将地块编号映射为当前画面中心点坐标。"""
        if not refs:
            return []
        self.ui.device.screenshot()
        land_right_anchor = self.ui.appear_location(BTN_LAND_RIGHT, offset=30, threshold=0.95, static=False)
        land_left_anchor = self.ui.appear_location(BTN_LAND_LEFT, offset=30, threshold=0.95, static=False)
        if land_right_anchor is None and land_left_anchor is None:
            logger.warning('自动施肥: 未识别到地块锚点 | refs={}', refs)
            return []

        all_lands = get_lands_from_land_anchor(
            (int(land_right_anchor[0]), int(land_right_anchor[1])) if land_right_anchor is not None else None,
            (int(land_left_anchor[0]), int(land_left_anchor[1])) if land_left_anchor is not None else None,
        )
        if not all_lands:
            logger.warning('自动施肥: 网格生成失败 | refs={}', refs)
            return []

        center_by_plot_id = {str(cell.label): (int(cell.center[0]), int(cell.center[1])) for cell in all_lands}
        targets: list[tuple[str, tuple[int, int]]] = []
        for ref in refs:
            point = center_by_plot_id.get(str(ref))
            if point is None:
                logger.warning('自动施肥: 当前画面缺失地块坐标 | plot={}', ref)
                continue
            targets.append((str(ref), point))
        return targets

    def _probe_fertilizer_hours(self, *, plot_ref: str, point: tuple[int, int]) -> int | None:
        """打开一个地块弹窗并识别当前肥料库存（小时）。"""
        opened = self._open_plot_popup_for_fertilize(plot_ref=plot_ref, point=point)
        if not opened:
            self.ui.device.click_button(GOTO_MAIN)
            self.ui.device.sleep(0.2)
            return None
        hours = self._read_fertilizer_hours_from_popup()
        self.ui.device.click_button(GOTO_MAIN)
        self.ui.device.sleep(0.2)
        return hours

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

    def _read_fertilizer_hours_from_popup(self) -> int:
        """从地块弹窗 OCR 提取肥料库存小时数。"""
        cv_img = self.ui.device.screenshot()
        if cv_img is None:
            return 0

        # 优先根据肥料按钮位置计算文字区域（普通化肥在左，有机化肥在右，小时数在按钮下方）
        ordinary_loc = self.ui.appear_location(BTN_ORDINARY_FERTILIZER, offset=30, static=False)
        organic_loc = self.ui.appear_location(BTN_ORGANIC_FERTILIZER, offset=30, static=False)
        if ordinary_loc is not None or organic_loc is not None:
            points = [p for p in (ordinary_loc, organic_loc) if p is not None]
            min_x = int(min(p[0] for p in points))
            max_x = int(max(p[0] for p in points))
            max_y = int(max(p[1] for p in points))
            region = (
                max(0, min_x - 80),
                max(0, max_y + 10),
                min(540, max_x + 80),
                min(960, max_y + 90),
            )
        else:
            region = FERTILIZE_HOURS_OCR_REGION

        items = self.engine._get_ocr_tool().detect(
            cv_img,
            region=region,
            scale=1.3,
            alpha=1.15,
            beta=0.0,
        )
        merged = ''.join(str(item.text or '').strip() for item in items if str(item.text or '').strip())
        merged = merged.replace(' ', '')
        logger.debug('自动施肥: 肥料库存OCR原始文本 | raw={}', merged or '<empty>')
        matched_hours = [int(float(num)) for num in FERTILIZE_HOURS_PATTERN.findall(merged)]
        if matched_hours:
            value = max(matched_hours)
            logger.info('自动施肥: 识别肥料库存={}h | raw={}', value, merged or '<empty>')
            return value
        raw_numbers = [int(float(num)) for num in FERTILIZE_NUMBER_PATTERN.findall(merged)]
        if raw_numbers:
            value = max(raw_numbers)
            logger.info('自动施肥: 识别肥料库存(兜底)={}h | raw={}', value, merged or '<empty>')
            return value
        logger.warning('自动施肥: 未识别到肥料库存数字 | raw={}', merged or '<empty>')
        return 0

    def _ensure_fertilizer_hours(
        self,
        *,
        target_hours: int,
        probe_ref: str,
    ) -> tuple[int, bool]:
        """自动购买并复检库存，直到达到目标阈值或达到上限。"""
        last_hours = 0
        for round_index in range(1, FERTILIZE_BUY_MAX_ROUNDS + 1):
            probe_targets = self._collect_fertilize_targets_for_refs([probe_ref])
            if not probe_targets:
                logger.warning('自动施肥: 补货复检缺失地块坐标 | plot={}', probe_ref)
                break
            _, probe_point = probe_targets[0]
            probed = self._probe_fertilizer_hours(plot_ref=probe_ref, point=probe_point)
            if probed is not None:
                last_hours = max(0, int(probed))
            if last_hours >= int(target_hours):
                return last_hours, True
            logger.info(
                '自动施肥: 自动补货第{}轮 | current={}h target={}h',
                round_index,
                last_hours,
                target_hours,
            )
            if not self._buy_fertilizer_once():
                break
        return last_hours, last_hours >= int(target_hours)

    def _buy_fertilizer_once(self) -> bool:
        """执行一次肥料购买流程：进商店 → OCR 定位肥料 → 点击 → 确认购买。"""
        logger.info('自动施肥: 开始购买肥料')
        self.ui.ui_ensure(page_shop, confirm_wait=0.5)
        target_item = self._locate_fertilizer_item()
        if target_item is None:
            logger.warning('自动施肥: 商店未识别到肥料商品')
            self.ui.ui_ensure(page_main)
            return False

        click_buy = False
        wait_timer = Timer(6.0, count=1).start()
        while 1:
            self.ui.device.screenshot()
            if click_buy and not self.ui.appear(BTN_SHOP_BUY_CHECK, offset=30):
                self.ui.ui_ensure(page_main)
                logger.info('自动施肥: 肥料购买成功')
                return True
            if self.ui.appear(BTN_SHOP_BUY_CHECK, offset=30) and self.ui.appear_then_click(
                BTN_SHOP_BUY_CONFIRM,
                offset=30,
                interval=1,
            ):
                click_buy = True
                continue
            if (
                self.ui.appear(SHOP_CHECK, offset=30)
                and not self.ui.appear(BTN_SHOP_BUY_CHECK, offset=30)
                and not self.ui.appear(BTN_SHOP_BUY_CONFIRM, offset=30)
            ):
                self.ui.device.click_point(
                    int(target_item.center_x),
                    int(target_item.center_y),
                    desc=f'选择{target_item.name}',
                )
                self.ui.device.sleep(0.5)
                continue
            if wait_timer.reached():
                logger.warning('自动施肥: 购买流程超时')
                self.ui.ui_ensure(page_main)
                return False

    def _locate_fertilizer_item(self):
        """OCR 定位肥料商品，必要时上滑翻页。"""
        use_organic = bool(self.task.main.feature.use_organic_fertilizer)
        if use_organic:
            candidates = ('有机化肥', '普通化肥', '化肥')
        else:
            candidates = ('普通化肥', '有机化肥', '化肥')
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
