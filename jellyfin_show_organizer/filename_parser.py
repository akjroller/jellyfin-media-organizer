from __future__ import annotations

import re
from datetime import date
from pathlib import PurePosixPath

from .models import ParseResult

_LEADING_TAGS = re.compile(r"^(?:\[[^\]]+\][ ._-]*)+")
_TRAILING_YEAR = re.compile(r"(?:^|[\s(])(?P<year>(?:18|19|20|21)\d{2})\)?$")
_TVMAZE_ID = re.compile(r"(?i)(?:\[?tvmaze(?:[ ._-]?id)?[ ._-]?)(?P<id>\d+)\]?")
_SXE = re.compile(
    r"(?i)S(?P<season>\d{1,2})[ ._-]*E(?P<episode>\d{1,3})(?!\d)"
    r"(?P<segment>[A-Za-z](?!\d))?"
    r"(?P<tail>(?:(?:[ ._-]*E\d{1,3}(?!\d))|"
    r"(?:[ ._-]*-[ ._-]*E?\d{1,3}(?!\d)))*)"
)
_X_NOTATION = re.compile(r"(?i)(?<!\d)(?P<season>\d{1,2})x(?P<episode>\d{1,3})(?!\d)")
_EPISODE_WORD = re.compile(
    r"(?i)(?<![A-Za-z0-9])episode[ ._-]*(?P<episode>\d{1,3})"
    r"(?P<segment>[A-Za-z])?(?![A-Za-z0-9])"
)
_SPECIAL_NUMBERING = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?P<kind>OVA|OAD)[ ._-]*(?P<episode>\d{1,3})(?!\d)"
)
_EPISODE_DATE = re.compile(
    r"(?<!\d)(?P<year>(?:18|19|20|21)\d{2})[-._]"
    r"(?P<month>0[1-9]|1[0-2])[-._](?P<day>0[1-9]|[12]\d|3[01])(?!\d)"
)
_LEGACY_BRACKETED = re.compile(
    r"(?i)^(?P<series>.+?)"
    r"\[\s*season[ ._-]*(?P<season>\d{1,2})\s*\]"
    r"\s*\[\s*episod(?:e)?[ ._-]*(?P<episode>\d{1,3})"
    r"(?P<segment>[A-Za-z])?\s*\]"
)
_DUAL_ABSOLUTE_AFTER_SXE = re.compile(
    r"^[ ._-]*(?:\((?P<paren>\d{1,3})\)|\[(?P<bracket>\d{1,3})\])"
    r"(?=$|[ ._\-\[])"
)
_PARENTHESIZED_ABSOLUTE = re.compile(
    r"^(?P<series>.+?)\s*\((?P<episode>\d{1,3})\)(?=$|[ ._\-\[])"
)
_ABSOLUTE = re.compile(
    r"^(?P<series>.+?)[ ._]+-[ ._]+(?P<episode>\d{1,3})(?:v\d+)?"
    r"(?=$|[ ._\-\[(])"
)
_BARE_ABSOLUTE = re.compile(
    r"^(?P<series>.+?)[ ._-]+(?P<episode>\d{1,3})(?:v\d+)?(?=$|\s|[([])"
)
_TECH_SUFFIX = re.compile(
    r"(?i)(?:^|\s)(?:2160p|1080p|720p|576p|480p|webrip|web-dl|bluray|"
    r"bdrip|hdtv|dvdrip|remux|x264|x265|h264|h265|hevc|av1|aac|flac|"
    r"opus|synth|10bit|hi10)"
    r"(?=[\s._-]|$)"
)
_RELEASE_TAIL = re.compile(
    r"(?i)(?:^|[ ._\-\[(])(?:2160p|1080p|720p|576p|480p|webrip|web-dl|"
    r"bluray|bdrip|hdtv|dvdrip|remux|x264|x265|h264|h265|hevc|av1|aac|"
    r"flac|opus|10bit|hi10)(?=$|[ ._\-\])])"
)
_SEASON_NOISE = re.compile(
    r"(?i)(?:^|[ ._\-\[(])(?:s(?:eason)?[ ._-]*\d{1,2})"
    r"(?=$|[ ._\-\])])"
)
_GENERIC_SEASON_DIR = re.compile(r"(?i)^(?:season[ ._-]*\d{1,2}|s\d{1,2})$")
_CHECKSUM = re.compile(r"(?i)(?:^|\s)[A-F0-9]{8}(?=$|\s)")
_UNBRACKETED_RELEASE_PREFIX = re.compile(r"^(?P<tag>[A-Za-z0-9]+)-(?P<series>.+)$")


