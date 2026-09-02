from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

import pytest

from jellyfin_show_organizer.decision_hash import stable_decision_hash
from jellyfin_show_organizer.filename_parser import parse_video_path
from jellyfin_show_organizer.models import (
    CanonicalShow,
    MatchEvidence,
    NumberingMode,
    OrganizerPlan,
    ParseResult,
    PlanRecord,
    ProviderIdentity,
    SourceFile,
    SourceFingerprint,
    TerminalStatus,
)
from jellyfin_show_organizer.overrides import load_overrides
from jellyfin_show_organizer.parenthetical_aliases import parenthetical_show_aliases
from jellyfin_show_organizer.providers import (
    ProviderEpisodeCatalog,
    ProviderSearchSnapshot,
    ProviderShow,
)
from jellyfin_show_organizer.schema import plan_to_manifest, stable_plan_hash
from jellyfin_show_organizer.show_resolver import (
    ResolutionStatus,
    resolve_show_group_with_provider,
)

pytestmark = pytest.mark.local

ALPHA = ProviderIdentity("fixture", "alpha")
BETA = ProviderIdentity("fixture", "beta")


class AliasProvider:
    provider_name = "fixture"

    def __init__(
        self,
        searches: Mapping[str, ProviderSearchSnapshot],
    ) -> None:
        self.searches = dict(searches)
        self.search_calls: list[str] = []

    def search_shows(self, title: str) -> ProviderSearchSnapshot:
        self.search_calls.append(title)
        return self.searches.get(
            title,
            ProviderSearchSnapshot(
                provider="fixture",
                request_key=f"search:{title}",
                cache_snapshot_id=f"empty:{title}",
                shows=(),
            ),
        )

    def episode_catalog(self, show_identity: ProviderIdentity) -> ProviderEpisodeCatalog:
        return ProviderEpisodeCatalog(
            provider="fixture",
            request_key=f"episodes:{show_identity.value}",
            cache_snapshot_id=f"episodes:{show_identity.value}:v1",
            show_identity=show_identity,
            episodes=(),
        )


def _snapshot(title: str, *shows: ProviderShow) -> ProviderSearchSnapshot:
    return ProviderSearchSnapshot(
        provider="fixture",
        request_key=f"search:{title}",
        cache_snapshot_id=f"snapshot:{title}",
        shows=tuple(shows),
    )


def _failure(title: str) -> ProviderSearchSnapshot:
    return ProviderSearchSnapshot(
        provider="fixture",
        request_key=f"search:{title}",
        cache_snapshot_id=f"failure:{title}",
        shows=(),
        unresolved_reason="fixture-provider-failure",
    )


def _parse() -> ParseResult:
    return parse_video_path("Primary Series (Alternate Series) S01E01.mkv")


def test_parser_preserves_one_safe_parenthetical_alias_pair() -> None:
    parsed = _parse()

    assert parsed.series_hint == "Primary Series Alternate Series"
    assert parsed.series_aliases == ("Primary Series", "Alternate Series")
    assert parsed.season == 1
    assert parsed.episodes == (1,)


def test_parenthetical_alias_extractor_rejects_non_title_groups() -> None:
    assert parenthetical_show_aliases("Example Series (2024) S01E01") == ()
    assert parenthetical_show_aliases("Example Series (12) S01E01") == ()
    assert parenthetical_show_aliases("Example Series (English Dub) S01E01") == ()
    assert parenthetical_show_aliases("Example Series (1080p HEVC) S01E01") == ()
    assert parenthetical_show_aliases("Example (First Title) (Second Title) S01E01") == ()


def test_parentheses_after_episode_coordinate_are_not_show_aliases() -> None:
    parsed = parse_video_path("Example Series S01E01 (Episode Story Name).mkv")

    assert parsed.series_aliases == ()
    assert parsed.title_hint == "Episode Story Name"


