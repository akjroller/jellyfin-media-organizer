from __future__ import annotations

import pytest

from jellyfin_show_organizer.episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group_with_provider,
)
from jellyfin_show_organizer.mixed_episode_assignment import _source_dates
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
REGULAR_ID = ProviderIdentity("fixture", "regular-1")
SPECIAL_A_ID = ProviderIdentity("fixture", "special-a")
SPECIAL_B_ID = ProviderIdentity("fixture", "special-b")
SHOW = CanonicalShow(
    source_key="Fabricated Series",
    provider_identity=SHOW_ID,
    title="Fabricated Series",
    year=2001,
    numbering_mode=NumberingMode.ABSOLUTE,
)


class FixtureProvider:
    provider_name = "fixture"

    def __init__(self, episodes: tuple[ProviderEpisode, ...]) -> None:
        self.catalog = ProviderEpisodeCatalog(
            provider="fixture",
            request_key="episodes:show-1",
            cache_snapshot_id="fixture-catalog-v1",
            show_identity=SHOW_ID,
            episodes=episodes,
        )

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        return ProviderSearchSnapshot(
            provider="fixture",
            request_key=f"search:{title}",
            cache_snapshot_id=f"search:{title}:empty",
            shows=(),
        )

    def episode_catalog(
        self, show_identity: ProviderIdentity
    ) -> ProviderEpisodeCatalog:
        assert show_identity == SHOW_ID
        return self.catalog


def _regular(*, airdate: str | None = "2001-03-30") -> ProviderEpisode:
    return ProviderEpisode(
        identity=REGULAR_ID,
        season=1,
        number=1,
        title="First Regular",
        airdate=airdate,
        episode_type="regular",
    )


def _special(
    identity: ProviderIdentity,
    title: str,
    *,
    airdate: str = "1998-11-27",
    number: int = 1,
) -> ProviderEpisode:
    return ProviderEpisode(
        identity=identity,
        season=0,
        number=number,
        title=title,
        airdate=airdate,
        episode_type="special",
    )


def _short_source(
    *,
    source_date: str = "1998-11-27",
    title_hint: str | None = None,
    context: str = "Shorts",
) -> SourceEpisodeInput:
    return SourceEpisodeInput(
        source_key=(
            f"Fabricated Series/{context}/Fabricated Series - 001 - {source_date}.mkv"
        ),
        parse=ParseResult(absolute_episode=1, title_hint=title_hint),
    )


def _regular_source() -> SourceEpisodeInput:
    return SourceEpisodeInput(
        source_key="Fabricated Series/Fabricated Series - 001.mkv",
        parse=ParseResult(absolute_episode=1),
    )


def _human_aired_source(
    aired_text: str,
    *,
    context: str = "Shorts",
) -> SourceEpisodeInput:
    return SourceEpisodeInput(
        source_key=(
            "Fabricated Series/"
            f"{context}/Fabricated Series - 001 - Early Short ({aired_text}).mkv"
        ),
        parse=ParseResult(absolute_episode=1, title_hint="Early Short"),
    )


def _by_source(result):
    return {assignment.source_key: assignment for assignment in result.assignments}


def test_pre_series_short_without_provider_special_stays_unresolved() -> None:
    short = _short_source()
    regular = _regular_source()
    result = assign_episode_group_with_provider(
        SHOW,
        (short, regular),
        FixtureProvider((_regular(),)),
    )
    assignments = _by_source(result)

    assert assignments[short.source_key].status is AssignmentStatus.UNRESOLVED
    assert assignments[short.source_key].episodes == ()
    assert "missing-pre-premiere-catalog-entry:1998-11-27" in (
        assignments[short.source_key].evidence.reasons
    )
    assert assignments[regular.source_key].status is AssignmentStatus.MATCHED
    assert assignments[regular.source_key].episodes[0].identity == REGULAR_ID


def test_human_aired_date_activates_pre_premiere_guard() -> None:
    short = _human_aired_source("aired Sep 4 98")
    special = _special(SPECIAL_A_ID, "Early Short", airdate="1998-09-04")
    result = assign_episode_group_with_provider(
        SHOW,
        (short,),
        FixtureProvider((special, _regular())),
    )
    assignment = result.assignments[0]

    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].identity == SPECIAL_A_ID
    assert "pre-premiere-source-date:1998-09-04" in assignment.evidence.reasons


def test_full_month_four_digit_human_aired_date_is_supported() -> None:
    short = _human_aired_source("aired September 4, 1998")
    special = _special(SPECIAL_A_ID, "Early Short", airdate="1998-09-04")
    result = assign_episode_group_with_provider(
        SHOW,
        (short,),
        FixtureProvider((special, _regular())),
    )

    assert result.assignments[0].status is AssignmentStatus.MATCHED
    assert result.assignments[0].episodes[0].identity == SPECIAL_A_ID


def test_human_aired_two_digit_year_uses_fixed_pivot() -> None:
    assert _source_dates("aired Jan 2 49") == ("2049-01-02",)
    assert _source_dates("aired Jan 2 50") == ("1950-01-02",)


def test_invalid_human_aired_date_is_ignored() -> None:
    source = _human_aired_source("aired Feb 30 98")
    result = assign_episode_group_with_provider(
        SHOW,
        (source,),
        FixtureProvider((_regular(),)),
    )

    assert result.assignments[0].status is AssignmentStatus.MATCHED
    assert result.assignments[0].episodes[0].identity == REGULAR_ID
    assert result.assignments[0].evidence.method == "episode-catalog"


