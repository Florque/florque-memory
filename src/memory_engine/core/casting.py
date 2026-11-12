"""Coerce loosely typed values (CLI strings, LLM JSON) into the types the ontology declares."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

from .parser import TYPE_KINDS, EnumDef, Slot


class CastError(ValueError):
    pass


_TRUE = {"true", "yes", "y", "1", "on"}
_FALSE = {"false", "no", "n", "0", "off"}


def _scalar(value: Any) -> None:
    if isinstance(value, (dict, list, tuple, set)):
        raise CastError(f"expected a single value, got {type(value).__name__}")


def _to_string(value: Any) -> str:
    _scalar(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _to_integer(value: Any) -> int:
    _scalar(value)
    if isinstance(value, bool):
        raise CastError("expected an integer, got a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip().replace("_", "")
        if re.fullmatch(r"[+-]?\d+", text):
            return int(text)
        if re.fullmatch(r"[+-]?\d+\.0*", text):
            return int(float(text))
    raise CastError(f"expected an integer, got {value!r}")


def _to_float(value: Any) -> float:
    _scalar(value)
    if isinstance(value, bool):
        raise CastError("expected a number, got a boolean")
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().replace("_", ""))
    except ValueError:
        raise CastError(f"expected a number, got {value!r}") from None


def _to_boolean(value: Any) -> bool:
    _scalar(value)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise CastError(f"expected true or false, got {value!r}")


def _to_datetime(value: Any) -> datetime:
    _scalar(value)
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime(value.year, value.month, value.day)
    else:
        try:
            result = datetime.fromisoformat(str(value).strip())
        except ValueError:
            raise CastError(f"expected an ISO datetime like 2026-03-31T12:00:00Z, got {value!r}") from None
    return result if result.tzinfo else result.replace(tzinfo=UTC)


def _to_date(value: Any) -> date:
    _scalar(value)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        raise CastError(f"expected an ISO date like 2026-03-31, got {value!r}") from None


_CASTERS = {
    "string": _to_string,
    "integer": _to_integer,
    "float": _to_float,
    "boolean": _to_boolean,
    "date": _to_date,
    "datetime": _to_datetime,
}


def _norm(text: str) -> str:
    return re.sub(r"[\s-]+", "_", text.strip().lower())


def _to_enum(value: Any, enum: EnumDef) -> str:
    _scalar(value)
    text = str(value).strip()
    if text in enum.values:
        return text
    for name in enum.values:
        if _norm(name) == _norm(text):
            return name
    raise CastError(f"`{value}` is not a valid {enum.name}; allowed: {', '.join(enum.values)}")


def cast_value(value: Any, range_name: str, enums: Mapping[str, EnumDef]) -> Any:
    if range_name in enums:
        return _to_enum(value, enums[range_name])
    if range_name in TYPE_KINDS:
        return _CASTERS[TYPE_KINDS[range_name]](value)
    raise CastError(f"cannot cast to unknown range `{range_name}`")


def _cast_any(value: Any, ranges: tuple[str, ...], enums: Mapping[str, EnumDef]) -> Any:
    errors = []
    for rng in ranges:
        try:
            return cast_value(value, rng, enums)
        except CastError as exc:
            errors.append(str(exc))
    raise CastError("; ".join(errors))


def cast_slot(slot: Slot, value: Any, enums: Mapping[str, EnumDef]) -> Any:
    """Cast a property value for `slot`, honouring multivalued and any_of ranges."""
    if slot.multivalued:
        items = list(value) if isinstance(value, (list, tuple)) else [value]
        return [_cast_any(item, slot.ranges, enums) for item in items]
    return _cast_any(value, slot.ranges, enums)


def cast_filter(slot: Slot, value: Any, enums: Mapping[str, EnumDef]) -> Any:
    """Cast a single filter value, even for multivalued slots."""
    return cast_slot(replace(slot, multivalued=False), value, enums)


def is_text_slot(slot: Slot) -> bool:
    return (
        not slot.is_edge
        and bool(slot.ranges)
        and all(TYPE_KINDS.get(r) == "string" for r in slot.ranges)
    )
