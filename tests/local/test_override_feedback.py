from pathlib import Path

import pytest

from jellyfin_show_organizer.cli import main
from jellyfin_show_organizer.overrides import load_overrides

pytestmark = pytest.mark.local


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_default_override_catalog_remains_valid():
    catalog = load_overrides()

    assert catalog.schema_version == 1
    assert catalog.shows
    assert len(catalog.snapshot_id) == 64


def test_snapshot_is_stable_across_table_and_alias_order(tmp_path: Path):
    first = _write(
        tmp_path / "first.toml",
        """schema_version = 1

[[shows]]
key = "fabricated-one-key"
tvmaze_id = 41001
aliases = ["Fabricated One Alt", "Fabricated One"]
year = 2024
numbering_mode = "aired"
title_preference = "provider"

[[shows]]
key = "fabricated-two-key"
tvmaze_id = 41002
aliases = ["Fabricated Two"]
numbering_mode = "absolute"
title_preference = "override"
preferred_title = "Fabricated Two Preferred"
""",
    )
    second = _write(
        tmp_path / "second.toml",
        """schema_version = 1

[[shows]]
key = "fabricated-two-key"
tvmaze_id = 41002
aliases = ["Fabricated Two"]
numbering_mode = "absolute"
title_preference = "override"
preferred_title = "Fabricated Two Preferred"

[[shows]]
key = "fabricated-one-key"
tvmaze_id = 41001
aliases = ["Fabricated One", "Fabricated One Alt"]
year = 2024
numbering_mode = "aired"
title_preference = "provider"
""",
    )

    first_catalog = load_overrides(first)
    second_catalog = load_overrides(second)

    assert first_catalog.canonical_bytes() == second_catalog.canonical_bytes()
    assert first_catalog.snapshot_id == second_catalog.snapshot_id


def test_unknown_top_level_field_is_rejected(tmp_path: Path):
    path = _write(
        tmp_path / "unknown.toml",
        """schema_version = 1
private_path = "/fabricated/private/path"
""",
    )

    with pytest.raises(ValueError, match="unknown top-level override fields"):
        load_overrides(path)


def test_unsupported_schema_version_is_rejected(tmp_path: Path):
    path = _write(tmp_path / "future.toml", "schema_version = 999\n")

    with pytest.raises(ValueError, match="unsupported override schema_version"):
        load_overrides(path)


def test_normalized_identity_collision_is_rejected(tmp_path: Path):
    path = _write(
        tmp_path / "ambiguous.toml",
        """schema_version = 1

[[shows]]
key = "first-key"
aliases = ["Example Series"]
numbering_mode = "aired"
title_preference = "provider"

[[shows]]
key = "second-key"
aliases = ["Ｅxample Series"]
numbering_mode = "aired"
title_preference = "provider"
""",
    )

    with pytest.raises(ValueError, match="ambiguous after normalization"):
        load_overrides(path)


def test_duplicate_provider_identity_is_rejected(tmp_path: Path):
    path = _write(
        tmp_path / "duplicate-provider.toml",
        """schema_version = 1

[[shows]]
key = "first-key"
tvmaze_id = 42001
numbering_mode = "aired"
title_preference = "provider"

[[shows]]
key = "second-key"
tvmaze_id = 42001
numbering_mode = "aired"
title_preference = "provider"
""",
    )

    with pytest.raises(ValueError, match="assigned to multiple entries"):
        load_overrides(path)


def test_duplicate_alias_after_normalization_is_rejected(tmp_path: Path):
    path = _write(
        tmp_path / "duplicate-alias.toml",
        """schema_version = 1

[[shows]]
key = "example-key"
aliases = ["Example Alias", "ｅxample alias"]
numbering_mode = "aired"
title_preference = "provider"
""",
    )

    with pytest.raises(ValueError, match="unique after normalization"):
        load_overrides(path)


