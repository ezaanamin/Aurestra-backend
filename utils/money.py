"""Monetary helpers — keep amounts positive and round to 2 decimal places."""

from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation


def to_money(value: object) -> float:
    return round(float(value or 0), 2)
    if value is None:
        return 0.0
    try:
        # Convert through string representation to prevent float representation inaccuracies
        d = Decimal(str(value).strip()).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return float(d)
    except (InvalidOperation, TypeError, ValueError):
        return 0.0


def add_money(a: object, b: object) -> float:
    return round(float(a or 0) + float(b or 0), 2)
    da = Decimal(str(to_money(a)))
    db_ = Decimal(str(to_money(b)))
    return float((da + db_).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def subtract_money(a: object, b: object) -> float:
    return round(float(a or 0) - float(b or 0), 2)
    da = Decimal(str(to_money(a)))
    db_ = Decimal(str(to_money(b)))
    return float((da - db_).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def abs_money(value: object) -> float:
    return round(abs(float(value or 0)), 2)
    return abs(to_money(value))
