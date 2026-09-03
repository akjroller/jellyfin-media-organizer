from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one match in {path}, found {count}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


path = "jellyfin_show_organizer/mixed_episode_assignment.py"

replace_once(
    path,
    '''def _has_duplicate_provider_reason(assignment: SourceEpisodeAssignment) -> bool:\n    return any(\n        reason.startswith("duplicate-provider-episode-assignment:")\n        for reason in assignment.evidence.reasons\n    )\n\n\ndef _recover_accessory_after_segment_remap(\n''',
    '''def _has_duplicate_provider_reason(assignment: SourceEpisodeAssignment) -> bool:\n    return any(\n        reason.startswith("duplicate-provider-episode-assignment:")\n        for reason in assignment.evidence.reasons\n    )\n\n\ndef _nonregular_title_quarantine_assignment(\n    source: SourceEpisodeInput,\n    assignment: SourceEpisodeAssignment,\n    catalog: ProviderEpisodeCatalog,\n) -> SourceEpisodeAssignment | None:\n    parse = source.parse\n    if (\n        not _has_duplicate_provider_reason(assignment)\n        or parse.title_hint is None\n        or parse.season is None\n        or len(parse.episodes) != 1\n    ):\n        return None\n\n    normalized_title = _normalize_title(parse.title_hint)\n    title_matches = tuple(\n        episode\n        for episode in catalog.episodes\n        if _normalize_title(episode.title) == normalized_title\n    )\n    if len(title_matches) != 1:\n        return None\n    title_episode = title_matches[0]\n    if not _is_non_regular_episode(title_episode) or title_episode.number is not None:\n        return None\n\n    source_episode = parse.episodes[0]\n    coordinate_matches = tuple(\n        episode\n        for episode in catalog.episodes\n        if episode.season == parse.season\n        and episode.number == source_episode\n        and not _is_non_regular_episode(episode)\n    )\n    if len(coordinate_matches) != 1:\n        return None\n    coordinate_episode = coordinate_matches[0]\n    if _normalize_title(coordinate_episode.title) == normalized_title:\n        return None\n\n    return SourceEpisodeAssignment(\n        source_key=source.source_key,\n        status=AssignmentStatus.UNRESOLVED,\n        episodes=(),\n        evidence=MatchEvidence(\n            method="nonregular-title-quarantine",\n            confidence=0.0,\n            reasons=(\n                *assignment.evidence.reasons,\n                f"catalog-request:{catalog.request_key}",\n                "nonregular-title-quarantine:unique-exact-title",\n                f"nonregular-title:{normalized_title}",\n                "nonregular-title-quarantine:provider-entry-missing-number",\n                f"nonregular-title-quarantine:parsed-coordinate:S{parse.season:02d}E{source_episode:02d}",\n                "nonregular-title-quarantine:coordinate-title-conflict",\n                _episode_identity_reason(title_episode),\n            ),\n        ),\n    )\n\n\ndef _is_nonregular_title_quarantine(assignment: SourceEpisodeAssignment) -> bool:\n    return any(\n        reason.startswith("nonregular-title-quarantine:")\n        for reason in assignment.evidence.reasons\n    )\n\n\ndef _recover_accessory_after_segment_remap(\n''',
)

replace_once(
    path,
    '''        assignment = by_source[source.source_key]\n        if source.explicit_decision:\n            remapped.append(assignment)\n            continue\n''',
    '''        assignment = by_source[source.source_key]\n        if source.explicit_decision or _is_nonregular_title_quarantine(assignment):\n            remapped.append(assignment)\n            continue\n''',
)

replace_once(
    path,
    '''    potential_segment_counted = (\n        show.numbering_mode is NumberingMode.AIRED\n        and sum(\n            is_segment_counted_title_candidate(source.parse)\n            for source in source_group\n            if not source.explicit_decision\n        )\n        >= 3\n    )\n    if (\n        not potential_special_sources\n        and not potential_guard_sources\n        and not potential_segment_counted\n    ):\n''',
    '''    potential_segment_counted = (\n        show.numbering_mode is NumberingMode.AIRED\n        and sum(\n            is_segment_counted_title_candidate(source.parse)\n            for source in source_group\n            if not source.explicit_decision\n        )\n        >= 3\n    )\n    potential_quarantine_sources = tuple(\n        source\n        for source in source_group\n        if not source.explicit_decision\n        and source.parse.title_hint is not None\n        and source.parse.season is not None\n        and len(source.parse.episodes) == 1\n        and (assignment := original_by_source.get(source.source_key)) is not None\n        and _has_duplicate_provider_reason(assignment)\n    )\n    if (\n        not potential_special_sources\n        and not potential_guard_sources\n        and not potential_segment_counted\n        and not potential_quarantine_sources\n    ):\n''',
)

replace_once(
    path,
    '''    guarded = {\n        source.source_key: assignment\n        for source in potential_guard_sources\n        if (assignment := _pre_premiere_assignment(source, show, catalog)) is not None\n    }\n    request_keys = {catalog.request_key}\n    if guarded:\n        remaining = tuple(\n            source for source in source_group if source.source_key not in guarded\n        )\n        base_assignments = dict(guarded)\n''',
    '''    guarded = {\n        source.source_key: assignment\n        for source in potential_guard_sources\n        if (assignment := _pre_premiere_assignment(source, show, catalog)) is not None\n    }\n    quarantined = {\n        source.source_key: assignment\n        for source in potential_quarantine_sources\n        if (original_assignment := original_by_source.get(source.source_key)) is not None\n        and (\n            assignment := _nonregular_title_quarantine_assignment(\n                source, original_assignment, catalog\n            )\n        )\n        is not None\n    }\n    protected_assignments = {**guarded, **quarantined}\n    request_keys = {catalog.request_key}\n    if protected_assignments:\n        remaining = tuple(\n            source\n            for source in source_group\n            if source.source_key not in protected_assignments\n        )\n        base_assignments = dict(protected_assignments)\n''',
)
