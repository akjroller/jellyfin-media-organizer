from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

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
from jellyfin_show_organizer.overrides import load_overrides
from jellyfin_show_organizer.providers import (
    ProviderEpisode,
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
    ProviderShow,
    TvmazeProviderAdapter,
)
from jellyfin_show_organizer.show_resolver import (
    ResolutionStatus,
    resolve_show_group_with_provider,
)
from jellyfin_show_organizer.tvmaze_cache import (
    TVMAZE_EPISODES_URL,
    TVMAZE_SEARCH_URL,
    TvmazeCatalogCache,
)

pytestmark = pytest.mark.local


class RecordingGetter:
    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Mapping[str, str] | None]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, str] | None = None,
    ) -> object:
        self.calls.append((url, params))
        return self.responses[url]


class FixtureProvider:
    provider_name = "fixture"

    def __init__(self) -> None:
        self.search_calls: list[str] = []
        self.catalog_calls: list[ProviderIdentity] = []

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        self.search_calls.append(title)
        return ProviderSearchSnapshot(
            provider=self.provider_name,
            request_key=f"search:{title.casefold()}",
            shows=(
                ProviderShow(
                    identity=ProviderIdentity(self.provider_name, "show-17"),
                    title="Fixture Harbor",
                    year=2026,
                ),
            ),
        )

    def episode_catalog(
        self,
        show_identity: ProviderIdentity,
    ) -> ProviderEpisodeCatalog:
        self.catalog_calls.append(show_identity)
        return ProviderEpisodeCatalog(
            provider=self.provider_name,
            request_key=f"episodes:{show_identity.value}",
            show_identity=show_identity,
            episodes=(
                ProviderEpisode(
                    provider_identity=ProviderIdentity(self.provider_name, "ep-a"),
                    season=1,
                    number=1,
                    title="Arrival",
                ),
                ProviderEpisode(
                    provider_identity=ProviderIdentity(self.provider_name, "ep-b"),
                    season=1,
                    number=2,
                    title="Departure",
                ),
            ),
        )


def test_provider_identity_is_namespaced_and_normalized() -> None:
    identity = ProviderIdentity("Fixture-Provider", "show-17")

    assert identity.provider == "fixture-provider"
    assert identity.value == "show-17"
    assert identity.key == "fixture-provider:show-17"


def test_canonical_show_keeps_tvmaze_compatibility_without_storing_bare_identity() -> (
    None
):
    show = CanonicalShow(
        source_key="example",
        tvmaze_id=4242,
        title="Example",
        year=2024,
        numbering_mode=NumberingMode.AIRED,
    )

    assert show.provider_identity == ProviderIdentity("tvmaze", "4242")
    assert show.provider == "tvmaze"
    assert show.provider_id == "4242"
    assert show.tvmaze_id == 4242


def test_generic_canonical_show_does_not_require_tvmaze_identity() -> None:
    show = CanonicalShow(
        source_key="fixture-harbor",
        provider_identity=ProviderIdentity("fixture", "show-17"),
        title="Fixture Harbor",
        year=2026,
        numbering_mode=NumberingMode.AIRED,
    )

    assert show.provider_identity.key == "fixture:show-17"
    with pytest.raises(ValueError, match="expected 'tvmaze'"):
        _ = show.tvmaze_id


def test_tvmaze_adapter_normalizes_raw_search_and_catalog_once(tmp_path: Path) -> None:
    getter = RecordingGetter(
        {
            TVMAZE_SEARCH_URL: [
                {
                    "score": 1.0,
                    "show": {
                        "id": 101,
                        "name": "Fixture Harbor",
                        "premiered": "2026-03-04",
                    },
                }
            ],
            TVMAZE_EPISODES_URL.format(tvmaze_id=101): [
                {"id": 1001, "season": 1, "number": 1, "name": "Arrival"},
            ],
        }
    )
    adapter = TvmazeProviderAdapter(TvmazeCatalogCache(tmp_path / "cache"), getter)

    search = adapter.search_shows("Fixture Harbor")
    catalog = adapter.episode_catalog(search.shows[0].identity)

    assert search.provider == "tvmaze"
    assert search.snapshot_identity.startswith("tvmaze:search:")
    assert search.shows == (
        ProviderShow(
            identity=ProviderIdentity("tvmaze", "101"),
            title="Fixture Harbor",
            year=2026,
        ),
    )
    assert catalog.show_identity == ProviderIdentity("tvmaze", "101")
    assert catalog.episodes[0].identity == ProviderIdentity("tvmaze", "1001")
    assert catalog.episodes[0].tvmaze_episode_id == 1001
    assert [call[0] for call in getter.calls] == [
        TVMAZE_SEARCH_URL,
        TVMAZE_EPISODES_URL.format(tvmaze_id=101),
    ]


