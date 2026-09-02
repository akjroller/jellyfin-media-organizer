from pathlib import Path


path = Path("jellyfin_show_organizer/segment_counted_titles.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "    one_to_one: bool\n    proven: bool\n",
    "    one_to_one: bool\n    triggered: bool\n    proven: bool\n",
    1,
)
text = text.replace(
    "    proven = (\n        eligible_count >= _MIN_EXACT_MATCHES\n        and exact_match_count >= _MIN_EXACT_MATCHES\n        and exact_match_count * 2 >= eligible_count\n        and disagreement_count >= _MIN_COORDINATE_DISAGREEMENTS\n        and ambiguous_count == 0\n        and one_to_one\n    )\n",
    "    triggered = (\n        exact_match_count >= _MIN_EXACT_MATCHES\n        and disagreement_count >= _MIN_COORDINATE_DISAGREEMENTS\n    )\n    proven = (\n        triggered\n        and eligible_count >= _MIN_EXACT_MATCHES\n        and exact_match_count * 2 >= eligible_count\n        and ambiguous_count == 0\n        and one_to_one\n    )\n",
    1,
)
text = text.replace(
    "        one_to_one=one_to_one,\n        proven=proven,\n",
    "        one_to_one=one_to_one,\n        triggered=triggered,\n        proven=proven,\n",
    1,
)
text = text.replace(
    '            f"segment-counted-title-one-to-one:{str(one_to_one).casefold()}",\n            f"segment-counted-title-compatible:{str(proven).casefold()}",\n',
    '            f"segment-counted-title-one-to-one:{str(one_to_one).casefold()}",\n            f"segment-counted-title-triggered:{str(triggered).casefold()}",\n            f"segment-counted-title-compatible:{str(proven).casefold()}",\n',
    1,
)
path.write_text(text, encoding="utf-8")

path = Path("jellyfin_show_organizer/show_structural_evidence.py")
text = path.read_text(encoding="utf-8")
old = "from .providers import MetadataProvider, ProviderEpisodeCatalog\n"
new = old + "from .segment_counted_titles import (\n    SegmentCountedTitleAnalysis,\n    analyze_segment_counted_titles,\n    is_segment_counted_title_candidate,\n)\n"
if text.count(old) != 1:
    raise SystemExit("expected show structural provider import once")
text = text.replace(old, new, 1)
if "def segment_counted_title_rescue(" in text:
    raise SystemExit("segment rescue already exists")
text += '''

def segment_counted_title_rescue(
    provider: MetadataProvider,
    parses: tuple[ParseResult, ...],
    ranked: tuple[CandidateEvidence, ...],
    *,
    minimum_gap: float,
    suspicious_threshold: float,
) -> StructuralCatalogDecision | None:
    """Resolve a same-title show tie only after repeated exact title proof."""

    eligible = tuple(parse for parse in parses if is_segment_counted_title_candidate(parse))
    if len(eligible) < 3:
        return None
    if len(ranked) < 2 or ranked[0].score < suspicious_threshold:
        return None
    top_score = ranked[0].score
    contenders = tuple(
        candidate for candidate in ranked if top_score - candidate.score < minimum_gap
    )
    if len(contenders) < 2:
        return None

    analyses: dict[ProviderIdentity, SegmentCountedTitleAnalysis] = {}
    indeterminate: set[ProviderIdentity] = set()
    extra_reasons: dict[ProviderIdentity, tuple[str, ...]] = {}
    for candidate in contenders:
        catalog = provider.episode_catalog(candidate.provider_identity)
        request_reason = f"segment-counted-title-request:{catalog.request_key}"
        if not catalog.resolved:
            indeterminate.add(candidate.provider_identity)
            extra_reasons[candidate.provider_identity] = (
                request_reason,
                "segment-counted-title-rescue:indeterminate-catalog",
            )
            continue
        if catalog.errors:
            indeterminate.add(candidate.provider_identity)
            extra_reasons[candidate.provider_identity] = (
                request_reason,
                *(f"segment-counted-title-error:{error}" for error in catalog.errors),
            )
            continue
        analysis = analyze_segment_counted_titles(parses, catalog)
        analyses[candidate.provider_identity] = analysis
        extra_reasons[candidate.provider_identity] = (
            request_reason,
            *analysis.reasons,
        )

    triggered = {identity for identity, analysis in analyses.items() if analysis.triggered}
    if not triggered:
        return None

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
    if indeterminate:
        return StructuralCatalogDecision(
            winner=None,
            candidates=enriched,
            reasons=("segment-counted-title-rescue:indeterminate-candidate-catalog",),
        )

    proven = tuple(
        identity
        for identity in sorted(triggered, key=lambda item: item.key)
        if analyses[identity].proven
    )
    if len(triggered) != 1 or len(proven) != 1:
        return StructuralCatalogDecision(
            winner=None,
            candidates=enriched,
            reasons=("segment-counted-title-rescue:no-unique-safe-candidate",),
        )

    winner = proven[0]
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
            "segment-counted-title-rescue:unique-compatible-candidate",
            f"segment-counted-title-rescue-winner:{winner.key}",
        ),
    )
'''
path.write_text(text, encoding="utf-8")

