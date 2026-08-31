from __future__ import annotations

import re
from pathlib import PurePosixPath

from .models import ParseResult

_LEADING_TAGS = re.compile(r"^(?:\[[^\]]+\][ ._-]*)+")
_TRAILING_YEAR = re.compile(r"(?:^|[\s(])(?P<year>(?:18|19|20|21)\d{2})\)?$")
_TVMAZE_ID = re.compile(r"(?i)(?:\[?tvmaze(?:[ ._-]?id)?[ ._-]?)(?P<id>\d+)\]?")
_SXE = re.compile(
    r"(?i)(?<![A-Za-z0-9])S(?P<season>\d{1,2})E(?P<episode>\d{1,3})"
    r"(?P<segment>[A-Za-z](?!\d))?"
    r"(?P<tail>(?:E\d{1,3}|-E?\d{1,3})*)"
)
_X_NOTATION = re.compile(r"(?i)(?<!\d)(?P<season>\d{1,2})x(?P<episode>\d{1,3})(?!\d)")
_EPISODE_WORD = re.compile(
    r"(?i)(?<![A-Za-z0-9])episode[ ._-]*(?P<episode>\d{1,3})"
    r"(?P<segment>[A-Za-z])?(?![A-Za-z0-9])"
)
_PARENTHESIZED_ABSOLUTE = re.compile(
    r"^(?P<series>.+?)\s*\((?P<episode>\d{1,3})\)(?=$|[ ._\-\[])"
)
_ABSOLUTE = re.compile(
    r"^(?P<series>.+?)[ ._]+-[ ._]+(?P<episode>\d{1,3})"
    r"(?=$|[ ._\-\[(])"
)
_TECH_SUFFIX = re.compile(
    r"(?i)(?:^|\s)(?:2160p|1080p|720p|576p|480p|webrip|web-dl|web|bluray|"
    r"bdrip|hdtv|dvdrip|x264|x265|h264|h265|hevc|av1|aac|flac|synth)"
    r"(?=[\s._-]|$)"
)


def _normalize_text(value: str) -> str:
    value = _LEADING_TAGS.sub("", value)
    value = value.replace("_", " ").replace(".", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -_.[]()")


def _series_and_year(value: str) -> tuple[str | None, int | None]:
    series = _normalize_text(value)
    if not series:
        return None, None

    year_match = _TRAILING_YEAR.search(series)
    if year_match is None:
        return series, None

    year = int(year_match.group("year"))
    series = series[: year_match.start()].strip()
    return series or None, year


def _episode_list(first: int, tail: str) -> tuple[int, ...]:
    episodes = [first]
    for value in re.findall(r"\d{1,3}", tail):
        episode = int(value)
        if episode not in episodes:
            episodes.append(episode)
    return tuple(episodes)


def _title_hint(stem: str, start: int) -> str | None:
    value = stem[start:]
    value = _TVMAZE_ID.sub(" ", value)
    value = _normalize_text(value)
    if not value:
        return None

    suffix = _TECH_SUFFIX.search(value)
    if suffix is not None:
        value = value[: suffix.start()].strip(" -_.[]()")
    return value or None


def _embedded_tvmaze_id(stem: str) -> int | None:
    match = _TVMAZE_ID.search(stem)
    return int(match.group("id")) if match is not None else None


def _fallback_series(path: PurePosixPath) -> tuple[str | None, int | None]:
    if len(path.parts) < 2:
        return None, None
    return _series_and_year(path.parent.name)


def parse_video_path(relative_path: str) -> ParseResult:
    """Parse deterministic episode hints from one relative video path.

    Only filename and immediate-parent text are inspected. The parser performs
    no filesystem access and no provider or network calls.
    """

    normalized_path = relative_path.replace("\\", "/")
    path = PurePosixPath(normalized_path)
    stem = path.stem
    embedded_id = _embedded_tvmaze_id(stem)

    match = _SXE.search(stem)
    if match is not None:
        series, year = _series_and_year(stem[: match.start()])
        if series is None:
            series, year = _fallback_series(path)
        return ParseResult(
            series_hint=series,
            season=int(match.group("season")),
            episodes=_episode_list(int(match.group("episode")), match.group("tail")),
            segment_hint=(match.group("segment") or None),
            year=year,
            embedded_tvmaze_id=embedded_id,
            title_hint=_title_hint(stem, match.end()),
        )

    match = _X_NOTATION.search(stem)
    if match is not None:
        series, year = _series_and_year(stem[: match.start()])
        if series is None:
            series, year = _fallback_series(path)
        return ParseResult(
            series_hint=series,
            season=int(match.group("season")),
            episodes=(int(match.group("episode")),),
            year=year,
            embedded_tvmaze_id=embedded_id,
            title_hint=_title_hint(stem, match.end()),
        )

    match = _EPISODE_WORD.search(stem)
    if match is not None:
        series, year = _series_and_year(stem[: match.start()])
        if series is None:
            series, year = _fallback_series(path)
        return ParseResult(
            series_hint=series,
            absolute_episode=int(match.group("episode")),
            segment_hint=(match.group("segment") or None),
            year=year,
            embedded_tvmaze_id=embedded_id,
            title_hint=_title_hint(stem, match.end()),
        )

    cleaned_stem = _TVMAZE_ID.sub(" ", stem)
    match = _PARENTHESIZED_ABSOLUTE.search(cleaned_stem)
    if match is not None:
        series, year = _series_and_year(match.group("series"))
        return ParseResult(
            series_hint=series,
            absolute_episode=int(match.group("episode")),
            year=year,
            embedded_tvmaze_id=embedded_id,
            title_hint=_title_hint(cleaned_stem, match.end()),
        )

    match = _ABSOLUTE.search(cleaned_stem)
    if match is not None:
        series, year = _series_and_year(match.group("series"))
        return ParseResult(
            series_hint=series,
            absolute_episode=int(match.group("episode")),
            year=year,
            embedded_tvmaze_id=embedded_id,
            title_hint=_title_hint(cleaned_stem, match.end()),
        )

    series, year = _fallback_series(path)
    return ParseResult(
        series_hint=series,
        year=year,
        embedded_tvmaze_id=embedded_id,
    )