def test_boolean_provider_id_is_not_accepted_as_integer(tmp_path: Path):
    path = _write(
        tmp_path / "boolean-id.toml",
        """schema_version = 1

[[shows]]
key = "example-key"
tvmaze_id = true
numbering_mode = "aired"
title_preference = "provider"
""",
    )

    with pytest.raises(ValueError, match="tvmaze_id must be an integer"):
        load_overrides(path)


def test_show_season_map_is_loaded_and_hash_bound(tmp_path: Path):
    path = tmp_path / "overrides.toml"
    path.write_text(
        """schema_version = 4

[[shows]]
key = "Pokemon Master Journeys The Series"
tvmaze_id = 590
season_map = { "1" = 24, "2" = 25 }
""",
        encoding="utf-8",
    )
    catalog = load_overrides(path)
    show = catalog.shows[0]
    assert show.provider_season(1) == 24
    assert show.provider_season(9) == 9
    assert b'"season_map":{"1":24,"2":25}' in catalog.canonical_bytes()


def test_provider_season_remap_changes_only_catalog_coordinate():
    from jellyfin_show_organizer.models import ParseResult
    from jellyfin_show_organizer.planner import _provider_season_parse

    original = ParseResult(series_hint="Example", season=1, episodes=(3,))
    remapped, reasons = _provider_season_parse(original, {1: 24})
    assert remapped.series_hint == original.series_hint
    assert remapped.episodes == original.episodes
    assert remapped.season == 24
    assert reasons == ("provider-season-remap:S01->S24",)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"season_map": ((1, 24), (1, 25)), "tvmaze_id": 590}, "unique"),
        ({"season_map": ((0, 24),), "tvmaze_id": 590}, "positive"),
        ({"season_map": ((1, 24),)}, "requires"),
        ({"year": 1799}, "outside"),
    ],
)
def test_show_override_rejects_unsafe_season_map_values(kwargs, message):
    from jellyfin_show_organizer.overrides import ShowOverride

    with pytest.raises(ValueError, match=message):
        ShowOverride(key="example", **kwargs)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("year", '"2024"', "year must be an integer"),
        ("preferred_title", "7", "preferred_title must be a string"),
        ("season_map", "[1]", "season_map must be a table"),
        ("season_map", '{"bad" = 24}', "season_map keys"),
        ("season_map", '{"1" = "24"}', "season_map values"),
        ("numbering_mode", '"invalid"', "invalid override numbering_mode"),
        ("title_preference", '"invalid"', "invalid override title_preference"),
    ],
)
def test_show_override_parser_rejects_invalid_new_fields(
    tmp_path: Path, field: str, value: str, message: str
):
    path = _write(
        tmp_path / "invalid-show-field.toml",
        f"""schema_version = 4

[[shows]]
key = "example"
tvmaze_id = 590
{field} = {value}
""",
    )
    with pytest.raises(ValueError, match=message):
        load_overrides(path)


def test_validate_cli_reports_snapshot_without_local_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    path = _write(
        tmp_path / "local-overrides.toml",
        """schema_version = 1

[[shows]]
key = "example-key"
tvmaze_id = 43001
aliases = ["Example Series"]
numbering_mode = "aired"
title_preference = "provider"
""",
    )

    assert main(["overrides", "validate", str(path)]) == 0
    output = capsys.readouterr().out

    assert "Override file valid" in output
    assert "schema=1" in output
    assert "shows=1" in output
    assert "snapshot=" in output
    assert str(path) not in output


def test_validate_cli_fails_closed_without_echoing_local_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    path = _write(tmp_path / "invalid-overrides.toml", "schema_version = 9\n")

    assert main(["overrides", "validate", str(path)]) == 2
    error = capsys.readouterr().err

    assert "Override file invalid" in error
    assert "unsupported override schema_version" in error
    assert str(path) not in error


def test_validate_cli_missing_file_does_not_echo_local_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    path = tmp_path / "missing-private-overrides.toml"

    assert main(["overrides", "validate", str(path)]) == 2
    error = capsys.readouterr().err

    assert "cannot read file" in error
    assert str(path) not in error