def test_month_text_without_aired_marker_is_not_a_date_signal() -> None:
    source = _human_aired_source("Sep 4 98")
    result = assign_episode_group_with_provider(
        SHOW,
        (source,),
        FixtureProvider((_regular(),)),
    )

    assert result.assignments[0].status is AssignmentStatus.MATCHED
    assert result.assignments[0].episodes[0].identity == REGULAR_ID
    assert result.assignments[0].evidence.method == "episode-catalog"


def test_human_aired_date_without_special_context_keeps_normal_assignment() -> None:
    source = SourceEpisodeInput(
        source_key=(
            "Fabricated Series/Archive/"
            "Fabricated Series - 001 - Opening Story (aired Sep 4 98).mkv"
        ),
        parse=ParseResult(absolute_episode=1, title_hint="Opening Story"),
    )
    result = assign_episode_group_with_provider(
        SHOW,
        (source,),
        FixtureProvider((_regular(),)),
    )

    assert result.assignments[0].status is AssignmentStatus.MATCHED
    assert result.assignments[0].episodes[0].identity == REGULAR_ID
    assert result.assignments[0].evidence.method == "episode-catalog"


def test_pre_series_short_can_match_unique_non_regular_catalog_entry() -> None:
    short = _short_source()
    regular = _regular_source()
    special = _special(SPECIAL_A_ID, "Early Short")
    result = assign_episode_group_with_provider(
        SHOW,
        (short, regular),
        FixtureProvider((special, _regular())),
    )
    assignments = _by_source(result)

    assert assignments[short.source_key].status is AssignmentStatus.MATCHED
    assert assignments[short.source_key].episodes[0].identity == SPECIAL_A_ID
    assert assignments[short.source_key].evidence.method == "pre-premiere-catalog"
    assert assignments[regular.source_key].status is AssignmentStatus.MATCHED
    assert assignments[regular.source_key].episodes[0].identity == REGULAR_ID


def test_same_date_pre_series_specials_remain_suspicious_without_title_tiebreak() -> (
    None
):
    short = _short_source()
    result = assign_episode_group_with_provider(
        SHOW,
        (short,),
        FixtureProvider(
            (
                _special(SPECIAL_A_ID, "Early Short A", number=1),
                _special(SPECIAL_B_ID, "Early Short B", number=2),
                _regular(),
            )
        ),
    )
    assignment = result.assignments[0]

    assert assignment.status is AssignmentStatus.SUSPICIOUS
    assert assignment.episodes == ()
    assert "ambiguous-pre-premiere-catalog-entry:1998-11-27" in (
        assignment.evidence.reasons
    )


def test_exact_title_hint_can_break_same_date_special_tie() -> None:
    short = _short_source(title_hint="Early Short B")
    result = assign_episode_group_with_provider(
        SHOW,
        (short,),
        FixtureProvider(
            (
                _special(SPECIAL_A_ID, "Early Short A", number=1),
                _special(SPECIAL_B_ID, "Early Short B", number=2),
                _regular(),
            )
        ),
    )
    assignment = result.assignments[0]

    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].identity == SPECIAL_B_ID
    assert "pre-premiere-title-match:early short b" in assignment.evidence.reasons


def test_old_date_without_special_context_does_not_change_normal_assignment() -> None:
    source = _short_source(context="Archive")
    result = assign_episode_group_with_provider(
        SHOW,
        (source,),
        FixtureProvider((_regular(),)),
    )
    assignment = result.assignments[0]

    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].identity == REGULAR_ID
    assert assignment.evidence.method == "episode-catalog"


def test_special_context_without_date_does_not_change_normal_assignment() -> None:
    source = SourceEpisodeInput(
        source_key="Fabricated Series/Shorts/Fabricated Series - 001.mkv",
        parse=ParseResult(absolute_episode=1),
    )
    result = assign_episode_group_with_provider(
        SHOW,
        (source,),
        FixtureProvider((_regular(),)),
    )
    assignment = result.assignments[0]

    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].identity == REGULAR_ID


def test_same_year_date_is_not_pre_premiere_when_regular_dates_are_missing() -> None:
    source = _short_source(source_date="2001-01-01")
    result = assign_episode_group_with_provider(
        SHOW,
        (source,),
        FixtureProvider((_regular(airdate=None),)),
    )
    assignment = result.assignments[0]

    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].identity == REGULAR_ID


def test_earlier_year_fallback_blocks_when_regular_dates_are_missing() -> None:
    source = _short_source(source_date="2000-01-01")
    result = assign_episode_group_with_provider(
        SHOW,
        (source,),
        FixtureProvider((_regular(airdate=None),)),
    )
    assignment = result.assignments[0]

    assert assignment.status is AssignmentStatus.UNRESOLVED
    assert "pre-premiere-before-show-year:2001" in assignment.evidence.reasons


def test_current_series_absolute_episode_remains_unchanged() -> None:
    source = SourceEpisodeInput(
        source_key="Fabricated Series/Fabricated Series - 001 - 2001-03-30.mkv",
        parse=ParseResult(absolute_episode=1),
    )
    result = assign_episode_group_with_provider(
        SHOW,
        (source,),
        FixtureProvider((_regular(),)),
    )
    assignment = result.assignments[0]

    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].identity == REGULAR_ID


def test_pre_premiere_guard_is_input_order_deterministic() -> None:
    short = _short_source()
    regular = _regular_source()
    provider = FixtureProvider((_regular(),))

    forward = _by_source(
        assign_episode_group_with_provider(SHOW, (short, regular), provider)
    )
    reverse = _by_source(
        assign_episode_group_with_provider(SHOW, (regular, short), provider)
    )

    assert forward.keys() == reverse.keys()
    for source_key in forward:
        assert forward[source_key].status == reverse[source_key].status
        assert forward[source_key].episodes == reverse[source_key].episodes
        assert forward[source_key].evidence == reverse[source_key].evidence
