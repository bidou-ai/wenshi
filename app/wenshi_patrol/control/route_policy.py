"""Wens1 正式路线的不可变约束。"""

from __future__ import annotations


ROUTE_ORDER = ("LM1", "LM4", "LM3", "LM2")


class RoutePolicyError(ValueError):
    """路线与已验证 Wens1 顺序不一致。"""


def validate_route(route: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    normalized = tuple(str(name) for name in route)
    if normalized != ROUTE_ORDER:
        expected = " -> ".join(ROUTE_ORDER)
        actual = " -> ".join(normalized) or "<empty>"
        raise RoutePolicyError(f"正式路线必须为 {expected}，当前为 {actual}")
    return normalized

