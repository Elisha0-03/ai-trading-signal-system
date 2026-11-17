"""UTC-aligned scheduler for periodic analysis cycles."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable


def seconds_until_next_cycle(now: datetime, interval_minutes: int, delay_after_close_seconds: int = 0) -> float:
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    interval = interval_minutes * 60
    current_boundary = (int(now.timestamp()) // interval) * interval
    delay = max(0, int(delay_after_close_seconds))
    target = current_boundary + delay
    if target < now.timestamp():
        target += interval
    return max(0.0, target - now.timestamp())


def run_forever(cycle: Callable[[], None], interval_minutes: int, delay_after_close_seconds: int = 0,
                run_immediately: bool = False) -> None:
    if run_immediately:
        cycle()
    while True:
        now = datetime.now(timezone.utc)
        time.sleep(seconds_until_next_cycle(now, interval_minutes, delay_after_close_seconds))
        cycle()
