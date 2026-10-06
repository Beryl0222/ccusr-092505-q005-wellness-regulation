"""资质过期限制新订单、历史服务不可篡改、服务重启后的提醒与复核延续。"""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal

import helpers
from wellness_regulation.errors import ImmutableRecordError, OrderRestrictedError


class EnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.system = helpers.new_system()
        self.entity = helpers.base_entity(self.system)
        self.category = helpers.base_category(self.system)
        self.offer = helpers.base_offer(self.system, self.entity, self.category)
        self.license = helpers.base_license(
            self.system, self.entity, valid_until=datetime(2026, 10, 10, tzinfo=helpers.CN)
        )

    def test_license_expiry_restricts_new_orders_but_keeps_history(self) -> None:
        record = self.system.place_order(
            offer_id=self.offer.offer_id,
            order_no="ORDER-H1",
            consumer_name="王女士",
            amount="199.00",
            served_at=datetime(2026, 10, 5, 15, 0, tzinfo=helpers.CN),
        )
        expired = self.system.sweep_license_expiry(
            now=datetime(2026, 10, 11, 9, 0, tzinfo=helpers.CN)
        )
        self.assertEqual([self.license.license_id], [lic.license_id for lic in expired])
        # 立即限制新订单
        with self.assertRaises(OrderRestrictedError):
            self.system.place_order(
                offer_id=self.offer.offer_id,
                order_no="ORDER-H2",
                consumer_name="李女士",
                amount="199.00",
                served_at=datetime(2026, 10, 12, 15, 0, tzinfo=helpers.CN),
            )
        # 已发生的服务记录保持原样
        kept = self.system.store.get("service_records", record.record_id)
        self.assertEqual(record, kept)
        self.assertEqual(Decimal("199.00"), kept.amount)
        # 任何覆盖历史服务的写入都会被拒绝
        with self.assertRaises(ImmutableRecordError):
            self.system.store.put(
                "service_records", record.record_id, replace(record, amount=Decimal("1.00"))
            )

    def test_restart_resumes_reminders_and_pending_reviews(self) -> None:
        # 另一张即将到期的资质和一笔未完成复核
        expiring = self.system.register_license(
            entity_id=self.entity.entity_id,
            license_type="卫生许可证",
            license_no="WS-002",
            valid_from=helpers.NOW,
            valid_until=helpers.NOW + timedelta(days=20),
            now=helpers.NOW,
        )
        case = helpers.open_case_shortcut(self.system, self.entity)
        plan = self.system.order_remediation(
            case_id=case.case_id,
            requirements="下架夸大宣传页面",
            deadline=helpers.NOW + timedelta(days=15),
            now=helpers.NOW,
        )
        # 资质过期触发限制，随后主体换发新资质并重启
        self.system.sweep_license_expiry(now=datetime(2026, 10, 11, 9, 0, tzinfo=helpers.CN))
        self.assertIsNotNone(self.system.active_restriction(self.entity.entity_id))
        self.system.register_license(
            entity_id=self.entity.entity_id,
            license_type="营业执照",
            license_no="LICENSE-002",
            valid_from=datetime(2026, 10, 11, tzinfo=helpers.CN),
            valid_until=datetime(2028, 10, 11, tzinfo=helpers.CN),
            now=datetime(2026, 10, 11, 10, 0, tzinfo=helpers.CN),
        )
        restart_at = datetime(2026, 10, 12, 9, 0, tzinfo=helpers.CN)
        self.system.restart_service(entity_id=self.entity.entity_id, now=restart_at)
        self.assertIsNone(self.system.active_restriction(self.entity.entity_id))
        # 重启后可以受理新订单
        self.system.place_order(
            offer_id=self.offer.offer_id,
            order_no="ORDER-H3",
            consumer_name="李女士",
            amount="199.00",
            served_at=restart_at,
        )
        # 到期提醒继续
        reminders = self.system.expiry_reminders(now=restart_at, within_days=30)
        self.assertIn(expiring.license_id, [lic.license_id for lic in reminders])
        # 未完成复核继续
        self.assertEqual(
            [plan.plan_id], [p.plan_id for p in self.system.pending_reviews()]
        )


if __name__ == "__main__":
    unittest.main()
