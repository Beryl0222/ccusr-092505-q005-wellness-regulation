"""监管员访问控制。

门控有两层，缺一不可：
1. 辖区职责：监管员只能访问主办/协同辖区内案件；
2. 消费者授权：访问个人信息须有对应消费者有效授权。
每次放行都会在案件上追加 CASE_PERSONAL_INFO_ACCESSED 留痕。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import AccessDenied
from .inspection import InspectionCase


@dataclass(frozen=True)
class Inspector:
    inspector_id: str
    name: str
    regions: tuple[str, ...]


class AccessPolicy:
    def authorize_case_access(self, inspector: Inspector, case: InspectionCase) -> None:
        allowed = set(case.involved_regions)
        if not allowed:
            raise AccessDenied("案件尚无辖区信息，禁止访问")
        if not any(region in allowed for region in inspector.regions):
            raise AccessDenied(
                f"监管员 {inspector.inspector_id} 不在案件辖区 "
                f"{'、'.join(sorted(allowed))} 职责范围内"
            )

    def authorize_personal_info(
        self,
        inspector: Inspector,
        case: InspectionCase,
        *,
        authorization_active: bool,
    ) -> None:
        self.authorize_case_access(inspector, case)
        if not authorization_active:
            raise AccessDenied("缺少消费者有效授权，禁止访问个人信息")
