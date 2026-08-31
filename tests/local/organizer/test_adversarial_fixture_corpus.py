import json
from collections import defaultdict
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

pytestmark = pytest.mark.local

CORPUS_PATH = (
    Path(__file__).parents[2] / "fixtures" / "organizer" / "adversarial_filenames.json"
)
REQUIRED_FEATURES = {
    "avatar",
    "edition-disambiguation",
    "extra",
    "fairy-tail-absolute",
    "frieren-alias",
    "fullmetal-absolute",
    "mojibake",
    "multi-episode-cartoon",
    "naruto-parenthesized-absolute",
    "spongebob-segment",
    "duplicate",
}
EXPECTED_FIELDS = {
    "classification",
    "canonical_series",
    "series_year",
    "numbering_mode",
    "season",
    "episodes",
    "absolute_episodes",
    "segments",
    "title",
}
PRIVATE_PATH_PARTS = {".codex", "desktop", "documents", "jellyfin", "users"}
VIDEO_EXTENSIONS = {".mkv", ".mp4"}
WINDOWS_RESERVED_NAMES = {
    "aux",
    "con",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
WINDOWS_FORBIDDEN_CHARACTERS = set('<>:"\\|?*')


def load_corpus() -> dict[str, Any]:
    """Load the checked-in, synthetic-only fixture corpus."""
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def test_adversarial_corpus_has_a_versioned_unique_schema():
    corpus = load_corpus()

    assert corpus["schema_version"] == 1
    assert corpus["description"]
    assert corpus["cases"]

    case_ids = [case["id"] for case in corpus["cases"]]
    relative_paths = [case["relative_path"] for case in corpus["cases"]]
    assert len(case_ids) == len(set(case_ids))
    assert len(relative_paths) == len(set(relative_paths))

    for case in corpus["cases"]:
        assert set(case) >= {"id", "features", "relative_path", "expected"}
        assert case["id"]
        assert case["features"]
        assert set(case["expected"]) >= EXPECTED_FIELDS
        assert case["expected"]["classification"] in {"episode", "extra"}


def test_adversarial_corpus_covers_every_required_filename_family():
    corpus = load_corpus()
    covered_features = {
        feature for case in corpus["cases"] for feature in case["features"]
    }

    assert REQUIRED_FEATURES <= covered_features


def test_fixture_paths_are_relative_synthetic_and_windows_safe():
    corpus = load_corpus()

    for case in corpus["cases"]:
        raw_path = case["relative_path"]
        posix_path = PurePosixPath(raw_path)
        windows_path = PureWindowsPath(raw_path)

        assert "\\" not in raw_path
        assert not posix_path.is_absolute()
        assert not windows_path.is_absolute()
        assert not windows_path.drive
        assert posix_path.parts[:2] == ("synthetic", "inbox")
        assert all(part not in {"", ".", ".."} for part in posix_path.parts)
        assert not ({part.casefold() for part in posix_path.parts} & PRIVATE_PATH_PARTS)
        assert posix_path.suffix.casefold() in VIDEO_EXTENSIONS

        for part in posix_path.parts:
            assert not (set(part) & WINDOWS_FORBIDDEN_CHARACTERS)
            assert part.rstrip(" .") == part
            assert (
                part.split(".", maxsplit=1)[0].casefold() not in WINDOWS_RESERVED_NAMES
            )


def test_fixture_media_materializes_as_zero_bytes_only_under_tmp_path(tmp_path: Path):
    corpus = load_corpus()
    fixture_root = tmp_path.resolve()

    for case in corpus["cases"]:
        relative_path = PurePosixPath(case["relative_path"])
        media_path = tmp_path.joinpath(*relative_path.parts)
        resolved_media_path = media_path.resolve()

        assert resolved_media_path.is_relative_to(fixture_root)
        media_path.parent.mkdir(parents=True, exist_ok=True)
        media_path.touch()

        assert media_path.is_file()
        assert media_path.stat().st_size == 0


def test_duplicate_fixtures_share_identity_but_not_source_path():
    corpus = load_corpus()
    duplicate_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for case in corpus["cases"]:
        if "duplicate" in case["features"]:
            duplicate_groups[case["duplicate_group"]].append(case)

    assert duplicate_groups
    for cases in duplicate_groups.values():
        assert len(cases) >= 2
        assert len({case["relative_path"] for case in cases}) == len(cases)
        identities = {
            (
                case["expected"]["canonical_series"],
                case["expected"]["series_year"],
                case["expected"]["season"],
                tuple(case["expected"]["episodes"]),
                tuple(case["expected"]["absolute_episodes"]),
                tuple(case["expected"]["segments"]),
            )
            for case in cases
        }
        assert len(identities) == 1