path = Path("jellyfin_show_organizer/_show_resolver_core.py")
text = path.read_text(encoding="utf-8")
old = '''    catalog_title_tiebreak,
    structural_title_score,
'''
new = '''    catalog_title_tiebreak,
    segment_counted_title_rescue,
    structural_title_score,
'''
if text.count(old) != 1:
    raise SystemExit("expected resolver structural import once")
text = text.replace(old, new, 1)
old = '''        mode = _numbering_mode(override)
        tie_break = _catalog_tie_break(parse_group, mode, provider, active_ranked)
'''
new = '''        mode = _numbering_mode(override)
        if mode is NumberingMode.AIRED:
            segment_title_rescue = segment_counted_title_rescue(
                provider,
                parse_group,
                active_ranked,
                minimum_gap=_MINIMUM_MATCH_GAP,
                suspicious_threshold=_SUSPICIOUS_THRESHOLD,
            )
            if segment_title_rescue is not None:
                if segment_title_rescue.winner is None:
                    return ShowResolution(
                        status=ResolutionStatus.SUSPICIOUS,
                        show=None,
                        evidence=MatchEvidence(
                            method=f"{method}+segment-counted-title-rescue",
                            confidence=top.score,
                            reasons=(
                                *search_reasons,
                                *(
                                    alias_result.reasons
                                    if alias_result is not None
                                    else ()
                                ),
                                *segment_title_rescue.reasons,
                                f"candidate-gap:{gap:.3f}",
                            ),
                            candidates=segment_title_rescue.candidates,
                        ),
                    )
                provider_show = next(
                    candidate
                    for candidate in provider_candidates
                    if candidate.identity == segment_title_rescue.winner
                )
                title = _preferred_title(override, source_title, provider_show.title)
                assert title is not None
                return _resolved_show_result(
                    source_key=source_key,
                    parse_group=parse_group,
                    override=override,
                    provider=provider,
                    provider_identity=provider_show.identity,
                    title=title,
                    year=(
                        provider_show.year
                        if provider_show.year is not None
                        else year_hint
                    ),
                    method=f"{method}+segment-counted-title-rescue",
                    confidence=top.score,
                    reasons=(
                        *search_reasons,
                        *(alias_result.reasons if alias_result is not None else ()),
                        *segment_title_rescue.reasons,
                        f"candidate-gap:{gap:.3f}",
                    ),
                    candidates=segment_title_rescue.candidates,
                )

        tie_break = _catalog_tie_break(parse_group, mode, provider, active_ranked)
'''
if text.count(old) != 1:
    raise SystemExit("expected catalog tiebreak insertion point once")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

path = Path("jellyfin_show_organizer/mixed_episode_assignment.py")
text = path.read_text(encoding="utf-8")
old = '''from .providers import (
    MetadataProvider,
    ProviderEpisode,
    ProviderEpisodeCatalog,
    TvmazeProviderAdapter,
)
'''
new = old + '''from .segment_counted_titles import (
    analyze_segment_counted_titles,
    is_segment_counted_title_candidate,
)
'''
if text.count(old) != 1:
    raise SystemExit("expected mixed provider import once")
text = text.replace(old, new, 1)
marker = "\ndef assign_episode_group_with_provider(\n"
if text.count(marker) != 1:
    raise SystemExit("expected mixed assignment wrapper once")
