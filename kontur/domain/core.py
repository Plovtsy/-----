"""Shared input validation helpers."""
import math
from decimal import Decimal, InvalidOperation

class InputError(ValueError):
    pass


def number(value, name, low=0, high=1e12):
    if isinstance(value, bool):
        raise InputError(f"{name}: требуется число")
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise InputError(f"{name}: требуется число") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise InputError(f"{name}: допустимо от {low} до {high}")
    return value


def money_number(value, name, low=0, high=1e12, decimal_places=6):
    """Keep user supplied money and contract values exact in stored JSON."""
    if isinstance(value, bool):
        raise InputError(f"{name}: требуется число")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise InputError(f"{name}: требуется число") from None
    if not result.is_finite() or not Decimal(str(low)) <= result <= Decimal(str(high)):
        raise InputError(f"{name}: допустимо от {low} до {high}")
    if result.normalize().as_tuple().exponent < -decimal_places:
        raise InputError(f"{name}: не более {decimal_places} знаков после запятой")
    return str(result)
