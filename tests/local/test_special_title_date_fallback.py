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

SHOW_ID = ProviderIdentity("fixture", "show-1")
SHOW = CanonicalShow(
    source_key="Fabricated Series",
    provider_identity=SHOW_ID,
    title="Fabricated Series",
    year=2024,
    numbering_mode=NumberingMode.SPECIAL,
)


class FixtureProvider:
    provider_name = "fixture"

    def __init__(self, episodes: tuple[ProviderEpisode, ...]) -> None:
        self.episodes = episodes
        self.catalog_calls = 0

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        return ProviderSearchSnapshot(
            provider="fixture",
            request_key=f"search:{title}",
            cache_snapshot_id=f"search:{title}:empty",
            shows=(),
        )

    def episode_catalog(self, show_identity: ProviderIdentity) -> ProviderEpisodeCatalog:
        assert show_identity == SHOW_ID
        self.catalog_calls += 1
        return ProviderEpisodeCatalog(
            provider="fixture",
            request_key="episodes:show-1",
            cache_snapshot_id="fixture-catalog-v1",
            show_identity=SHOW_ID,
            episodes=self.episodes,
        )


def _special(
    key: str,
    *,
    number: int,
    title: str,
    airdate: str | None = None,
) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", key),
        season=0,
        number=number,
        title=title,
        airdate=airdate,
        episode_type="special",
    )


def _regular(
    key: str,
    *,
    number: int,
    title: str,
    airdate: str | None = None,
) -> ProviderEpisode:
    return ProviderEpisode(
        identity=ProviderIdentity("fixture", key),
        season=1,
        number=number,
        title=title,
        airdate=airdate,
        episode_type="regular",
    )


def _source(
    *,
    kind: str = "ova",
    requested_number: int = 1,
    title_hint: str | None = None,
    source_date: str | None = None,
) -> SourceEpisodeInput:
    date_suffix = f" - {source_date}" if source_date is not None else ""
    return SourceEpisodeInput(
        source_key=(
            f"Fabricated Series/Fabricated Series {kind.upper()}{requested_number:02d}"
            f"{date_suffix}.mkv"
        ),
        parse=ParseResult(
            special_kind=kind,
            special_episode=requested_number,
            title_hint=title_hint,
        ),
    )


def _assignment(
    source: SourceEpisodeInput,
    episodes: tuple[ProviderEpisode, ...],
):
    provider = FixtureProvider(episodes)
    result = assign_episode_group_with_provider(SHOW, (source,), provider)
    return result.assignments[0], provider


def test_missing_ova_number_recovers_from_unique_exact_title() -> None:
    source = _source(title_hint="Harbor Day")
    assignment, provider = _assignment(
        source,
        (
            _special("special-5", number=5, title="Harbor Day"),
            _special("special-6", number=6, title="Moonlight"),
        ),
    )

    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].identity == ProviderIdentity("fixture", "special-5")
    assert "special-fallback-title-match:harbor day" in assignment.evidence.reasons
    assert "special-fallback-match:OVA1->S00E05" in assignment.evidence.reasons
    assert provider.catalog_calls == 2


def test_missing_oad_number_recovers_from_unique_source_date() -> None:
    source = _source(kind="oad", source_date="2024-02-03")
    assignment, _provider = _assignment(
        source,
        (
            _special(
                "special-5",
                number=5,
                title="Provider Title",
                airdate="2024-02-03",
            ),
            _special(
                "special-6",
                number=6,
                title="Other",
                airdate="2024-03-01",
            ),
        ),
    )

    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].identity == ProviderIdentity("fixture", "special-5")
    assert "special-fallback-date-match:2024-02-03" in assignment.evidence.reasons


def test_title_and_date_can_agree_on_same_non_regular_entry() -> None:
    source = _source(title_hint="Harbor Day", source_date="2024-02-03")
    assignment, _provider = _assignment(
        source,
        (
            _special(
                "special-5",
                number=5,
                title="Harbor Day",
                airdate="2024-02-03",
            ),
        ),
    )

    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].identity == ProviderIdentity("fixture", "special-5")
    assert "special-fallback-title-match:harbor day" in assignment.evidence.reasons
    assert "special-fallback-date-match:2024-02-03" in assignment.evidence.reasons


