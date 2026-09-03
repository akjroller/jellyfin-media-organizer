from __future__ import annotations

from collections.abc import Mapping

import pytest

from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group_with_provider,
)
from jellyfin_show_organizer.filename_parser import parse_video_path
from jellyfin_show_organizer.models import (
    CanonicalShow,
    NumberingMode,
    ParseResult,
    ProviderIdentity,
)
from jellyfin_show_organizer.overrides import load_overrides
from jellyfin_show_organizer.providers import (
    ProviderEpisode,
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
    ProviderShow,
)
from jellyfin_show_organizer.segment_counted_titles import normalize_episode_title
from jellyfin_show_organizer.show_resolver import (
    ResolutionStatus,
    normalize_show_identity,
    resolve_show_group_with_provider,
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

    def __init__(
        self,
        *,
        shows: tuple[ProviderShow, ...] | None = None,
        catalogs: Mapping[ProviderIdentity, ProviderEpisodeCatalog] | None = None,
    ) -> None:
        self.shows = shows or (ProviderShow(SHOW_ID, "Example Series", 2024),)
        self.catalogs = dict(catalogs or {})

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        return ProviderSearchSnapshot(
            provider="fixture",
            request_key=f"search:{normalize_show_identity(title)}",
            cache_snapshot_id="search:v1",
            shows=self.shows,
        )

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
        return self.catalogs[show_identity]


def test_show_identity_folds_diacritics_without_lowering_thresholds() -> None:
    assert normalize_show_identity("Café Élan") == "cafe elan"
    accent_id = ProviderIdentity("fixture", "accent")
    resolution = resolve_show_group_with_provider(
        "Cafe Odyssey",
        (ParseResult(series_hint="Cafe Odyssey", year=2024),),
        load_overrides(),
        Provider(shows=(ProviderShow(accent_id, "Café Odyssey", 2024),)),
    )
    assert resolution.status is ResolutionStatus.MATCHED
    assert resolution.show is not None
    assert resolution.show.provider_identity == accent_id


def test_diacritic_fold_preserves_provider_ambiguity_guard() -> None:
    one = ProviderIdentity("fixture", "one")
    two = ProviderIdentity("fixture", "two")
    resolution = resolve_show_group_with_provider(
        "Cafe Odyssey",
        (ParseResult(series_hint="Cafe Odyssey"),),
        load_overrides(),
        Provider(
            shows=(
                ProviderShow(one, "Cafe Odyssey", None),
                ProviderShow(two, "Café Odyssey", None),
            )
        ),
    )
    assert resolution.status is ResolutionStatus.SUSPICIOUS
    assert resolution.show is None


def test_parent_episode_can_confirm_compact_multiword_abbreviation() -> None:
    parsed = parse_video_path(
        "synthetic/Example.Gamma.S01E03.Release/TEAM-exagam.S01E03.720p.mkv"
    )
    assert parsed.series_hint == "Example Gamma"
    assert parsed.season == 1
    assert parsed.episodes == (3,)


def test_parent_does_not_confirm_unrelated_compact_leaf_series() -> None:
    parsed = parse_video_path(
        "synthetic/Example.Gamma.S01E03.Release/TEAM-exafoo.S01E03.720p.mkv"
    )
    assert parsed.series_hint == "TEAM-exafoo"
    assert parsed.season == 1
    assert parsed.episodes == (3,)


def test_season_collection_context_can_prove_one_subtitle_suffix() -> None:
    parsed = parse_video_path(
        "Example Series S04 -END/[Team] Example Series Final - 05 (WEB 1080p).mkv"
    )
    assert parsed.series_hint == "Example Series"
    assert parsed.season == 4
    assert parsed.episodes == (5,)
    assert parsed.absolute_episode is None


def test_season_collection_context_never_promotes_zero_episode() -> None:
    parsed = parse_video_path(
        "Example Series S04 -END/[Team] Example Series Final - 00 (WEB 1080p).mkv"
    )
    assert parsed.series_hint == "Example Series Final"
    assert parsed.absolute_episode == 0
    assert parsed.season is None


def test_season_collection_context_requires_related_titles() -> None:
    parsed = parse_video_path(
        "Other Program S04 -END/[Team] Example Series Final - 05 (WEB 1080p).mkv"
    )
    assert parsed.series_hint == "Example Series Final"
    assert parsed.absolute_episode == 5
    assert parsed.season is None


def test_episode_title_normalization_handles_camelcase_possessives_and_accents() -> (
    None
):
    assert normalize_episode_title("CafeHero's Return") == "cafe heros return"
    assert normalize_episode_title("Café Hero’s Return") == "cafe heros return"


def _segment_fixture(ambiguous: bool = False):
    episodes = [
        _episode(1, "First Story / Second Story"),
        _episode(2, "Third Story / Fourth Story"),
        _episode(3, "Fifth Story"),
        _episode(4, "Sixth Story"),
    ]
    if ambiguous:
        episodes.append(_episode(5, "Sixth Stork"))
    catalog = ProviderEpisodeCatalog(
        provider="fixture",
        request_key="episodes:show",
        cache_snapshot_id="catalog:v1",
        show_identity=SHOW_ID,
        episodes=tuple(episodes),
    )
    parses = (
        ParseResult(
            series_hint="Example Series",
            season=1,
            episodes=(1, 2),
            title_hint="First Story & Second Story",
        ),
        ParseResult(
            series_hint="Example Series",
            season=1,
            episodes=(3, 4),
            title_hint="Third Story & Fourth Story",
        ),
        ParseResult(
            series_hint="Example Series",
            season=1,
            episodes=(5,),
            title_hint="Fifth Story",
        ),
        ParseResult(
            series_hint="Example Series",
            season=1,
            episodes=(6,),
            title_hint="Sixth Stor" if ambiguous else "Sixth Storyy",
        ),
    )
    sources = tuple(
        SourceEpisodeInput(f"source-{index}.mkv", parse)
        for index, parse in enumerate(parses, start=1)
    )
    show = CanonicalShow(
        source_key="Example Series",
        provider_identity=SHOW_ID,
        title="Example Series",
        year=2024,
        numbering_mode=NumberingMode.AIRED,
    )
    return show, sources, catalog


def test_group_proven_unique_near_title_member_is_recovered() -> None:
    show, sources, catalog = _segment_fixture()
    result = assign_episode_group_with_provider(
        show, sources, Provider(catalogs={SHOW_ID: catalog})
    )
    by_source = {assignment.source_key: assignment for assignment in result.assignments}
    recovered = by_source["source-4.mkv"]
    assert recovered.status is AssignmentStatus.MATCHED
    assert recovered.episodes[0].identity == ProviderIdentity("fixture", "episode-4")
    assert (
        "segment-counted-title-remap:unique-near-title-proof"
        in recovered.evidence.reasons
    )


def test_near_title_recovery_stays_blocked_when_candidates_are_too_close() -> None:
    show, sources, catalog = _segment_fixture(ambiguous=True)
    result = assign_episode_group_with_provider(
        show, sources, Provider(catalogs={SHOW_ID: catalog})
    )
    by_source = {assignment.source_key: assignment for assignment in result.assignments}
    assert by_source["source-4.mkv"].status is AssignmentStatus.UNRESOLVED
    assert not by_source["source-4.mkv"].episodes
