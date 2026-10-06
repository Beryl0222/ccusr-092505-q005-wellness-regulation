"""医疗诊疗边界与一般体验的分开判定。"""

from __future__ import annotations

import unittest

import helpers
from wellness_regulation.errors import OrderRestrictedError
from wellness_regulation.models import Boundary, DeterminationResult
from wellness_regulation.system import MEDICAL_LICENSE_TYPE


class BoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.system = helpers.new_system()
        self.entity = helpers.base_entity(self.system)
        self.exp_category = helpers.base_category(self.system, "音疗体验", Boundary.EXPERIENCE)
        self.med_category = helpers.base_category(self.system, "中医诊疗", Boundary.MEDICAL)
        self.system.register_negative_item(
            kind="MEDICAL_CLAIM", keyword="治疗", description="宣称治疗作用"
        )
        self.system.register_negative_item(
            kind="MEDICAL_CLAIM", keyword="治愈率", description="宣称治愈率"
        )

    def test_experience_offer_with_medical_claim_is_violation_and_restricted(self) -> None:
        offer = helpers.base_offer(
            self.system, self.entity, self.exp_category, title="颂钵音疗治疗失眠疗程"
        )
        determination = self.system.determine_offer_boundary(
            offer_id=offer.offer_id, now=helpers.NOW
        )
        self.assertEqual(DeterminationResult.VIOLATION, determination.result)
        self.assertTrue(determination.matched_item_ids)
        self.assertIn("治疗", determination.basis)
        # 触碰禁项后立即限制新订单
        with self.assertRaises(OrderRestrictedError):
            self.system.place_order(
                offer_id=offer.offer_id,
                order_no="ORDER-X1",
                consumer_name="王女士",
                amount="199.00",
                served_at=helpers.NOW,
            )

    def test_plain_experience_offer_passes(self) -> None:
        offer = helpers.base_offer(self.system, self.entity, self.exp_category)
        determination = self.system.determine_offer_boundary(
            offer_id=offer.offer_id, now=helpers.NOW
        )
        self.assertEqual(DeterminationResult.GENERAL_EXPERIENCE, determination.result)
        record = self.system.place_order(
            offer_id=offer.offer_id,
            order_no="ORDER-X2",
            consumer_name="王女士",
            amount="199.00",
            served_at=helpers.NOW,
        )
        self.assertEqual(offer.offer_id, record.offer_id)

    def test_medical_claim_in_publicity_counts_as_violation(self) -> None:
        offer = helpers.base_offer(self.system, self.entity, self.exp_category)
        self.system.publish_publicity(
            entity_id=self.entity.entity_id,
            source_url="https://example.com/promo",
            content="音疗课程，有效治疗焦虑",
            offer_id=offer.offer_id,
            now=helpers.NOW,
        )
        determination = self.system.determine_offer_boundary(
            offer_id=offer.offer_id, now=helpers.NOW
        )
        self.assertEqual(DeterminationResult.VIOLATION, determination.result)

    def test_medical_offer_requires_valid_medical_license(self) -> None:
        offer = helpers.base_offer(
            self.system, self.entity, self.med_category, title="中医推拿诊疗"
        )
        unlicensed = self.system.determine_offer_boundary(
            offer_id=offer.offer_id, now=helpers.NOW
        )
        self.assertEqual(DeterminationResult.VIOLATION, unlicensed.result)
        self.assertIn("医疗资质", unlicensed.basis)

        self.system.register_license(
            entity_id=self.entity.entity_id,
            license_type=MEDICAL_LICENSE_TYPE,
            license_no="PDY-001",
            valid_from=helpers.NOW.replace(year=2025),
            valid_until=helpers.NOW.replace(year=2027),
            now=helpers.NOW,
        )
        licensed = self.system.determine_offer_boundary(
            offer_id=offer.offer_id, now=helpers.NOW
        )
        self.assertEqual(DeterminationResult.MEDICAL_TREATMENT, licensed.result)


if __name__ == "__main__":
    unittest.main()