def test_conflicting_unique_title_and_date_evidence_stays_suspicious() -> None:
    source = _source(title_hint="Harbor Day", source_date="2024-02-03")
    assignment, _provider = _assignment(
        source,
        (
            _special(
                "title-special",
                number=5,
                title="Harbor Day",
                airdate="2024-03-01",
            ),
            _special(
                "date-special",
                number=6,
                title="Other",
                airdate="2024-02-03",
            ),
        ),
    )

    assert assignment.status is AssignmentStatus.SUSPICIOUS
    assert assignment.episodes == ()
    assert any(
        reason.startswith("special-fallback-evidence-conflict:")
        for reason in assignment.evidence.reasons
    )


def test_ambiguous_exact_title_stays_suspicious() -> None:
    source = _source(title_hint="Harbor Day")
    assignment, _provider = _assignment(
        source,
        (
            _special("special-5", number=5, title="Harbor Day"),
            _special("special-6", number=6, title="Harbor Day"),
        ),
    )

    assert assignment.status is AssignmentStatus.SUSPICIOUS
    assert "special-fallback-title-ambiguous:harbor day" in assignment.evidence.reasons


def test_ambiguous_source_date_stays_suspicious() -> None:
    source = _source(source_date="2024-02-03")
    assignment, _provider = _assignment(
        source,
        (
            _special(
                "special-5", number=5, title="One", airdate="2024-02-03"
            ),
            _special(
                "special-6", number=6, title="Two", airdate="2024-02-03"
            ),
        ),
    )

    assert assignment.status is AssignmentStatus.SUSPICIOUS
    assert "special-fallback-date-ambiguous:2024-02-03" in assignment.evidence.reasons


def test_regular_episode_is_never_used_by_special_fallback() -> None:
    source = _source(title_hint="Harbor Day", source_date="2024-02-03")
    assignment, _provider = _assignment(
        source,
        (
            _regular(
                "regular-1",
                number=1,
                title="Harbor Day",
                airdate="2024-02-03",
            ),
            _special(
                "unrelated-special",
                number=5,
                title="Moonlight",
                airdate="2024-03-01",
            ),
        ),
    )

    assert assignment.status is AssignmentStatus.UNRESOLVED
    assert assignment.episodes == ()
    assert "missing-special-catalog-entry:1" in assignment.evidence.reasons


def test_missing_number_without_fallback_evidence_remains_unresolved() -> None:
    source = _source()
    assignment, provider = _assignment(
        source,
        (_special("special-5", number=5, title="Harbor Day"),),
    )

    assert assignment.status is AssignmentStatus.UNRESOLVED
    assert "missing-special-catalog-entry:1" in assignment.evidence.reasons
    assert provider.catalog_calls == 1


def test_existing_exact_special_number_match_remains_authoritative() -> None:
    source = _source(title_hint="Different Title", source_date="2024-02-03")
    assignment, provider = _assignment(
        source,
        (
            _special("exact-special", number=1, title="Exact", airdate="2024-03-01"),
            _special(
                "fallback-looking",
                number=5,
                title="Different Title",
                airdate="2024-02-03",
            ),
        ),
    )

    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].identity == ProviderIdentity("fixture", "exact-special")
    assert assignment.evidence.method == "episode-catalog"
    assert provider.catalog_calls == 1


def test_special_fallback_is_provider_catalog_order_deterministic() -> None:
    source = _source(title_hint="Harbor Day")
    episodes = (
        _special("special-5", number=5, title="Harbor Day"),
        _special("special-6", number=6, title="Moonlight"),
    )

    forward, _provider = _assignment(source, episodes)
    reverse, _provider = _assignment(source, tuple(reversed(episodes)))

    assert forward.status == reverse.status == AssignmentStatus.MATCHED
    assert forward.episodes == reverse.episodes
    assert forward.evidence == reverse.evidence
