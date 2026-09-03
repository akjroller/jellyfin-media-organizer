from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"integration point changed for {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "jellyfin_show_organizer/_show_resolver_core.py",
    '''def normalize_show_identity(value: str) -> str:\n    normalized = unicodedata.normalize("NFKC", value).casefold()\n    normalized = re.sub(r"[^\\w]+", " ", normalized, flags=re.UNICODE)\n    return " ".join(normalized.split())\n''',
    '''def normalize_show_identity(value: str) -> str:\n    decomposed = unicodedata.normalize("NFKD", value)\n    normalized = "".join(\n        character\n        for character in decomposed\n        if unicodedata.category(character) != "Mn"\n    ).casefold()\n    normalized = re.sub(r"[^\\w]+", " ", normalized, flags=re.UNICODE)\n    return " ".join(normalized.split())\n''',
)

replace_once(
    "jellyfin_show_organizer/filename_parser.py",
    '''_GENERIC_SEASON_DIR = re.compile(r"(?i)^(?:season[ ._-]*\\d{1,2}|s\\d{1,2})$")\n_CHECKSUM = re.compile(r"(?i)(?:^|\\s)[A-F0-9]{8}(?=$|\\s)")\n''',
    '''_GENERIC_SEASON_DIR = re.compile(r"(?i)^(?:season[ ._-]*\\d{1,2}|s\\d{1,2})$")\n_SEASON_COLLECTION = re.compile(\n    r"(?i)^(?P<series>.+?)[ ._-]+(?:s|season[ ._-]*)(?P<season>\\d{1,2})"\n    r"(?=$|[ ._-])"\n)\n_CHECKSUM = re.compile(r"(?i)(?:^|\\s)[A-F0-9]{8}(?=$|\\s)")\n''',
)

replace_once(
    "jellyfin_show_organizer/filename_parser.py",
    "def _parent_confirmed_prefixed_series(\n",
    '''def _compact_parent_abbreviation_matches(compact: str, parent: str) -> bool:\n    compact_tokens = re.findall(r"[A-Za-z0-9]+", _normalize_text(compact).casefold())\n    parent_tokens = re.findall(r"[A-Za-z0-9]+", _normalize_text(parent).casefold())\n    if len(compact_tokens) != 1 or not 2 <= len(parent_tokens) <= 5:\n        return False\n    if any(len(token) < 3 for token in parent_tokens):\n        return False\n\n    value = compact_tokens[0]\n    solutions = 0\n\n    def walk(token_index: int, offset: int, shortened: bool) -> None:\n        nonlocal solutions\n        if solutions > 1:\n            return\n        if token_index == len(parent_tokens):\n            if offset == len(value) and shortened:\n                solutions += 1\n            return\n        token = parent_tokens[token_index]\n        remaining_tokens = len(parent_tokens) - token_index - 1\n        max_length = min(len(token), len(value) - offset - 2 * remaining_tokens)\n        for length in range(2, max_length + 1):\n            piece = value[offset : offset + length]\n            if token.startswith(piece):\n                walk(\n                    token_index + 1,\n                    offset + length,\n                    shortened or length < len(token),\n                )\n\n    walk(0, 0, False)\n    return solutions == 1\n\n\ndef _season_collection_context(\n    path: PurePosixPath,\n    leaf_series: str | None,\n    episode: int,\n) -> tuple[str, int, int | None] | None:\n    if leaf_series is None or episode <= 0:\n        return None\n    leaf_tokens = re.findall(\n        r"[A-Za-z0-9]+", _normalize_text(leaf_series).casefold()\n    )\n    if len(leaf_tokens) < 3:\n        return None\n\n    candidates: list[tuple[str, int, int | None]] = []\n    for component in reversed(path.parts[:-1]):\n        match = _SEASON_COLLECTION.search(component)\n        if match is None:\n            continue\n        parent_series, parent_year = _series_and_year(match.group("series"))\n        if parent_series is None:\n            continue\n        parent_tokens = re.findall(\n            r"[A-Za-z0-9]+", _normalize_text(parent_series).casefold()\n        )\n        if len(parent_tokens) < 2:\n            continue\n        exact = leaf_tokens == parent_tokens\n        one_suffix = (\n            len(leaf_tokens) == len(parent_tokens) + 1\n            and leaf_tokens[: len(parent_tokens)] == parent_tokens\n            and len(leaf_tokens[-1]) >= 3\n            and not leaf_tokens[-1].isdigit()\n        )\n        if not exact and not one_suffix:\n            continue\n        candidates.append((parent_series, int(match.group("season")), parent_year))\n\n    unique = {\n        (series.casefold(), season, year): (series, season, year)\n        for series, season, year in candidates\n    }\n    if len(unique) != 1:\n        return None\n    return next(iter(unique.values()))\n\n\ndef _parent_confirmed_prefixed_series(\n''',
)

