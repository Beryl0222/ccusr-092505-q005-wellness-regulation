"""领域错误类型：所有业务规则违例都以这些异常抛出。"""

from __future__ import annotations


class DomainError(Exception):
    """业务规则被违反。"""


class NotFoundError(DomainError):
    """引用的聚合不存在。"""


class StateError(DomainError):
    """当前流程状态不允许该操作。"""


class OrderRestrictedError(DomainError):
    """经营主体已被限制新订单。"""


class ImmutableRecordError(DomainError):
    """已发生的服务或已封存的证据不可篡改。"""


class RationaleRequiredError(DomainError):
    """整改延期、复核结论、申诉撤销等决定必须留下依据。"""
