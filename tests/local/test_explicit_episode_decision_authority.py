from __future__ import annotations

import pytest

from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group_with_provider,
)
from jellyfin_show_organizer.models import (
    CanonicalShow,
    NumberingMode,
    ParseResult,
    ProviderIdentity,
)
from jellyfin_show_organizer.providers import (
    ProviderEpisode,
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
)

pytestmark = pytest.mark.local

SHOW_ID = ProviderIdentity("fixture", "show")


def _episode(number: int, title: str) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", f"episode-{number}"),
        season=1,
        number=number,
        title=title,
    )


class Provider:
    provider_name = "fixture"

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        raise AssertionError("show search is not expected during assignment")

    def episode_catalog(self, show_identity: ProviderIdentity) -> ProviderEpisodeCatalog:
        assert show_identity == SHOW_ID
        return ProviderEpisodeCatalog(
            provider="fixture",
            request_key="episodes:show",
            cache_snapshot_id="catalog:v1",
            show_identity=SHOW_ID,
            episodes=(
                _episode(1, "First Story / Second Story"),
                _episode(2, "Third Story / Fourth Story"),
                _episode(3, "Fifth Story"),
                _episode(4, "Override Target"),
            ),
        )


def _show() -> CanonicalShow:
    return CanonicalShow(
        source_key="Example Series",
        provider_identity=SHOW_ID,
        title="Example Series",
        year=2024,
        numbering_mode=NumberingMode.AIRED,
    )


def test_explicit_decision_survives_segment_counted_recovery() -> None:
    sources = (
        SourceEpisodeInput(
            "source-1.mkv",
            ParseResult(
                series_hint="Example Series",
                season=1,
                episodes=(1, 2),
                title_hint="First Story & Second Story",
            ),
        ),
        SourceEpisodeInput(
            "source-2.mkv",
            ParseResult(
                series_hint="Example Series",
                season=1,
                episodes=(3, 4),
                title_hint="Third Story & Fourth Story",
            ),
        ),
        SourceEpisodeInput(
            "source-3.mkv",
            ParseResult(
                series_hint="Example Series",
                season=1,
                episodes=(5,),
                title_hint="Fifth Story",
            ),
        ),
        SourceEpisodeInput(
            "explicit.mkv",
            ParseResult(
                series_hint="Example Series",
                season=1,
                episodes=(4,),
                title_hint="A title that does not match the provider",
            ),
            explicit_decision=True,
        ),
    )

    result = assign_episode_group_with_provider(_show(), sources, Provider())
    by_source = {assignment.source_key: assignment for assignment in result.assignments}

    explicit = by_source["explicit.mkv"]
    assert explicit.status is AssignmentStatus.MATCHED
    assert [episode.number for episode in explicit.episodes] == [4]
    assert "segment-counted-title-remap:missing-exact-title-proof" not in (
        explicit.evidence.reasons
    )

    assert by_source["source-1.mkv"].status is AssignmentStatus.MATCHED
    assert by_source["source-2.mkv"].status is AssignmentStatus.MATCHED
    assert by_source["source-3.mkv"].status is AssignmentStatus.MATCHED


def test_source_episode_input_defaults_to_non_authoritative() -> None:
    source = SourceEpisodeInput("ordinary.mkv", ParseResult())
    assert not source.explicit_decision
