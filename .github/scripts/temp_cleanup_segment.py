from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"integration point changed for {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "jellyfin_show_organizer/segment_counted_titles.py",
    '''import re\nimport unicodedata\nfrom dataclasses import dataclass\n''',
    '''import re\nimport unicodedata\nfrom dataclasses import dataclass\nfrom difflib import SequenceMatcher\n''',
)
replace_once(
    "jellyfin_show_organizer/segment_counted_titles.py",
    '''_MIN_EXACT_MATCHES = 3\n_MIN_COORDINATE_DISAGREEMENTS = 2\n''',
    '''_MIN_EXACT_MATCHES = 3\n_MIN_COORDINATE_DISAGREEMENTS = 2\n_NEAR_TITLE_THRESHOLD = 0.92\n_NEAR_TITLE_GAP = 0.08\n_MIN_NEAR_TITLE_LENGTH = 8\n''',
)
replace_once(
    "jellyfin_show_organizer/segment_counted_titles.py",
    '''@dataclass(frozen=True, slots=True)\nclass SegmentCountedTitleAnalysis:\n''',
    '''@dataclass(frozen=True, slots=True)\nclass SegmentCountedTitleRecovery:\n    parse_index: int\n    episode: ProviderEpisode\n    score: float\n\n\n@dataclass(frozen=True, slots=True)\nclass SegmentCountedTitleAnalysis:\n''',
)
replace_once(
    "jellyfin_show_organizer/segment_counted_titles.py",
    '''def normalize_episode_title(value: str) -> str:\n    normalized = unicodedata.normalize("NFKC", value).casefold()\n    normalized = re.sub(r"[^\\w]+", " ", normalized, flags=re.UNICODE)\n    return " ".join(normalized.split())\n''',
    '''def normalize_episode_title(value: str) -> str:\n    normalized = unicodedata.normalize("NFKD", value)\n    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", normalized)\n    normalized = re.sub(r"(?i)([A-Za-z0-9])['’]s\\b", r"\\1s", normalized)\n    normalized = "".join(\n        character\n        for character in normalized\n        if unicodedata.category(character) != "Mn"\n    ).casefold()\n    normalized = re.sub(r"[^\\w]+", " ", normalized, flags=re.UNICODE)\n    return " ".join(normalized.split())\n''',
)

target = Path("jellyfin_show_organizer/segment_counted_titles.py")
text = target.read_text(encoding="utf-8")
text += '''\n\ndef recover_unique_near_segment_titles(\n    parses: tuple[ParseResult, ...],\n    catalog: ProviderEpisodeCatalog,\n    analysis: SegmentCountedTitleAnalysis,\n) -> tuple[SegmentCountedTitleRecovery, ...]:\n    """Recover one near-title member only after exact evidence proves the group."""\n\n    if not analysis.proven:\n        return ()\n\n    claimed = {\n        observation.episode.identity\n        for observation in analysis.observations\n        if observation.episode is not None\n    }\n    tentative: list[SegmentCountedTitleRecovery] = []\n    for observation in analysis.observations:\n        if observation.episode is not None or observation.ambiguous:\n            continue\n        if len(observation.normalized_title) < _MIN_NEAR_TITLE_LENGTH:\n            continue\n        parse = parses[observation.parse_index]\n        if parse.season is None:\n            continue\n\n        scored: list[tuple[float, ProviderEpisode]] = []\n        for episode in catalog.episodes:\n            if episode.season != parse.season or episode.number is None:\n                continue\n            candidate_title = normalize_episode_title(episode.title)\n            if len(candidate_title) < _MIN_NEAR_TITLE_LENGTH:\n                continue\n            score = SequenceMatcher(\n                None, observation.normalized_title, candidate_title, autojunk=False\n            ).ratio()\n            scored.append((score, episode))\n        scored.sort(key=lambda item: (-item[0], item[1].identity.key))\n        if not scored or scored[0][0] < _NEAR_TITLE_THRESHOLD:\n            continue\n        top_score, top_episode = scored[0]\n        runner_score = scored[1][0] if len(scored) > 1 else 0.0\n        if top_score - runner_score < _NEAR_TITLE_GAP:\n            continue\n        if top_episode.identity in claimed:\n            continue\n        tentative.append(\n            SegmentCountedTitleRecovery(\n                parse_index=observation.parse_index,\n                episode=top_episode,\n                score=top_score,\n            )\n        )\n\n    identity_counts: dict[object, int] = {}\n    for recovery in tentative:\n        identity_counts[recovery.episode.identity] = (\n            identity_counts.get(recovery.episode.identity, 0) + 1\n        )\n    return tuple(\n        recovery\n        for recovery in tentative\n        if identity_counts[recovery.episode.identity] == 1\n    )\n'''
target.write_text(text, encoding="utf-8")

