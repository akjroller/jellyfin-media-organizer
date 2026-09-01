from __future__ import annotations

import pytest

from jellyfin_show_organizer.models import ParseResult, ProviderIdentity
from jellyfin_show_organizer.overrides import load_overrides
from jellyfin_show_organizer.providers import (
    ProviderEpisode,
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
    ProviderShow,
)
from jellyfin_show_organizer.show_resolver import (
    ResolutionStatus,
    resolve_show_group_with_provider,
)

pytestmark = pytest.mark.local

SHOW_ID = ProviderIdentity("fixture", "frontier")


class Provider:
    provider_name = "fixture"

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        assert title == "Example Frontier"
        return ProviderSearchSnapshot(
            provider="fixture",
            request_key="search:example frontier",
            cache_snapshot_id="search:v1",
            shows=(
                ProviderShow(
                    identity=SHOW_ID,
                    title="Example Frontier: The Long Journey",
                    year=2024,
                ),
            ),
        )

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
        assert show_identity == SHOW_ID
        return ProviderEpisodeCatalog(
            provider="fixture",
            request_key="episodes:frontier",
            cache_snapshot_id="episodes:v1",
            show_identity=SHOW_ID,
            episodes=(
                ProviderEpisode(
                    identity=ProviderIdentity("fixture", "episode-1"),
                    season=1,
                    number=1,
                    title="Arrival",
                ),
                ProviderEpisode(
                    identity=ProviderIdentity("fixture", "episode-2"),
                    season=1,
                    number=2,
                    title="Departure",
                ),
            ),
        )


def test_provider_subtitle_prefix_requires_multi_episode_catalog_confirmation() -> (
    None
):
    result = resolve_show_group_with_provider(
        "Example Frontier",
        (
            ParseResult(
                series_hint="Example Frontier",
                season=1,
                episodes=(1,),
                year=2024,
            ),
            ParseResult(
                series_hint="Example Frontier",
                season=1,
                episodes=(2,),
                year=2024,
            ),
        ),
        load_overrides(),
        Provider(),
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == SHOW_ID
    assert result.evidence.confidence == 0.88
    assert (
        "aired-catalog-rescue:unique-compatible-candidate" in result.evidence.reasons
    )
    assert any(
        "provider-subtitle-prefix" in reason
        for candidate in result.evidence.candidates
        for reason in candidate.reasons
    )


def test_single_episode_provider_subtitle_prefix_stays_unresolved() -> None:
    result = resolve_show_group_with_provider(
        "Example Frontier",
        (
            ParseResult(
                series_hint="Example Frontier",
                season=1,
                episodes=(1,),
                year=2024,
            ),
        ),
        load_overrides(),
        Provider(),
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert result.evidence.confidence == 0.88
    assert "provider-evidence-below-threshold" in result.evidence.reasons
