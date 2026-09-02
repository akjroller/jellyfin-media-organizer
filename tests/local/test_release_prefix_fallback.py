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
from jellyfin_show_organizer.release_prefix_fallback import release_prefix_title
from jellyfin_show_organizer.show_resolver import (
    ResolutionStatus,
    resolve_show_group_with_provider,
)

pytestmark = pytest.mark.local

ALPHA = ProviderIdentity("fixture", "alpha")
BETA = ProviderIdentity("fixture", "beta")


def _show(identity: ProviderIdentity, title: str, year: int = 2024) -> ProviderShow:
    return ProviderShow(identity, title, year)


def _episode(
    identity: str, season: int, number: int, title: str = "Episode"
) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", identity),
        season=season,
        number=number,
        title=title,
    )


def _snapshot(title: str, *shows: ProviderShow) -> ProviderSearchSnapshot:
    return ProviderSearchSnapshot(
        provider="fixture",
        request_key=f"search:{title}",
        cache_snapshot_id=f"snapshot:{title}",
        shows=tuple(shows),
    )


def _failed_snapshot(title: str) -> ProviderSearchSnapshot:
    return ProviderSearchSnapshot(
        provider="fixture",
        request_key=f"search:{title}",
        cache_snapshot_id=f"failure:{title}",
        shows=(),
        unresolved_reason="fixture-provider-failure",
    )


def _catalog(
    identity: ProviderIdentity,
    *episodes: ProviderEpisode,
    unresolved: bool = False,
) -> ProviderEpisodeCatalog:
    return ProviderEpisodeCatalog(
        provider="fixture",
        request_key=f"episodes:{identity.value}",
        cache_snapshot_id=f"catalog:{identity.value}",
        show_identity=identity,
        episodes=tuple(episodes),
        unresolved_reason="fixture-provider-failure" if unresolved else None,
    )