helper = '''

def _has_duplicate_provider_reason(assignment: SourceEpisodeAssignment) -> bool:
    return any(
        reason.startswith("duplicate-provider-episode-assignment:")
        for reason in assignment.evidence.reasons
    )


def _recover_accessory_after_segment_remap(
    source: SourceEpisodeInput,
    assignment: SourceEpisodeAssignment,
    show: CanonicalShow,
    provider: MetadataProvider,
) -> SourceEpisodeAssignment:
    if not _has_duplicate_provider_reason(assignment):
        return assignment
    family = _evidence_family(source.parse, show.numbering_mode)
    mode = _FAMILY_MODE.get(family)
    if mode is None or family == _expected_family(show.numbering_mode):
        return assignment
    result = _assign_strict_group(replace(show, numbering_mode=mode), (source,), provider)
    recovered = result.assignments
    if len(recovered) != 1:
        return assignment
    return _annotate_accessory(
        recovered,
        family=family,
        primary_mode=show.numbering_mode,
    )[0]


def _apply_segment_counted_title_remap(
    show: CanonicalShow,
    sources: tuple[SourceEpisodeInput, ...],
    assignments: tuple[SourceEpisodeAssignment, ...],
    provider: MetadataProvider,
    catalog: ProviderEpisodeCatalog,
) -> tuple[SourceEpisodeAssignment, ...]:
    if show.numbering_mode is not NumberingMode.AIRED:
        return assignments

    parses = tuple(source.parse for source in sources)
    analysis = analyze_segment_counted_titles(parses, catalog)
    if not analysis.triggered:
        return assignments

    by_source = {assignment.source_key: assignment for assignment in assignments}
    observations = {
        observation.parse_index: observation for observation in analysis.observations
    }
    remapped: list[SourceEpisodeAssignment] = []
    for index, source in enumerate(sources):
        assignment = by_source[source.source_key]
        family = _evidence_family(source.parse, show.numbering_mode)
        if family != "aired":
            remapped.append(
                _recover_accessory_after_segment_remap(
                    source, assignment, show, provider
                )
            )
            continue

        base_reasons = (
            f"primary-numbering-mode:{show.numbering_mode.value}",
            f"catalog-request:{catalog.request_key}",
            *analysis.reasons,
        )
        if not analysis.proven:
            remapped.append(
                SourceEpisodeAssignment(
                    source_key=source.source_key,
                    status=AssignmentStatus.SUSPICIOUS,
                    episodes=(),
                    evidence=MatchEvidence(
                        method="segment-counted-title-remap",
                        confidence=0.0,
                        reasons=(
                            *base_reasons,
                            "segment-counted-title-remap:group-proof-rejected",
                        ),
                    ),
                )
            )
            continue

        observation = observations.get(index)
        if observation is None:
            remapped.append(
                SourceEpisodeAssignment(
                    source_key=source.source_key,
                    status=AssignmentStatus.UNRESOLVED,
                    episodes=(),
                    evidence=MatchEvidence(
                        method="segment-counted-title-remap",
                        confidence=0.0,
                        reasons=(
                            *base_reasons,
                            "segment-counted-title-remap:group-proven",
                            "segment-counted-title-remap:missing-title-evidence",
                        ),
                    ),
                )
            )
            continue
        if observation.ambiguous:
            remapped.append(
                SourceEpisodeAssignment(
                    source_key=source.source_key,
                    status=AssignmentStatus.SUSPICIOUS,
                    episodes=(),
                    evidence=MatchEvidence(
                        method="segment-counted-title-remap",
                        confidence=0.0,
                        reasons=(
                            *base_reasons,
                            "segment-counted-title-remap:group-proven",
                            "segment-counted-title-remap:ambiguous-exact-title",
                            f"segment-counted-title:{observation.normalized_title}",
                        ),
                    ),
                )
            )
            continue
        episode = observation.episode
        if episode is None:
            remapped.append(
                SourceEpisodeAssignment(
                    source_key=source.source_key,
                    status=AssignmentStatus.UNRESOLVED,
                    episodes=(),
                    evidence=MatchEvidence(
                        method="segment-counted-title-remap",
                        confidence=0.0,
                        reasons=(
                            *base_reasons,
                            "segment-counted-title-remap:group-proven",
                            "segment-counted-title-remap:missing-exact-title-proof",
                            f"segment-counted-title:{observation.normalized_title}",
                        ),
                    ),
                )
            )
            continue
        assert episode.number is not None
        assert source.parse.season is not None
        source_coordinates = ",".join(
            f"S{source.parse.season:02d}E{number:02d}"
            for number in source.parse.episodes
        )
        remapped.append(
            SourceEpisodeAssignment(
                source_key=source.source_key,
                status=AssignmentStatus.MATCHED,
                episodes=(episode,),
                evidence=MatchEvidence(
                    method="segment-counted-title-remap",
                    confidence=1.0,
                    reasons=(
                        *base_reasons,
                        "segment-counted-title-remap:group-proven",
                        f"segment-counted-title:{observation.normalized_title}",
                        f"segment-counted-source-coordinates:{source_coordinates}",
                        "segment-counted-provider-coordinate:"
                        f"S{episode.season:02d}E{episode.number:02d}",
                        _episode_identity_reason(episode),
                    ),
                ),
            )
        )
    return tuple(remapped)
'''
text = text.replace(marker, helper + marker, 1)
old = '''    if not potential_special_sources and not potential_guard_sources:
        return original

    catalog = provider.episode_catalog(show.provider_identity)
'''
new = '''    potential_segment_counted = (
        show.numbering_mode is NumberingMode.AIRED
        and sum(
            is_segment_counted_title_candidate(source.parse)
            for source in source_group
        )
        >= 3
    )
    if (
        not potential_special_sources
        and not potential_guard_sources
        and not potential_segment_counted
    ):
        return original

    catalog = provider.episode_catalog(show.provider_identity)
'''
if text.count(old) != 1:
    raise SystemExit("expected recovery early return once")
text = text.replace(old, new, 1)
old = '''    ordered = tuple(
        sorted(
            base_assignments.values(),
            key=lambda assignment: (
                assignment.source_key.casefold(),
                assignment.source_key,
            ),
        )
    )
    ordered = _protect_provider_episode_identity(ordered)
'''
new = '''    ordered = tuple(
        sorted(
            base_assignments.values(),
            key=lambda assignment: (
                assignment.source_key.casefold(),
                assignment.source_key,
            ),
        )
    )
    if potential_segment_counted:
        ordered = _apply_segment_counted_title_remap(
            show, source_group, ordered, provider, catalog
        )
    ordered = _protect_provider_episode_identity(ordered)
'''
if text.count(old) != 1:
    raise SystemExit("expected final protection block once")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
