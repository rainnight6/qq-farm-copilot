"""自动种草任务。"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import cv2
import numpy as np
from loguru import logger

from core.base.timer import Timer
from core.engine.task.registry import TaskResult
from core.ui.assets import (
    BTN_FARMING,
    BTN_FRIEND_RIGHT_FRAME,
    BTN_GRASS,
    BTN_GRASS_END,
    BTN_HOME,
    BTN_LAND_LEFT,
    BTN_LAND_RIGHT,
    BTN_STEAL,
    BTN_VISIT_FIRST,
    MAIN_GOTO_FRIEND,
)
from core.ui.page import GOTO_MAIN, page_friend_list, page_main
from tasks.base import TaskBase
from utils.land_grid import (
    LAND_LEFT_ANCHOR_BASELINE,
    LAND_RIGHT_ANCHOR_BASELINE,
    get_lands_from_land_anchor,
)

if TYPE_CHECKING:
    from core.engine.bot.local_engine import LocalBotEngine
    from core.ui.ui import UI

# 识别种草按钮的置信度阈值。
GRASS_DETECT_THRESHOLD = 0.74
# 识别种草结束按钮的置信度阈值。
GRASS_END_DETECT_THRESHOLD = 0.74
# 草图标参考平均颜色（BGR，由模板非透明区域统计得到）。
GRASS_COLOR_REF_BGR = np.array([63.0, 171.0, 116.0], dtype=np.float32)
# 结束图标参考平均颜色（BGR，由模板非透明区域统计得到）。
GRASS_END_COLOR_REF_BGR = np.array([136.0, 145.0, 147.0], dtype=np.float32)
# 识别帮忙按钮的置信度阈值。
HELP_DETECT_THRESHOLD = 0.74
# 识别偷菜按钮的置信度阈值。
STEAL_DETECT_THRESHOLD = 0.74
# 连续识别不到草图标时，退出任务的阈值次数。
MAX_CONSECUTIVE_FAIL_COUNT = 6
# 切换到下一个好友时，在当前选中框中心 x 基础上的偏移量（像素）。
FRIEND_NEXT_OFFSET_X = 70
# 进入好友详情页的最大等待时间（秒）。
ENTER_FRIEND_DETAIL_TIMEOUT_SECONDS = 10.0
# 点击帮忙按钮后的稳定等待时间（秒）。
HELP_CLICK_SLEEP_SECONDS = 0.5
# 点击地块打开弹窗后的稳定等待时间（秒）。
PLOT_CLICK_SLEEP_SECONDS = 0.5
# 拖拽到每个地块后的间隔（秒）。
DRAG_MOVE_SLEEP_SECONDS = 0.15
# 拖拽按下/移动的持续时间（秒）。
DRAG_DURATION_SECONDS = 0.1


class TaskGrass(TaskBase):
    """封装 `TaskGrass` 任务的执行入口与步骤。"""

    def __init__(self, engine: 'LocalBotEngine', ui: 'UI'):
        """初始化对象并准备运行所需状态。"""
        super().__init__(engine, ui)

    def run(self, rect: tuple[int, int, int, int]) -> TaskResult:
        """执行自动种草主流程。"""
        _ = rect
        logger.info('自动种草: 开始')

        # 进入好友列表
        self.ui.ui_ensure(page_friend_list)
        self._wait_list_loading()

        if not self._enter_first_friend_detail():
            logger.info('自动种草: 未能进入首位好友详情，结束')
            self.back_to_home()
            return self.ok()

        processed_count = 0
        consecutive_fail_count = 0
        consecutive_end_count = 0
        while 1:
            self.ui.device.screenshot()
            if not self.ui.appear(BTN_HOME, offset=30):
                logger.info('自动种草: 当前不在好友农场，结束')
                break

            processed_count += 1
            logger.info('自动种草: 处理第 {} 位好友', processed_count)

            # 1. 先点击一次帮忙按钮（如果存在）
            self._maybe_click_help_button()

            # 2. 若存在偷菜按钮，点击后跳过当前好友，不计入失败次数
            if self._maybe_click_steal_and_skip():
                if not self._goto_next_friend():
                    logger.info('自动种草: 切换下一位好友失败，结束')
                    break
                continue

            # 3. 在弹窗打开前先收集地块网格（弹窗会遮挡地块锚点）
            land_cells = self._collect_land_cells_for_grass()
            if not land_cells:
                logger.warning('自动种草: 未识别到地块网格，结束')
                break

            # 4. 点击 1-1 地块打开种草弹窗
            if not self._open_grass_popup(land_cells=land_cells):
                logger.info('自动种草: 未打开种草弹窗，结束')
                break

            # 5. 识别种草按钮/种草结束按钮
            detection = self._detect_grass_or_end()
            if detection is None:
                consecutive_fail_count += 1
                logger.info(
                    '自动种草: 未识别到草/结束图标，累计失败 {}/{}',
                    consecutive_fail_count,
                    MAX_CONSECUTIVE_FAIL_COUNT,
                )
                self._close_grass_popup()
                if consecutive_fail_count >= MAX_CONSECUTIVE_FAIL_COUNT:
                    logger.info('自动种草: 连续失败达到阈值，结束任务')
                    break
                if not self._goto_next_friend():
                    logger.info('自动种草: 切换下一位好友失败，结束')
                    break
                continue

            kind, grass_point, score = detection
            if kind == 'end':
                consecutive_fail_count = 0
                consecutive_end_count += 1
                if consecutive_end_count >= 3:
                    logger.info(
                        '自动种草: 连续{}次识别到结束图标，确认种草次数已用完，结束任务',
                        consecutive_end_count,
                    )
                    self._close_grass_popup()
                    break
                logger.info(
                    '自动种草: 第{}次识别到结束图标，继续切换下一位好友确认',
                    consecutive_end_count,
                )
                self._close_grass_popup()
                if not self._goto_next_friend():
                    logger.info('自动种草: 切换下一位好友失败，结束')
                    break
                continue

            # kind == 'grass'
            consecutive_fail_count = 0
            consecutive_end_count = 0
            logger.info('自动种草: 识别到草图标 | score={:.3f}', score)

            # 6. 按配置概率决定是否跳过当前好友（0 不跳过，1 全部跳过无意义）
            skip_probability = max(0.0, min(1.0, float(self.task.grass.feature.skip_probability)))
            if 0.0 < skip_probability and random.random() < skip_probability:
                logger.info(
                    '自动种草: 随机跳过当前好友 | probability={:.2f}',
                    skip_probability,
                )
                self._close_grass_popup()
                if not self._goto_next_friend():
                    logger.info('自动种草: 切换下一位好友失败，结束')
                    break
                continue

            # 7. 拖拽种草到所有地块
            self._drag_grass_to_lands(grass_point, land_cells)

            # 关闭弹窗/清空状态，准备切换下一位好友
            self._close_grass_popup()

            # 8. 切换下一位好友
            if not self._goto_next_friend():
                logger.info('自动种草: 切换下一位好友失败，结束')
                break

        self.back_to_home()
        logger.info('自动种草: 结束 | 处理好友数={}', processed_count)
        return self.ok()

    def _wait_list_loading(self):
        """等待好友列表加载完成。"""
        while 1:
            self.ui.device.screenshot()
            if self.ui.appear(BTN_VISIT_FIRST, offset=30):
                break

    def _enter_first_friend_detail(self) -> bool:
        """从好友列表页进入第一位好友详情页。"""
        timer = Timer(ENTER_FRIEND_DETAIL_TIMEOUT_SECONDS, count=0).start()
        while 1:
            self.ui.device.screenshot()
            if self.ui.appear(BTN_HOME, offset=30):
                logger.info('自动种草: 已进入好友详情页')
                return True
            if timer.reached():
                logger.warning('自动种草: 进入好友详情页超时')
                return False
            self.ui.appear_then_click(BTN_VISIT_FIRST, offset=30, interval=1)

    def _maybe_click_help_button(self) -> bool:
        """若当前好友农场存在帮忙按钮，点击一次。"""
        if self.ui.appear(BTN_FARMING, offset=30, threshold=HELP_DETECT_THRESHOLD, static=False):
            clicked = self.ui.appear_then_click(
                BTN_FARMING,
                offset=30,
                interval=1,
                threshold=HELP_DETECT_THRESHOLD,
                static=False,
            )
            if clicked:
                logger.info('自动种草: 点击帮忙按钮')
                self.ui.device.sleep(HELP_CLICK_SLEEP_SECONDS)
                return True
        return False

    def _maybe_click_steal_and_skip(self) -> bool:
        """若当前好友农场存在偷菜按钮，点击一次并跳过该好友。"""
        self.ui.device.screenshot()
        if not self.ui.appear(BTN_STEAL, offset=30, threshold=STEAL_DETECT_THRESHOLD, static=False):
            return False
        if self.ui.appear_then_click(
            BTN_STEAL,
            offset=30,
            interval=1,
            threshold=STEAL_DETECT_THRESHOLD,
            static=False,
        ):
            logger.info('自动种草: 检测到偷菜按钮并点击，跳过当前好友')
            self.ui.device.sleep(0.3)
            return True
        return False

    def _collect_land_cells_for_grass(self) -> list:
        """为种草任务收集地块网格：好友农场视角下使用更宽松的锚点检测。"""
        self.ui.device.screenshot()
        right_anchor = self.appear_land_right(
            offset=(-30, -30, 160, 30),
            threshold=0.7,
            static=False,
        )
        left_anchor = self.ui.appear_location(
            BTN_LAND_LEFT,
            offset=(-160, -30, 30, 30),
            threshold=0.7,
            static=False,
        )

        if right_anchor is not None or left_anchor is not None:
            cells = get_lands_from_land_anchor(
                right_anchor,
                left_anchor,
                rows=4,
                cols=6,
                start_anchor='right',
            )
            if cells:
                logger.info(
                    '自动种草: 网格识别 | 右锚点={} 左锚点={} 地块总计={}',
                    right_anchor,
                    left_anchor,
                    len(cells),
                )
                return cells

        logger.warning('自动种草: 地块锚点识别失败，尝试背景树回正并使用基准锚点')
        self.align_view_by_background_tree(log_prefix='自动种草')
        cells = get_lands_from_land_anchor(
            LAND_RIGHT_ANCHOR_BASELINE,
            LAND_LEFT_ANCHOR_BASELINE,
            rows=4,
            cols=6,
            start_anchor='right',
        )
        logger.info('自动种草: 网格识别（基准回退）| 地块总计={}', len(cells))
        return cells

    def _open_grass_popup(self, *, land_cells: list) -> bool:
        """点击 1-1 地块中心，打开种草弹窗。"""
        plot_1_1 = self._locate_1_1_plot_center(land_cells=land_cells)
        if plot_1_1 is None:
            logger.warning('自动种草: 未找到 1-1 地块坐标')
            return False

        x, y = plot_1_1
        logger.info('自动种草: 点击 1-1 地块 | location=({},{})', x, y)
        if not self.ui.device.click_point(x, y, desc='点击1-1地块打开种草弹窗'):
            logger.warning('自动种草: 点击 1-1 地块失败')
            return False
        self.ui.device.sleep(PLOT_CLICK_SLEEP_SECONDS)
        return True

    def _locate_1_1_plot_center(
        self,
        *,
        land_cells: list,
    ) -> tuple[int, int] | None:
        """定位 1-1 地块中心：优先使用已识别的地块网格，失败后识别锚点/基准锚点回退。"""
        # 1) 优先使用弹窗打开前已收集的地块网格
        for cell in land_cells:
            if cell.label == '1-1':
                return cell.center

        # 2) 回退：尝试识别当前画面左右地块锚点
        self.ui.device.screenshot()
        right_anchor = self.appear_land_right(
            offset=(-30, -30, 160, 30),
            threshold=0.8,
            static=False,
        )
        left_anchor = self.ui.appear_location(
            BTN_LAND_LEFT,
            offset=(-160, -30, 30, 30),
            threshold=0.8,
            static=False,
        )
        if right_anchor is not None or left_anchor is not None:
            cells = get_lands_from_land_anchor(
                right_anchor,
                left_anchor,
                rows=4,
                cols=6,
                start_anchor='right',
            )
            for cell in cells:
                if cell.label == '1-1':
                    return cell.center

        # 3) 回退：按背景树回正画面后使用基准锚点计算网格
        logger.info('自动种草: 地块锚点识别失败，尝试背景树回正并使用基准锚点')
        self.align_view_by_background_tree(log_prefix='自动种草')
        cells = get_lands_from_land_anchor(
            LAND_RIGHT_ANCHOR_BASELINE,
            LAND_LEFT_ANCHOR_BASELINE,
            rows=4,
            cols=6,
            start_anchor='right',
        )
        for cell in cells:
            if cell.label == '1-1':
                return cell.center
        return None

    def _close_grass_popup(self) -> None:
        """点击空白处关闭种草弹窗。"""
        self.ui.device.click_button(GOTO_MAIN)
        self.ui.device.sleep(0.3)

    def _detect_grass_or_end(self) -> tuple[str, tuple[int, int], float] | None:
        """同时识别种草按钮与种草结束按钮，按匹配区域颜色仲裁真实类型。"""
        self.ui.device.screenshot()

        grass_loc = self.ui.appear_location(
            BTN_GRASS,
            offset=30,
            threshold=GRASS_DETECT_THRESHOLD,
            static=False,
        )
        grass_score = float(getattr(BTN_GRASS, '_last_score', 0.0))

        end_loc = self.ui.appear_location(
            BTN_GRASS_END,
            offset=30,
            threshold=GRASS_END_DETECT_THRESHOLD,
            static=False,
        )
        end_score = float(getattr(BTN_GRASS_END, '_last_score', 0.0))

        grass_ok = grass_loc is not None
        end_ok = end_loc is not None

        if not grass_ok and not end_ok:
            logger.debug(
                '自动种草: 草图标={:.3f}, 结束图标={:.3f}, 均未命中',
                grass_score,
                end_score,
            )
            return None

        # 两图标形状相同、仅颜色不同，模板匹配可能互相串高相似度。
        # 分别对两个命中区域做颜色分类，然后优先选择“模板类型与颜色分类一致”的候选，
        # 若都不一致，则选择颜色分类置信度更高的那个。
        candidates: list[tuple[str, str, tuple[int, int], float, float]] = []
        if grass_ok:
            kind, confidence = self._classify_grass_region(BTN_GRASS)
            candidates.append(('grass', kind, grass_loc, grass_score, confidence))
        if end_ok:
            kind, confidence = self._classify_grass_region(BTN_GRASS_END)
            candidates.append(('end', kind, end_loc, end_score, confidence))

        consistent = [
            (expected, kind, loc, score, conf) for expected, kind, loc, score, conf in candidates if expected == kind
        ]
        if consistent:
            _, kind, loc, score, _ = max(consistent, key=lambda item: item[3])
        else:
            _, kind, loc, score, _ = max(candidates, key=lambda item: item[4])

        logger.debug(
            '自动种草: 颜色仲裁为{} | 草相似度={:.3f}, 结束相似度={:.3f}',
            '草图标' if kind == 'grass' else '结束图标',
            grass_score,
            end_score,
        )
        return (kind, loc, score)

    def _classify_grass_region(self, button) -> tuple[str, float]:
        """根据按钮匹配区域的平均颜色判断是草图标还是结束图标。

        返回 (kind, confidence)，confidence 越大表示分类越确定。
        """
        image = self.ui.device.image
        offset = getattr(button, '_button_offset', None)
        if image is None or offset is None:
            return 'end', 0.0

        x1, y1, x2, y2 = offset
        h, w = image.shape[:2]
        x1 = max(0, min(int(x1), w - 1))
        y1 = max(0, min(int(y1), h - 1))
        x2 = max(x1 + 1, min(int(x2), w))
        y2 = max(y1 + 1, min(int(y2), h))
        region = image[y1:y2, x1:x2]
        if region.size == 0:
            return 'end', 0.0

        tmpl = getattr(button, 'image', None)
        if tmpl is not None and tmpl.shape[:2] == region.shape[:2]:
            gray = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY)
            mask = (gray > 0).astype(np.float32)
            mask_sum = float(mask.sum())
            if mask_sum > 0:
                mean_bgr = (region.astype(np.float32) * mask[:, :, None]).sum(axis=(0, 1)) / mask_sum
            else:
                mean_bgr = region.mean(axis=(0, 1)).astype(np.float32)
        else:
            mean_bgr = region.mean(axis=(0, 1)).astype(np.float32)

        dist_grass = float(np.linalg.norm(mean_bgr - GRASS_COLOR_REF_BGR))
        dist_end = float(np.linalg.norm(mean_bgr - GRASS_END_COLOR_REF_BGR))
        kind = 'grass' if dist_grass < dist_end else 'end'
        # confidence: 距离差越大越确定
        confidence = abs(dist_end - dist_grass)
        return kind, confidence

    def _drag_grass_to_lands(
        self,
        grass_point: tuple[int, int],
        land_cells: list,
    ) -> None:
        """拖拽种草按钮到所有地块。"""
        cells = sorted(land_cells, key=lambda cell: int(cell.order))
        dragging = False
        try:
            drag_x, drag_y = int(grass_point[0]), int(grass_point[1])
            self.engine.device.drag_down_point(drag_x, drag_y, duration=DRAG_DURATION_SECONDS)
            dragging = True
            self.ui.device.sleep(0.1)

            for cell in cells:
                land_x, land_y = int(cell.center[0]), int(cell.center[1])
                self.engine.device.drag_move_point(land_x, land_y, duration=DRAG_DURATION_SECONDS)
                self.ui.device.sleep(DRAG_MOVE_SLEEP_SECONDS)
        finally:
            if dragging:
                self.engine.device.drag_up()
                logger.info('自动种草: 种草完成 | 地块数={}', len(cells))

    def _goto_next_friend(self) -> bool:
        """点击下一位好友。"""
        self.ui.device.stuck_record_clear()
        self.ui.device.click_record_clear()

        self.ui.device.screenshot()
        current_location = self.ui.appear_location(BTN_FRIEND_RIGHT_FRAME, offset=30, threshold=0.83, static=False)
        if not current_location:
            logger.info('自动种草: 未识别到当前选中好友框')
            return False

        current_x, current_y = current_location
        next_x = int(current_x + FRIEND_NEXT_OFFSET_X)
        next_y = int(current_y)
        if self.ui.device.click_point(next_x, next_y, desc='切换下一位好友'):
            logger.info('自动种草: 切换下一位好友 | offset={}', FRIEND_NEXT_OFFSET_X)
            self.ui.device.sleep(0.5)
            return True

        logger.info('自动种草: 点击下一位好友失败')
        return False

    def back_to_home(self):
        """返回主页。"""
        self.ui.ui_ensure(page_main)
        while 1:
            self.ui.device.screenshot()
            if self.ui.appear_then_click(BTN_HOME, offset=30, interval=1):
                continue
            if self.ui.appear(MAIN_GOTO_FRIEND, offset=30):
                break
