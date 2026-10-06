"""经营主体及其资质。

资质过期或触碰禁项由系统在限单判定点统一检查并限制新订单；
限单只影响"新订单"，已经发生的服务记录不可变更。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .clock import Clock
from .engine import Aggregate, StoredEvent
from .errors import DomainError


@dataclass(frozen=True)
class LicenseView:
    license_id: str
    scope: str
    valid_from: str
    valid_until: str
    status: str  # valid | expired | renewed
    renewed_from: str | None = None
    basis: str = ""

    def is_valid_on(self, day: datetime) -> bool:
        if self.status != "valid":
            return False
        end = datetime.fromisoformat(self.valid_until)
        return day <= end


class Provider(Aggregate):
    aggregate_type = "provider"

    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.name: str | None = None
        self.home_region: str | None = None
        self.licenses: dict[str, LicenseView] = {}
        self.order_blocked = False
        self.block_reasons: tuple[str, ...] = ()
        self.blocked_at: str | None = None
        self.restored_at: str | None = None

    def register(self, clock: Clock, name: str, home_region: str) -> None:
        self._record(
            "PROVIDER_REGISTERED",
            f"登记经营主体 {name}",
            clock.now,
            clock.new_id,
            name=name,
            home_region=home_region,
        )

    def verify_license(
        self,
        clock: Clock,
        license_id: str,
        scope: str,
        valid_from: str,
        valid_until: str,
        basis: str,
    ) -> None:
        if not basis.strip():
            raise DomainError("资质核验必须记录依据")
        _require_date(valid_from)
        _require_date(valid_until)
        self._record(
            "LICENSE_VERIFIED",
            f"核验资质 {license_id}（有效期至 {valid_until}）",
            clock.now,
            clock.new_id,
            license_id=license_id,
            scope=scope,
            valid_from=valid_from,
            valid_until=valid_until,
            basis=basis,
        )

    def renew_license(
        self,
        clock: Clock,
        license_id: str,
        new_valid_until: str,
        basis: str,
    ) -> None:
        if license_id not in self.licenses:
            raise DomainError(f"资质 {license_id} 不存在，不能续期")
        if not basis.strip():
            raise DomainError("资质续期必须记录依据")
        _require_date(new_valid_until)
        self._record(
            "LICENSE_RENEWED",
            f"续期资质 {license_id} 至 {new_valid_until}",
            clock.now,
            clock.new_id,
            license_id=license_id,
            new_valid_until=new_valid_until,
            basis=basis,
        )

    def block_new_orders(self, clock: Clock, reasons: list[str], basis: str) -> None:
        if not reasons:
            raise DomainError("限制新订单必须给出原因")
        if not basis.strip():
            raise DomainError("限制新订单必须记录依据")
        if self.order_blocked:
            raise DomainError("该主体已处于限单状态")
        self._record(
            "PROVIDER_ORDER_BLOCKED",
            "限制新订单：" + "、".join(reasons),
            clock.now,
            clock.new_id,
            reasons=list(reasons),
            basis=basis,
        )

    def restore_new_orders(self, clock: Clock, basis: str) -> None:
        if not basis.strip():
            raise DomainError("恢复新订单必须记录依据")
        if not self.order_blocked:
            raise DomainError("该主体未被限单")
        self._record(
            "PROVIDER_ORDER_RESTORED",
            "恢复新订单",
            clock.now,
            clock.new_id,
            basis=basis,
        )

    # -- 判定（只读，不落事件；调用方据此决定是否限单） -----------------------

    def valid_licenses_on(self, day: datetime) -> list[LicenseView]:
        return [lic for lic in self.licenses.values() if lic.is_valid_on(day)]

    def expiring_licenses(self, day: datetime, within_days: int) -> list[LicenseView]:
        result = []
        for lic in self.licenses.values():
            if lic.status != "valid":
                continue
            end = datetime.fromisoformat(lic.valid_until)
            if end < day:
                continue
            delta = (end - day).days
            if delta <= within_days:
                result.append(lic)
        return result

    def evaluate_block_reasons(
        self,
        day: datetime,
        *,
        required_scopes: tuple[str, ...] = (),
        prohibited_hits: tuple[str, ...] = (),
        medical_boundary_crossed: bool = False,
    ) -> list[str]:
        """集中判定限单触发条件：资质过期/缺失、越界宣称诊疗或触碰禁项。"""
        reasons: list[str] = []
        if required_scopes:
            valid_scopes = {lic.scope for lic in self.valid_licenses_on(day)}
            missing = [s for s in required_scopes if s not in valid_scopes]
            if missing:
                reasons.append("资质缺失或过期：" + "、".join(sorted(missing)))
        elif self.licenses and not self.valid_licenses_on(day):
            reasons.append("全部资质已过有效期")
        if medical_boundary_crossed:
            reasons.append("一般体验项目越界宣称医疗诊疗")
        if prohibited_hits:
            reasons.append("触碰负面清单禁项：" + "、".join(sorted(set(prohibited_hits))))
        return reasons

    # -- apply --------------------------------------------------------------

    def apply_PROVIDER_REGISTERED(self, event: StoredEvent) -> None:
        self.name = event.get("name")
        self.home_region = event.get("home_region")

    def apply_LICENSE_VERIFIED(self, event: StoredEvent) -> None:
        self.licenses[event.get("license_id")] = LicenseView(
            license_id=event.get("license_id"),
            scope=event.get("scope"),
            valid_from=event.get("valid_from"),
            valid_until=event.get("valid_until"),
            status="valid",
            basis=event.get("basis", ""),
        )

    def apply_LICENSE_RENEWED(self, event: StoredEvent) -> None:
        previous = self.licenses[event.get("license_id")]
        self.licenses[event.get("license_id")] = LicenseView(
            license_id=previous.license_id,
            scope=previous.scope,
            valid_from=previous.valid_from,
            valid_until=event.get("new_valid_until"),
            status="valid",
            renewed_from=previous.valid_until,
            basis=event.get("basis", ""),
        )

    def apply_PROVIDER_ORDER_BLOCKED(self, event: StoredEvent) -> None:
        self.order_blocked = True
        self.block_reasons = tuple(event.get("reasons", []))
        self.blocked_at = event.occurred_at

    def apply_PROVIDER_ORDER_RESTORED(self, event: StoredEvent) -> None:
        self.order_blocked = False
        self.block_reasons = ()
        self.blocked_at = None
        self.restored_at = event.occurred_at


def _require_date(value: str) -> None:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("日期必须包含时区")
