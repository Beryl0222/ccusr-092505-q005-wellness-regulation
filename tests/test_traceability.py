"""处罚可追溯：哪条宣传、哪次服务、哪份有效资质。"""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path

import helpers
from wellness_regulation.contracts import validate_event
from wellness_regulation.errors import DomainError

ROOT = Path(__file__).resolve().parents[1]


class TraceabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.system = helpers.new_system()
        self.chain = helpers.penalty_chain(self.system)

    def test_penalty_explains_publicity_service_and_license(self) -> None:
        explanation = self.system.explain_penalty(penalty_id=self.chain.penalty.penalty_id)
        self.assertEqual(
            self.chain.publicity.version_id, explanation["publicity"]["version_id"]
        )
        self.assertEqual("https://example.com/promo", explanation["publicity"]["source_url"])
        self.assertEqual("颂钵音疗体验课 199元", explanation["publicity"]["content"])
        self.assertEqual("ORDER-001", explanation["service"]["order_no"])
        self.assertEqual(self.chain.record.record_id, explanation["service"]["record_id"])
        self.assertEqual(self.chain.license.license_no, explanation["license"]["license_no"])
        self.assertEqual(self.chain.case.case_id, explanation["case_id"])

    def test_penalty_rejected_when_license_not_valid_at_service_time(self) -> None:
        system = helpers.new_system()
        entity = helpers.base_entity(system)
        category = helpers.base_category(system)
        offer = helpers.base_offer(system, entity, category)
        # 资质 5 月才生效，服务发生在 3 月
        license_ = helpers.base_license(
            system,
            entity,
            valid_from=datetime(2026, 5, 1, tzinfo=helpers.CN),
            valid_until=datetime(2027, 5, 1, tzinfo=helpers.CN),
        )
        version = system.publish_publicity(
            entity_id=entity.entity_id,
            source_url="https://example.com/promo",
            content="颂钵音疗体验课 199元",
            offer_id=offer.offer_id,
            now=datetime(2026, 3, 1, tzinfo=helpers.CN),
        )
        record = system.place_order(
            offer_id=offer.offer_id,
            order_no="ORDER-T1",
            consumer_name="王女士",
            amount="199.00",
            served_at=datetime(2026, 3, 5, tzinfo=helpers.CN),
        )
        case = helpers.open_case_shortcut(system, entity)
        with self.assertRaises(DomainError):
            system.issue_penalty(
                case_id=case.case_id,
                publicity_version_id=version.version_id,
                service_record_id=record.record_id,
                license_id=license_.license_id,
                rationale="虚假宣传",
                amount="5000.00",
                now=helpers.NOW,
            )

    def test_penalty_rejected_when_publicity_not_effective_at_service_time(self) -> None:
        system = helpers.new_system()
        entity = helpers.base_entity(system)
        category = helpers.base_category(system)
        offer = helpers.base_offer(system, entity, category)
        license_ = helpers.base_license(system, entity)
        v1 = system.publish_publicity(
            entity_id=entity.entity_id,
            source_url="https://example.com/promo",
            content="第一版宣传",
            offer_id=offer.offer_id,
            now=datetime(2026, 3, 1, tzinfo=helpers.CN),
        )
        system.publish_publicity(
            entity_id=entity.entity_id,
            source_url="https://example.com/promo",
            content="第二版宣传",
            offer_id=offer.offer_id,
            now=datetime(2026, 3, 10, tzinfo=helpers.CN),
        )
        # 服务发生时消费者看到的是第二版，不能拿第一版定责
        record = system.place_order(
            offer_id=offer.offer_id,
            order_no="ORDER-T2",
            consumer_name="王女士",
            amount="199.00",
            served_at=datetime(2026, 3, 15, tzinfo=helpers.CN),
        )
        case = helpers.open_case_shortcut(system, entity)
        with self.assertRaises(DomainError):
            system.issue_penalty(
                case_id=case.case_id,
                publicity_version_id=v1.version_id,
                service_record_id=record.record_id,
                license_id=license_.license_id,
                rationale="虚假宣传",
                amount="5000.00",
                now=helpers.NOW,
            )

    def test_all_emitted_events_satisfy_contract(self) -> None:
        schema = json.loads(
            (ROOT / "contracts" / "domain.schema.json").read_text(encoding="utf-8")
        )
        self.assertTrue(self.system.store.events)
        for event in self.system.store.events:
            self.assertEqual([], validate_event(event, schema), msg=str(event))


if __name__ == "__main__":
    unittest.main()
