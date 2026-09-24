# 疗愈服务合规巡检库

本项目提供疗愈服务合规巡检库所需的领域事件交换约定与基础校验库。接入方使用统一的聚合标识、事件版本和带时区的发生时间，保证业务事实在不同环节之间可以复核。

## 目录

- `contracts/domain.schema.json`：领域事件信封和已登记类型。
- `data/sample.json`：中文联调样例。
- `src/wellness_regulation/contracts.py`：不依赖第三方包的基础校验器。
- `tests/test_contracts.py`：契约边界检查。

当前核心对象包括provider_license、service_offer、inspection_case、remediation_plan，事件类型包括LICENSE_VERIFIED、OFFER_CAPTURED、CASE_ASSIGNED、REMEDIATION_ORDERED、REVIEW_CLOSED。校验器负责交换层必填字段、类型、时间和版本检查，具体业务流程在此约定上扩展。

## 测试

```bash
python3 -m unittest discover -s tests
```

## 编译检查

```bash
python3 -m compileall -q src tests
```
