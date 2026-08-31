from pathlib import Path

import pytest

from jellyfin_show_organizer.models import NumberingMode, TitlePreference
from jellyfin_show_organizer.overrides import load_overrides

pytestmark = pytest.mark.local


def test_numbering_modes_are_explicit_and_versionable():
    assert {mode.value for mode in NumberingMode} == {
        "aired",
        "absolute",
        "parenthesized-absolute",
        "segment-title",
        "special",
        "date",
    }


def test_default_overrides_are_synthetic_and_data_driven():
    catalog = load_overrides()

    aired = catalog.get("EXAMPLE-AIRED")
    assert aired is not None
    assert aired.aliases == ("Example Aired Series",)
    assert aired.numbering_mode is NumberingMode.AIRED

    parenthesized = catalog.get("example-parenthesized")
    assert parenthesized is not None
    assert parenthesized.numbering_mode is NumberingMode.PARENTHESIZED_ABSOLUTE

    segment = catalog.get("example-segment")
    assert segment is not None
    assert segment.numbering_mode is NumberingMode.SEGMENT_TITLE


def test_overrides_support_year_title_preference_and_preferred_title(tmp_path: Path):
    override_file = tmp_path / "overrides.toml"
    override_file.write_text(
        """
schema_version = 2

[[shows]]
key = "example"
tvmaze_id = 123
aliases = ["Example Alias"]
year = 2001
numbering_mode = "absolute"
title_preference = "override"
preferred_title = "Canonical Example"
""".strip(),
        encoding="utf-8",
    )

    catalog = load_overrides(override_file)
    show = catalog.get("example")

    assert catalog.schema_version == 2
    assert show is not None
    assert show.tvmaze_id == 123
    assert show.aliases == ("Example Alias",)
    assert show.year == 2001
    assert show.numbering_mode is NumberingMode.ABSOLUTE
    assert show.title_preference is TitlePreference.OVERRIDE
    assert show.preferred_title == "Canonical Example"


def test_override_title_preference_requires_explicit_title(tmp_path: Path):
    override_file = tmp_path / "invalid.toml"
    override_file.write_text(
        """
schema_version = 1

[[shows]]
key = "example"
numbering_mode = "aired"
title_preference = "override"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires preferred_title"):
        load_overrides(override_file)
