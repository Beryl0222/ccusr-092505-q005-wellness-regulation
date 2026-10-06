"""生成联调样例：

- data/walkthrough.json：完整端到端事件流（可整体重放）
- data/sample.json：单个领域事件信封样例

用法：python3 scripts/generate_demo.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wellness_regulation import (  # noqa: E402
    Clock,
    InspectionCase,
    RegulationSystem,
    Inspector,
)


def main() -> None:
    start = datetime(2026, 9, 14, 9, 0, tzinfo=timezone(timedelta(hours=8)))
    counter = iter(range(10000))
    clock = Clock(now_fn=lambda: start, id_fn=lambda: f"demo-{next(counter):04d}")
    system = RegulationSystem(clock=clock)

    def advance(**delta: int) -> None:
        nonlocal start
        start += timedelta(**delta)

    # 1. 服务类别与负面清单
    system.define_category("sound", "音声放松", "general_experience", "音声聆听等一般体验，不含诊疗")
    system.update_negative_list(
        "sound", ["包治失眠", "无效退款保证"], ["诱导大额预付"], "疗愈服务负面清单2026版"
    )

    # 2. 经营主体与资质有效期
    pid = system.register_provider("静心文化传播有限公司", "杭州", provider_id="demo-provider-01")
    advance(hours=1)
    system.verify_license(
        pid,
        "杭卫证字-2026-0731",
        "一般体验服务",
        "2026-01-01T00:00:00+08:00",
        "2026-11-30T23:59:59+08:00",
        "证照核验单及政务网比对结果",
    )

    # 3. 服务项目、从业人员、首版宣传（杭州页面）
    oid = system.register_offer(pid, "sound", "深度睡眠音声训练营", offer_id="demo-offer-01")
    system.register_practitioner(
        "王聆", pid, "音声引导", "2027-06-30T23:59:59+08:00", practitioner_id="demo-prac-01"
    )
    advance(hours=2)
    intake1, _, _ = system.seal_evidence(
        "fp-20260914-page-a",
        "webpage",
        "https://shop.example/wellness/sleep-camp",
        "2026-09-14T11:05:00+08:00",
    )
    system.capture_promotion(oid, "舒缓聆听，帮助放松身心", "fp-20260914-page-a", "杭州", intake1)

    # 4. 上海页面内容变更：同网页同受理号，形成宣传新版本
    advance(days=1)
    intake2, status, revision = system.seal_evidence(
        "fp-20260915-page-b",
        "webpage",
        "https://shop.example/wellness/sleep-camp",
        "2026-09-15T10:00:00+08:00",
    )
    outcome, promo_version = system.capture_promotion(
        oid,
        "本训练营可治疗失眠，包治失眠，无效退款保证",
        "fp-20260915-page-b",
        "上海",
        intake2,
    )
    assert (status, outcome) == ("revised", "new")

    # 5. 价格与退款条款（套餐拆分附加消费）
    system.publish_pricing_terms(
        oid,
        {
            "currency": "CNY",
            "items": [
                {"component": "package", "name": "训练营主套餐", "price": 2980,
                 "refundable": True, "refund_window_days": 7},
                {"component": "addon", "name": "一对一深度加时包", "price": 1200,
                 "refundable": False, "refund_window_days": 3},
            ],
        },
        "fp-20260915-price-1",
        intake2,
    )

    # 6. 巡检触碰医疗边界与禁项：立即限制新订单
    system.screen_offer(oid, "sound", "本训练营可治疗失眠，包治失眠，无效退款保证")

    # 7. 已发生的服务事实不被限单影响
    system.record_service_occurrence(
        pid,
        oid,
        "demo-prac-01",
        "consumer-张某某",
        "上海",
        "ORD-20260912-8821",
        "2026-09-12T19:30:00+08:00",
        component="addon",
        occurrence_id="demo-occ-01",
    )

    # 8. 线上投诉：证据已封存，再受理分派；跨区域协同；受控访问个人信息
    system.grant_authorization(
        "consumer-张某某",
        "投诉调查与个人信息核实",
        "2027-09-14T23:59:59+08:00",
        auth_id="demo-auth-01",
    )
    advance(hours=1)
    case_id = system.file_online_complaint(
        pid,
        "上海",
        oid,
        intake2,
        "消费者反映上海页面宣称治疗、加时包不予退款",
        consumer_ref="consumer-张某某",
        authorization_id="demo-auth-01",
        case_id="demo-case-01",
    )
    system.assign_case(case_id, Inspector("insp-sh-02", "上海监管员李某", ("上海",)), "服务发生地管辖")
    system.link_jurisdiction(
        case_id,
        Inspector("insp-hz-07", "杭州监管员陈某", ("杭州",)),
        "杭州",
        "经营主体属地协同调查",
    )
    system.access_personal_info(
        case_id,
        Inspector("insp-sh-02", "上海监管员李某", ("上海",)),
        "核实付款账户与服务记录",
        "消费者授权+发生地管辖",
    )

    # 9. 责令整改、延期（留依据）、复核通过
    plan_id = system.order_remediation(
        case_id,
        ["删除治疗、包治等医疗与保证性表述", "取消加时包概不退款条款"],
        "2026-09-25T23:59:59+08:00",
        "《广告法》第十七条、消费者权益保护相关规定",
        plan_id="demo-plan-01",
    )
    advance(days=3)
    system.extend_remediation(
        plan_id,
        "2026-09-30T23:59:59+08:00",
        "商家提交页面修改排期与部分截图，经主办审核同意延期5日",
    )
    advance(days=10)
    system.review_remediation(
        plan_id, True, "现场复核两地页面均已删除违禁表述，加时包支持7日无理由退款"
    )

    # 10. 处罚：关联宣传版本、服务事实与服务时有效资质
    system.issue_penalty(
        case_id,
        "demo-occ-01",
        promo_version,
        "《广告法》第十七条",
        "责令停止发布违法宣传，罚款人民币20000元",
        f"封存页面（受理号 {intake2}）、订单凭证、询问笔录、资质核验单",
        penalty_id="demo-penalty-01",
    )
    system.close_remediation(plan_id, "整改到位，违法表述已清除", "复核通过，依法结案")

    events = system.export_events()
    data_dir = ROOT / "data"
    (data_dir / "walkthrough.json").write_text(
        json.dumps(events, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    sample = next(e for e in events if e["event_type"] == "PROVIDER_ORDER_BLOCKED")
    (data_dir / "sample.json").write_text(
        json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    rebuilt = RegulationSystem.from_events(events)
    assert rebuilt.repo.load(InspectionCase, case_id).status == "closed"
    assert rebuilt.explain_penalty("demo-penalty-01")["license_snapshot"]["license_id"] == (
        "杭卫证字-2026-0731"
    )
    print(f"已生成 {len(events)} 个事件；受理号 {intake2}，宣传版本 v{promo_version}")


if __name__ == "__main__":
    main()
