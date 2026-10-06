"""可替换的时钟与标识生成，测试中可固定。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from .engine import utc_now, uuid_id


class Clock:
    """注入时间与 ID 生成；事件时间必须带时区，由引擎统一处理。"""

    def __init__(
        self,
        now_fn: Callable[[], datetime] | None = None,
        id_fn: Callable[[], str] | None = None,
    ) -> None:
        self._now_fn = now_fn or utc_now
        self._id_fn = id_fn or uuid_id

    def now(self) -> datetime:
        value = self._now_fn()
        if value.tzinfo is None:
            raise ValueError("时钟返回的时间必须带时区")
        return value

    def new_id(self) -> str:
        value = self._id_fn()
        if not value:
            raise ValueError("标识生成器返回了空值")
        return value
