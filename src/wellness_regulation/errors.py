"""领域错误：所有业务规则违例都使用这些类型，便于接入方区分处理。"""

from __future__ import annotations


class DomainError(Exception):
    """业务规则不满足（缺依据、状态不允许等）。"""


class ContractViolation(DomainError):
    """事件在落库前未通过交换契约校验。"""

    def __init__(self, issues: list[str]) -> None:
        super().__init__("；".join(issues))
        self.issues = issues


class ConcurrencyError(DomainError):
    """追加事件时聚合版本与库内版本不连续。"""


class AccessDenied(DomainError):
    """监管员访问了职责范围外或未获消费者授权的个人信息。"""


class ImmutableFactError(DomainError):
    """试图修改已经发生的服务事实——服务记录只允许追加，不允许篡改。"""