replace_once(
    "jellyfin_show_organizer/filename_parser.py",
    '''    if remainder_series.casefold() != parent_series.casefold():\n        return None\n''',
    '''    if (\n        remainder_series.casefold() != parent_series.casefold()\n        and not _compact_parent_abbreviation_matches(remainder_series, parent_series)\n    ):\n        return None\n''',
)

replace_once(
    "jellyfin_show_organizer/filename_parser.py",
    '''    match = _ABSOLUTE.search(cleaned_stem)\n    if match is not None:\n        source = match.group("series")\n        series, year = _series_and_year(source)\n        return ParseResult(\n            series_hint=series,\n            series_aliases=_series_aliases(series, source),\n            absolute_episode=int(match.group("episode")),\n            year=year,\n            embedded_tvmaze_id=embedded_id,\n            title_hint=_title_hint(cleaned_stem, match.end()),\n        )\n''',
    '''    match = _ABSOLUTE.search(cleaned_stem)\n    if match is not None:\n        source = match.group("series")\n        series, year = _series_and_year(source)\n        episode = int(match.group("episode"))\n        season_context = _season_collection_context(path, series, episode)\n        if season_context is not None:\n            context_series, context_season, context_year = season_context\n            return ParseResult(\n                series_hint=context_series,\n                season=context_season,\n                episodes=(episode,),\n                year=year if year is not None else context_year,\n                embedded_tvmaze_id=embedded_id,\n                title_hint=_title_hint(cleaned_stem, match.end()),\n            )\n        return ParseResult(\n            series_hint=series,\n            series_aliases=_series_aliases(series, source),\n            absolute_episode=episode,\n            year=year,\n            embedded_tvmaze_id=embedded_id,\n            title_hint=_title_hint(cleaned_stem, match.end()),\n        )\n''',
)

replace_once(
    "jellyfin_show_organizer/filename_parser.py",
    '''    match = _BARE_ABSOLUTE.search(cleaned_stem)\n    if match is not None and _bare_absolute_is_unambiguous(path, cleaned_stem, match):\n        source = match.group("series")\n        series, year = _series_and_year(source)\n        return ParseResult(\n            series_hint=series,\n            series_aliases=_series_aliases(series, source),\n            absolute_episode=int(match.group("episode")),\n            year=year,\n            embedded_tvmaze_id=embedded_id,\n            title_hint=_title_hint(cleaned_stem, match.end()),\n        )\n''',
    '''    match = _BARE_ABSOLUTE.search(cleaned_stem)\n    if match is not None and _bare_absolute_is_unambiguous(path, cleaned_stem, match):\n        source = match.group("series")\n        series, year = _series_and_year(source)\n        episode = int(match.group("episode"))\n        season_context = _season_collection_context(path, series, episode)\n        if season_context is not None:\n            context_series, context_season, context_year = season_context\n            return ParseResult(\n                series_hint=context_series,\n                season=context_season,\n                episodes=(episode,),\n                year=year if year is not None else context_year,\n                embedded_tvmaze_id=embedded_id,\n                title_hint=_title_hint(cleaned_stem, match.end()),\n            )\n        return ParseResult(\n            series_hint=series,\n            series_aliases=_series_aliases(series, source),\n            absolute_episode=episode,\n            year=year,\n            embedded_tvmaze_id=embedded_id,\n            title_hint=_title_hint(cleaned_stem, match.end()),\n        )\n''',
)