replace_once(
    "jellyfin_show_organizer/mixed_episode_assignment.py",
    '''from .segment_counted_titles import (\n    analyze_segment_counted_titles,\n    is_segment_counted_title_candidate,\n)\n''',
    '''from .segment_counted_titles import (\n    analyze_segment_counted_titles,\n    is_segment_counted_title_candidate,\n    recover_unique_near_segment_titles,\n)\n''',
)
replace_once(
    "jellyfin_show_organizer/mixed_episode_assignment.py",
    '''    observations = {\n        observation.parse_index: observation for observation in analysis.observations\n    }\n    remapped: list[SourceEpisodeAssignment] = []\n''',
    '''    observations = {\n        observation.parse_index: observation for observation in analysis.observations\n    }\n    recoveries = {\n        recovery.parse_index: recovery\n        for recovery in recover_unique_near_segment_titles(parses, catalog, analysis)\n    }\n    remapped: list[SourceEpisodeAssignment] = []\n''',
)
replace_once(
    "jellyfin_show_organizer/mixed_episode_assignment.py",
    '''        episode = observation.episode\n        if episode is None:\n            remapped.append(\n                SourceEpisodeAssignment(\n                    source_key=source.source_key,\n                    status=AssignmentStatus.UNRESOLVED,\n                    episodes=(),\n                    evidence=MatchEvidence(\n                        method="segment-counted-title-remap",\n                        confidence=0.0,\n                        reasons=(\n                            *base_reasons,\n                            "segment-counted-title-remap:group-proven",\n                            "segment-counted-title-remap:missing-exact-title-proof",\n                            f"segment-counted-title:{observation.normalized_title}",\n                        ),\n                    ),\n                )\n            )\n            continue\n''',
    '''        episode = observation.episode\n        recovery_reasons: tuple[str, ...] = ()\n        if episode is None:\n            recovery = recoveries.get(index)\n            if recovery is None:\n                remapped.append(\n                    SourceEpisodeAssignment(\n                        source_key=source.source_key,\n                        status=AssignmentStatus.UNRESOLVED,\n                        episodes=(),\n                        evidence=MatchEvidence(\n                            method="segment-counted-title-remap",\n                            confidence=0.0,\n                            reasons=(\n                                *base_reasons,\n                                "segment-counted-title-remap:group-proven",\n                                "segment-counted-title-remap:missing-exact-title-proof",\n                                f"segment-counted-title:{observation.normalized_title}",\n                            ),\n                        ),\n                    )\n                )\n                continue\n            episode = recovery.episode\n            recovery_reasons = (\n                "segment-counted-title-remap:unique-near-title-proof",\n                f"segment-counted-title-near-score:{recovery.score:.3f}",\n            )\n''',
)
replace_once(
    "jellyfin_show_organizer/mixed_episode_assignment.py",
    '''                        "segment-counted-title-remap:group-proven",\n                        f"segment-counted-title:{observation.normalized_title}",\n                        f"segment-counted-source-coordinates:{source_coordinates}",\n''',
    '''                        "segment-counted-title-remap:group-proven",\n                        *recovery_reasons,\n                        f"segment-counted-title:{observation.normalized_title}",\n                        f"segment-counted-source-coordinates:{source_coordinates}",\n''',
)
