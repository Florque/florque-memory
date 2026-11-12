from datetime import UTC, date, datetime

import pytest

from memory_engine.core.casting import CastError, cast_slot, cast_value
from memory_engine.core.parser import Slot


@pytest.mark.parametrize(
    "value, rng, expected",
    [
        ("42", "integer", 42),
        (" -7 ", "integer", -7),
        ("3.0", "integer", 3),
        (3.0, "integer", 3),
        ("1.5", "float", 1.5),
        (2, "float", 2.0),
        ("yes", "boolean", True),
        ("False", "boolean", False),
        ("2026-03-31", "date", date(2026, 3, 31)),
        ("2026-03-31T10:00:00", "date", date(2026, 3, 31)),
        ("2026-03-31T10:00:00Z", "datetime", datetime(2026, 3, 31, 10, tzinfo=UTC)),
        ("2026-03-31", "datetime", datetime(2026, 3, 31, tzinfo=UTC)),
        (12, "string", "12"),
    ],
)
def test_cast_types(value, rng, expected):
    assert cast_value(value, rng, {}) == expected


@pytest.mark.parametrize(
    "value, rng",
    [("4.2", "integer"), (True, "integer"), ("maybe", "boolean"), ("31/03/2026", "date"), ({"a": 1}, "string")],
)
def test_cast_rejects(value, rng):
    with pytest.raises(CastError):
        cast_value(value, rng, {})


def test_enum_normalizes_llm_spellings(ontology):
    assert cast_value("key_result", "GoalLevel", ontology.enums) == "key_result"
    assert cast_value("Key Result", "GoalLevel", ontology.enums) == "key_result"
    assert cast_value("KEY-RESULT", "GoalLevel", ontology.enums) == "key_result"
    with pytest.raises(CastError, match="allowed: objective, key_result"):
        cast_value("milestone", "GoalLevel", ontology.enums)


def test_multivalued_and_any_of():
    assert cast_slot(Slot("n", range="integer", multivalued=True), ["1", 2], {}) == [1, 2]
    assert cast_slot(Slot("n", range="integer", multivalued=True), "5", {}) == [5]
    either = Slot("n", any_of=("integer", "boolean"))
    assert cast_slot(either, "true", {}) is True
    with pytest.raises(CastError):
        cast_slot(Slot("n", range="integer"), [1, 2], {})
