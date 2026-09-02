from __future__ import annotations

from pathlib import Path

SOURCE = Path("jellyfin_show_organizer/show_structural_evidence.py")
TESTS = Path("tests/local/test_show_resolver_mixed_segment_tiebreak.py")


def patch_source() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    start = text.index("def _mixed_segment_title_rescue(")
    end = text.index("\n\ndef aired_catalog_rescue(", start)
    replacement = '''def _mixed_segment_title_rescue(
    provider: MetadataProvider,
    parses: tuple[ParseResult, ...],
    ranked: tuple[CandidateEvidence, ...],
) -> StructuralCatalogDecision | None:
    titles = _mixed_segment_title_observations(parses)
    if len(titles) < _MIN_TITLE_OBSERVATIONS:
        return None

    match_counts: dict[ProviderIdentity, int | None] = {}
    exact_compatibility: dict[ProviderIdentity, bool] = {}
    partial_qualification: dict[ProviderIdentity, bool] = {}
    extra_reasons: dict[ProviderIdentity, tuple[str, ...]] = {}
    for candidate in sorted(ranked, key=lambda item: item.provider_identity.key):
        catalog = provider.episode_catalog(candidate.provider_identity)
        request_reason = f"mixed-segment-title-request:{catalog.request_key}"
        if not catalog.resolved:
            match_counts[candidate.provider_identity] = None
            exact_compatibility[candidate.provider_identity] = False
            partial_qualification[candidate.provider_identity] = False
            extra_reasons[candidate.provider_identity] = (
                request_reason,
                "mixed-segment-title-rescue:indeterminate-catalog",
            )
            continue
        if catalog.errors:
            match_counts[candidate.provider_identity] = None
            exact_compatibility[candidate.provider_identity] = False
            partial_qualification[candidate.provider_identity] = False
            extra_reasons[candidate.provider_identity] = (
                request_reason,
                *(f"mixed-segment-title-error:{error}" for error in catalog.errors),
            )
            continue

        by_title: dict[str, list[ProviderIdentity]] = {}
        for episode in catalog.episodes:
            title = _normalize(episode.title)
            if title:
                by_title.setdefault(title, []).append(episode.identity)

        selected: list[ProviderIdentity] = []
        missing = 0
        ambiguous = 0
        reasons: list[str] = [request_reason]
        for title in titles:
            matches = tuple(by_title.get(title, ()))
            if len(matches) == 1:
                selected.append(matches[0])
            elif not matches:
                missing += 1
                reasons.append(f"mixed-segment-title-missing:{title}")
            else:
                ambiguous += 1
                reasons.append(f"mixed-segment-title-ambiguous:{title}")

        if len(set(selected)) != len(selected):
            match_counts[candidate.provider_identity] = 0
            exact_compatibility[candidate.provider_identity] = False
            partial_qualification[candidate.provider_identity] = False
            reasons.extend(
                (
                    "mixed-segment-title-distinct-titles-collapse",
                    f"mixed-segment-title-exact-matches:0/{len(titles)}",
                    "mixed-segment-title-compatible:false",
                    "mixed-segment-title-partial-qualified:false",
                )
            )
            extra_reasons[candidate.provider_identity] = tuple(reasons)
            continue

        exact_matches = len(selected)
        exact = missing == 0 and ambiguous == 0 and exact_matches == len(titles)
        partial = (
            not exact
            and exact_matches >= 3
            and exact_matches * 2 >= len(titles)
        )
        match_counts[candidate.provider_identity] = exact_matches
        exact_compatibility[candidate.provider_identity] = exact
        partial_qualification[candidate.provider_identity] = partial
        reasons.extend(
            (
                f"mixed-segment-title-exact-matches:{exact_matches}/{len(titles)}",
                f"mixed-segment-title-compatible:{str(exact).casefold()}",
                f"mixed-segment-title-partial-qualified:{str(partial).casefold()}",
            )
        )
        extra_reasons[candidate.provider_identity] = tuple(reasons)

    enriched = tuple(
        replace(
            candidate,
            reasons=(
                *candidate.reasons,
                *extra_reasons.get(candidate.provider_identity, ()),
            ),
        )
        for candidate in ranked
    )
    if any(value is None for value in match_counts.values()):
        return StructuralCatalogDecision(
            winner=None,
            candidates=enriched,
            reasons=("mixed-segment-title-rescue:indeterminate-candidate-catalog",),
        )

    exact_winners = tuple(
        identity
        for identity, compatible in sorted(
            exact_compatibility.items(), key=lambda item: item[0].key
        )
        if compatible
    )
    if len(exact_winners) > 1:
        return StructuralCatalogDecision(
            winner=None,
            candidates=enriched,
            reasons=("mixed-segment-title-rescue:no-unique-compatible-candidate",),
        )

    partial_winner = False
    if exact_winners:
        winner = exact_winners[0]
    else:
        qualified = tuple(
            identity
            for identity, is_qualified in sorted(
                partial_qualification.items(), key=lambda item: item[0].key
            )
            if is_qualified
        )
        if not qualified:
            return StructuralCatalogDecision(
                winner=None,
                candidates=enriched,
                reasons=("mixed-segment-title-rescue:no-unique-compatible-candidate",),
            )

        best_count = max(match_counts[identity] or 0 for identity in qualified)
        best = tuple(
            identity
            for identity in qualified
            if (match_counts[identity] or 0) == best_count
        )
        if len(best) != 1:
            return StructuralCatalogDecision(
                winner=None,
                candidates=enriched,
                reasons=("mixed-segment-title-rescue:no-unique-compatible-candidate",),
            )
        winner = best[0]
        runner_up_count = max(
            (
                count or 0
                for identity, count in match_counts.items()
                if identity != winner
            ),
            default=0,
        )
        if best_count - runner_up_count < 2:
            return StructuralCatalogDecision(
                winner=None,
                candidates=enriched,
                reasons=("mixed-segment-title-rescue:partial-margin-insufficient",),
            )
        partial_winner = True

    winner_first = tuple(
        sorted(
            enriched,
            key=lambda candidate: (
                candidate.provider_identity != winner,
                -candidate.score,
                candidate.provider_identity.key,
            ),
        )
    )
    return StructuralCatalogDecision(
        winner=winner,
        candidates=winner_first,
        reasons=(
            (
                "mixed-segment-title-rescue:unique-partial-candidate"
                if partial_winner
                else "mixed-segment-title-rescue:unique-compatible-candidate"
            ),
            f"mixed-segment-title-rescue-winner:{winner.key}",
        ),
    )
'''
    SOURCE.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


