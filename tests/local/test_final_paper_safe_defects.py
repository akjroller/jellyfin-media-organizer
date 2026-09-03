from __future__ import annotations

from collections.abc import Mapping

import pytest

from jellyfin_show_organizer.filename_parser import parse_video_path
from jellyfin_show_organizer.models import ProviderIdentity
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
ALPHA = ProviderIdentity("fixture", "alpha")
BETA = ProviderIdentity("fixture", "beta")


def test_explicit_multi_episode_range_expands_contiguously() -> None:
    parsed = parse_video_path("Example Show/Example.Show.S06E18-E22.1080p.mkv")
    assert parsed.season == 6
    assert parsed.episodes == (18, 19, 20, 21, 22)


def test_non_range_multi_episode_list_is_not_expanded() -> None:
    parsed = parse_video_path("Example Show/Example.Show.S06E18E22.1080p.mkv")
    assert parsed.episodes == (18, 22)


def test_parent_season_proves_packed_coordinate() -> None:
    parsed = parse_video_path(
        "Example.Show.S04.Release/Example.Show.406.Tabula.Rasa.mkv"
    )
    assert parsed.series_hint == "Example Show"
    assert parsed.season == 4
    assert parsed.episodes == (6,)
    assert parsed.absolute_episode is None
    assert parsed.title_hint == "Tabula Rasa"


def test_packed_coordinate_requires_matching_parent_series() -> None:
    parsed = parse_video_path("Other.Show.S04.Release/Example.Show.406.Tabula.Rasa.mkv")
    assert parsed.season is None
    assert not parsed.episodes


def test_packed_coordinate_never_reinterprets_year() -> None:
    parsed = parse_video_path("Example.Show.S20.Release/Example.Show.2024.Special.mkv")
    assert parsed.season is None
    assert not parsed.episodes


def test_season_only_package_does_not_parse_audio_channel_as_absolute() -> None:
    parsed = parse_video_path(
        "Example.Show.S01.BluRay.1080p.Opus.2.0/"
        "Example.Show.S01.BluRay.1080p.Opus.2.0.mkv"
    )
    assert parsed.series_hint == "Example Show"
    assert parsed.absolute_episode is None
    assert parsed.season is None
    assert not parsed.episodes


class Provider:
    provider_name = "fixture"

    def __init__(
        self,
        shows: tuple[ProviderShow, ...],
        catalogs: Mapping[ProviderIdentity, ProviderEpisodeCatalog],
    ) -> None:
        self.shows = shows
        self.catalogs = dict(catalogs)

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        return ProviderSearchSnapshot(
            provider="fixture",
            request_key=f"search:{title.casefold()}",
            cache_snapshot_id="search:v1",
            shows=self.shows,
        )

    def episode_catalog(self, identity: ProviderIdentity) -> ProviderEpisodeCatalog:
        return self.catalogs[identity]


def _catalog(identity: ProviderIdentity, title: str) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{identity.value}",
        cache_snapshot_id=f"catalog:{identity.value}:v1",
        show_identity=identity,
        episodes=(
            ProviderEpisode(
                identity=ProviderIdentity("fixture", f"{identity.value}-episode-1"),
                season=1,
                number=1,
                title=title,
            ),
        ),
    )


def _resolve(provider: Provider, *, title_hint: str | None = "Opening Story"):
    parsed = parse_video_path(
        "Example Program S01E01"
        + (f" {title_hint}" if title_hint is not None else "")
        + ".mkv"
    )
    return resolve_show_group_with_provider(
        "Example Program",
        (parsed,),
        load_overrides(),
        provider,
    )


def test_exact_coordinate_and_title_can_confirm_borderline_show() -> None:
    provider = Provider(
        (ProviderShow(ALPHA, "Example Program: Extended Name", 2026),),
        {ALPHA: _catalog(ALPHA, "Opening Story")},
    )
    result = _resolve(provider)
    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == ALPHA
    assert "catalog-coordinate-title-rescue:unique-compatible-candidate" in (
        result.evidence.reasons
    )


def test_coordinate_title_rescue_rejects_title_mismatch() -> None:
    provider = Provider(
        (ProviderShow(ALPHA, "Example Program: Extended Name", 2026),),
        {ALPHA: _catalog(ALPHA, "Different Story")},
    )
    result = _resolve(provider)
    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None


def test_coordinate_title_rescue_rejects_multiple_compatible_candidates() -> None:
    provider = Provider(
        (
            ProviderShow(ALPHA, "Example Program: First Edition", 2025),
            ProviderShow(BETA, "Example Program: Second Edition", 2026),
        ),
        {
            ALPHA: _catalog(ALPHA, "Opening Story"),
            BETA: _catalog(BETA, "Opening Story"),
        },
    )
    result = _resolve(provider)
    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None


def test_coordinate_title_rescue_does_not_use_titleless_source() -> None:
    provider = Provider(
        (ProviderShow(ALPHA, "Example Program: Extended Name", 2026),),
        {ALPHA: _catalog(ALPHA, "Opening Story")},
    )
    result = _resolve(provider, title_hint=None)
    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
