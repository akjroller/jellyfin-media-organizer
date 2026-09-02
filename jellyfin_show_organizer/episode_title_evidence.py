from __future__ import annotations

import re
import unicodedata

from .models import ParseResult

_TRAILING_MEDIA_NOISE = re.compile(
    r"(?i)(?:[ ._-]+(?:repack|proper|v\d+|ntsc|pal|dvd|bd|aac\d+(?:[ ._-]+\d+)?))+$"
)
_COMPOSITE_SEPARATOR = re.compile(r"[&/]")


def normalize_episode_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def normalized_episode_title_hint(value: str) -> str:
    cleaned = _TRAILING_MEDIA_NOISE.sub("", value).strip(" ._-")
    return normalize_episode_title(cleaned)


def _has_explicit_composite_title(value: str) -> bool:
    for match in _COMPOSITE_SEPARATOR.finditer(value):
        left = normalize_episode_title(value[: match.start()])
        right = normalized_episode_title_hint(value[match.end() :])
        if left and right:
            return True
    return False


def is_title_authoritative_aired_parse(parse: ParseResult) -> bool:
    if (
        parse.season is None
        or not parse.episodes
        or parse.title_hint is None
        or not parse.title_hint.strip()
        or parse.absolute_episode is not None
        or parse.special_episode is not None
        or parse.special_kind is not None
        or parse.episode_date is not None
        or parse.segment_hint is not None
    ):
        return False
    if len(parse.episodes) == 1:
        return True
    if len(parse.episodes) != 2:
        return False
    first, second = parse.episodes
    return second == first + 1 and _has_explicit_composite_title(parse.title_hint)


def is_composite_title_authoritative_group(parses: tuple[ParseResult, ...]) -> bool:
    if len(parses) < 3 or not all(is_title_authoritative_aired_parse(parse) for parse in parses):
        return False
    return any(len(parse.episodes) == 2 for parse in parses)
