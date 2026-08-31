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
    path = _write(
        tmp_path / "invalid-overrides.toml",
        "schema_version = 9\n",
    )

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
