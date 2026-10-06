"""领域规则测试：每条监管要求至少对应一个断言。"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wellness_regulation import (  # noqa: E402
    Clock,
    ImmutableFactError,
    KIND_GENERAL,
    KIND_MEDICAL,
    RegulationSystem,
    Inspector,
)
from wellness_regulation.errors import AccessDenied, ConcurrencyError, DomainError  # noqa: E402

CST = timezone(timedelta(hours=8))


class FixedClock(Clock):
    def __init__(self, start: datetime | None = None) -> None:
        self.current = start or datetime(2026, 9, 1, 9, 0, tzinfo=CST)
        counter = iter(range(10_000))
        super().__init__(now_fn=lambda: self.current, id_fn=lambda: f"id-{next(counter)}")

    def advance(self, **delta: int) -> None:
        self.current += timedelta(**delta)


def iso(day: str, hour: str = "12:00:00") -> str:
    return f"{day}T{hour}+08:00"


class RegulationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FixedClock()
        self.sys = RegulationSystem(clock=self.clock)
        self.sys.define_category("sound", "音声放松", KIND_GENERAL, "一般体验")
        self.sys.update_negative_list(
            "sound", ["包治失眠", "无效退款保证"], ["诱导大额预付"], "负面清单 2026 版"
        )
        self.pid = self.sys.register_provider("静心文化", "杭州", provider_id="p1")
        self.sys.verify_license(
            self.pid, "L1", "体验服务", iso("2026-01-01"), iso("2026-12-31", "23:59:59"), "证照核验单"
        )
        self.oid = self.sys.register_offer(self.pid, "sound", "深度睡眠音声课", offer_id="o1")
        self.prac = self.sys.register_practitioner(
            "王老师", self.pid, "音声引导", iso("2027-01-01"), practitioner_id="w1"
        )


# -- 医疗边界与负面清单 -------------------------------------------------------


class BoundaryRuleTests(RegulationTestCase):
    def test_general_experience_claiming_treatment_crosses_boundary(self) -> None:
        analysis = self.sys.analyze_claim("sound", "舒缓聆听，放松身心")
        self.assertTrue(analysis.compliant)
        bad = self.sys.analyze_claim("sound", "本课程可以治疗失眠")
        self.assertTrue(bad.crosses_medical_boundary)
        self.assertFalse(bad.hits_negative_list)

    def test_negative_list_terms_are_detected(self) -> None:
        analysis = self.sys.analyze_claim("sound", "包治失眠，无效退款保证")
        self.assertTrue(analysis.hits_negative_list)
        self.assertEqual(
            sorted(h.term for h in analysis.hits if h.kind == "prohibited"),
            ["包治失眠", "无效退款保证"],
        )

    def test_medical_category_is_distinct_from_experience(self) -> None:
        self.sys.define_category("clinic", "针灸诊疗", KIND_MEDICAL)
        # 医疗诊疗类别中"治疗"是其固有性质，不被当作越界宣传
        analysis = self.sys.analyze_claim("clinic", "规范治疗")
        self.assertFalse(analysis.crosses_medical_boundary)
        with self.assertRaises(ValueError):
            self.sys.define_category("bad", "未知类别", "something_else")

    def test_screening_crossing_boundary_blocks_new_orders_immediately(self) -> None:
        self.sys.screen_offer(self.oid, "sound", "本音声课可治疗焦虑")
        with self.assertRaises(DomainError):
            self.sys.place_new_order(self.pid)

    def test_screening_prohibited_term_blocks_new_orders_immediately(self) -> None:
        self.sys.screen_offer(self.oid, "sound", "包治失眠")
        provider = self.sys.repo.load(__import__("wellness_regulation").Provider, self.pid)
        self.assertTrue(provider.order_blocked)
        self.assertTrue(any("负面清单" in r for r in provider.block_reasons))

    def test_compliant_promotion_does_not_block(self) -> None:
        self.sys.screen_offer(self.oid, "sound", "舒缓聆听")
        self.sys.place_new_order(self.pid)  # 不抛异常即放行


# -- 套餐拆分与退款陷阱 -------------------------------------------------------


class PricingRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sys = RegulationSystem()

    def test_addon_non_refundable_is_flagged(self) -> None:
        analysis = self.sys.analyze_pricing(
            {"items": [
                {"component": "package", "name": "主套餐", "refund_window_days": 7},
                {"component": "addon", "name": "私教加时包", "refundable": False},
            ]}
        )
        self.assertTrue(analysis.has_refund_trap)
        self.assertEqual(analysis.findings[0].code, "ADDON_NON_REFUNDABLE")

    def test_addon_shorter_refund_window_is_flagged(self) -> None:
        analysis = self.sys.analyze_pricing(
            {"items": [
                {"component": "package", "name": "主套餐", "refund_window_days": 7},
                {"component": "addon", "name": "加时包", "refund_window_days": 3},
            ]}
        )
        self.assertEqual(
            {f.code for f in analysis.findings}, {"ADDON_REFUND_WINDOW_SHORTER"}
        )

    def test_fair_terms_pass(self) -> None:
        analysis = self.sys.analyze_pricing(
            {"items": [
                {"component": "package", "name": "主套餐", "refund_window_days": 7, "refundable": True},
                {"component": "addon", "name": "加时包", "refund_window_days": 14, "refundable": True},
            ]}
        )
        self.assertFalse(analysis.has_refund_trap)


# -- 资质有效期与限单 ---------------------------------------------------------


class LicenseRuleTests(RegulationTestCase):
    def test_license_requires_basis_and_timezone(self) -> None:
        pid = self.sys.register_provider("无证商家", "宁波", provider_id="p2")
        with self.assertRaises(DomainError):
            self.sys.verify_license(pid, "X", "s", iso("2026-01-01"), iso("2026-12-31"), "  ")
        with self.assertRaises(ValueError):
            self.sys.verify_license(pid, "X", "s", iso("2026-01-01"), "2026-12-31T23:59:59", "b")

    def test_expired_license_triggers_block(self) -> None:
        pid = self.sys.register_provider("临期商家", "温州", provider_id="p3")
        self.sys.verify_license(pid, "L9", "体验服务", iso("2025-01-01"), iso("2026-08-31"), "核验")
        reasons = self.sys.enforce_provider_policy(pid)
        self.assertTrue(reasons)
        with self.assertRaises(DomainError):
            self.sys.place_new_order(pid)

    def test_required_scope_missing_triggers_block(self) -> None:
        reasons = self.sys.enforce_provider_policy(self.pid, required_scopes=("医疗诊疗",))
        self.assertTrue(any("医疗诊疗" in r for r in reasons))

    def test_renewal_requires_existing_license_and_basis(self) -> None:
        with self.assertRaises(DomainError):
            self.sys.renew_license(self.pid, "NOPE", iso("2027-12-31"), "续期凭证")
        with self.assertRaises(DomainError):
            self.sys.renew_license(self.pid, "L1", iso("2027-12-31"), " ")
        self.sys.renew_license(self.pid, "L1", iso("2027-12-31", "23:59:59"), "续期凭证")
        provider = self.sys.repo.load(__import__("wellness_regulation").Provider, self.pid)
        self.assertEqual(provider.licenses["L1"].valid_until, iso("2027-12-31", "23:59:59"))

    def test_restore_requires_basis_and_keeps_block_history(self) -> None:
        pid = self.sys.register_provider("临期", "温州", provider_id="p4")
        self.sys.verify_license(pid, "L9", "体验服务", iso("2025-01-01"), iso("2026-08-31"), "核验")
        self.sys.enforce_provider_policy(pid)
        with self.assertRaises(DomainError):
            self.sys.restore_new_orders(pid, " ")
        self.sys.restore_new_orders(pid, "现场复核资质已补全")
        self.sys.place_new_order(pid)

    def test_blocking_new_orders_never_touches_rendered_services(self) -> None:
        occ = self.sys.record_service_occurrence(
            self.pid, self.oid, self.prac, "c1", "上海", "ORD-1", iso("2026-08-30"),
            occurrence_id="occ1",
        )
        self.sys.screen_offer(self.oid, "sound", "包治失眠")
        from wellness_regulation import ServiceOccurrence

        service = self.sys.repo.load(ServiceOccurrence, occ).service
        self.assertEqual(service.order_ref, "ORD-1")  # 已发生服务原样保留


# -- 宣传版本与证据受理号 -----------------------------------------------------


class VersionAndEvidenceRuleTests(RegulationTestCase):
    def seal(self, fp: str, source: str = "https://x/p", kind: str = "webpage") -> tuple[str, str, int]:
        return self.sys.seal_evidence(fp, kind, source, iso("2026-09-01", "08:00:00"))

    def test_same_webpage_keeps_intake_and_duplicates_same_content(self) -> None:
        intake1, st1, rev1 = self.seal("fp-a")
        intake2, st2, rev2 = self.seal("fp-a")
        self.assertEqual(intake1, intake2)
        self.assertEqual((st1, rev1), ("new", 1))
        self.assertEqual((st2, rev2), ("duplicate", 1))

    def test_content_change_keeps_intake_but_creates_revision(self) -> None:
        intake1, _, _ = self.seal("fp-a")
        intake2, st2, rev2 = self.seal("fp-b")
        self.assertEqual(intake1, intake2)
        self.assertEqual((st2, rev2), ("revised", 2))

    def test_different_source_gets_own_intake(self) -> None:
        intake1, _, _ = self.seal("fp-a", "https://x/p")
        intake2, _, _ = self.seal("fp-a", "https://y/p")
        self.assertNotEqual(intake1, intake2)

    def test_promotion_versions_only_advance_on_change(self) -> None:
        intake, _, _ = self.seal("fp-a")
        self.assertEqual(self.sys.capture_promotion(self.oid, "放松", "fp-a", "杭州", intake),
                         ("new", 1))
        self.assertEqual(self.sys.capture_promotion(self.oid, "放松", "fp-a", "杭州", intake),
                         ("unchanged", 1))
        intake2, _, _ = self.seal("fp-b")
        self.assertEqual(self.sys.capture_promotion(self.oid, "治疗", "fp-b", "上海", intake2),
                         ("new", 2))

    def test_pricing_versions_are_independent_from_promotions(self) -> None:
        intake, _, _ = self.seal("fp-a")
        self.sys.capture_promotion(self.oid, "放松", "fp-a", "杭州", intake)
        outcome, version = self.sys.publish_pricing_terms(
            self.oid, {"items": []}, "price-1", intake
        )
        self.assertEqual((outcome, version), ("new", 1))

    def test_online_complaint_must_seal_before_filing(self) -> None:
        with self.assertRaises(DomainError):
            self.sys.file_online_complaint(
                self.pid, "上海", self.oid, "intake-missing", "未封存就分派", case_id="c"
            )


# -- 已发生服务不可篡改 -------------------------------------------------------


class ImmutableServiceTests(RegulationTestCase):
    def _record(self, occ_id: str = "occ1") -> str:
        return self.sys.record_service_occurrence(
            self.pid, self.oid, self.prac, "c1", "上海", "ORD-1", iso("2026-08-30"),
            occurrence_id=occ_id,
        )

    def test_double_record_is_rejected(self) -> None:
        self._record()
        with self.assertRaises(ImmutableFactError):
            self._record()

    def test_amend_is_rejected(self) -> None:
        occ_id = self._record()
        from wellness_regulation import ServiceOccurrence

        occurrence = self.sys.repo.load(ServiceOccurrence, occ_id)
        with self.assertRaises(ImmutableFactError):
            occurrence.amend(order_ref="FORGED")

    def test_replay_rejects_two_rendered_events(self) -> None:
        from wellness_regulation import ServiceOccurrence

        occ_id = self._record()
        events = self.sys.store.events_for(occ_id)
        with self.assertRaises(ImmutableFactError):
            ServiceOccurrence.replay(occ_id, list(events) * 2)


# -- 引擎与契约 ---------------------------------------------------------------


class EngineRuleTests(RegulationTestCase):
    def test_event_version_gap_is_rejected(self) -> None:
        payload = {
            "event_id": "x",
            "event_type": "PROVIDER_REGISTERED",
            "aggregate_type": "provider",
            "aggregate_id": "p99",
            "occurred_at": iso("2026-09-01"),
            "version": 5,
            "summary": "跳号",
            "name": "x",
            "home_region": "杭州",
        }
        with self.assertRaises(ConcurrencyError):
            self.sys.store.append(payload)

    def test_contract_violation_is_rejected(self) -> None:
        from wellness_regulation.errors import ContractViolation

        with self.assertRaises(ContractViolation):
            self.sys.store.append(
                {
                    "event_id": "x",
                    "event_type": "UNKNOWN",
                    "aggregate_type": "provider",
                    "aggregate_id": "p98",
                    "occurred_at": "2026-09-01T09:00:00",
                    "version": 1,
                    "summary": "",
                }
            )


if __name__ == "__main__":
    unittest.main()
