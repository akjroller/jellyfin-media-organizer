from __future__ import annotations

import pytest

from jellyfin_show_organizer.models import (
    CandidateEvidence,
    ParseResult,
    ProviderIdentity,
)
from jellyfin_show_organizer.providers import ProviderEpisode, ProviderEpisodeCatalog
from jellyfin_show_organizer.show_structural_evidence import (
    aired_catalog_rescue,
    structural_title_score,
    token_merge_queries,
)

pytestmark = pytest.mark.local


def test_token_merge_queries_are_limited_to_exactly_two_tokens() -> None:
    assert token_merge_queries("Example Hero") == ("ExampleHero",)
    assert token_merge_queries("Example Hero New") == ()
    assert token_merge_queries("Example") == ()


def test_structural_initialism_is_strong_but_compaction_is_not_direct_match() -> None:
    score, reasons = structural_title_score(
        ("example fleet tng",),
        "Example Fleet The Next Generation",
    )
    assert score == 0.90
    assert reasons == ("token-initialism-equivalent",)

    compact_score, compact_reasons = structural_title_score(
        ("example hero",),
        "ExampleHero Adventures",
    )
    assert compact_score == 0.78
    assert compact_reasons == ("compacted-source-prefix",)


def test_provider_colon_subtitle_is_structural_evidence_not_a_direct_match() -> None:
    score, reasons = structural_title_score(
        ("example frontier",),
        "Example Frontier: The Long Journey",
    )

    assert score == 0.78
    assert reasons == ("provider-subtitle-prefix",)
    assert structural_title_score(("example",), "Example: The Long Journey") == (
        None,
        (),
    )
    assert structural_title_score(
        ("example frontier",),
        "Example Frontier Revisited",
    ) == (None, ())


def test_provider_ampersand_is_complete_title_conjunction_equivalence() -> None:
    score, reasons = structural_title_score(
        ("example heroes and villains",),
        "Example Heroes & Villains",
    )

    assert score == 0.90
    assert reasons == ("provider-ampersand-equivalent",)
    assert structural_title_score(
        ("example heroes and villains",),
        "Example Heroes & Strangers",
    ) == (None, ())
    assert structural_title_score(
        ("example heroes villains",),
        "Example Heroes & Villains",
    ) == (None, ())


def test_air_catalog_rescue_needs_multiple_coordinates() -> None:
    identity = ProviderIdentity("fixture", "one")
    ranked = (
        CandidateEvidence(
            provider_identity=identity,
            title="Example Academy",
            score=0.70,
            reasons=("fixture",),
        ),
    )

    class Provider:
        provider_name = "fixture"

        def search_shows(self, title: str):
            raise AssertionError(title)

        def episode_catalog(self, show_identity: ProviderIdentity):
            assert show_identity == identity
            return ProviderEpisodeCatalog(
                provider="fixture",
                request_key="episodes:one",
                cache_snapshot_id="catalog:one",
                show_identity=identity,
                episodes=(
                    ProviderEpisode(
                        identity=ProviderIdentity("fixture", "one-1"),
                        season=2,
                        number=1,
                        title="Return",
                    ),
                ),
            )

    result = aired_catalog_rescue(
        Provider(),
        (ParseResult(series_hint="Example Academy New", season=2, episodes=(1,)),),
        ranked,
    )
    assert result is None
