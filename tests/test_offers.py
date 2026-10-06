"""价格与退款条款管理、从业人员证书到期提醒。"""

from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal

import helpers
from wellness_regulation.models import PriceComponent, RefundPolicy


class OfferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.system = helpers.new_system()
        self.entity = helpers.base_entity(self.system)
        self.category = helpers.base_category(self.system)

    def test_offer_terms_update_keeps_history_and_snapshots(self) -> None:
        offer = helpers.base_offer(self.system, self.entity, self.category)
        record = self.system.place_order(
            offer_id=offer.offer_id,
            order_no="ORDER-P1",
            consumer_name="王女士",
            amount="199.00",
            served_at=helpers.NOW,
        )
        new_policy = RefundPolicy(
            refundable_within_days=15,
            non_refundable_items=(),
            terms="十五天内可全额退款",
        )
        updated = self.system.update_offer_terms(
            offer_id=offer.offer_id,
            prices=(PriceComponent("体验课", Decimal("219.00"), True),),
            refund_policy=new_policy,
            now=helpers.NOW + timedelta(days=1),
        )
        self.assertEqual(("体验课",), tuple(p.name for p in updated.prices))
        # 条款更新不改写已发生的服务
        kept = self.system.store.get("service_records", record.record_id)
        self.assertEqual(Decimal("199.00"), kept.amount)
        snapshots = [
            e for e in self.system.store.events if e["event_type"] == "OFFER_CAPTURED"
        ]
        self.assertEqual(2, len(snapshots))

    def test_practitioner_cert_reminders(self) -> None:
        soon = self.system.register_practitioner(
            entity_id=self.entity.entity_id,
            name="张老师",
            cert_type="心理咨询师",
            cert_no="ZX-001",
            cert_valid_until=helpers.NOW + timedelta(days=20),
            now=helpers.NOW,
        )
        self.system.register_practitioner(
            entity_id=self.entity.entity_id,
            name="李老师",
            cert_type="音疗师",
            cert_no="YL-002",
            cert_valid_until=helpers.NOW + timedelta(days=200),
            now=helpers.NOW,
        )
        due = self.system.practitioner_cert_reminders(now=helpers.NOW, within_days=30)
        self.assertEqual([soon.practitioner_id], [p.practitioner_id for p in due])


if __name__ == "__main__":
    unittest.main()