def test_show_resolution_consumes_generic_provider_models() -> None:
    provider = FixtureProvider()

    resolution = resolve_show_group_with_provider(
        "fixture-harbor",
        (ParseResult(series_hint="Fixture Harbor", year=2026),),
        load_overrides(),
        provider,
    )

    assert resolution.status is ResolutionStatus.MATCHED
    assert resolution.show is not None
    assert resolution.show.provider_identity == ProviderIdentity("fixture", "show-17")
    assert resolution.evidence.candidates[0].provider_identity == ProviderIdentity(
        "fixture", "show-17"
    )
    assert "provider-snapshot:fixture:search:fixture harbor" in (
        resolution.evidence.reasons
    )
    assert provider.search_calls == ["Fixture Harbor"]


def test_episode_assignment_consumes_generic_provider_catalog() -> None:
    provider = FixtureProvider()
    show = CanonicalShow(
        source_key="fixture-harbor",
        provider_identity=ProviderIdentity("fixture", "show-17"),
        title="Fixture Harbor",
        year=2026,
        numbering_mode=NumberingMode.AIRED,
    )

    result = assign_episode_group_with_provider(
        show,
        (
            SourceEpisodeInput(
                "episode.mkv",
                ParseResult(season=1, episodes=(1,)),
            ),
        ),
        provider,
    )

    assert result.status is AssignmentStatus.MATCHED
    assert result.assignments[0].episodes[0].identity == ProviderIdentity(
        "fixture", "ep-a"
    )
    assert "catalog-match:S01E01->fixture:ep-a" in (
        result.assignments[0].evidence.reasons
    )
    assert provider.catalog_calls == [ProviderIdentity("fixture", "show-17")]


def test_generic_override_identity_requires_no_provider_specific_field(
    tmp_path: Path,
) -> None:
    path = tmp_path / "overrides.toml"
    path.write_text(
        """
schema_version = 2

[[shows]]
key = "fixture-harbor"
provider = "fixture"
provider_id = "show-17"
aliases = ["Fixture Harbor"]
year = 2026
numbering_mode = "aired"
""".strip(),
        encoding="utf-8",
    )

    override = load_overrides(path).get("fixture-harbor")

    assert override is not None
    assert override.provider_identity == ProviderIdentity("fixture", "show-17")
    assert override.tvmaze_id is None


def test_explicit_generic_override_resolves_without_provider_search(
    tmp_path: Path,
) -> None:
    path = tmp_path / "overrides.toml"
    path.write_text(
        """
schema_version = 2

[[shows]]
key = "fixture-harbor"
provider = "fixture"
provider_id = "show-17"
aliases = ["Fixture Harbor"]
year = 2026
numbering_mode = "aired"
""".strip(),
        encoding="utf-8",
    )
    provider = FixtureProvider()

    resolution = resolve_show_group_with_provider(
        "fixture-harbor",
        (ParseResult(series_hint="Fixture Harbor", year=2026),),
        load_overrides(path),
        provider,
    )

    assert resolution.status is ResolutionStatus.MATCHED
    assert resolution.show is not None
    assert resolution.show.provider_identity.key == "fixture:show-17"
    assert provider.search_calls == []


def test_tvmaze_adapter_rejects_foreign_show_identity_without_http(
    tmp_path: Path,
) -> None:
    getter = RecordingGetter({})
    adapter = TvmazeProviderAdapter(TvmazeCatalogCache(tmp_path / "cache"), getter)

    catalog = adapter.episode_catalog(ProviderIdentity("fixture", "show-17"))

    assert not catalog.resolved
    assert catalog.unresolved_reason == "provider-identity-not-supported:fixture"
    assert getter.calls == []
