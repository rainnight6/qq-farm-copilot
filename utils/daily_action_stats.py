"""每日动作统计 CSV 工具（按实例隔离）。"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

from utils.app_paths import instance_dir

# 统计列顺序：按动作类型分项保存，operation 保留为兼容列但不再写入新数据。
DAILY_ACTION_STAT_FIELDS = [
    'date',
    'harvest',
    'operation',
    'plant',
    'farming',
    'fertilize',
    'merchant',
    'sell',
    'friend_steal',
    'friend_help',
]


class _DailyActionRow:
    """单日统计行，兼容旧 CSV（缺少的字段按 0 处理）。"""

    __slots__ = (
        'harvest',
        'operation',
        'plant',
        'farming',
        'fertilize',
        'merchant',
        'sell',
        'friend_steal',
        'friend_help',
    )

    def __init__(
        self,
        *,
        harvest: int = 0,
        operation: int = 0,
        plant: int = 0,
        farming: int = 0,
        fertilize: int = 0,
        merchant: int = 0,
        sell: int = 0,
        friend_steal: int = 0,
        friend_help: int = 0,
    ):
        self.harvest = int(harvest)
        self.operation = int(operation)
        self.plant = int(plant)
        self.farming = int(farming)
        self.fertilize = int(fertilize)
        self.merchant = int(merchant)
        self.sell = int(sell)
        self.friend_steal = int(friend_steal)
        self.friend_help = int(friend_help)

    def add(
        self,
        *,
        harvest: int = 0,
        operation: int = 0,
        plant: int = 0,
        farming: int = 0,
        fertilize: int = 0,
        merchant: int = 0,
        sell: int = 0,
        friend_steal: int = 0,
        friend_help: int = 0,
    ) -> '_DailyActionRow':
        return _DailyActionRow(
            harvest=self.harvest + max(0, int(harvest)),
            operation=self.operation + max(0, int(operation)),
            plant=self.plant + max(0, int(plant)),
            farming=self.farming + max(0, int(farming)),
            fertilize=self.fertilize + max(0, int(fertilize)),
            merchant=self.merchant + max(0, int(merchant)),
            sell=self.sell + max(0, int(sell)),
            friend_steal=self.friend_steal + max(0, int(friend_steal)),
            friend_help=self.friend_help + max(0, int(friend_help)),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            'harvest': self.harvest,
            'operation': self.operation,
            'plant': self.plant,
            'farming': self.farming,
            'fertilize': self.fertilize,
            'merchant': self.merchant,
            'sell': self.sell,
            'friend_steal': self.friend_steal,
            'friend_help': self.friend_help,
        }

    @classmethod
    def from_dict(cls, row: dict[str, str]) -> '_DailyActionRow':
        return cls(
            harvest=_safe_int(row.get('harvest'), 0),
            operation=_safe_int(row.get('operation'), 0),
            plant=_safe_int(row.get('plant'), 0),
            farming=_safe_int(row.get('farming'), 0),
            fertilize=_safe_int(row.get('fertilize'), 0),
            merchant=_safe_int(row.get('merchant'), 0),
            sell=_safe_int(row.get('sell'), 0),
            friend_steal=_safe_int(row.get('friend_steal'), 0),
            friend_help=_safe_int(row.get('friend_help'), 0),
        )


def _csv_path(instance_id: str) -> Path:
    p = instance_dir(instance_id) / 'stats'
    p.mkdir(parents=True, exist_ok=True)
    return p / 'daily_action_stats.csv'


def _safe_int(value: str | int | None, default: int = 0) -> int:
    try:
        return int(str(value or default).strip())
    except Exception:
        return int(default)


def record_daily_action(
    instance_id: str,
    *,
    harvest: int = 0,
    operation: int = 0,
    plant: int = 0,
    farming: int = 0,
    fertilize: int = 0,
    merchant: int = 0,
    sell: int = 0,
    friend_steal: int = 0,
    friend_help: int = 0,
) -> None:
    today = date.today().isoformat()
    path = _csv_path(instance_id)
    rows: dict[str, _DailyActionRow] = {}

    if path.exists():
        with path.open(newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                day = str(row.get('date') or '').strip()
                if not day:
                    continue
                rows[day] = _DailyActionRow.from_dict(row)

    rows[today] = rows.get(today, _DailyActionRow()).add(
        harvest=harvest,
        operation=operation,
        plant=plant,
        farming=farming,
        fertilize=fertilize,
        merchant=merchant,
        sell=sell,
        friend_steal=friend_steal,
        friend_help=friend_help,
    )

    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=DAILY_ACTION_STAT_FIELDS)
        writer.writeheader()
        for d, row in sorted(rows.items()):
            writer.writerow({'date': d, **row.to_dict()})


def load_daily_actions(
    instance_id: str, days: int = 30
) -> list[tuple[str, int, int, int, int, int, int, int, int, int]]:
    path = _csv_path(instance_id)
    rows: dict[str, _DailyActionRow] = {}
    if path.exists():
        with path.open(newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                day = str(row.get('date') or '').strip()
                if not day:
                    continue
                rows[day] = _DailyActionRow.from_dict(row)

    today = date.today()
    out: list[tuple[str, int, int, int, int, int, int, int, int, int]] = []
    for i in range(days):
        current_day = (today - timedelta(days=days - 1 - i)).isoformat()
        row = rows.get(current_day, _DailyActionRow())
        out.append(
            (
                current_day,
                row.harvest,
                row.operation,
                row.plant,
                row.farming,
                row.fertilize,
                row.merchant,
                row.sell,
                row.friend_steal,
                row.friend_help,
            )
        )
    return out
