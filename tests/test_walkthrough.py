"""联调样例 data/walkthrough.json 必须可完整重放并得到监管结论。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wellness_regulation import (  # noqa: E402
    Clock,
    InspectionCase,
    Provider,
    RegulationSystem,
    ServiceOccurrence,
)


class WalkthroughTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.events = json.loads((ROOT / "data" / "walkthrough.json").read_text(encoding="utf-8"))
        cls.system = RegulationSystem.from_events(cls.events)

    def test_story_replays_to_closed_case_with_penalty(self) -> None:
        case = self.system.repo.load(InspectionCase, "demo-case-01")
        self.assertEqual(case.status, "closed")
        self.assertEqual(case.involved_regions, ("上海", "杭州"))
        self.assertIn("demo-penalty-01", case.penalty_ids)

    def test_penalty_explains_promotion_service_and_license(self) -> None:
        explanation = self.system.explain_penalty("demo-penalty-01")
        self.assertEqual(explanation["promotion"]["promotion_version"], 2)
        self.assertTrue(explanation["promotion"]["evidence_intake_no"].startswith("intake-"))
        self.assertEqual(explanation["occurrence"]["order_ref"], "ORD-20260912-8821")
        self.assertEqual(explanation["occurrence"]["service_region"], "上海")
        self.assertEqual(explanation["license_snapshot"]["license_id"], "杭卫证字-2026-0731")

    def test_provider_blocked_but_rendered_service_intact(self) -> None:
        provider = self.system.repo.load(Provider, "demo-provider-01")
        self.assertTrue(provider.order_blocked)
        occurrence = self.system.repo.load(ServiceOccurrence, "demo-occ-01")
        self.assertEqual(occurrence.service.order_ref, "ORD-20260912-8821")

    def test_intake_number_stable_across_revisions(self) -> None:
        intake = self.system.projections.intake
        self.assertEqual(
            intake.intake_for_source("webpage", "https://shop.example/wellness/sleep-camp"),
            self.system.explain_penalty("demo-penalty-01")["promotion"]["evidence_intake_no"],
        )

    def test_reminders_and_no_pending_review_after_close(self) -> None:
        licenses = {d.license_id for d in self.system.license_reminders(within_days=400)}
        self.assertIn("杭卫证字-2026-0731", licenses)
        self.assertEqual(self.system.pending_reviews(), [])


if __name__ == "__main__":
    unittest.main()
