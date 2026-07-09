"""TaskMain 土地相关逻辑（扩建/升级）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from core.base.timer import Timer
from core.ui.assets import *
from core.ui.page import GOTO_MAIN, page_main

if TYPE_CHECKING:
    from core.engine.bot.local_engine import LocalBotEngine
    from core.ui.ui import UI

# 固定截图宽高（宽x高）。
LAND_SCAN_FRAME_WIDTH = 540
LAND_SCAN_FRAME_HEIGHT = 960
# 空地弹窗升级图标 ROI：相对 BTN_CROP_REMOVAL 中心 (dx1, dy1, dx2, dy2)。
LAND_UPGRADE_REGION_OFFSET = (-100, -50, 0, -0)

# 自动扩建按钮识别 ROI：全屏宽度，垂直方向 46%-68% 区域。
BTN_EXPAND_SEARCH_ROI = (
    0,
    int(LAND_SCAN_FRAME_HEIGHT * 0.46),
    LAND_SCAN_FRAME_WIDTH,
    int(LAND_SCAN_FRAME_HEIGHT * 0.68),
)
BTN_EXPAND_THRESHOLD = 0.8


class TaskMainLandMixin:
    """提供自动扩建与自动升级流程。"""

    engine: 'LocalBotEngine'
    ui: 'UI'

    def _run_feature_expand(self) -> str | None:
        """自动扩建"""
        return self._try_expand()

    def _run_feature_upgrade(self) -> str | None:
        """自动升级"""
        return self._try_upgrade()

    def _run_upgrade_steps_for_selected_land(self, *, plot_ref: str) -> None:
        """在已选中地块弹窗上执行升级步骤。"""
        while 1:
            self.ui.device.screenshot()
            if self.ui.appear(BTN_LAND_UPGRADE_CHECK, offset=30) and self.ui.appear_then_click(
                BTN_LAND_UPGRADE_CONFIRM, offset=30, interval=1
            ):
                continue
            if not self.ui.appear(BTN_LAND_UPGRADE_CHECK, offset=30):
                logger.info('自动升级流程: 地块升级完成 | 序号={}', plot_ref)
                break

    @staticmethod
    def _build_upgrade_icon_region(center: tuple[int, int]) -> tuple[int, int, int, int]:
        """按锚点与偏移构造升级图标检测 ROI。"""
        dx1, dy1, dx2, dy2 = LAND_UPGRADE_REGION_OFFSET
        cx = int(center[0])
        cy = int(center[1])
        x1 = int(cx + dx1)
        y1 = int(cy + dy1)
        x2 = int(cx + dx2)
        y2 = int(cy + dy2)
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1
        x1 = max(0, min(x1, LAND_SCAN_FRAME_WIDTH - 1))
        y1 = max(0, min(y1, LAND_SCAN_FRAME_HEIGHT - 1))
        x2 = max(x1 + 1, min(x2, LAND_SCAN_FRAME_WIDTH))
        y2 = max(y1 + 1, min(y2, LAND_SCAN_FRAME_HEIGHT))
        return x1, y1, x2, y2

    def _collect_upgrade_plot_refs(self) -> list[str]:
        """从土地详情中收集 need_upgrade=true 的地块编号。"""
        refs: list[str] = []
        seen: set[str] = set()
        for item in self.parse_land_detail_plots_by_flag('need_upgrade'):
            ref = str(item.get('plot_id', '') or '').strip()
            if ref and ref not in seen:
                refs.append(ref)
                seen.add(ref)
        return refs

    def _upgrade_single_plot(self, plot_ref: str, point: tuple[int, int]) -> bool:
        """点击指定地块并执行升级，返回是否成功。"""
        logger.info('自动升级流程: 开始升级地块 | 序号={}', plot_ref)

        need_upgrade = False
        upgrade_button = None
        click_timer = Timer(1, count=3).start()
        self.engine.device.click_point(int(point[0]), int(point[1]), desc=f'点击待升级地块 {plot_ref}')
        while 1:
            self.ui.device.screenshot()
            removal_location = None
            if self.ui.appear(BTN_CROP_REMOVAL, offset=30, static=False):
                removal_location = self.ui.appear_location(BTN_CROP_REMOVAL, offset=30, static=False)
                BTN_CROP_REMOVAL._button_offset = None
            elif self.ui.appear(BTN_LAND_POP_EMPTY, offset=(-160, -180, 280, 280), threshold=0.65):
                removal_location = self.ui.appear_location(
                    BTN_LAND_POP_EMPTY, offset=(-160, -180, 280, 280), threshold=0.65, static=False
                )
                BTN_LAND_POP_EMPTY._button_offset = None

            if removal_location is not None:
                matched = self.ui.match_gif_multi(
                    ICON_LAND_UPGRADE,
                    roi=self._build_upgrade_icon_region(removal_location),
                )
                if matched:
                    need_upgrade = True
                    upgrade_button = matched[0]
                else:
                    logger.warning('自动升级流程: 地块不需要升级 | 序号={}', plot_ref)
                break
            if click_timer.reached():
                logger.warning('自动升级流程: 地块点击出错 | 序号={}', plot_ref)
                break
            self.ui.device.sleep(0.1)

        if need_upgrade and upgrade_button is not None:
            self.engine.device.click_point(
                int(upgrade_button.location[0]), int(upgrade_button.location[1]), desc='点击升级'
            )
            while 1:
                self.ui.device.screenshot()
                if self.ui.appear(BTN_LAND_UPGRADE_CHECK, offset=30):
                    break
            self._run_upgrade_steps_for_selected_land(plot_ref=plot_ref)
            self.backfill_land_flag_false([plot_ref], 'need_upgrade', log_prefix='自动升级')

        self.ui.device.click_button(GOTO_MAIN)
        self.ui.device.sleep(0.2)
        return need_upgrade

    def _try_expand(self) -> str | None:
        """执行一次土地扩建流程"""
        logger.info('自动扩建: 开始')
        self.ui.ui_ensure(page_main)
        # 点击空白处
        self.ui.device.click_button(GOTO_MAIN)
        self.ui.device.screenshot()
        if self.ui.appear_location_in_roi(BTN_EXPAND, BTN_EXPAND_SEARCH_ROI, threshold=BTN_EXPAND_THRESHOLD) is None:
            logger.info('自动扩建: 未发现待扩建土地')
            return None

        confirm_timer = Timer(0.5, count=2)
        while 1:
            self.ui.device.screenshot()

            if self.ui.appear_then_click_in_roi(
                BTN_EXPAND, BTN_EXPAND_SEARCH_ROI, interval=1, threshold=BTN_EXPAND_THRESHOLD
            ):
                confirm_timer.clear()
                continue
            if self.ui.appear(BTN_EXPAND_CHECK, offset=30) and self.ui.appear_then_click(
                BTN_EXPAND_DIRECT_CONFIRM, offset=30, interval=1
            ):
                continue
            if self.ui.appear(BTN_EXPAND_CHECK, offset=30) and self.ui.appear_then_click(
                BTN_EXPAND_CONFIRM, offset=30, interval=1
            ):
                continue
            if not confirm_timer.started():
                confirm_timer.start()
            if confirm_timer.reached():
                logger.info('自动扩建: 已完成')
                break

        return None

    def _try_upgrade(self) -> str | None:
        """按土地详情待升级列表逐地块执行升级流程。"""
        logger.info('自动升级流程: 开始')
        self.ui.ui_ensure(page_main)
        self.ui.device.click_button(GOTO_MAIN)
        self.align_view_by_background_tree(log_prefix='自动升级流程')

        target_refs = self._collect_upgrade_plot_refs()
        if not target_refs:
            logger.info('自动升级流程: 无待升级地块')
            return None

        # 复用自动施肥的地块坐标映射逻辑：基于当前画面锚点一次性计算所有地块中心点
        all_targets = self._collect_fertilize_targets_for_refs(
            target_refs, anchor_threshold=0.8, log_prefix='自动升级流程'
        )
        if not all_targets:
            logger.warning('自动升级流程: 地块坐标映射失败')
            return None

        # 逐个地块升级，复用自动施肥的横向视图偏移修正
        view_offset_x = 0
        upgraded_count = 0
        for plot_ref, point in all_targets:
            adjusted_point, view_offset_x = self._adjust_fertilize_view_offset(point, view_offset_x)
            if self._upgrade_single_plot(plot_ref, adjusted_point):
                upgraded_count += 1

        self.align_view_by_background_tree(log_prefix='自动升级流程-结束回正')
        logger.info('自动升级流程: 结束 | upgraded={}', upgraded_count)
        return '自动升级' if upgraded_count > 0 else None
