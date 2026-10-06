"""监管员职责范围判定与个人信息脱敏。"""

from __future__ import annotations

from dataclasses import asdict

from .models import ConsumerProfile, InspectionCase, Regulator

MASK = "***"


def can_view_pii(regulator: Regulator, case: InspectionCase) -> bool:
    """监管员仅在案件责任地或协同地落在其职责区域时可查看个人信息。"""
    return bool(set(regulator.regions) & set(case.coordinating_regions))


def consumer_view(
    regulator: Regulator, case: InspectionCase, consumer: ConsumerProfile
) -> dict[str, str]:
    """按职责范围返回消费者信息视图，范围外一律脱敏。"""
    if can_view_pii(regulator, case):
        return asdict(consumer)
    return {"name": MASK, "phone": MASK, "id_number": MASK}
