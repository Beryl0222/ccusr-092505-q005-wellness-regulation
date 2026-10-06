"""消费者授权证据：重复取证保持原受理号、封存、线上投诉先封存再分派。"""

from __future__ import annotations

import unittest

import helpers
from wellness_regulation.errors import StateError
from wellness_regulation.models import CaseStatus, ComplaintStatus


class EvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.system = helpers.new_system()
        self.entity = helpers.base_entity(self.system)

    def _capture(self, source_type: str = "WEBPAGE", source_key: str = "https://example.com/promo"):
        return self.system.capture_evidence(
            source_type=source_type,
            source_key=source_key,
            payload="页面快照：音疗治疗失眠，套餐附加消费不退",
            captured_by="监管员甲",
            consumer_authorization="授权书A1",
            now=helpers.NOW,
        )

    def test_duplicate_webpage_capture_keeps_original_acceptance_no(self) -> None:
        first = self._capture()
        second = self.system.capture_evidence(
            source_type="WEBPAGE",
            source_key="https://example.com/promo",
            payload="调查前页面已改，再次取证",
            captured_by="监管员乙",
            consumer_authorization="授权书A2",
            now=helpers.NOW,
        )
        self.assertEqual(first.acceptance_no, second.acceptance_no)
        self.assertEqual(first.evidence_id, second.evidence_id)
        self.assertEqual(1, len(self.system.store.evidences))

    def test_duplicate_order_capture_keeps_original_acceptance_no(self) -> None:
        first = self._capture(source_type="ORDER", source_key="ORDER-001")
        second = self._capture(source_type="ORDER", source_key="ORDER-001")
        self.assertEqual(first.acceptance_no, second.acceptance_no)
        self.assertEqual(1, len(self.system.store.evidences))

    def test_distinct_sources_get_distinct_acceptance_numbers(self) -> None:
        first = self._capture()
        other = self._capture(source_key="https://example.com/other")
        self.assertNotEqual(first.acceptance_no, other.acceptance_no)

    def test_seal_computes_digest_and_is_idempotent(self) -> None:
        evidence = self._capture()
        sealed = self.system.seal_evidence(
            evidence_id=evidence.evidence_id, now=helpers.NOW
        )
        self.assertTrue(sealed.sealed)
        self.assertIsNotNone(sealed.digest)
        again = self.system.seal_evidence(
            evidence_id=evidence.evidence_id, now=helpers.NOW
        )
        self.assertEqual(sealed.digest, again.digest)

    def test_online_complaint_must_be_sealed_before_assignment(self) -> None:
        evidence = self._capture()
        complaint = self.system.file_complaint(
            channel="ONLINE",
            entity_id=self.entity.entity_id,
            occurrence_region="杭州市",
            description="网页宣称治疗且附加消费不退",
            consumer_name="王女士",
            consumer_phone="13800000000",
            consumer_id_number="330102199001011234",
            evidence_ids=(evidence.evidence_id,),
            now=helpers.NOW,
        )
        # 未封存直接分派被拒绝
        with self.assertRaises(StateError):
            self.system.open_case(
                complaint_id=complaint.complaint_id, assigned_to="监管员甲", now=helpers.NOW
            )
        # 先封存再分派
        self.system.seal_complaint_evidence(
            complaint_id=complaint.complaint_id, now=helpers.NOW
        )
        self.assertEqual(ComplaintStatus.EVIDENCE_SEALED, complaint.status)
        case = self.system.open_case(
            complaint_id=complaint.complaint_id, assigned_to="监管员甲", now=helpers.NOW
        )
        self.assertEqual(CaseStatus.ASSIGNED, case.status)
        sealed = self.system.store.get("evidences", evidence.evidence_id)
        self.assertTrue(sealed.sealed)


if __name__ == "__main__":
    unittest.main()