def _normalize_text(value: str) -> str:
    value = _LEADING_TAGS.sub("", value)
    value = re.sub(r"[\[\](){}]+", " ", value)
    value = value.replace("_", " ").replace(".", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -_.[]()")


def _series_and_year(value: str) -> tuple[str | None, int | None]:
    series = _normalize_text(value)
    if not series:
        return None, None

    release_match = _RELEASE_TAIL.search(series)
    if release_match is not None and release_match.start() > 0:
        series = series[: release_match.start()].strip()

    season_match = _SEASON_NOISE.search(series)
    if season_match is not None and season_match.start() > 0:
        series = series[: season_match.start()].strip()

    year_match = _TRAILING_YEAR.search(series)
    if year_match is None:
        return series or None, None

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


def _dual_absolute_after_sxe(
    stem: str,
    match: re.Match[str],
) -> tuple[int | None, int]:
    remainder = stem[match.end() :]
    candidate = _DUAL_ABSOLUTE_AFTER_SXE.match(remainder)
    if candidate is None:
        return None, match.end()

    trailing = remainder[candidate.end() :]
    if _DUAL_ABSOLUTE_AFTER_SXE.match(trailing) is not None:
        return None, match.end()

    value = candidate.group("paren") or candidate.group("bracket")
    assert value is not None
    return int(value), match.end() + candidate.end()


def _title_hint(stem: str, start: int) -> str | None:
    value = stem[start:]
    value = _TVMAZE_ID.sub(" ", value)
    value = _normalize_text(value)
    if not value:
        return None

    suffix = _TECH_SUFFIX.search(value)
    checksum = _CHECKSUM.search(value)
    cut_points = [match.start() for match in (suffix, checksum) if match is not None]
    if cut_points:
        value = value[: min(cut_points)].strip(" -_.[]()")
    return value or None


def _embedded_tvmaze_id(stem: str) -> int | None:
    match = _TVMAZE_ID.search(stem)
    return int(match.group("id")) if match is not None else None


def _episode_date_value(match: re.Match[str]) -> str | None:
    value = f"{match.group('year')}-{match.group('month')}-{match.group('day')}"
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _fallback_series(path: PurePosixPath) -> tuple[str | None, int | None]:
    if len(path.parts) < 2:
        return None, None

    for component in reversed(path.parts[:-1]):
        normalized = _normalize_text(component)
        if not normalized or _GENERIC_SEASON_DIR.fullmatch(normalized):
            continue
        series, year = _series_and_year(component)
        if series:
            return series, year
    return None, None


def _parent_confirmed_prefixed_series(
    stem: str,
    path: PurePosixPath,
    match: re.Match[str],
) -> tuple[str, int | None] | None:
    """Strip one leading release token only when the parent proves the remainder."""

    if len(path.parts) < 2:
        return None
    parent = path.parts[-2]
    parent_match = _SXE.search(parent)
    if parent_match is None:
        return None

    file_episodes = _episode_list(int(match.group("episode")), match.group("tail"))
    parent_episodes = _episode_list(
        int(parent_match.group("episode")), parent_match.group("tail")
    )
    if (
        int(match.group("season")) != int(parent_match.group("season"))
        or file_episodes != parent_episodes
        or (match.group("segment") or None) != (parent_match.group("segment") or None)
    ):
        return None

    prefix = stem[: match.start()].strip(" ._-")
    prefixed = _UNBRACKETED_RELEASE_PREFIX.fullmatch(prefix)
    if prefixed is None:
        return None

    remainder_series, remainder_year = _series_and_year(prefixed.group("series"))
    parent_series, parent_year = _series_and_year(parent[: parent_match.start()])
    if remainder_series is None or parent_series is None:
        return None
    if remainder_series.casefold() != parent_series.casefold():
        return None
    if (
        remainder_year is not None
        and parent_year is not None
        and remainder_year != parent_year
    ):
        return None
    return parent_series, remainder_year if remainder_year is not None else parent_year


def _series_for_match(
    stem: str,
    path: PurePosixPath,
    match: re.Match[str],
) -> tuple[str | None, int | None]:
    series, year = _series_and_year(stem[: match.start()])
    embedded_in_token = match.start() > 0 and stem[match.start() - 1].isalnum()
    if series is None or embedded_in_token:
        fallback_series, fallback_year = _fallback_series(path)
        if fallback_series is not None:
            return fallback_series, fallback_year
    return series, year


def _bare_absolute_is_unambiguous(
    path: PurePosixPath,
    stem: str,
    match: re.Match[str],
) -> bool:
    remainder = stem[match.end() :]
    if re.match(
        r"(?i)^\s*[([]?\s*(?:2160p|1080p|720p|576p|480p|web-dl|webrip|"
        r"bluray|bdrip|hdtv|dvdrip|remux|x264|x265|h264|h265|hevc|av1|aac|"
        r"flac|opus|10bit|hi10)",
        remainder,
    ):
        return True

    explicit_series, _ = _series_and_year(match.group("series"))
    fallback_series, _ = _fallback_series(path)
    return (
        explicit_series is not None
        and fallback_series is not None
        and explicit_series.casefold() == fallback_series.casefold()
    )


def _ancestor_episode_context(path: PurePosixPath) -> ParseResult | None:
    candidates: list[ParseResult] = []
    for component in reversed(path.parts[:-1]):
        match = _SXE.search(component)
        if match is not None:
            series, year = _series_and_year(component[: match.start()])
            candidates.append(
                ParseResult(
                    series_hint=series,
                    season=int(match.group("season")),
                    episodes=_episode_list(
                        int(match.group("episode")), match.group("tail")
                    ),
                    segment_hint=(match.group("segment") or None),
                    year=year,
                )
            )
            continue

        match = _X_NOTATION.search(component)
        if match is not None:
            series, year = _series_and_year(component[: match.start()])
            candidates.append(
                ParseResult(
                    series_hint=series,
                    season=int(match.group("season")),
                    episodes=(int(match.group("episode")),),
                    year=year,
                )
            )

    if len(candidates) != 1:
        return None
    return candidates[0]


def parse_video_path(relative_path: str) -> ParseResult:
    """Parse deterministic episode hints from one relative video path.

    Only path text is inspected. The parser performs no filesystem access and
    no provider or network calls. Ambiguous compact-number forms remain
    unresolved rather than being guessed.
    """

    normalized_path = relative_path.replace("\\", "/")
    path = PurePosixPath(normalized_path)
    stem = path.stem
    embedded_id = _embedded_tvmaze_id(stem)

    match = _SXE.search(stem)
    if match is not None:
        series, year = _series_for_match(stem, path, match)
        parent_confirmed = _parent_confirmed_prefixed_series(stem, path, match)
        if parent_confirmed is not None:
            series, year = parent_confirmed
        absolute_episode, title_start = _dual_absolute_after_sxe(stem, match)
        return ParseResult(
            series_hint=series,
            season=int(match.group("season")),
            episodes=_episode_list(int(match.group("episode")), match.group("tail")),
            absolute_episode=absolute_episode,
            segment_hint=(match.group("segment") or None),
            year=year,
            embedded_tvmaze_id=embedded_id,
            title_hint=_title_hint(stem, title_start),
        )

    match = _X_NOTATION.search(stem)
    if match is not None:
        series, year = _series_for_match(stem, path, match)
        return ParseResult(
            series_hint=series,
            season=int(match.group("season")),
            episodes=(int(match.group("episode")),),
            year=year,
            embedded_tvmaze_id=embedded_id,
            title_hint=_title_hint(stem, match.end()),
        )

    match = _LEGACY_BRACKETED.search(stem)
    if match is not None:
        series, year = _series_and_year(match.group("series"))
        return ParseResult(
            series_hint=series,
            season=int(match.group("season")),
            episodes=(int(match.group("episode")),),
            segment_hint=(match.group("segment") or None),
            year=year,
            embedded_tvmaze_id=embedded_id,
            title_hint=_title_hint(stem, match.end()),
        )

    match = _SPECIAL_NUMBERING.search(stem)
    if match is not None:
        series, year = _series_for_match(stem, path, match)
        return ParseResult(
            series_hint=series,
            special_kind=match.group("kind").casefold(),
            special_episode=int(match.group("episode")),
            year=year,
            embedded_tvmaze_id=embedded_id,
            title_hint=_title_hint(stem, match.end()),
        )

    date_match = _EPISODE_DATE.search(stem)
    if date_match is not None:
        episode_date = _episode_date_value(date_match)
        if episode_date is not None:
            series, year = _series_for_match(stem, path, date_match)
            return ParseResult(
                series_hint=series,
                episode_date=episode_date,
                year=year,
                embedded_tvmaze_id=embedded_id,
                title_hint=_title_hint(stem, date_match.end()),
            )

    match = _EPISODE_WORD.search(stem)
    if match is not None:
        series, year = _series_for_match(stem, path, match)
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

    match = _BARE_ABSOLUTE.search(cleaned_stem)
    if match is not None and _bare_absolute_is_unambiguous(path, cleaned_stem, match):
        series, year = _series_and_year(match.group("series"))
        return ParseResult(
            series_hint=series,
            absolute_episode=int(match.group("episode")),
            year=year,
            embedded_tvmaze_id=embedded_id,
            title_hint=_title_hint(cleaned_stem, match.end()),
        )

    context = _ancestor_episode_context(path)
    if context is not None:
        return ParseResult(
            series_hint=context.series_hint,
            season=context.season,
            episodes=context.episodes,
            absolute_episode=context.absolute_episode,
            segment_hint=context.segment_hint,
            year=context.year,
            embedded_tvmaze_id=embedded_id,
        )

    series, year = _fallback_series(path)
    return ParseResult(
        series_hint=series,
        year=year,
        embedded_tvmaze_id=embedded_id,
    )
