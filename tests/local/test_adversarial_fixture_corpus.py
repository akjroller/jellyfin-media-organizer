import json
import re
from collections import Counter
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

pytestmark = pytest.mark.local
CORPUS_PATH = (
    Path(__file__).parents[1] / "fixtures" / "organizer" / "adversarial_filenames.json"
)
REQUIRED_FEATURES = {
    "edition-ambiguity",
    "alias",
    "absolute",
    "parenthesized-absolute",
    "multi-episode",
    "segment",
    "extra",
    "duplicate",
    "mojibake",
    "embedded-id",
    "title-hint",
}
EXPECTED_FIELDS = {
    "series",
    "year",
    "numbering_mode",
    "season",
    "episodes",
    "absolute_episode",
    "segment",
    "title",
    "classification",
}
VIDEO_EXTENSIONS = {".avi", ".mkv", ".mp4"}
EMAIL_PATTERN = re.compile(r"[^/\\\s]+@[^/\\\s]+")
IPV4_PATTERN = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")


def _load_corpus() -> dict[str, Any]:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def test_fixture_corpus_is_versioned_complete_and_synthetic():
    corpus = _load_corpus()
    cases = corpus["cases"]

    assert corpus["schema_version"] == 1
    assert cases
    assert len({case["id"] for case in cases}) == len(cases)

    features = {feature for case in cases for feature in case["features"]}
    assert REQUIRED_FEATURES <= features

    for case in cases:
        path_text = case["relative_path"]
        path = PurePosixPath(path_text)
        windows_path = PureWindowsPath(path_text)

        assert path.parts[0] == "synthetic"
        assert not path.is_absolute()
        assert not windows_path.is_absolute()
        assert ".." not in path.parts
        assert path.suffix.casefold() in VIDEO_EXTENSIONS
        assert EMAIL_PATTERN.search(path_text) is None
        assert IPV4_PATTERN.search(path_text) is None
        assert EXPECTED_FIELDS <= set(case["expected"])


def test_duplicate_fixture_groups_have_multiple_candidates():
    corpus = _load_corpus()
    groups = Counter(
        case["duplicate_group"] for case in corpus["cases"] if "duplicate_group" in case
    )

    assert groups
    assert all(count >= 2 for count in groups.values())


def test_fixture_corpus_materializes_only_zero_byte_files_under_tmp_path(
    tmp_path: Path,
):
    corpus = _load_corpus()

    for case in corpus["cases"]:
        relative = PurePosixPath(case["relative_path"])
        target = tmp_path.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")

    materialized = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(materialized) == len(corpus["cases"])
    assert all(path.stat().st_size == 0 for path in materialized)
    assert all(path.is_relative_to(tmp_path) for path in materialized)