def patch_tests() -> None:
    text = TESTS.read_text(encoding="utf-8")
    marker = "def test_mixed_segment_rescue_allows_one_missing_title_with_strong_unique_evidence()"
    if marker in text:
        return
    text += '''\n\ndef test_mixed_segment_rescue_allows_one_missing_title_with_strong_unique_evidence() -> None:
    parses = (
        ParseResult(series_hint="Example Collection", season=1, episodes=(1,), segment_hint="a", title_hint="First Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(1,), segment_hint="b", title_hint="Second Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(2,), segment_hint="a", title_hint="Third Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(2,), segment_hint="b", title_hint="Variant Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(3,), title_hint="Full Episode"),
    )
    provider = MixedSegmentProvider(
        {
            ALPHA: _catalog(
                ALPHA,
                (
                    _episode("alpha", "one", 1, 1, "First Story"),
                    _episode("alpha", "two", 1, 2, "Second Story"),
                    _episode("alpha", "three", 1, 3, "Third Story"),
                ),
            ),
            BETA: _catalog(BETA, (_episode("beta", "other", 1, 1, "Unrelated Story"),)),
        }
    )

    result = _resolve(provider, parses)

    assert result.status is ResolutionStatus.MATCHED
    assert result.show is not None
    assert result.show.provider_identity == ALPHA
    assert "mixed-segment-title-rescue:unique-partial-candidate" in result.evidence.reasons
    alpha = next(
        candidate
        for candidate in result.evidence.candidates
        if candidate.provider_identity == ALPHA
    )
    assert "mixed-segment-title-exact-matches:3/4" in alpha.reasons
    assert "mixed-segment-title-missing:variant story" in alpha.reasons


def test_mixed_segment_rescue_partial_evidence_requires_three_exact_titles() -> None:
    parses = (
        ParseResult(series_hint="Example Collection", season=1, episodes=(1,), segment_hint="a", title_hint="First Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(1,), segment_hint="b", title_hint="Second Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(2,), segment_hint="a", title_hint="Variant Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(3,), title_hint="Full Episode"),
    )
    provider = MixedSegmentProvider(
        {
            ALPHA: _catalog(
                ALPHA,
                (
                    _episode("alpha", "one", 1, 1, "First Story"),
                    _episode("alpha", "two", 1, 2, "Second Story"),
                ),
            ),
            BETA: _catalog(BETA, (_episode("beta", "other", 1, 1, "Unrelated Story"),)),
        }
    )

    result = _resolve(provider, parses)

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None


def test_mixed_segment_rescue_partial_evidence_requires_unique_best_candidate() -> None:
    parses = (
        ParseResult(series_hint="Example Collection", season=1, episodes=(1,), segment_hint="a", title_hint="First Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(1,), segment_hint="b", title_hint="Second Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(2,), segment_hint="a", title_hint="Third Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(2,), segment_hint="b", title_hint="Variant Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(3,), title_hint="Full Episode"),
    )
    shared = (
        _episode("shared", "one", 1, 1, "First Story"),
        _episode("shared", "two", 1, 2, "Second Story"),
        _episode("shared", "three", 1, 3, "Third Story"),
    )
    provider = MixedSegmentProvider(
        {
            ALPHA: _catalog(ALPHA, shared),
            BETA: _catalog(BETA, shared),
        }
    )

    result = _resolve(provider, parses)

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert "mixed-segment-title-rescue:no-unique-compatible-candidate" in result.evidence.reasons


def test_mixed_segment_rescue_partial_evidence_requires_two_match_margin() -> None:
    parses = (
        ParseResult(series_hint="Example Collection", season=1, episodes=(1,), segment_hint="a", title_hint="First Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(1,), segment_hint="b", title_hint="Second Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(2,), segment_hint="a", title_hint="Third Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(2,), segment_hint="b", title_hint="Fourth Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(3,), segment_hint="a", title_hint="Variant Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(4,), title_hint="Full Episode"),
    )
    provider = MixedSegmentProvider(
        {
            ALPHA: _catalog(
                ALPHA,
                (
                    _episode("alpha", "one", 1, 1, "First Story"),
                    _episode("alpha", "two", 1, 2, "Second Story"),
                    _episode("alpha", "three", 1, 3, "Third Story"),
                    _episode("alpha", "four", 1, 4, "Fourth Story"),
                ),
            ),
            BETA: _catalog(
                BETA,
                (
                    _episode("beta", "one", 1, 1, "First Story"),
                    _episode("beta", "two", 1, 2, "Second Story"),
                    _episode("beta", "three", 1, 3, "Third Story"),
                ),
            ),
        }
    )

    result = _resolve(provider, parses)

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
    assert "mixed-segment-title-rescue:partial-margin-insufficient" in result.evidence.reasons


def test_mixed_segment_rescue_partial_evidence_requires_half_title_coverage() -> None:
    parses = (
        ParseResult(series_hint="Example Collection", season=1, episodes=(1,), segment_hint="a", title_hint="First Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(1,), segment_hint="b", title_hint="Second Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(2,), segment_hint="a", title_hint="Third Story"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(2,), segment_hint="b", title_hint="Missing Four"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(3,), segment_hint="a", title_hint="Missing Five"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(3,), segment_hint="b", title_hint="Missing Six"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(4,), segment_hint="a", title_hint="Missing Seven"),
        ParseResult(series_hint="Example Collection", season=1, episodes=(5,), title_hint="Full Episode"),
    )
    provider = MixedSegmentProvider(
        {
            ALPHA: _catalog(
                ALPHA,
                (
                    _episode("alpha", "one", 1, 1, "First Story"),
                    _episode("alpha", "two", 1, 2, "Second Story"),
                    _episode("alpha", "three", 1, 3, "Third Story"),
                ),
            ),
            BETA: _catalog(BETA, (_episode("beta", "other", 1, 1, "Unrelated Story"),)),
        }
    )

    result = _resolve(provider, parses)

    assert result.status is ResolutionStatus.SUSPICIOUS
    assert result.show is None
'''
    TESTS.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    patch_source()
    patch_tests()
