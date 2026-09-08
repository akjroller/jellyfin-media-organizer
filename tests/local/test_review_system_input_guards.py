from __future__ import annotations

import json

import pytest

from jellyfin_show_organizer.review_system import (
    ReviewConfigurationError,
    _duplicate_collision_class,
    _mapping,
    _record_show_key,
    _string,
    load_review_answers,
)

pytestmark = pytest.mark.local


def _answers_payload(plan_sha256: object) -> bytes:
    return json.dumps(
        {
            "schema_version": 2,
            "plan_sha256": plan_sha256,
            "base_override_snapshot": "b" * 64,
            "session_sha256": "c" * 64,
            "answers": [],
        }
    ).encode()


def test_answer_hash_validation_rejects_non_string_and_non_hex_values() -> None:
    with pytest.raises(ReviewConfigurationError, match="must be a SHA-256 string"):
        load_review_answers(_answers_payload(7))

    with pytest.raises(
        ReviewConfigurationError, match="must contain 64 hex characters"
    ):
        load_review_answers(_answers_payload("z" * 64))


def test_review_record_primitives_fail_closed_on_wrong_types() -> None:
    with pytest.raises(ReviewConfigurationError, match="record must be an object"):
        _mapping(None, "record")

    with pytest.raises(
        ReviewConfigurationError, match="field must be a non-empty string"
    ):
        _string("", "field")


def test_duplicate_collision_class_requires_structured_valid_value() -> None:
    with pytest.raises(
        ReviewConfigurationError,
        match="missing structured collision_class",
    ):
        _duplicate_collision_class({})

    with pytest.raises(ReviewConfigurationError, match="collision_class is invalid"):
        _duplicate_collision_class({"collision_class": "fabricated-invalid"})


def test_record_show_key_falls_back_to_source_parent() -> None:
    assert (
        _record_show_key(
            {
                "source": {
                    "relative_path": "Fallback Series/Mystery.mkv",
                }
            }
        )
        == "Fallback Series"
    )
