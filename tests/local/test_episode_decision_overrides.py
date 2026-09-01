from pathlib import Path

import pytest

from jellyfin_show_organizer.models import NumberingMode, ParseResult, ProviderIdentity
from jellyfin_show_organizer.overrides import load_overrides

pytestmark = pytest.mark.local


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_schema_three_loads_scoped_aired_episode_decision(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "episode-decision.toml",
        """schema_version = 3

[[episode_decisions]]
source = "Example Series/Example Series - ambiguous.mkv"
show_provider = "tvmaze"
show_provider_id = 45001
numbering_mode = "aired"
season = 1
episodes = [2, 3]
reasons = ["reviewed against the provider catalog"]
""",
    )

    catalog = load_overrides(path)
    decision = catalog.episode_decisions[0]

    assert decision.source == "Example Series/Example Series - ambiguous.mkv"
    assert decision.show_provider_identity == ProviderIdentity.tvmaze(45001)
    assert decision.numbering_mode is NumberingMode.AIRED
    assert decision.parse.season == 1
    assert decision.parse.episodes == (2, 3)
    assert catalog.episode_decision_for(decision.source) == decision


def test_episode_decision_replaces_only_numbering_evidence(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "episode-decision.toml",
        """schema_version = 3

[[episode_decisions]]
source = "Example Series/ambiguous.mkv"
show_provider = "tvmaze"
show_provider_id = 45001
numbering_mode = "date"
episode_date = "2024-02-03"
""",
    )
    decision = load_overrides(path).episode_decisions[0]
    original = ParseResult(
        series_hint="Example Series",
        season=9,
        episodes=(9,),
        absolute_episode=99,
        year=2024,
        embedded_tvmaze_id=45001,
        title_hint="Original Title",
    )

    replaced = decision.apply_to(original)

    assert replaced.series_hint == "Example Series"
    assert replaced.year == 2024
    assert replaced.embedded_tvmaze_id == 45001
    assert replaced.title_hint == "Original Title"
    assert replaced.season is None
    assert replaced.episodes == ()
    assert replaced.absolute_episode is None
    assert replaced.episode_date == "2024-02-03"


def test_episode_decisions_require_schema_three(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "old-schema.toml",
        """schema_version = 2

[[episode_decisions]]
source = "Example Series/episode.mkv"
show_provider = "tvmaze"
show_provider_id = 45001
numbering_mode = "aired"
season = 1
episodes = [1]
""",
    )

    with pytest.raises(ValueError, match="require override schema_version 3"):
        load_overrides(path)


def test_duplicate_episode_decision_source_is_rejected_after_normalization(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path / "duplicates.toml",
        """schema_version = 3

[[episode_decisions]]
source = "Example Series/episode.mkv"
show_provider = "tvmaze"
show_provider_id = 45001
numbering_mode = "aired"
season = 1
episodes = [1]

[[episode_decisions]]
source = "Ｅxample Series/EPISODE.mkv"
show_provider = "tvmaze"
show_provider_id = 45001
numbering_mode = "aired"
season = 1
episodes = [2]
""",
    )

    with pytest.raises(ValueError, match="configured more than once"):
        load_overrides(path)


def test_mixed_episode_decision_numbering_evidence_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "mixed.toml",
        """schema_version = 3

[[episode_decisions]]
source = "Example Series/episode.mkv"
show_provider = "tvmaze"
show_provider_id = 45001
numbering_mode = "aired"
season = 1
episodes = [1]
absolute_episode = 4
""",
    )

    with pytest.raises(ValueError, match="does not match numbering_mode"):
        load_overrides(path)


def test_episode_decision_source_must_be_relative(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "absolute.toml",
        """schema_version = 3

[[episode_decisions]]
source = "/private/Example Series/episode.mkv"
show_provider = "tvmaze"
show_provider_id = 45001
numbering_mode = "aired"
season = 1
episodes = [1]
""",
    )

    with pytest.raises(ValueError, match="episode decision source must be relative"):
        load_overrides(path)


def test_episode_decision_snapshot_is_order_independent(tmp_path: Path) -> None:
    first = _write(
        tmp_path / "first.toml",
        """schema_version = 3

[[episode_decisions]]
source = "Example Series/b.mkv"
show_provider = "tvmaze"
show_provider_id = 45001
numbering_mode = "absolute"
absolute_episode = 8
reasons = ["second reason", "first reason"]

[[episode_decisions]]
source = "Example Series/a.mkv"
show_provider = "tvmaze"
show_provider_id = 45001
numbering_mode = "aired"
season = 1
episodes = [2]
""",
    )
    second = _write(
        tmp_path / "second.toml",
        """schema_version = 3

[[episode_decisions]]
source = "Example Series/a.mkv"
show_provider = "tvmaze"
show_provider_id = 45001
numbering_mode = "aired"
season = 1
episodes = [2]

[[episode_decisions]]
source = "Example Series/b.mkv"
show_provider = "tvmaze"
show_provider_id = 45001
numbering_mode = "absolute"
absolute_episode = 8
reasons = ["first reason", "second reason"]
""",
    )

    first_catalog = load_overrides(first)
    second_catalog = load_overrides(second)

    assert first_catalog.canonical_bytes() == second_catalog.canonical_bytes()
    assert first_catalog.snapshot_id == second_catalog.snapshot_id


def test_non_segment_episode_decision_rejects_unused_title_hint(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "unused-title.toml",
        """schema_version = 3

[[episode_decisions]]
source = "Example Series/episode.mkv"
show_provider = "tvmaze"
show_provider_id = 45001
numbering_mode = "aired"
season = 1
episodes = [1]
title_hint = "Ignored"
""",
    )

    with pytest.raises(ValueError, match="only valid for segment-title mode"):
        load_overrides(path)
