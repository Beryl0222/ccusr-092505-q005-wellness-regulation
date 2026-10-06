"""从业人员与已经发生的服务记录。

服务记录是不可变事实：只有 SERVICE_RENDERED 一次落库，
限单、整改、处罚都不得修改它，只能追加关联。
"""

from __future__ import annotations

from dataclasses import dataclass

from .clock import Clock
from .engine import Aggregate, StoredEvent
from .errors import ImmutableFactError


class Practitioner(Aggregate):
    aggregate_type = "practitioner"

    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.name: str | None = None
        self.provider_id: str | None = None
        self.credential_scope: str | None = None
        self.credential_valid_until: str | None = None
        self.registered = False

    def register(
        self,
        clock: Clock,
        name: str,
        provider_id: str,
        credential_scope: str,
        credential_valid_until: str,
    ) -> None:
        if self.registered:
            raise ImmutableFactError("从业人员登记事实不可重复登记")
        self._record(
            "PRACTITIONER_REGISTERED",
            f"登记从业人员 {name}",
            clock.now,
            clock.new_id,
            name=name,
            provider_id=provider_id,
            credential_scope=credential_scope,
            credential_valid_until=credential_valid_until,
        )

    def apply_PRACTITIONER_REGISTERED(self, event: StoredEvent) -> None:
        self.registered = True
        self.name = event.get("name")
        self.provider_id = event.get("provider_id")
        self.credential_scope = event.get("credential_scope")
        self.credential_valid_until = event.get("credential_valid_until")


@dataclass(frozen=True)
class RenderedService:
    occurrence_id: str
    provider_id: str
    offer_id: str
    practitioner_id: str
    consumer_ref: str
    service_region: str
    order_ref: str
    component: str  # package | addon
    rendered_at: str
    basis: str


class ServiceOccurrence(Aggregate):
    """一次已发生的服务；创建后任何改写尝试都抛 ImmutableFactError。"""

    aggregate_type = "service_occurrence"

    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.service: RenderedService | None = None

    def record(
        self,
        clock: Clock,
        provider_id: str,
        offer_id: str,
        practitioner_id: str,
        consumer_ref: str,
        service_region: str,
        order_ref: str,
        rendered_at: str,
        component: str = "package",
        basis: str = "订单与服务凭证",
    ) -> None:
        if self.service is not None:
            raise ImmutableFactError("已经发生的服务记录不得篡改")
        if component not in ("package", "addon"):
            raise ValueError("服务组成只能是 package 或 addon")
        self._record(
            "SERVICE_RENDERED",
            f"封存服务事实（订单 {order_ref}）",
            clock.now,
            clock.new_id,
            occurrence_id=self.id,
            provider_id=provider_id,
            offer_id=offer_id,
            practitioner_id=practitioner_id,
            consumer_ref=consumer_ref,
            service_region=service_region,
            order_ref=order_ref,
            component=component,
            rendered_at=rendered_at,
            basis=basis,
        )

    def amend(self, **_: object) -> None:
        raise ImmutableFactError("已经发生的服务记录不得篡改，只能追加新的关联事实")

    def apply_SERVICE_RENDERED(self, event: StoredEvent) -> None:
        if self.service is not None:
            raise ImmutableFactError("同一服务记录出现两次 SERVICE_RENDERED")
        self.service = RenderedService(
            occurrence_id=event.get("occurrence_id"),
            provider_id=event.get("provider_id"),
            offer_id=event.get("offer_id"),
            practitioner_id=event.get("practitioner_id"),
            consumer_ref=event.get("consumer_ref"),
            service_region=event.get("service_region"),
            order_ref=event.get("order_ref"),
            component=event.get("component", "package"),
            rendered_at=event.get("rendered_at"),
            basis=event.get("basis", ""),
        )
