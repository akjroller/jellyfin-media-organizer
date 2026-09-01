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

FIRST_ID = ProviderIdentity("fixture", "first")
SECOND_ID = ProviderIdentity("fixture", "second")


class Provider:
    provider_name = "fixture"

    def __init__(self, shows: tuple[ProviderShow, ...]) -> None:
        self.shows = shows

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        assert title == "Example Heroes And Villains"
        return ProviderSearchSnapshot(
            provider="fixture",
            request_key="search:example heroes and villains",
            cache_snapshot_id="search:v1",
            shows=self.shows,
        )

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
        assert show_identity in {FIRST_ID, SECOND_ID}
        return ProviderEpisodeCatalog(
            provider="fixture",
            request_key=f"episodes:{show_identity.provider_id}",
            cache_snapshot_id=f"episodes:{show_identity.provider_id}:v1",
            show_identity=show_identity,
            episodes=(
                ProviderEpisode(
                    identity=ProviderIdentity(
                        "fixture", f"{show_identity.provider_id}-episode-1"
                    ),
                    season=1,
                    number=1,
                    title="Arrival",
                ),
            ),
        )


def test_complete_ampersand_equivalence_can_resolve_one_provider_candidate() -> None:
    result = resolve_show_group_with_provider(
        "Example Heroes And Villains",
        (
            ParseResult(
                series_hint="Example Heroes And Villains",
                season=1,
                episodes=(1,),
            ),
        ),
        load_overrides(),
        Provider(
            (
                ProviderShow(
                    identity=FIRST_ID,
                    title="Example Heroes & Villains",
                    year=None,
                ),
            )
        ),
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == FIRST_ID
    assert result.evidence.confidence == 0.90
    assert any(
        "provider-ampersand-equivalent" in reason
        for candidate in result.evidence.candidates
        for reason in candidate.reasons
    )


def test_multiple_ampersand_equivalent_candidates_remain_suspicious() -> None:
    result = resolve_show_group_with_provider(
        "Example Heroes And Villains",
        (
            ParseResult(
                series_hint="Example Heroes And Villains",
                season=1,
                episodes=(1,),
            ),
        ),
        load_overrides(),
        Provider(
            (
                ProviderShow(
                    identity=FIRST_ID,
                    title="Example Heroes & Villains",
                    year=None,
                ),
                ProviderShow(
                    identity=SECOND_ID,
                    title="Example Heroes & Villains",
                    year=None,
                ),
            )
        ),
    )

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert result.evidence.confidence == 0.90
    assert "ambiguous-top-candidates" in result.evidence.reasons
    assert "catalog-tiebreak:no-unique-compatible-candidate" in result.evidence.reasons
