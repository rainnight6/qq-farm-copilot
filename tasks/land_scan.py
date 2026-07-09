"""地块巡查任务。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from loguru import logger

from core.base.timer import Timer
from core.engine.task.registry import TaskResult
from core.ui.assets import (
    BTN_CROP_MATURITY_TIME_SUFFIX,
    BTN_CROP_REMOVAL,
    BTN_EXPAND_BRAND,
    BTN_LAND_LEFT,
    BTN_LAND_POP_EMPTY,
    BTN_LAND_RIGHT,
    ICON_LAND_UPGRADE,
)
from core.ui.page import GOTO_MAIN, page_main
from tasks.base import TaskBase
from tasks.main_actions import TaskMainActionsMixin
from utils.land_grid import LandCell
from utils.ocr_utils import OCRItem, OCRTool

# 画面横向回正手势点位 P1。
LAND_SCAN_SWIPE_H_P1 = (250, 190)
# 画面横向回正手势点位 P2。
LAND_SCAN_SWIPE_H_P2 = (200, 190)
# 地块网格行数（逻辑行）。
LAND_SCAN_ROWS = 4
# 地块网格列数（逻辑列）。
LAND_SCAN_COLS = 6
# 固定截图宽高（宽x高）。
LAND_SCAN_FRAME_WIDTH = 540
LAND_SCAN_FRAME_HEIGHT = 960
# 画面物理列总数（1,2,3,4,4,4,3,2,1）。
LAND_SCAN_PHYSICAL_COLS = 9
# 右半阶段扫描的物理列数量（右侧 5 列，从右往左）。
LAND_SCAN_RIGHT_STAGE_COL_COUNT = 5
# 左半阶段扫描的物理列数量（左侧 4 列，从左往右）。
LAND_SCAN_LEFT_STAGE_COL_COUNT = 4
# 边缘点击前进行横向滑动的 x 阈值。
LAND_SCAN_EDGE_SWIPE_LEFT_THRESHOLD = 160
LAND_SCAN_EDGE_SWIPE_RIGHT_THRESHOLD = 330
# 成熟时间 OCR 识别大区域：相对 BTN_CROP_MATURITY_TIME_SUFFIX 中心 (dx1, dy1, dx2, dy2)。
LAND_SCAN_OCR_REGION_OFFSET = (-200, -50, 100, 50)
# 成熟时间 OCR 二次筛选窗口：相对 BTN_CROP_MATURITY_TIME_SUFFIX 中心，x 起点偏移（像素）。
LAND_SCAN_TIME_PICK_X1 = -100
# 成熟时间 OCR 二次筛选窗口：相对 BTN_CROP_MATURITY_TIME_SUFFIX 中心，x 终点偏移（像素）。
LAND_SCAN_TIME_PICK_X2 = -40
# 成熟时间 OCR 二次筛选窗口：相对 BTN_CROP_MATURITY_TIME_SUFFIX 中心，y 上边界偏移（像素）。
LAND_SCAN_TIME_PICK_Y1 = -20
# 成熟时间 OCR 二次筛选窗口：相对 BTN_CROP_MATURITY_TIME_SUFFIX 中心，y 下边界偏移（像素）。
LAND_SCAN_TIME_PICK_Y2 = 20
# 成熟时间文本正则（仅提取 HH:MM:SS）。
LAND_SCAN_MATURITY_TIME_PATTERN = re.compile(r'(\d{2}:\d{2}:\d{2})')
# 地块等级文本正则（中文等级关键词）。
LAND_SCAN_LEVEL_PATTERN = re.compile(r'(未扩建|普通|紫晶|红|黑|金)')
# 地块等级英文值到中文日志文案映射。
LAND_SCAN_LEVEL_LABELS: dict[str, str] = {
    'unbuilt': '未扩建',
    'normal': '普通土地',
    'red': '红土地',
    'black': '黑土地',
    'gold': '金土地',
    'amethyst': '紫晶土地',
}
# 空地弹窗地块等级 OCR 区域：相对 BTN_LAND_POP_EMPTY 中心 (dx1, dy1, dx2, dy2)。
LAND_SCAN_LEVEL_REGION_OFFSET = (-60, -50, 40, 50)
# 已播种地块等级颜色采样点：相对 BTN_CROP_MATURITY_TIME_SUFFIX 中心 (dx, dy)。
LAND_SCAN_PLOTTED_LEVEL_COLOR_OFFSET = (85, -10)
# 已播种地块等级颜色采样窗口半径（像素，采样 (2r+1)x(2r+1) 均值）。
LAND_SCAN_PLOTTED_LEVEL_COLOR_SAMPLE_RADIUS = 1
# 已播种地块等级颜色判定阈值（RGB 欧氏距离）。
LAND_SCAN_PLOTTED_LEVEL_COLOR_DISTANCE_THRESHOLD = 42.0
# 已播种地块等级颜色静态表（RGB）。
LAND_SCAN_PLOTTED_LEVEL_COLORS_RGB: dict[str, tuple[int, int, int]] = {
    'normal': (178, 131, 74),
    'red': (223, 87, 55),
    'black': (92, 67, 42),
    'gold': (249, 203, 50),
    'amethyst': (209, 168, 232),
}
# 空地弹窗升级图标 ROI：相对 BTN_LAND_POP_EMPTY 中心 (dx1, dy1, dx2, dy2)。
LAND_SCAN_UPGRADE_EMPTY_REGION_OFFSET = (-100, -50, 0, -0)
# 非空地弹窗升级图标 ROI：相对 BTN_CROP_MATURITY_TIME_SUFFIX 中心 (dx1, dy1, dx2, dy2)。
LAND_SCAN_UPGRADE_NON_EMPTY_REGION_OFFSET = (0, -50, 130, 50)
# 滑动后锚点稳定判定总时长（秒）。
LAND_SCAN_ANCHOR_STABLE_SECONDS = 0.5
# Timer reached 附加计数门槛（reached_count > count）。
LAND_SCAN_ANCHOR_STABLE_REQUIRED_HITS = 3
# 滑动后等待锚点稳定的最长超时（秒）。
LAND_SCAN_ANCHOR_STABLE_TIMEOUT_SECONDS = 5.0
# 连续两次横向滑动之间的停顿（秒），等待画面惯性消散。
LAND_SCAN_SWIPE_STEP_DELAY = 0.2
# 点击地块后等待地块弹窗的最长超时（秒），超时则跳过当前地块。
LAND_SCAN_POPUP_WAIT_TIMEOUT_SECONDS = 3.0
# 右半阶段 BTN_EXPAND_BRAND 固定识别 ROI（x0-300, y420-700）。
LAND_SCAN_EXPAND_BRAND_RIGHT_ROI = (0, 420, 300, 700)
# 左半阶段 BTN_EXPAND_BRAND 固定识别 ROI（x240-540, y460-700）。
LAND_SCAN_EXPAND_BRAND_LEFT_ROI = (240, 460, 540, 700)


class TaskLandScan(TaskMainActionsMixin, TaskBase):
    """按预设顺序遍历地块并进行 OCR 收集。"""

    def __init__(self, engine, ui, *, ocr_tool: OCRTool | None = None):
        super().__init__(engine, ui)
        self.ocr_tool = ocr_tool
        self._ocr_disabled_logged = False

    def run(self, rect: tuple[int, int, int, int]) -> TaskResult:
        """
        执行地块巡查流程，按照物理排列顺序依次点击收集信息。
        列顺序为：1-4 1-3 1-2 1-1 2-1， 6-1 5-1 4-1 3-1
        ---------------1-1---------
        ------------2-1---1-2------
        ---------3-1---2-2---1-3---
        ------4-1---3-2---2-3---1-4
        ---5-1---4-2---3-3---2-4---
        6-1---5-2---4-3---3-4------
        ---6-2---5-3---4-4---------
        ------6-3---5-4------------
        ---------6-4---------------
        """
        _ = rect
        logger.info('地块巡查: 开始')
        self.ui.ui_ensure(page_main)
        self._reset_zoom_via_skin_page()
        aligned = False
        for attempt in range(3):
            aligned = self.align_view_by_background_tree(log_prefix='地块巡查')
            if aligned:
                break
            logger.warning('地块巡查: 画面回正失败，清理弹窗后重试 | attempt={}', attempt + 1)
            self.ui.device.click_button(GOTO_MAIN)
            self.ui.device.sleep(0.5)
        if not aligned:
            logger.warning('地块巡查: 画面回正多次失败，继续尝试执行')
        right_swipe_times = int(self.config.planting.land_swipe_right_times)
        left_swipe_times = int(self.config.planting.land_swipe_left_times)
        logger.info('地块巡查: 滑动次数 | 右滑={} 左滑={}', right_swipe_times, left_swipe_times)
        if left_swipe_times <= 0:
            logger.warning('地块巡查: 左滑次数为 0，将只扫描右半部分地块')
        # self.ui.device.click_button(GOTO_MAIN)

        # 在回正状态下同时识别左右锚点并记录日志，侧滑后统一使用固定基线 span 推断对侧锚点。
        self.ui.device.sleep(0.2)
        self.ui.device.screenshot()
        full_right_anchor = self.appear_land_right(offset=(-30, -30, 160, 30), threshold=0.8, static=False)
        full_left_anchor = self.ui.appear_location(
            BTN_LAND_LEFT, offset=(-160, -30, 30, 30), threshold=0.8, static=False
        )
        logger.info(
            '地块巡查: 初始锚点识别 | 右锚点={} 左锚点={}',
            full_right_anchor,
            full_left_anchor,
        )
        anchor_span: tuple[int, int] | None = None
        if full_right_anchor is not None and full_left_anchor is not None:
            anchor_span = (
                int(full_left_anchor[0] - full_right_anchor[0]),
                int(full_left_anchor[1] - full_right_anchor[1]),
            )
            logger.info('地块巡查: 实测左右锚点间距={}', anchor_span)

        try:
            # 右滑：手指从 P1 滑向 P2，画面向右侧移动，露出右侧地块
            logger.info('地块巡查: 开始右滑')
            for i in range(right_swipe_times):
                self.ui.device.swipe(LAND_SCAN_SWIPE_H_P1, LAND_SCAN_SWIPE_H_P2, speed=30)
                if i < right_swipe_times - 1:
                    self.ui.device.sleep(float(LAND_SCAN_SWIPE_STEP_DELAY))
            if not self._wait_anchor_position_stable(anchor_button=BTN_LAND_RIGHT):
                logger.warning('地块巡查: 右滑后右锚点未稳定，继续尝试识别网格')
            logger.info(
                '地块巡查: 右滑完成，开始扫描右侧 {} 列（物理列 1~{}，从右往左）',
                LAND_SCAN_RIGHT_STAGE_COL_COUNT,
                LAND_SCAN_RIGHT_STAGE_COL_COUNT,
            )

            cells_after_left = self.collect_land_cells(
                rows=LAND_SCAN_ROWS,
                cols=LAND_SCAN_COLS,
                start_anchor='right',
                log_prefix='地块巡查',
                static=False,
                anchor_span=anchor_span,
            )
            if not cells_after_left:
                logger.warning('地块巡查: 未识别到地块网格，跳过任务')
                return self.fail('未识别到地块网格')
            cells_after_left, expand_target_key = self._exclude_expand_brand_related_cells(
                cells_after_left,
                brand_roi=LAND_SCAN_EXPAND_BRAND_RIGHT_ROI,
            )
            self._scan_cells_by_physical_columns(
                cells_after_left,
                from_side='right',
                column_count=LAND_SCAN_RIGHT_STAGE_COL_COUNT,
                scan_direction='rtl',
            )

            # 左滑：手指从 P2 滑向 P1，画面向左侧移动，露出左侧地块
            logger.info('地块巡查: 开始左滑')
            for i in range(left_swipe_times):
                self.ui.device.swipe(LAND_SCAN_SWIPE_H_P2, LAND_SCAN_SWIPE_H_P1, speed=30)
                if i < left_swipe_times - 1:
                    self.ui.device.sleep(float(LAND_SCAN_SWIPE_STEP_DELAY))
            if not self._wait_anchor_position_stable(anchor_button=BTN_LAND_LEFT):
                logger.warning('地块巡查: 左滑后左锚点未稳定，继续尝试识别网格')
            logger.info(
                '地块巡查: 左滑完成，开始扫描左侧 {} 列（物理列 {}~{}，从左往右）',
                LAND_SCAN_LEFT_STAGE_COL_COUNT,
                LAND_SCAN_RIGHT_STAGE_COL_COUNT + 1,
                LAND_SCAN_PHYSICAL_COLS,
            )

            cells_after_right = self.collect_land_cells(
                rows=LAND_SCAN_ROWS,
                cols=LAND_SCAN_COLS,
                start_anchor='right',
                log_prefix='地块巡查',
                static=False,
                anchor_span=anchor_span,
            )
            if not cells_after_right:
                logger.warning('地块巡查: 未识别到地块网格，跳过任务')
                return self.fail('未识别到地块网格')
            cells_after_right, _ = self._exclude_expand_brand_related_cells(
                cells_after_right,
                target_key=expand_target_key,
                brand_roi=LAND_SCAN_EXPAND_BRAND_LEFT_ROI,
            )
            # 左侧 4 列按物理列 9,8,7,6（从左往右）扫描
            left_scan_cols = list(range(LAND_SCAN_PHYSICAL_COLS, LAND_SCAN_RIGHT_STAGE_COL_COUNT, -1))
            self._scan_cells_by_physical_columns(
                cells_after_right,
                from_side='right',
                column_count=LAND_SCAN_LEFT_STAGE_COL_COUNT,
                fixed_cols=left_scan_cols,
                scan_direction='ltr',
            )
        finally:
            self._reset_zoom_via_skin_page()
            self.align_view_by_background_tree(log_prefix='地块巡查')
            self.ui.ui_ensure(page_main)

        self._schedule_timed_harvest_after_scan()
        self._trigger_main_task_if_needed()
        logger.info('地块巡查: 结束')
        return self.ok()

    def _schedule_timed_harvest_after_scan(self) -> None:
        """地块巡查完成后，按最新倒计时快照重排定时收获。"""
        if not self.is_task_enabled('timed_harvest'):
            return
        timed_view = self.task.timed_harvest
        aggregation_seconds = timed_view.feature.aggregation_seconds

        from tasks.timed_harvest import TaskTimedHarvest

        schedule_points = TaskTimedHarvest.build_schedule_points(
            self.config.land.plots,
            aggregation_seconds=aggregation_seconds,
        )
        target_time = TaskTimedHarvest.pick_next_schedule_target(
            schedule_points,
            now=datetime.now(),
            fallback_to_now_when_all_past=True,
        )
        task_item = getattr(self.engine, '_executor_tasks', {}).get('timed_harvest')
        executor = getattr(self.engine, '_task_executor', None)
        if (
            target_time is None
            or task_item is None
            or executor is None
            or not executor.task_delay('timed_harvest', target_time=target_time)
        ):
            return
        task_item.next_run = target_time
        persist = getattr(self.engine, '_persist_task_next_run', None)
        if callable(persist):
            persist('timed_harvest')
        logger.info(
            '定时收获: 已更新下次执行 | 下次执行={} 执行点数量={} 聚合秒数={}',
            target_time.strftime('%Y-%m-%d %H:%M:%S'),
            len(schedule_points),
            aggregation_seconds,
        )

    def _trigger_main_task_if_needed(self) -> None:
        """存在待播种或待升级地块时，拉起农场巡查任务。"""
        pending_planting = bool(self.parse_land_detail_plots_by_flag('need_planting'))
        pending_upgrade = bool(self.parse_land_detail_plots_by_flag('need_upgrade'))
        if not pending_planting and not pending_upgrade:
            return

        self.task.main.call(force_call=False)
        logger.info(
            '地块巡查: 存在待处理地块，执行农场巡查 | 待播种={} 待升级={}',
            pending_planting,
            pending_upgrade,
        )

    def _wait_anchor_position_stable(self, *, anchor_button, timeout_seconds: float | None = None) -> bool:
        """等待目标土地锚点位置稳定；超时或检测到对侧锚点时返回失败。"""
        stable_seconds = float(LAND_SCAN_ANCHOR_STABLE_SECONDS)
        required_hits = int(LAND_SCAN_ANCHOR_STABLE_REQUIRED_HITS)
        stable_timer = Timer(stable_seconds, count=required_hits)
        last_anchor: tuple[int, int] | None = None
        timeout = (
            float(timeout_seconds) if timeout_seconds is not None else float(LAND_SCAN_ANCHOR_STABLE_TIMEOUT_SECONDS)
        )
        deadline = Timer(timeout).start()

        target_offset = (-30, -30, 160, 30)
        opposite_button = BTN_LAND_LEFT
        opposite_offset = (-160, -30, 30, 30)
        opposite_is_right = False
        if anchor_button == BTN_LAND_LEFT:
            target_offset = (-160, -30, 30, 30)
            opposite_button = BTN_LAND_RIGHT
            opposite_offset = (-30, -30, 160, 30)
            opposite_is_right = True

        while 1:
            self.ui.device.screenshot()
            if anchor_button == BTN_LAND_RIGHT:
                location = self.appear_land_right(offset=target_offset, threshold=0.9, static=False)
            else:
                location = self.ui.appear_location(anchor_button, offset=target_offset, threshold=0.9, static=False)
            current_anchor: tuple[int, int] | None = None
            if location is not None:
                current_anchor = (int(location[0]), int(location[1]))
                logger.debug('地块巡查: 锚点识别 | 锚点={} 位置={}', anchor_button.name, current_anchor)

            if current_anchor is None:
                if opposite_is_right:
                    opposite = self.appear_land_right(offset=opposite_offset, threshold=0.9, static=False)
                else:
                    opposite = self.ui.appear_location(
                        opposite_button, offset=opposite_offset, threshold=0.9, static=False
                    )
                if opposite is not None:
                    logger.warning(
                        '地块巡查: 期望锚点未命中但对侧锚点可见，疑似滑过头 | 期望={} 对侧={}',
                        anchor_button.name,
                        opposite_button.name,
                    )
                    return False
                last_anchor = None
                stable_timer.clear()
                if deadline.reached():
                    logger.warning(
                        '地块巡查: 等待锚点稳定超时 | 锚点={} timeout={}s',
                        anchor_button.name,
                        timeout,
                    )
                    return False
                self.ui.device.sleep(0.1)
                continue

            if current_anchor != last_anchor:
                last_anchor = current_anchor
                if stable_timer.started():
                    stable_timer.reset()
                else:
                    stable_timer.start()
                self.ui.device.sleep(0.05)
                continue

            if stable_timer.reached():
                logger.info(
                    '地块巡查: 锚点已稳定 | 锚点={} 位置={}',
                    anchor_button.name,
                    current_anchor,
                )
                return True

    def _swipe_horizontal_interval(self, direction: str, interval: int) -> None:
        """按指定方向和 x 间隔执行横向滑动（direction='left' 为左滑，'right' 为右滑）。"""
        base_y = LAND_SCAN_SWIPE_H_P1[1]
        if direction == 'left':
            start = (LAND_SCAN_SWIPE_H_P1[0], base_y)
            end = (LAND_SCAN_SWIPE_H_P1[0] - interval, base_y)
        else:
            start = (LAND_SCAN_SWIPE_H_P2[0], base_y)
            end = (LAND_SCAN_SWIPE_H_P2[0] + interval, base_y)
        logger.debug('地块巡查: 边缘滑动 | 方向={} 起点={} 终点={}', direction, start, end)
        self.ui.device.swipe(start, end, speed=30)
        self.ui.device.sleep(float(LAND_SCAN_SWIPE_STEP_DELAY))

    def _scan_cells_by_physical_columns(
        self,
        cells: list[LandCell],
        *,
        from_side: str,
        column_count: int,
        fixed_cols: list[int] | None = None,
        scan_direction: str = 'rtl',
    ):
        """按画面物理列扫描地块（列内顺序：从上到下）。

        scan_direction='rtl' 表示从右往左扫描，x<160 时右滑修正；
        scan_direction='ltr' 表示从左往右扫描，x>330 时左滑修正。
        """
        if fixed_cols is not None:
            scan_cols = [int(col) for col in fixed_cols]
        else:
            scan_cols = self._resolve_scan_columns(cells, from_side=from_side, column_count=column_count)

        logger.info('地块巡查: 物理列={}', scan_cols)

        x_interval = abs(LAND_SCAN_SWIPE_H_P1[0] - LAND_SCAN_SWIPE_H_P2[0])
        view_offset_x = 0

        col_map: dict[int, list[LandCell]] = {}
        for cell in cells:
            physical_col = self._physical_col_rtl(cell)
            col_map.setdefault(physical_col, []).append(cell)

        for physical_col in scan_cols:
            col_cells = list(col_map.get(int(physical_col), []))
            col_cells.sort(key=lambda cell: (int(cell.center[1]), int(cell.center[0])))
            for cell in col_cells:
                final_x = cell.center[0] + view_offset_x
                final_y = cell.center[1]
                if scan_direction == 'rtl' and final_x < LAND_SCAN_EDGE_SWIPE_LEFT_THRESHOLD:
                    self._swipe_horizontal_interval('right', x_interval)
                    view_offset_x += x_interval
                    old_x = final_x
                    final_x = cell.center[0] + view_offset_x
                    logger.info(
                        '地块巡查: 右滑修正 | 列={} 序号={} 原x={} 新x={}',
                        physical_col,
                        cell.label,
                        old_x,
                        final_x,
                    )
                elif scan_direction == 'ltr' and final_x > LAND_SCAN_EDGE_SWIPE_RIGHT_THRESHOLD:
                    self._swipe_horizontal_interval('left', x_interval)
                    view_offset_x -= x_interval
                    old_x = final_x
                    final_x = cell.center[0] + view_offset_x
                    logger.info(
                        '地块巡查: 左滑修正 | 列={} 序号={} 原x={} 新x={}',
                        physical_col,
                        cell.label,
                        old_x,
                        final_x,
                    )

                calibrated_cell = LandCell(
                    order=cell.order,
                    row=cell.row,
                    col=cell.col,
                    label=cell.label,
                    center=(int(final_x), int(final_y)),
                    vertices=cell.vertices,
                )
                self._run_actions_before_ocr_cell()
                self._click_and_ocr_cell(cell=calibrated_cell)
                self.ui.device.click_button(GOTO_MAIN)
                self.ui.device.sleep(0.2)
                self.ui.device.stuck_record_clear()
                self.ui.device.click_record_clear()

        return

    def _run_actions_before_ocr_cell(self) -> None:
        """点击地块前先做一键收获与务农，减少弹窗噪声。"""
        self._run_feature_harvest()
        # self._run_feature_maintain_actions(enable_farming=True)

    def _resolve_scan_columns(self, cells: list[LandCell], *, from_side: str, column_count: int) -> list[int]:
        """根据当前网格确定本轮应扫描的物理列（排除前确定，避免补列）。"""
        col_map: dict[int, list[LandCell]] = {}
        for cell in cells:
            physical_col = self._physical_col_rtl(cell)
            col_map.setdefault(physical_col, []).append(cell)
        rtl_cols = sorted(col_map.keys())
        if str(from_side).strip().lower() == 'left':
            return list(reversed(rtl_cols))[: max(0, int(column_count))]
        return rtl_cols[: max(0, int(column_count))]

    def _click_and_ocr_cell(self, *, cell: LandCell, max_retries: int = 1):
        """点击单个地块并采集 OCR 文本；成熟时间 OCR 失败时可重试一次。"""
        max_attempts = max(1, int(max_retries) + 1)
        for attempt in range(max_attempts):
            x, y = int(cell.center[0]), int(cell.center[1])
            self.ui.device.click_point(x, y, desc=f'序号 {cell.label}')
            self.ui.device.sleep(0.3)

            suffix_location: tuple[int, int] | None = None
            popup_wait_timeout = Timer(LAND_SCAN_POPUP_WAIT_TIMEOUT_SECONDS, count=0).start()
            while not popup_wait_timeout.reached():
                self.ui.device.screenshot()
                removal_location = self.ui.appear_location(BTN_CROP_REMOVAL, offset=30, static=False)
                # 正常弹窗
                if removal_location is not None:
                    suffix_location = self.ui.appear_location(
                        BTN_CROP_MATURITY_TIME_SUFFIX, offset=30, threshold=0.65, static=False
                    )
                    if suffix_location is not None:
                        break
                # 空土地弹窗
                empty_location = self.ui.appear_location(
                    BTN_LAND_POP_EMPTY, offset=(-160, -180, 280, 280), threshold=0.65, static=False
                )
                BTN_LAND_POP_EMPTY._button_offset = None
                if empty_location is not None and removal_location is None:
                    removal_location = empty_location
                    logger.debug(
                        '地块巡查: 弹窗锚点 | 序号={} 类型=empty 锚点={} 计算中心={}',
                        cell.label,
                        removal_location,
                        cell.center,
                    )
                    need_upgrade = self._detect_need_upgrade(anchor=removal_location, empty_plot=True)
                    need_planting = True
                    roi = self._build_land_level_region(removal_location)
                    level_items = self.ocr_tool.detect(self.ui.device.image, region=roi, scale=1.2, alpha=1.1, beta=0.0)
                    level_text = self._merge_ocr_items_text(level_items)
                    level = self._extract_land_level(level_text)
                    logger.info(
                        '地块巡查: 空地等级OCR | 序号={} text={} 等级={}',
                        cell.label,
                        self._short_text(level_text),
                        self._level_label(level),
                    )
                    update_level = level or None
                    if not level:
                        logger.warning(
                            '地块巡查: 未识别到等级，更新其他字段 | 序号={} 等级={} 需要升级={} 需要播种={}',
                            cell.label,
                            level_text,
                            need_upgrade,
                            need_planting,
                        )
                    updated = self._update_plot_fields(
                        plot_id=cell.label,
                        level=update_level,
                        countdown='',
                        countdown_sync_time='',
                        need_upgrade=need_upgrade,
                        need_planting=need_planting,
                    )
                    if updated:
                        self._save_plot_update(
                            plot_id=cell.label,
                            level=update_level,
                            countdown='',
                            countdown_sync_time='',
                            need_upgrade=need_upgrade,
                            need_planting=need_planting,
                        )
                    return
                self.ui.device.sleep(0.2)
            else:
                logger.warning(
                    '地块巡查: 等待地块弹窗超时，跳过当前地块 | 序号={} 中心={}',
                    cell.label,
                    cell.center,
                )
                return

            removal_location = suffix_location
            logger.debug(
                '地块巡查: 弹窗锚点 | 序号={} 类型=planted 锚点={} 计算中心={}',
                cell.label,
                removal_location,
                cell.center,
            )
            need_upgrade = self._detect_need_upgrade(anchor=removal_location, empty_plot=False)
            need_planting = False
            countdown: str | None = None
            countdown_sync_time: str | None = None

            if removal_location is None:
                logger.warning('地块巡查: 未识别到成熟时间锚点，跳过 OCR | 序号={}', cell.label)
            else:
                roi = self._build_ocr_region(removal_location)
                items = self.ocr_tool.detect(self.ui.device.image, region=roi, scale=1.2, alpha=1.1, beta=0.0)
                text, score, tokens = self._pick_time_tokens_near_suffix(items=items, anchor=removal_location)
                countdown = self._extract_maturity_time(text)
                observed_at = datetime.now().replace(microsecond=0)
                countdown_sync_time = observed_at.strftime('%Y-%m-%d %H:%M:%S') if countdown else ''
                display_text = countdown or text
                logger.debug(
                    '地块巡查: OCR筛选 | region={} pick_offset=({}, {}, {}, {}) tokens={} text={}',
                    roi,
                    LAND_SCAN_TIME_PICK_X1,
                    LAND_SCAN_TIME_PICK_Y1,
                    LAND_SCAN_TIME_PICK_X2,
                    LAND_SCAN_TIME_PICK_Y2,
                    tokens,
                    display_text or '<empty>',
                )
                logger.info(
                    '地块巡查: OCR | 序号={} text={} score={:.3f}', cell.label, self._short_text(display_text), score
                )

            if countdown:
                level, _, _ = self._detect_plotted_land_level_by_color(removal_location)
                updated = self._update_plot_fields(
                    plot_id=cell.label,
                    level=level or None,
                    countdown=countdown,
                    countdown_sync_time=countdown_sync_time,
                    need_upgrade=need_upgrade,
                    need_planting=need_planting,
                )
                if updated:
                    self._save_plot_update(
                        plot_id=cell.label,
                        level=level or None,
                        countdown=countdown,
                        countdown_sync_time=countdown_sync_time,
                        need_upgrade=need_upgrade,
                        need_planting=need_planting,
                    )
                return

            if attempt < max_attempts - 1:
                logger.warning(
                    '地块巡查: 成熟时间 OCR 失败，将重试一次 | 序号={} attempt={} text={}',
                    cell.label,
                    attempt + 1,
                    self._short_text(text),
                )
                self.ui.device.click_button(GOTO_MAIN)
                self.ui.device.sleep(0.2)
                continue

            logger.warning('地块巡查: 成熟时间 OCR 重试后仍失败 | 序号={} text={}', cell.label, self._short_text(text))
            level, _, _ = self._detect_plotted_land_level_by_color(removal_location)
            updated = self._update_plot_fields(
                plot_id=cell.label,
                level=level or None,
                countdown=countdown,
                countdown_sync_time=countdown_sync_time,
                need_upgrade=need_upgrade,
                need_planting=need_planting,
            )
            if updated:
                self._save_plot_update(
                    plot_id=cell.label,
                    level=level or None,
                    countdown=countdown,
                    countdown_sync_time=countdown_sync_time,
                    need_upgrade=need_upgrade,
                    need_planting=need_planting,
                )
            return

    def _detect_need_upgrade(self, *, anchor: tuple[int, int] | None, empty_plot: bool) -> bool:
        """识别当前地块弹窗是否出现升级图标（GIF 多帧匹配）。"""
        if anchor is None:
            return False
        roi = self._build_upgrade_icon_region(anchor, empty_plot=empty_plot)
        matched = self.ui.match_gif_multi(ICON_LAND_UPGRADE, roi=roi)
        return bool(matched)

    @staticmethod
    def _build_upgrade_icon_region(center: tuple[int, int], *, empty_plot: bool) -> tuple[int, int, int, int]:
        """按锚点与偏移构造升级图标检测 ROI。"""
        dx1, dy1, dx2, dy2 = (
            LAND_SCAN_UPGRADE_EMPTY_REGION_OFFSET if empty_plot else LAND_SCAN_UPGRADE_NON_EMPTY_REGION_OFFSET
        )
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

    def _detect_plotted_land_level_by_color(
        self,
        anchor: tuple[int, int] | None,
    ) -> tuple[str, tuple[int, int, int] | None, float]:
        """按成熟时间后缀锚点偏移取色，识别已播种地块等级。"""
        if anchor is None:
            return '', None, 0.0
        bgr = self._sample_color_bgr_near_anchor(
            anchor=anchor,
            offset=LAND_SCAN_PLOTTED_LEVEL_COLOR_OFFSET,
            radius=LAND_SCAN_PLOTTED_LEVEL_COLOR_SAMPLE_RADIUS,
        )
        if bgr is None:
            return '', None, 0.0
        rgb = (int(bgr[2]), int(bgr[1]), int(bgr[0]))
        best_level = ''
        best_distance = float('inf')
        for level, color_rgb in LAND_SCAN_PLOTTED_LEVEL_COLORS_RGB.items():
            dr = float(rgb[0] - int(color_rgb[0]))
            dg = float(rgb[1] - int(color_rgb[1]))
            db = float(rgb[2] - int(color_rgb[2]))
            distance = float((dr * dr + dg * dg + db * db) ** 0.5)
            if distance < best_distance:
                best_distance = distance
                best_level = str(level)
        if best_distance > float(LAND_SCAN_PLOTTED_LEVEL_COLOR_DISTANCE_THRESHOLD):
            return '', rgb, best_distance
        return best_level, rgb, best_distance

    def _sample_color_bgr_near_anchor(
        self,
        *,
        anchor: tuple[int, int],
        offset: tuple[int, int],
        radius: int,
    ) -> tuple[int, int, int] | None:
        """相对锚点采样颜色均值（BGR）。"""
        image = getattr(getattr(self.ui, 'device', None), 'image', None)
        if image is None:
            return None
        h, w = image.shape[:2]
        cx = int(anchor[0]) + int(offset[0])
        cy = int(anchor[1]) + int(offset[1])
        cx = max(0, min(cx, w - 1))
        cy = max(0, min(cy, h - 1))
        r = max(0, int(radius))
        x1 = max(0, cx - r)
        y1 = max(0, cy - r)
        x2 = min(w, cx + r + 1)
        y2 = min(h, cy + r + 1)
        patch = image[y1:y2, x1:x2]
        if patch.size <= 0:
            return None
        mean_bgr = patch.reshape(-1, 3).mean(axis=0)
        return int(mean_bgr[0]), int(mean_bgr[1]), int(mean_bgr[2])

    def _exclude_expand_brand_related_cells(
        self,
        cells: list[LandCell],
        target_key: tuple[int, int] | None = None,
        brand_roi: tuple[int, int, int, int] | None = None,
    ) -> tuple[list[LandCell], tuple[int, int] | None]:
        """按 BTN_EXPAND_BRAND 位置排除未扩建地块。

        规则：只保留坐标小于 target_cell 的地块（列优先、行升序比较）。
        例如 target=2-4 时，仅巡查 1-1/1-2/1-3/1-4/2-1/2-2/2-3。
        若传入 target_key，则直接复用，不再重新检测 BTN_EXPAND_BRAND。
        若传入 brand_roi，则在固定 ROI 内识别 BTN_EXPAND_BRAND。
        """
        if target_key is None:
            brand_location = self._detect_expand_brand_location(brand_roi=brand_roi)
            if brand_location is None:
                return cells, None
            target_cell = self._pick_nearest_cell(cells, brand_location)
            if target_cell is None:
                return cells, None
            target_key = (int(target_cell.col), int(target_cell.row))

        filtered = [cell for cell in cells if (int(cell.col), int(cell.row)) < target_key]
        excluded_labels = sorted({cell.label for cell in cells if (int(cell.col), int(cell.row)) >= target_key})
        logger.info(
            '地块巡查: 排除未扩建地块 | 参考坐标={}-{} 排除序号={} 剩余={}/{}',
            target_key[0],
            target_key[1],
            excluded_labels,
            len(filtered),
            len(cells),
        )
        return filtered, target_key

    def _detect_expand_brand_location(
        self,
        brand_roi: tuple[int, int, int, int] | None = None,
    ) -> tuple[int, int] | None:
        """识别 BTN_EXPAND_BRAND 中心位置；传入 brand_roi 时只在 ROI 内做 btn 识别。"""
        if brand_roi is not None:
            return self.ui.appear_location_in_roi(
                BTN_EXPAND_BRAND,
                brand_roi,
                offset=30,
                threshold=0.65,
            )
        loc = self.ui.appear_location(BTN_EXPAND_BRAND, offset=30, threshold=0.7, static=False)
        BTN_EXPAND_BRAND._button_offset = None
        return loc

    @staticmethod
    def _pick_nearest_cell(cells: list[LandCell], point: tuple[int, int]) -> LandCell | None:
        """返回与 point 距离最近的地块。"""
        if not cells:
            return None
        px = int(point[0])
        py = int(point[1])
        return min(cells, key=lambda cell: (int(cell.center[0]) - px) ** 2 + (int(cell.center[1]) - py) ** 2)

    @staticmethod
    def _physical_col_rtl(cell: LandCell) -> int:
        """将地块映射为物理列索引（右到左，范围 1..9）。"""
        logical_col = int(cell.col)
        logical_row = int(cell.row)
        idx = (LAND_SCAN_ROWS - logical_row) + (logical_col - 1) + 1
        return max(1, min(LAND_SCAN_PHYSICAL_COLS, idx))

    @staticmethod
    def _build_ocr_region(center: tuple[int, int]) -> tuple[int, int, int, int]:
        """以 center 为基准，按固定偏移构造 OCR ROI。"""
        cx = int(center[0])
        cy = int(center[1])
        dx1, dy1, dx2, dy2 = LAND_SCAN_OCR_REGION_OFFSET
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

    @staticmethod
    def _build_land_level_region(center: tuple[int, int]) -> tuple[int, int, int, int]:
        """以空地弹窗锚点为基准，构造地块等级 OCR ROI。"""
        cx = int(center[0])
        cy = int(center[1])
        dx1, dy1, dx2, dy2 = LAND_SCAN_LEVEL_REGION_OFFSET
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

    @staticmethod
    def _merge_ocr_items_text(items: list[OCRItem]) -> str:
        """将 OCR items 按 x 坐标拼接为文本。"""
        if not items:
            return ''
        ordered = sorted(items, key=lambda item: min(float(point[0]) for point in item.box))
        return ''.join(str(item.text or '').strip() for item in ordered if str(item.text or '').strip()).strip()

    @staticmethod
    def _extract_land_level(text: str) -> str:
        """从中文 land_level 文本解析配置 level 值（过滤常见前缀干扰）。"""
        raw = str(text or '').strip().replace(' ', '').replace('快', '')
        if not raw:
            return ''
        match = LAND_SCAN_LEVEL_PATTERN.search(raw)
        if not match:
            return ''
        token = str(match.group(1))
        if token == '未扩建':
            return 'unbuilt'
        if token == '普通':
            return 'normal'
        if token == '红':
            return 'red'
        if token == '黑':
            return 'black'
        if token == '金':
            return 'gold'
        if token == '紫晶':
            return 'amethyst'
        return ''

    @staticmethod
    def _level_label(level: str | None) -> str:
        """将配置等级值映射为中文日志文案。"""
        text = str(level or '').strip().lower()
        if not text:
            return '<empty>'
        return str(LAND_SCAN_LEVEL_LABELS.get(text, level))

    @staticmethod
    def _pick_time_tokens_near_suffix(
        items: list[OCRItem],
        anchor: tuple[int, int],
    ) -> tuple[str, float, list[str]]:
        """从 OCR 明细中二次筛选目标窗口内的 token，并按 x 从左到右拼接。"""
        ax = int(anchor[0])
        ay = int(anchor[1])
        x1 = float(ax + LAND_SCAN_TIME_PICK_X1)
        x2 = float(ax + LAND_SCAN_TIME_PICK_X2)
        y1 = float(ay + LAND_SCAN_TIME_PICK_Y1)
        y2 = float(ay + LAND_SCAN_TIME_PICK_Y2)
        if x1 > x2:
            x1, x2 = x2, x1
        if y1 > y2:
            y1, y2 = y2, y1

        candidates: list[tuple[float, str, float]] = []
        for item in items:
            text = str(item.text or '').strip()
            if not text:
                continue
            xs = [float(point[0]) for point in item.box]
            ys = [float(point[1]) for point in item.box]
            min_x = float(min(xs))
            max_x = float(max(xs))
            min_y = float(min(ys))
            max_y = float(max(ys))
            # 参考好友昵称做法：先拿 OCR item，再按目标窗口做 bbox 筛选。
            if max_x <= x1 or min_x >= x2:
                continue
            if max_y <= y1 or min_y >= y2:
                continue
            candidates.append((min_x, text, float(item.score)))

        candidates.sort(key=lambda row: row[0])
        tokens = [row[1] for row in candidates]
        merged = ''.join(tokens).strip()
        if not candidates:
            return '', 0.0, []
        score = float(sum(row[2] for row in candidates) / len(candidates))
        return merged, score, tokens

    @staticmethod
    def _short_text(text: str, limit: int = 36) -> str:
        """截断 OCR 日志文本，避免日志过长。"""
        clean = str(text or '').strip().replace('\n', ' ')
        if len(clean) <= limit:
            return clean or '<empty>'
        return f'{clean[:limit]}...'

    @staticmethod
    def _extract_maturity_time(text: str) -> str:
        """从 OCR 文本提取 HH:MM:SS（兼容中文全角冒号）。"""
        raw = str(text or '').strip().replace('：', ':')
        if not raw:
            return ''
        match = LAND_SCAN_MATURITY_TIME_PATTERN.search(raw)
        if not match:
            return ''
        return str(match.group(1))

    @staticmethod
    def _parse_maturity_countdown_seconds(text: str) -> int | None:
        """把 HH:MM:SS 解析为秒数。"""
        countdown = TaskLandScan._extract_maturity_time(text)
        if not countdown:
            return None
        try:
            hour, minute, second = map(int, countdown.split(':'))
            if minute < 0 or minute > 59 or second < 0 or second > 59:
                return None
            return hour * 3600 + minute * 60 + second
        except Exception:
            return None

    def _update_plot_fields(
        self,
        *,
        plot_id: str,
        level: str | None = None,
        countdown: str | None = None,
        countdown_sync_time: str | None = None,
        need_upgrade: bool | None = None,
        need_planting: bool | None = None,
    ) -> bool:
        """回写单个地块字段（同地块统一更新）。"""
        target = str(plot_id or '').strip()
        if not target:
            return False
        plots = self.config.land.plots
        if not isinstance(plots, list):
            return False

        normalized_level: str | None = None
        if level is not None:
            raw_level = str(level or '').strip().lower()
            normalized_level = raw_level or None
        normalized_countdown: str | None = None
        if countdown is not None:
            normalized_countdown = str(countdown or '').strip()
        normalized_countdown_sync_time: str | None = None
        if countdown_sync_time is not None:
            normalized_countdown_sync_time = str(countdown_sync_time or '').strip()

        for item in plots:
            if not isinstance(item, dict):
                continue
            if str(item.get('plot_id', '')).strip() != target:
                continue
            old_level = str(item.get('level', '') or '').strip().lower()
            old_countdown = str(item.get('maturity_countdown', '') or '').strip()
            old_countdown_sync_time = str(item.get('countdown_sync_time', '') or '').strip()
            old_need_upgrade = bool(item.get('need_upgrade', False))
            old_need_planting = bool(item.get('need_planting', False))
            changed = False
            if normalized_level is not None and old_level != normalized_level:
                item['level'] = normalized_level
                changed = True
            if normalized_countdown is not None and old_countdown != normalized_countdown:
                item['maturity_countdown'] = normalized_countdown
                changed = True
            if normalized_countdown_sync_time is not None and old_countdown_sync_time != normalized_countdown_sync_time:
                item['countdown_sync_time'] = normalized_countdown_sync_time
                changed = True
            if need_upgrade is not None and old_need_upgrade != bool(need_upgrade):
                item['need_upgrade'] = bool(need_upgrade)
                changed = True
            if need_planting is not None and old_need_planting != bool(need_planting):
                item['need_planting'] = bool(need_planting)
                changed = True

            # 地块巡查更新后，若真实剩余成熟时间变大或上次已成熟，清空普通化肥冷却。
            if normalized_countdown and normalized_countdown_sync_time:
                try:
                    new_sync_time = datetime.strptime(normalized_countdown_sync_time, '%Y-%m-%d %H:%M:%S')
                    new_countdown_seconds = self._parse_maturity_countdown_seconds(normalized_countdown)
                    if new_countdown_seconds is not None:
                        new_real_remaining = int(
                            (new_sync_time + timedelta(seconds=new_countdown_seconds) - datetime.now()).total_seconds()
                        )
                        new_real_remaining = max(0, new_real_remaining)
                        old_real_remaining_text = str(item.get('last_real_remaining_seconds') or '').strip()
                        old_real_remaining = int(old_real_remaining_text) if old_real_remaining_text else 0
                        # 加120秒防止计算误差
                        if new_real_remaining > (old_real_remaining + 120) or old_real_remaining <= 0:
                            old_fertilize_time = str(item.get('last_fertilize_time') or '').strip()
                            if old_fertilize_time:
                                item['last_fertilize_time'] = ''
                                changed = True
                            if old_real_remaining_text:
                                item['last_real_remaining_seconds'] = ''
                                changed = True
                            logger.info(
                                '地块巡查: 清空普通化肥冷却 | 序号={} new_remaining={}s old_remaining={}s',
                                plot_id,
                                new_real_remaining,
                                old_real_remaining,
                            )
                except Exception:
                    pass

            return changed
        return False

    def _save_plot_update(
        self,
        *,
        plot_id: str,
        level: str | None = None,
        countdown: str | None = None,
        countdown_sync_time: str | None = None,
        need_upgrade: bool | None = None,
        need_planting: bool | None = None,
    ) -> None:
        """单地块统一字段更新后立即落盘。"""
        try:
            self.config.save()
        except Exception as exc:
            logger.warning(
                (
                    '地块巡查: 地块信息写入配置失败 | 序号={} 等级={} 成熟倒计时={} '
                    '倒计时基准={} 需要升级={} 需要播种={} error={}'
                ),
                plot_id,
                self._level_label(level),
                countdown,
                countdown_sync_time,
                need_upgrade,
                need_planting,
                exc,
            )
            return
        self._emit_config_snapshot()
        logger.info(
            '地块巡查: 地块信息已更新 | 序号={} 等级={} 成熟倒计时={} 倒计时基准={} 需要升级={} 需要播种={}',
            plot_id,
            self._level_label(level),
            countdown,
            countdown_sync_time,
            need_upgrade,
            need_planting,
        )

    def _emit_config_snapshot(self) -> None:
        """写盘后主动推送一次配置快照，避免 UI 长时间持有旧数据。"""
        emitter = getattr(self.engine, '_emit_config_now', None)
        if callable(emitter):
            try:
                emitter()
            except Exception:
                return