class ReleasePrefixProvider:
    provider_name = "fixture"

    def __init__(
        self,
        searches: Mapping[str, ProviderSearchSnapshot],
        catalogs: Mapping[ProviderIdentity, ProviderEpisodeCatalog],
    ) -> None:
        self.searches = dict(searches)
        self.catalogs = dict(catalogs)
        self.search_calls: list[str] = []
        self.catalog_calls: list[ProviderIdentity] = []

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        self.search_calls.append(title)
        return self.searches.get(title, _snapshot(title))

    def episode_catalog(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderEpisodeCatalog:
        self.catalog_calls.append(show_identity)
        return self.catalogs.get(show_identity, _catalog(show_identity))


def _resolve(path: str, provider: ReleasePrefixProvider):
    parsed = parse_video_path(path)
    assert parsed.series_hint is not None
    return resolve_show_group_with_provider(
        parsed.series_hint,
        (parsed,),
        load_overrides(),
        provider,
    )


def test_release_prefix_split_is_deliberately_narrow() -> None:
    assert release_prefix_title("TAG-Example Series") == ("TAG", "Example Series")
    assert release_prefix_title("Spider-Man Adventures") is None
    assert release_prefix_title("TAG-SUB-Example Series") is None
    assert release_prefix_title("TAG-Example") is None
    assert release_prefix_title("tag-Example Series") is None


def test_catalog_confirmed_release_prefix_resolves() -> None:
    provider = ReleasePrefixProvider(
        {
            "TAG-Example Series": _snapshot("TAG-Example Series"),
            "Example Series": _snapshot(
                "Example Series",
                _show(ALPHA, "Example Series"),
            ),
        },
        {ALPHA: _catalog(ALPHA, _episode("alpha-1", 1, 1, "Pilot"))},
    )

    result = _resolve("TAG-Example Series S01E01.mkv", provider)

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == ALPHA
    assert "release-prefix-fallback:catalog-confirmed" in result.evidence.reasons
    assert "release-prefix-fallback-winner:fixture:alpha" in result.evidence.reasons


def test_release_prefix_requires_catalog_compatibility() -> None:
    provider = ReleasePrefixProvider(
        {
            "TAG-Example Series": _snapshot("TAG-Example Series"),
            "Example Series": _snapshot(
                "Example Series",
                _show(ALPHA, "Example Series"),
            ),
        },
        {ALPHA: _catalog(ALPHA, _episode("alpha-2", 1, 2))},
    )

    result = _resolve("TAG-Example Series S01E01.mkv", provider)

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert "release-prefix-fallback:catalog-incompatible" in result.evidence.reasons


def test_legitimate_hyphenated_title_is_not_rewritten_after_normal_match() -> None:
    provider = ReleasePrefixProvider(
        {
            "Spider-Man Adventures": _snapshot(
                "Spider-Man Adventures",
                _show(ALPHA, "Spider-Man Adventures"),
            )
        },
        {ALPHA: _catalog(ALPHA, _episode("alpha-1", 1, 1))},
    )

    result = _resolve("Spider-Man Adventures S01E01.mkv", provider)

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == ALPHA
    assert all(
        "release-prefix-fallback" not in reason for reason in result.evidence.reasons
    )
    assert "Man Adventures" not in provider.search_calls


def test_multiple_leading_prefix_tokens_are_not_retried() -> None:
    provider = ReleasePrefixProvider(
        {"TAG-SUB-Example Series": _snapshot("TAG-SUB-Example Series")},
        {},
    )

    result = _resolve("TAG-SUB-Example Series S01E01.mkv", provider)

    assert result.status is ResolutionStatus.UNRESOLVED
    assert "SUB-Example Series" not in provider.search_calls


def test_suspicious_original_identity_conflict_is_not_overridden() -> None:
    provider = ReleasePrefixProvider(
        {
            "TAG-Example Series": _snapshot(
                "TAG-Example Series",
                _show(ALPHA, "TAG-Example Series"),
                _show(BETA, "TAG-Example Series"),
            ),
            "Example Series": _snapshot(
                "Example Series",
                _show(ALPHA, "Example Series"),
            ),
        },
        {
            ALPHA: _catalog(ALPHA, _episode("alpha-1", 1, 1)),
            BETA: _catalog(BETA, _episode("beta-1", 1, 1)),
        },
    )

    result = _resolve("TAG-Example Series S01E01.mkv", provider)

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert "Example Series" not in provider.search_calls


def test_stripped_provider_failure_stays_unresolved() -> None:
    provider = ReleasePrefixProvider(
        {
            "TAG-Example Series": _snapshot("TAG-Example Series"),
            "Example Series": _failed_snapshot("Example Series"),
        },
        {},
    )

    result = _resolve("TAG-Example Series S01E01.mkv", provider)

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert (
        "release-prefix-fallback:stripped-search-indeterminate"
        in result.evidence.reasons
    )


def test_catalog_failure_stays_unresolved() -> None:
    provider = ReleasePrefixProvider(
        {
            "TAG-Example Series": _snapshot("TAG-Example Series"),
            "Example Series": _snapshot(
                "Example Series",
                _show(ALPHA, "Example Series"),
            ),
        },
        {ALPHA: _catalog(ALPHA, unresolved=True)},
    )

    result = _resolve("TAG-Example Series S01E01.mkv", provider)

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert "release-prefix-fallback:catalog-indeterminate" in result.evidence.reasons


def test_conflicting_metadata_for_same_identity_stays_unresolved() -> None:
    provider = ReleasePrefixProvider(
        {
            "TAG-Example Series": _snapshot(
                "TAG-Example Series",
                _show(ALPHA, "Unrelated Candidate"),
            ),
            "Example Series": _snapshot(
                "Example Series",
                _show(ALPHA, "Example Series"),
            ),
        },
        {ALPHA: _catalog(ALPHA, _episode("alpha-1", 1, 1))},
    )

    result = _resolve("TAG-Example Series S01E01.mkv", provider)

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert (
        "release-prefix-fallback:conflicting-candidate-metadata"
        in result.evidence.reasons
    )


def test_candidate_union_is_deterministic() -> None:
    primary = _show(BETA, "Unrelated Candidate")
    winner = _show(ALPHA, "Example Series")
    catalogs = {
        ALPHA: _catalog(ALPHA, _episode("alpha-1", 1, 1)),
        BETA: _catalog(BETA, _episode("beta-1", 1, 1)),
    }
    first = ReleasePrefixProvider(
        {
            "TAG-Example Series": _snapshot("TAG-Example Series", primary),
            "Example Series": _snapshot("Example Series", winner),
        },
        catalogs,
    )
    second = ReleasePrefixProvider(
        {
            "TAG-Example Series": _snapshot("TAG-Example Series", primary),
            "Example Series": _snapshot("Example Series", winner),
        },
        dict(reversed(tuple(catalogs.items()))),
    )

    first_result = _resolve("TAG-Example Series S01E01.mkv", first)
    second_result = _resolve("TAG-Example Series S01E01.mkv", second)

    assert first_result == second_result


def test_parent_confirmed_parser_stripping_remains_stronger_path() -> None:
    parsed = parse_video_path("Example Series S01E01/TAG-Example Series S01E01.mkv")

    assert parsed.series_hint == "Example Series"
