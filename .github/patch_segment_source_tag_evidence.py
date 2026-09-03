from __future__ import annotations

from pathlib import Path

STRICT = Path("jellyfin_show_organizer/episode_assignment_strict.py")
TESTS = Path("tests/local/test_episode_assignment.py")


def patch_strict() -> None:
    text = STRICT.read_text(encoding="utf-8")

    old_helper = '''_SEGMENT_TRAILING_BRACKET_TAG = re.compile(\n    r"\\s*\\[[^\\]\\r\\n]{0,48}\\d[^\\]\\r\\n]{0,48}\\]\\s*$"\n)\n\n\ndef _segment_source_title(value: str) -> str:\n    cleaned = unicodedata.normalize("NFKC", value).strip()\n    while True:\n        trimmed = _SEGMENT_TRAILING_BRACKET_TAG.sub("", cleaned).rstrip(" ._-")\n        if trimmed == cleaned:\n            break\n        cleaned = trimmed\n    return clean_episode_title_hint(cleaned)\n'''
    new_helper = '''_SEGMENT_TRAILING_BRACKET_TAG = re.compile(\n    r"\\s*\\[[^\\]\\r\\n]{0,48}\\d[^\\]\\r\\n]{0,48}\\]\\s*$"\n)\n_SEGMENT_SOURCE_TRAILING_BRACKET_TAG = re.compile(\n    r"\\[([^\\]\\r\\n]{0,48}\\d[^\\]\\r\\n]{0,48})\\](?=\\.[A-Za-z0-9]{1,12}$|$)"\n)\n\n\ndef _segment_source_title(\n    value: str, source_key: str\n) -> tuple[str, tuple[str, ...]]:\n    cleaned = unicodedata.normalize("NFKC", value).strip()\n    while True:\n        trimmed = _SEGMENT_TRAILING_BRACKET_TAG.sub("", cleaned).rstrip(" ._-")\n        if trimmed == cleaned:\n            break\n        cleaned = trimmed\n\n    normalized_title = clean_episode_title_hint(cleaned)\n    match = _SEGMENT_SOURCE_TRAILING_BRACKET_TAG.search(\n        unicodedata.normalize("NFKC", source_key)\n    )\n    if match is None:\n        return normalized_title, ()\n\n    normalized_tag = normalize_episode_title(match.group(1))\n    title_tokens = normalized_title.split()\n    tag_tokens = normalized_tag.split()\n    if (\n        not tag_tokens\n        or len(title_tokens) <= len(tag_tokens)\n        or title_tokens[-len(tag_tokens) :] != tag_tokens\n    ):\n        return normalized_title, ()\n\n    normalized_title = " ".join(title_tokens[: -len(tag_tokens)])\n    return normalized_title, (\n        f"segment-title-source-bracket-tag:{normalized_tag}",\n    )\n'''
    if old_helper not in text:
        raise RuntimeError("segment source title helper anchor not found")
    text = text.replace(old_helper, new_helper, 1)

    old_call = "    normalized_title = _segment_source_title(parse.title_hint)\n"
    new_call = (
        "    normalized_title, source_title_reasons = _segment_source_title(\n"
        "        parse.title_hint, source.source_key\n"
        "    )\n"
    )
    if old_call not in text:
        raise RuntimeError("segment source title call anchor not found")
    text = text.replace(old_call, new_call, 1)

    replacements = {
        '''            f"segment-hint:{parse.segment_hint.casefold()}",\n            f"ambiguous-segment-title-match:{normalized_title}",''': '''            f"segment-hint:{parse.segment_hint.casefold()}",\n            *source_title_reasons,\n            f"ambiguous-segment-title-match:{normalized_title}",''',
        '''                f"segment-hint:{parse.segment_hint.casefold()}",\n                f"ambiguous-segment-title-equivalent-match:{normalized_title}",''': '''                f"segment-hint:{parse.segment_hint.casefold()}",\n                *source_title_reasons,\n                f"ambiguous-segment-title-equivalent-match:{normalized_title}",''',
        '''                    f"segment-hint:{parse.segment_hint.casefold()}",\n                    f"missing-segment-title-match:{normalized_title}",''': '''                    f"segment-hint:{parse.segment_hint.casefold()}",\n                    *source_title_reasons,\n                    f"missing-segment-title-match:{normalized_title}",''',
        '''                    f"segment-hint:{parse.segment_hint.casefold()}",\n                    f"ambiguous-segment-title-near-match:{normalized_title}",''': '''                    f"segment-hint:{parse.segment_hint.casefold()}",\n                    *source_title_reasons,\n                    f"ambiguous-segment-title-near-match:{normalized_title}",''',
        '''        match_reasons = (f"segment-title-match:{normalized_title}",)\n''': '''        match_reasons = (\n            *source_title_reasons,\n            f"segment-title-match:{normalized_title}",\n        )\n''',
        '''            match_reasons = (f"segment-title-equivalent-match:{normalized_title}",)\n''': '''            match_reasons = (\n                *source_title_reasons,\n                f"segment-title-equivalent-match:{normalized_title}",\n            )\n''',
        '''            match_reasons = (\n                f"segment-title-near-match:{normalized_title}",\n                f"segment-title-near-score:{top_score:.3f}",\n            )\n''': '''            match_reasons = (\n                *source_title_reasons,\n                f"segment-title-near-match:{normalized_title}",\n                f"segment-title-near-score:{top_score:.3f}",\n            )\n''',
    }
    for old, new in replacements.items():
        if old not in text:
            raise RuntimeError(f"assignment replacement anchor not found: {old}")
        text = text.replace(old, new, 1)

    STRICT.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    text = TESTS.read_text(encoding="utf-8")
    if "test_segment_title_recovers_flattened_numeric_bracket_tag" in text:
        raise RuntimeError("tests already patched")

    additions = r'''


def test_segment_title_recovers_flattened_numeric_bracket_tag(tmp_path: Path) -> None:
    catalog = [{"id": 3010, "season": 1, "number": 20, "name": "Hooky"}]
    result = assign_episode_group(
        _show(NumberingMode.SEGMENT_TITLE),
        (
            SourceEpisodeInput(
                "Show - S01E20a - Hooky [Fester1500].mkv",
                ParseResult(
                    season=1,
                    episodes=(20,),
                    segment_hint="a",
                    title_hint="Hooky Fester1500",
                ),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(catalog),
    )

    assignment = result.assignments[0]
    assert assignment.status is AssignmentStatus.MATCHED
    assert assignment.episodes[0].tvmaze_episode_id == 3010
    assert "segment-title-source-bracket-tag:fester1500" in assignment.evidence.reasons
    assert "segment-title-match:hooky" in assignment.evidence.reasons


def test_segment_title_does_not_strip_unproven_plain_suffix(tmp_path: Path) -> None:
    catalog = [{"id": 3011, "season": 1, "number": 20, "name": "Hooky"}]
    result = assign_episode_group(
        _show(NumberingMode.SEGMENT_TITLE),
        (
            SourceEpisodeInput(
                "Show - S01E20a - Hooky Fester1500.mkv",
                ParseResult(
                    season=1,
                    episodes=(20,),
                    segment_hint="a",
                    title_hint="Hooky Fester1500",
                ),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(catalog),
    )

    assignment = result.assignments[0]
    assert assignment.status is AssignmentStatus.UNRESOLVED
    assert not assignment.episodes
    assert not any(
        reason.startswith("segment-title-source-bracket-tag:")
        for reason in assignment.evidence.reasons
    )


def test_segment_title_keeps_nonnumeric_bracket_suffix_conservative(
    tmp_path: Path,
) -> None:
    catalog = [{"id": 3012, "season": 1, "number": 20, "name": "Hooky"}]
    result = assign_episode_group(
        _show(NumberingMode.SEGMENT_TITLE),
        (
            SourceEpisodeInput(
                "Show - S01E20a - Hooky [Remastered].mkv",
                ParseResult(
                    season=1,
                    episodes=(20,),
                    segment_hint="a",
                    title_hint="Hooky Remastered",
                ),
            ),
        ),
        TvmazeCatalogCache(tmp_path / "cache"),
        CountingGetter(catalog),
    )

    assignment = result.assignments[0]
    assert assignment.status is AssignmentStatus.UNRESOLVED
    assert not assignment.episodes
'''
    TESTS.write_text(text + additions, encoding="utf-8")


def main() -> None:
    patch_strict()
    patch_tests()


if __name__ == "__main__":
    main()
