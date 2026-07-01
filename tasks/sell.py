"""出售任务。"""

from __future__ import annotations

from loguru import logger

from core.base.timer import Timer
from core.engine.task.registry import TaskResult
from core.ui.assets import (
    BTN_BATCH_SELL,
    BTN_CLOSE,
    BTN_CONFIRM,
    BTN_PLANTING,
    BTN_SPECIAL_WAREHOUSE,
    MAIN_GOTO_WAREHOUSE,
)
from core.ui.page import page_main, page_warehouse
from models.farm_state import ActionType
from tasks.base import TaskBase

BATCH_SELL_TIMEOUT_SECONDS = 8.0


class TaskSell(TaskBase):
    """封装 `TaskSell` 任务的执行入口与步骤。"""

    def __init__(self, engine, ui):
        """初始化对象并准备运行所需状态。"""
        super().__init__(engine, ui)

    def run(self, rect: tuple[int, int, int, int]) -> TaskResult:
        """执行独立出售任务并返回调度结果。"""
        self.ui.ui_ensure(page_warehouse)

        # if not self._batch_sell_once():
        #     return self.ok()
        self._batch_sell_once()
        self.ui.appear_then_click(MAIN_GOTO_WAREHOUSE, offset=30, interval=1, static=False)
        if self._switch_to_special_warehouse():
            if not self._batch_sell_once():
                self.ui.ui_ensure(page_main)
                return self.ok()
        # 切换到超变果实仓库并执行批量出售

        self.ui.ui_ensure(page_main)
        return self.ok()

    def _switch_to_special_warehouse(self) -> bool:
        """点击切换到超变果实仓库。"""
        self.ui.device.sleep(0.8)
        self.ui.device.screenshot()
        if self.ui.appear_then_click(BTN_SPECIAL_WAREHOUSE, offset=30, interval=1, static=False):
            logger.info('出售流程: 已切换到超变果实仓库')
            self.ui.device.sleep(0.5)
            return True
        logger.info('出售流程: 未识别到超变果实仓库按钮')
        return False

    def _batch_sell_once(self) -> bool:
        """仓库内执行一次批量出售；超时时视为完成并返回 True。"""
        logger.info('出售流程: 批量出售')
        batch_clicked = False
        timer = Timer(BATCH_SELL_TIMEOUT_SECONDS, count=0).start()

        while 1:
            self.ui.device.sleep(0.8)
            self.ui.device.screenshot()

            if timer.reached():
                logger.warning(
                    '出售流程: 批量出售超时（{} 秒），视为完成并结束',
                    BATCH_SELL_TIMEOUT_SECONDS,
                )
                return True
            if self.ui.appear_then_click(BTN_BATCH_SELL, offset=30, interval=1):
                batch_clicked = True
                continue
            if batch_clicked and self.ui.appear_then_click(BTN_CONFIRM, offset=30, interval=1, static=False):
                self.engine._record_stat(ActionType.SELL)
                self.ui.device.sleep(0.5)
                continue
            if self.ui.appear(BTN_PLANTING, offset=30) and self.ui.appear_then_click(
                BTN_CLOSE, offset=30, interval=1, static=False
            ):
                continue
            if self.ui.appear(MAIN_GOTO_WAREHOUSE, offset=30):
                if not batch_clicked:
                    return False
                else:
                    return True
