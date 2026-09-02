from __future__ import annotations

from collections.abc import Mapping

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
from jellyfin_show_organizer.show_structural_evidence import structural_title_score

pytestmark = pytest.mark.local

ALPHA = ProviderIdentity("fixture", "alpha")
BETA = ProviderIdentity("fixture", "beta")


def _show(identity: ProviderIdentity, title: str) -> ProviderShow:
    return ProviderShow(identity, title, 2024)


def _episode(identity: str, number: int) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", identity),
        season=1,
        number=number,
        title=f"Episode {number}",
    )


def _snapshot(title: str, *shows: ProviderShow) -> ProviderSearchSnapshot:
    return ProviderSearchSnapshot(
        provider="fixture",
        request_key=f"search:{title}",
        cache_snapshot_id=f"snapshot:{title}",
        shows=tuple(shows),
    )


def _catalog(
    identity: ProviderIdentity,
    *episodes: ProviderEpisode,
) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{identity.value}",
        cache_snapshot_id=f"catalog:{identity.value}",
        show_identity=identity,
        episodes=tuple(episodes),
    )


class ExpansionProvider:
    provider_name = "fixture"

    def __init__(
        self,
        shows: tuple[ProviderShow, ...],
        catalogs: Mapping[ProviderIdentity, ProviderEpisodeCatalog],
    ) -> None:
        self.shows = shows
        self.catalogs = dict(catalogs)

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        return _snapshot(title, *self.shows)

    def episode_catalog(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderEpisodeCatalog:
        return self.catalogs.get(show_identity, _catalog(show_identity))


def _parses(*episodes: int) -> tuple[ParseResult, ...]:
    return tuple(
        ParseResult(series_hint="Example Lab", season=1, episodes=(episode,))
        for episode in episodes
    )


def _resolve(provider: ExpansionProvider, *episodes: int):
    return resolve_show_group_with_provider(
        "Example Lab",
        _parses(*episodes),
        load_overrides(),
        provider,
    )


def test_structural_score_accepts_one_strict_token_expansion() -> None:
    score, reasons = structural_title_score(("example lab",), "Example Laboratory")

    assert score == 0.78
    assert reasons == ("single-token-prefix-expansion-equivalent",)


def test_structural_score_rejects_unsafe_prefix_shapes() -> None:
    assert structural_title_score(("example ab",), "Example Abacus")[0] is None
    assert structural_title_score(("example laboratory",), "Example Lab")[0] is None
    assert (
        structural_title_score(("example lab show",), "Examples Laboratory Show")[0]
        is None
    )
    assert structural_title_score(("example lab",), "Example Label")[0] is None


def test_multi_episode_catalog_rescue_can_promote_expansion() -> None:
    provider = ExpansionProvider(
        (_show(ALPHA, "Example Laboratory"),),
        {
            ALPHA: _catalog(
                ALPHA,
                _episode("alpha-1", 1),
                _episode("alpha-2", 2),
            )
        },
    )

    result = _resolve(provider, 1, 2)

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == ALPHA
    assert "aired-catalog-rescue:unique-compatible-candidate" in result.evidence.reasons
    assert result.evidence.candidates[0].score == 0.78
    assert (
        "single-token-prefix-expansion-equivalent"
        in result.evidence.candidates[0].reasons
    )


def test_single_episode_cannot_be_promoted_by_expansion_alone() -> None:
    provider = ExpansionProvider(
        (_show(ALPHA, "Example Laboratory"),),
        {ALPHA: _catalog(ALPHA, _episode("alpha-1", 1))},
    )

    result = _resolve(provider, 1)

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None


def test_catalog_incompatibility_remains_blocked() -> None:
    provider = ExpansionProvider(
        (_show(ALPHA, "Example Laboratory"),),
        {ALPHA: _catalog(ALPHA, _episode("alpha-1", 1))},
    )

    result = _resolve(provider, 1, 2)

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert (
        "aired-catalog-rescue:no-unique-compatible-candidate" in result.evidence.reasons
    )


def test_multiple_compatible_candidates_remain_blocked() -> None:
    provider = ExpansionProvider(
        (
            _show(ALPHA, "Example Laboratory"),
            _show(BETA, "Example Laboratory"),
        ),
        {
            ALPHA: _catalog(
                ALPHA,
                _episode("alpha-1", 1),
                _episode("alpha-2", 2),
            ),
            BETA: _catalog(
                BETA,
                _episode("beta-1", 1),
                _episode("beta-2", 2),
            ),
        },
    )

    result = _resolve(provider, 1, 2)

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert (
        "aired-catalog-rescue:no-unique-compatible-candidate" in result.evidence.reasons
    )
