from __future__ import annotations

import json

import pytest

from mnamer.organizer.models import NumberingMode, TitlePreference
from mnamer.organizer.overrides import (
    OVERRIDE_SCHEMA_VERSION,
    load_show_overrides,
    normalize_show_alias,
)

pytestmark = pytest.mark.local


def test_packaged_overrides_are_versioned_and_cover_known_numbering_modes():
    catalog = load_show_overrides()

    assert catalog.schema_version == OVERRIDE_SCHEMA_VERSION
    assert {show.numbering_mode for show in catalog.shows} == set(NumberingMode)


def test_alias_lookup_is_normalized_and_returns_visible_policy():
    catalog = load_show_overrides()

    frieren = catalog.find("SOUSOU.NO.FRIEREN")
    assert frieren is not None
    assert frieren.canonical_title == "Frieren: Beyond Journey's End"
    assert frieren.tvmaze_id == 69956
    assert frieren.year == 2023
    assert frieren.numbering_mode is NumberingMode.AIRED
    assert frieren.title_preference is TitlePreference.NUMBER_FIRST

    sponge_bob = catalog.find("Sponge & Bob")
    assert sponge_bob is None
    assert catalog.find("sponge_bob").canonical_title == "SpongeBob SquarePants"


def test_alias_normalization_handles_punctuation_ampersands_and_apostrophes():
    assert normalize_show_alias("Ed, Edd n Eddy") == "ed edd n eddy"
    assert normalize_show_alias("Rock & Roll") == "rock and roll"
    assert normalize_show_alias("Journey's End") == "journey s end"


def test_custom_override_file_supports_every_policy_field(tmp_path):
    config_path = tmp_path / "overrides.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "shows": [
                    {
                        "canonical_title": "Synthetic Show",
                        "aliases": ["Synthetic Alias"],
                        "tvmaze_id": 4321,
                        "year": 2012,
                        "numbering_mode": "segment-title",
                        "title_preference": "title-required",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    catalog = load_show_overrides(config_path)
    override = catalog.find("synthetic alias")

    assert override is not None
    assert override.tvmaze_id == 4321
    assert override.year == 2012
    assert override.numbering_mode is NumberingMode.SEGMENT_TITLE
    assert override.title_preference is TitlePreference.TITLE_REQUIRED


def test_duplicate_aliases_across_shows_are_rejected(tmp_path):
    config_path = tmp_path / "overrides.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "shows": [
                    {
                        "canonical_title": "First Show",
                        "aliases": ["Shared Alias"],
                        "tvmaze_id": 1,
                        "year": None,
                        "numbering_mode": "aired",
                        "title_preference": "number-first",
                    },
                    {
                        "canonical_title": "Second Show",
                        "aliases": ["shared.alias"],
                        "tvmaze_id": 2,
                        "year": None,
                        "numbering_mode": "aired",
                        "title_preference": "number-first",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="belongs to both"):
        load_show_overrides(config_path)


def test_unknown_override_schema_is_rejected(tmp_path):
    config_path = tmp_path / "overrides.json"
    config_path.write_text(
        json.dumps({"schema_version": 99, "shows": []}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="unsupported override schema version"):
        load_show_overrides(config_path)
