from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"expected one match in {path}, found {text.count(old)}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "jellyfin_show_organizer/episode_assignment_strict.py",
    "class SourceEpisodeInput:\n    source_key: str\n    parse: ParseResult\n\n    def __post_init__(self) -> None:\n",
    "class SourceEpisodeInput:\n    source_key: str\n    parse: ParseResult\n    explicit_decision: bool = False\n\n    def __post_init__(self) -> None:\n",
)

replace_once(
    "jellyfin_show_organizer/planner.py",
    "            SourceEpisodeInput(\n                source_key=source.relative_path,\n                parse=effective_parse,\n            )\n",
    "            SourceEpisodeInput(\n                source_key=source.relative_path,\n                parse=effective_parse,\n                explicit_decision=decision is not None,\n            )\n",
)

replace_once(
    "jellyfin_show_organizer/mixed_episode_assignment.py",
    "        if (assignment := original_by_source.get(source.source_key)) is not None\n        and _has_special_fallback_signal(source, assignment)\n",
    "        if not source.explicit_decision\n        and (assignment := original_by_source.get(source.source_key)) is not None\n        and _has_special_fallback_signal(source, assignment)\n",
)

replace_once(
    "jellyfin_show_organizer/mixed_episode_assignment.py",
    "            source\n            for source in source_group\n            if _has_pre_premiere_guard_signal(source, show)\n",
    "            source\n            for source in source_group\n            if not source.explicit_decision\n            and _has_pre_premiere_guard_signal(source, show)\n",
)

replace_once(
    "jellyfin_show_organizer/mixed_episode_assignment.py",
    "            is_segment_counted_title_candidate(source.parse) for source in source_group\n",
    "            is_segment_counted_title_candidate(source.parse)\n            for source in source_group\n            if not source.explicit_decision\n",
)

replace_once(
    "jellyfin_show_organizer/mixed_episode_assignment.py",
    "    for index, source in enumerate(sources):\n        assignment = by_source[source.source_key]\n        family = _evidence_family(source.parse, show.numbering_mode)\n",
    "    for index, source in enumerate(sources):\n        assignment = by_source[source.source_key]\n        if source.explicit_decision:\n            remapped.append(assignment)\n            continue\n        family = _evidence_family(source.parse, show.numbering_mode)\n",
)