def test_parenthetical_alias_search_resolves_one_provider_identity() -> None:
    alpha = ProviderShow(ALPHA, "Primary Series", 2024)
    provider = AliasProvider(
        {
            "Primary Series": _snapshot("Primary Series", alpha),
            "Alternate Series": _snapshot("Alternate Series", alpha),
        }
    )

    result = resolve_show_group_with_provider(
        "Primary Series (Alternate Series)",
        (_parse(),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == ALPHA
    assert "parenthetical-alias-search:complete" in result.evidence.reasons
    assert "parenthetical-alias-search-winner:fixture:alpha" in result.evidence.reasons


def test_parenthetical_alias_search_keeps_different_exact_candidates_blocked() -> None:
    provider = AliasProvider(
        {
            "Primary Series": _snapshot(
                "Primary Series", ProviderShow(ALPHA, "Primary Series", 2024)
            ),
            "Alternate Series": _snapshot(
                "Alternate Series", ProviderShow(BETA, "Alternate Series", 2024)
            ),
        }
    )

    result = resolve_show_group_with_provider(
        "Primary Series (Alternate Series)",
        (_parse(),),
        load_overrides(),
        provider,
    )

    assert result.status is not ResolutionStatus.MATCHED
    assert result.show is None
    assert "parenthetical-alias-search:conflicting-results" in result.evidence.reasons


def test_parenthetical_alias_search_rejects_conflicting_metadata_for_same_identity() -> None:
    provider = AliasProvider(
        {
            "Primary Series": _snapshot(
                "Primary Series", ProviderShow(ALPHA, "Primary Series", 2024)
            ),
            "Alternate Series": _snapshot(
                "Alternate Series", ProviderShow(ALPHA, "Primary Series", 2025)
            ),
        }
    )

    result = resolve_show_group_with_provider(
        "Primary Series (Alternate Series)",
        (_parse(),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert (
        "parenthetical-alias-search:conflicting-candidate-metadata"
        in result.evidence.reasons
    )


def test_parenthetical_alias_provider_failure_remains_unresolved() -> None:
    provider = AliasProvider(
        {
            "Primary Series": _snapshot(
                "Primary Series", ProviderShow(ALPHA, "Primary Series", 2024)
            ),
            "Alternate Series": _failure("Alternate Series"),
        }
    )

    result = resolve_show_group_with_provider(
        "Primary Series (Alternate Series)",
        (_parse(),),
        load_overrides(),
        provider,
    )

    assert result.status is ResolutionStatus.UNRESOLVED
    assert result.show is None
    assert "parenthetical-alias-search:indeterminate" in result.evidence.reasons


def test_parenthetical_alias_resolution_is_input_order_deterministic() -> None:
    alpha = ProviderShow(ALPHA, "Primary Series", 2024)
    searches = {
        "Primary Series": _snapshot("Primary Series", alpha),
        "Alternate Series": _snapshot("Alternate Series", alpha),
    }
    first_parse = _parse()
    second_parse = replace(first_parse, episodes=(2,))

    first = resolve_show_group_with_provider(
        "Primary Series (Alternate Series)",
        (first_parse, second_parse),
        load_overrides(),
        AliasProvider(searches),
    )
    second = resolve_show_group_with_provider(
        "Primary Series (Alternate Series)",
        (second_parse, first_parse),
        load_overrides(),
        AliasProvider(searches),
    )

    assert first == second


def _plan(parse: ParseResult) -> OrganizerPlan:
    return OrganizerPlan(
        schema_version=1,
        overrides_version=1,
        records=(
            PlanRecord(
                source=SourceFile(
                    relative_path="Example/episode.mkv",
                    extension=".mkv",
                    fingerprint=SourceFingerprint(size=10, mtime_ns=20),
                ),
                status=TerminalStatus.MATCHED,
                parse=parse,
                show=CanonicalShow(
                    source_key="Example",
                    provider_identity=ProviderIdentity.tvmaze(123),
                    title="Example",
                    year=2024,
                    numbering_mode=NumberingMode.AIRED,
                ),
                evidence=MatchEvidence(method="fixture", confidence=1.0),
                destination="Example (2024)/Season 01/Example (2024) S01E01.mkv",
                operation_group_id="op-example",
            ),
        ),
    )


def test_transient_aliases_do_not_change_plan_schema_or_decision_hash() -> None:
    plain = ParseResult(series_hint="Example", season=1, episodes=(1,))
    aliased = replace(plain, series_aliases=("Example Series", "Alternate Series"))

    plain_plan = _plan(plain)
    aliased_plan = _plan(aliased)
    manifest = plan_to_manifest(aliased_plan)

    assert "series_aliases" not in manifest["records"][0]["parse"]
    assert stable_plan_hash(plain_plan) == stable_plan_hash(aliased_plan)
    assert stable_decision_hash(plain_plan) == stable_decision_hash(aliased_plan)
