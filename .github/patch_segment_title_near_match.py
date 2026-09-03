from __future__ import annotations

from pathlib import Path

STRICT = Path("jellyfin_show_organizer/episode_assignment_strict.py")
TESTS = Path("tests/local/test_episode_assignment.py")


def patch_strict() -> None:
    text = STRICT.read_text(encoding="utf-8")

    text = text.replace(
        "from dataclasses import dataclass, replace\n",
        "from dataclasses import dataclass, replace\nfrom difflib import SequenceMatcher\n",
        1,
    )
    text = text.replace(
        "from .tvmaze_cache import JsonGetter, TvmazeCatalogCache\n",
        "from .segment_counted_titles import (\n"
        "    clean_episode_title_hint,\n"
        "    normalize_episode_title,\n"
        ")\n"
        "from .tvmaze_cache import JsonGetter, TvmazeCatalogCache\n",
        1,
    )

    normalize_anchor = '''def _normalize_title(value: str) -> str:\n    normalized = unicodedata.normalize("NFKC", value).casefold()\n    normalized = re.sub(r"[^\\w]+", " ", normalized, flags=re.UNICODE)\n    return " ".join(normalized.split())\n'''
    if normalize_anchor not in text:
        raise RuntimeError("normalize anchor not found")

    helper_block = normalize_anchor + '''\n\n_SEGMENT_NEAR_TITLE_THRESHOLD = 0.92\n_SEGMENT_NEAR_TITLE_GAP = 0.08\n_SEGMENT_MIN_NEAR_TITLE_LENGTH = 8\n_SEGMENT_TRAILING_BRACKET_TAG = re.compile(\n    r"\\s*\\[[^\\]\\r\\n]{0,48}\\d[^\\]\\r\\n]{0,48}\\]\\s*$"\n)\n\n\ndef _segment_source_title(value: str) -> str:\n    cleaned = unicodedata.normalize("NFKC", value).strip()\n    while True:\n        trimmed = _SEGMENT_TRAILING_BRACKET_TAG.sub("", cleaned).rstrip(" ._-")\n        if trimmed == cleaned:\n            break\n        cleaned = trimmed\n    return clean_episode_title_hint(cleaned)\n\n\ndef _segment_equivalence_key(normalized_title: str) -> str:\n    return "".join(\n        token for token in normalized_title.split() if token != "and"\n    )\n\n\ndef _segment_catalog_candidates(\n    parse: ParseResult,\n    catalog: ProviderEpisodeCatalog,\n) -> tuple[ProviderEpisode, ...]:\n    candidates = tuple(\n        episode for episode in catalog.episodes if episode.number is not None\n    )\n    if parse.season is None:\n        return candidates\n    return tuple(\n        episode for episode in candidates if episode.season == parse.season\n    )\n'''
    text = text.replace(normalize_anchor, helper_block, 1)

    start = text.index("def _segment_assignment(\n")
    end = text.index("\n\ndef _group_status(", start)
    replacement = '''def _segment_assignment(\n    source: SourceEpisodeInput,\n    show: CanonicalShow,\n    catalog: ProviderEpisodeCatalog,\n    request_key: str,\n) -> SourceEpisodeAssignment:\n    parse = source.parse\n    if parse.segment_hint is None:\n        return _assignment(\n            source.source_key,\n            AssignmentStatus.UNRESOLVED,\n            "episode-catalog",\n            f"numbering-mode:{show.numbering_mode.value}",\n            "missing-segment-hint",\n            f"catalog-request:{request_key}",\n        )\n    if parse.title_hint is None or not parse.title_hint.strip():\n        return _assignment(\n            source.source_key,\n            AssignmentStatus.UNRESOLVED,\n            "episode-catalog",\n            f"numbering-mode:{show.numbering_mode.value}",\n            f"segment-hint:{parse.segment_hint.casefold()}",\n            "missing-segment-title-evidence",\n            f"catalog-request:{request_key}",\n        )\n\n    normalized_title = _segment_source_title(parse.title_hint)\n    candidates = _segment_catalog_candidates(parse, catalog)\n    matches = tuple(\n        episode\n        for episode in candidates\n        if normalize_episode_title(episode.title) == normalized_title\n    )\n    match_reasons: tuple[str, ...] = ()\n\n    if len(matches) > 1:\n        return _assignment(\n            source.source_key,\n            AssignmentStatus.SUSPICIOUS,\n            "episode-catalog",\n            f"numbering-mode:{show.numbering_mode.value}",\n            f"segment-hint:{parse.segment_hint.casefold()}",\n            f"ambiguous-segment-title-match:{normalized_title}",\n            f"catalog-request:{request_key}",\n        )\n\n    if len(matches) == 1:\n        episode = matches[0]\n        match_reasons = (f"segment-title-match:{normalized_title}",)\n    else:\n        equivalence_key = _segment_equivalence_key(normalized_title)\n        equivalent = tuple(\n            episode\n            for episode in candidates\n            if equivalence_key\n            and _segment_equivalence_key(normalize_episode_title(episode.title))\n            == equivalence_key\n        )\n        if len(equivalent) > 1:\n            return _assignment(\n                source.source_key,\n                AssignmentStatus.SUSPICIOUS,\n                "episode-catalog",\n                f"numbering-mode:{show.numbering_mode.value}",\n                f"segment-hint:{parse.segment_hint.casefold()}",\n                f"ambiguous-segment-title-equivalent-match:{normalized_title}",\n                f"catalog-request:{request_key}",\n            )\n        if len(equivalent) == 1:\n            episode = equivalent[0]\n            match_reasons = (\n                f"segment-title-equivalent-match:{normalized_title}",\n            )\n        else:\n            source_key = _segment_equivalence_key(normalized_title)\n            scored: list[tuple[float, ProviderEpisode]] = []\n            if parse.season is not None and len(source_key) >= _SEGMENT_MIN_NEAR_TITLE_LENGTH:\n                for candidate in candidates:\n                    candidate_key = _segment_equivalence_key(\n                        normalize_episode_title(candidate.title)\n                    )\n                    if len(candidate_key) < _SEGMENT_MIN_NEAR_TITLE_LENGTH:\n                        continue\n                    score = SequenceMatcher(\n                        None, source_key, candidate_key, autojunk=False\n                    ).ratio()\n                    scored.append((score, candidate))\n                scored.sort(key=lambda item: (-item[0], item[1].identity.key))\n\n            if not scored or scored[0][0] < _SEGMENT_NEAR_TITLE_THRESHOLD:\n                return _assignment(\n                    source.source_key,\n                    AssignmentStatus.UNRESOLVED,\n                    "episode-catalog",\n                    f"numbering-mode:{show.numbering_mode.value}",\n                    f"segment-hint:{parse.segment_hint.casefold()}",\n                    f"missing-segment-title-match:{normalized_title}",\n                    f"catalog-request:{request_key}",\n                )\n\n            top_score, top_episode = scored[0]\n            runner_score = scored[1][0] if len(scored) > 1 else 0.0\n            if top_score - runner_score < _SEGMENT_NEAR_TITLE_GAP:\n                return _assignment(\n                    source.source_key,\n                    AssignmentStatus.SUSPICIOUS,\n                    "episode-catalog",\n                    f"numbering-mode:{show.numbering_mode.value}",\n                    f"segment-hint:{parse.segment_hint.casefold()}",\n                    f"ambiguous-segment-title-near-match:{normalized_title}",\n                    f"segment-title-near-score:{top_score:.3f}",\n                    f"segment-title-near-runner:{runner_score:.3f}",\n                    f"catalog-request:{request_key}",\n                )\n            episode = top_episode\n            match_reasons = (\n                f"segment-title-near-match:{normalized_title}",\n                f"segment-title-near-score:{top_score:.3f}",\n            )\n\n    return _assignment(\n        source.source_key,\n        AssignmentStatus.MATCHED,\n        "episode-catalog",\n        f"numbering-mode:{show.numbering_mode.value}",\n        f"segment-hint:{parse.segment_hint.casefold()}",\n        *match_reasons,\n        f"catalog-request:{request_key}",\n        _episode_identity_reason(episode),\n        episodes=(episode,),\n        confidence=1.0,\n    )\n'''
    text = text[:start] + replacement + text[end:]
    STRICT.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    text = TESTS.read_text(encoding="utf-8")
    marker = "def test_warm_cache_replays_equivalent_group_without_http_calls"
    if marker not in text:
        raise RuntimeError("test anchor not found")

    additions = r'''


def test_segment_title_accepts_spacing_and_conjunction_equivalence(tmp_path: Path) -> None:
    catalog = [
        {
            "id": 3001,
            "season": 1,
            "number": 7,
            "name": "Sandy, SpongeBob & the Worm",
        }
    ]
    result = assign_episode_group(
        _show(NumberingMode.SEGMENT_TITLE),
        (
            SourceEpisodeInput(
                "segment-a.mkv",
                ParseResult(
                    season=1,
                    episodes=(18,),
                    segment_hint="a",
                    title_hint="Sandy, Sponge Bob, and the Worm",
                ),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(catalog),
    )

    assignment = result.assignments[0]
    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].tvmaze_episode_id == 3001
    assert "segment-title-equivalent-match:sandy sponge bob and the worm" in (
        assignment.evidence.reasons
    )


def test_segment_title_accepts_unique_high_confidence_typo(tmp_path: Path) -> None:
    catalog = [
        {
            "id": 3002,
            "season": 1,
            "number": 8,
            "name": "Squidward the Unfriendly Ghost",
        }
    ]
    result = assign_episode_group(
        _show(NumberingMode.SEGMENT_TITLE),
        (
            SourceEpisodeInput(
                "segment-b.mkv",
                ParseResult(
                    season=1,
                    episodes=(11,),
                    segment_hint="b",
                    title_hint="Squidward, the Unfreindly Ghost",
                ),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(catalog),
    )

    assignment = result.assignments[0]
    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].tvmaze_episode_id == 3002
    assert "segment-title-near-match:squidward the unfreindly ghost" in (
        assignment.evidence.reasons
    )


def test_segment_title_strips_trailing_bracketed_release_tag(tmp_path: Path) -> None:
    catalog = [{"id": 3003, "season": 1, "number": 9, "name": "Hooky"}]
    result = assign_episode_group(
        _show(NumberingMode.SEGMENT_TITLE),
        (
            SourceEpisodeInput(
                "segment-a.mkv",
                ParseResult(
                    season=1,
                    episodes=(20,),
                    segment_hint="a",
                    title_hint="Hooky [Fester1500]",
                ),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(catalog),
    )

    assignment = result.assignments[0]
    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].tvmaze_episode_id == 3003
    assert "segment-title-match:hooky" in assignment.evidence.reasons


def test_segment_title_near_match_fails_closed_when_runner_up_is_too_close(
    tmp_path: Path,
) -> None:
    catalog = [
        {
            "id": 3004,
            "season": 1,
            "number": 10,
            "name": "Mermaid Man and Barnacle Boy",
        },
        {
            "id": 3005,
            "season": 1,
            "number": 11,
            "name": "Mermaid Fan and Barnacle Boy",
        },
    ]
    result = assign_episode_group(
        _show(NumberingMode.SEGMENT_TITLE),
        (
            SourceEpisodeInput(
                "segment-b.mkv",
                ParseResult(
                    season=1,
                    episodes=(20,),
                    segment_hint="b",
                    title_hint="Mermaid Nan and Barnacle Boy",
                ),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(catalog),
    )

    assignment = result.assignments[0]
    assert assignment.status is AssignmentStatus.SUSPICIOUS
    assert not assignment.episodes
    assert any(
        reason.startswith("ambiguous-segment-title-near-match:")
        for reason in assignment.evidence.reasons
    )


def test_segment_title_uses_source_season_to_disambiguate_repeated_title(
    tmp_path: Path,
) -> None:
    catalog = [
        {"id": 3006, "season": 1, "number": 12, "name": "Return"},
        {"id": 3007, "season": 2, "number": 12, "name": "Return"},
    ]
    result = assign_episode_group(
        _show(NumberingMode.SEGMENT_TITLE),
        (
            SourceEpisodeInput(
                "segment-a.mkv",
                ParseResult(
                    season=1,
                    episodes=(12,),
                    segment_hint="a",
                    title_hint="Return",
                ),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(catalog),
    )

    assignment = result.assignments[0]
    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].tvmaze_episode_id == 3006
'''
    if "test_segment_title_accepts_unique_high_confidence_typo" in text:
        raise RuntimeError("tests already patched")
    TESTS.write_text(text + additions, encoding="utf-8")


def main() -> None:
    patch_strict()
    patch_tests()


if __name__ == "__main__":
    main()
