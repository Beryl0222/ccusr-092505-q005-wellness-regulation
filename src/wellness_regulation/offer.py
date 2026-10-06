"""服务项目：宣传文案版本与价格/退款条款版本。

同一网页重复取证且内容未变时不产生新版本；内容一旦变化形成新版本，
由系统通知正在处理该项目的案件（见 inspection.Case）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .clock import Clock
from .engine import Aggregate, StoredEvent
from .errors import DomainError


@dataclass(frozen=True)
class PromotionVersion:
    version: int
    text: str
    fingerprint: str
    city: str | None
    evidence_ids: tuple[str, ...]
    captured_at: str


@dataclass(frozen=True)
class PricingVersion:
    version: int
    terms: dict[str, Any]
    fingerprint: str
    evidence_ids: tuple[str, ...]
    published_at: str


class ServiceOffer(Aggregate):
    aggregate_type = "service_offer"

    def __init__(self, aggregate_id: str) -> None:
        super().__init__(aggregate_id)
        self.provider_id: str | None = None
        self.category_code: str | None = None
        self.title: str | None = None
        self.promotions: list[PromotionVersion] = []
        self.pricing_versions: list[PricingVersion] = []

    def register(
        self, clock: Clock, provider_id: str, category_code: str, title: str
    ) -> None:
        self._record(
            "OFFER_REGISTERED",
            f"登记服务项目 {title}",
            clock.now,
            clock.new_id,
            provider_id=provider_id,
            category_code=category_code,
            title=title,
        )

    def capture_promotion(
        self,
        clock: Clock,
        text: str,
        fingerprint: str,
        evidence_id: str,
        city: str | None = None,
    ) -> tuple[str, int]:
        """登记宣传版本。返回 ('new'|'unchanged', 版本号)。

        指纹与最新版本一致时不产生新版本；证据归属在 evidence_record
        一侧记录，本聚合状态保持不变以便重放一致。
        """
        if self.promotions and self.promotions[-1].fingerprint == fingerprint:
            return "unchanged", self.promotions[-1].version
        new_version = (max((p.version for p in self.promotions), default=0)) + 1
        self._record(
            "OFFER_CAPTURED",
            f"宣传文案形成新版本 v{new_version}",
            clock.now,
            clock.new_id,
            text=text,
            fingerprint=fingerprint,
            city=city,
            evidence_id=evidence_id,
            promotion_version=new_version,
        )
        return "new", new_version

    def publish_pricing_terms(
        self, clock: Clock, terms: dict[str, Any], fingerprint: str, evidence_id: str
    ) -> tuple[str, int]:
        if self.pricing_versions and self.pricing_versions[-1].fingerprint == fingerprint:
            return "unchanged", self.pricing_versions[-1].version
        new_version = (max((p.version for p in self.pricing_versions), default=0)) + 1
        self._record(
            "PRICING_TERMS_PUBLISHED",
            f"价格与退款条款形成新版本 v{new_version}",
            clock.now,
            clock.new_id,
            terms=terms,
            fingerprint=fingerprint,
            evidence_id=evidence_id,
            pricing_version=new_version,
        )
        return "new", new_version

    def promotion_at(self, version: int) -> PromotionVersion:
        for item in self.promotions:
            if item.version == version:
                return item
        raise DomainError(f"宣传版本 v{version} 不存在")

    def pricing_at(self, version: int) -> PricingVersion:
        for item in self.pricing_versions:
            if item.version == version:
                return item
        raise DomainError(f"价格条款版本 v{version} 不存在")

    # -- apply --------------------------------------------------------------

    def apply_OFFER_REGISTERED(self, event: StoredEvent) -> None:
        self.provider_id = event.get("provider_id")
        self.category_code = event.get("category_code")
        self.title = event.get("title")

    def apply_OFFER_CAPTURED(self, event: StoredEvent) -> None:
        self.promotions.append(
            PromotionVersion(
                version=event.get("promotion_version"),
                text=event.get("text"),
                fingerprint=event.get("fingerprint"),
                city=event.get("city"),
                evidence_ids=(event.get("evidence_id"),),
                captured_at=event.occurred_at,
            )
        )
        self.promotions.sort(key=lambda p: p.version)

    def apply_PRICING_TERMS_PUBLISHED(self, event: StoredEvent) -> None:
        version = event.get("pricing_version")
        self.pricing_versions.append(
            PricingVersion(
                version=version,
                terms=dict(event.get("terms", {})),
                fingerprint=event.get("fingerprint"),
                evidence_ids=(event.get("evidence_id"),),
                published_at=event.occurred_at,
            )
        )
        self.pricing_versions.sort(key=lambda p: p.version)
