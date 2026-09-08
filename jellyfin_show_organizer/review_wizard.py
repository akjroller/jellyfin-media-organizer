from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any, TextIO, cast

from .destination import (
    DestinationStatus,
    JellyfinProvider,
    JellyfinProviderIdentifier,
    build_episode_destination,
    build_extra_destination,
)
from .episode_assignment_strict import AssignmentStatus, SourceEpisodeAssignment
from .models import CanonicalShow, ExtraDecision, MatchEvidence, NumberingMode
from .providers import MetadataProvider, ProviderEpisode, ProviderShow

InputFn = Callable[[str], str]


class ReviewConfigurationError(ValueError):
    """Raised when a review action cannot be completed safely."""


def _mapping(value: object) -> Mapping[str, object] | None:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _record_source(record: Mapping[str, object]) -> str:
    source = _mapping(record.get("source"))
    if source is None:
        raise ReviewConfigurationError("plan record is missing source identity")
    path = _string(source.get("relative_path"))
    if path is None:
        raise ReviewConfigurationError("plan record is missing source relative_path")
    return path


def _record_extension(record: Mapping[str, object]) -> str:
    source = _mapping(record.get("source"))
    if source is None:
        raise ReviewConfigurationError("plan record is missing source identity")
    extension = _string(source.get("extension"))
    if extension is None:
        raise ReviewConfigurationError("plan record is missing source extension")
    return extension


def _record_group_key(record: Mapping[str, object]) -> str:
    show = _mapping(record.get("show"))
    if show is not None:
        source_key = _string(show.get("source_key"))
        if source_key is not None:
            return source_key
    source = PurePosixPath(_record_source(record).replace("\\", "/"))
    if len(source.parts) > 1:
        return source.parts[0]
    parse = _mapping(record.get("parse"))
    if parse is not None:
        series_hint = _string(parse.get("series_hint"))
        if series_hint is not None:
            return series_hint
    return source.stem


def _record_series_hint(record: Mapping[str, object]) -> str:
    parse = _mapping(record.get("parse"))
    if parse is not None:
        series_hint = _string(parse.get("series_hint"))
        if series_hint is not None:
            return series_hint
    return _record_group_key(record)


def _record_parse_int(record: Mapping[str, object], field: str) -> int | None:
    parse = _mapping(record.get("parse"))
    return _integer(parse.get(field)) if parse is not None else None


def _record_parse_episodes(record: Mapping[str, object]) -> tuple[int, ...]:
    parse = _mapping(record.get("parse"))
    if parse is None:
        return ()
    raw = parse.get("episodes")
    if not isinstance(raw, list | tuple):
        return ()
    values = tuple(
        value for value in raw if isinstance(value, int) and not isinstance(value, bool)
    )
    return values if len(values) == len(raw) else ()


def _record_title_hint(record: Mapping[str, object]) -> str | None:
    parse = _mapping(record.get("parse"))
    return _string(parse.get("title_hint")) if parse is not None else None


def _prompt_choice(
    prompt: str,
    choices: Mapping[str, str],
    *,
    input_fn: InputFn,
    output: TextIO,
) -> str:
    while True:
        output.write(prompt + "\n")
        for key, description in choices.items():
            output.write(f"  {key}) {description}\n")
        value = input_fn("> ").strip().casefold()
        if value in choices:
            return value
        output.write("Invalid choice.\n")


def _prompt_int(
    prompt: str,
    *,
    input_fn: InputFn,
    output: TextIO,
    default: int | None = None,
) -> int | None:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw = input_fn(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            return default
        if raw.casefold() in {"cancel", "c"}:
            return None
        try:
            value = int(raw)
        except ValueError:
            output.write("Enter an integer or 'cancel'.\n")
            continue
        if value < 0:
            output.write("Value cannot be negative.\n")
            continue
        return value


def _source_key(value: str) -> str:
    return value.replace("\\", "/").casefold()


def _table(raw: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = raw.setdefault(name, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ReviewConfigurationError(f"override {name} must be an array of tables")
    return cast(list[dict[str, Any]], value)


def _remove_source_entries(
    raw: dict[str, Any], table_name: str, sources: Sequence[str]
) -> None:
    wanted = {_source_key(source) for source in sources}
    table = _table(raw, table_name)
    table[:] = [
        item
        for item in table
        if not isinstance(item.get("source"), str)
        or _source_key(cast(str, item["source"])) not in wanted
    ]


def _remove_exact_dispositions(raw: dict[str, Any], sources: Sequence[str]) -> None:
    for table_name in (
        "episode_decisions",
        "source_holds",
        "reviewed_episode_decisions",
        "extra_decisions",
    ):
        _remove_source_entries(raw, table_name, sources)


def _find_show_entry(raw: dict[str, Any], key: str) -> dict[str, Any] | None:
    normalized = key.casefold()
    return next(
        (
            item
            for item in _table(raw, "shows")
            if isinstance(item.get("key"), str)
            and cast(str, item["key"]).casefold() == normalized
        ),
        None,
    )


def _upsert_show(raw: dict[str, Any], key: str, show: ProviderShow) -> dict[str, Any]:
    entry = _find_show_entry(raw, key)
    if entry is None:
        entry = {
            "key": key,
            "aliases": [],
            "numbering_mode": "aired",
            "title_preference": "provider",
        }
        _table(raw, "shows").append(entry)
    entry.pop("tvmaze_id", None)
    entry["provider"] = show.identity.provider
    entry["provider_id"] = show.identity.value
    entry.setdefault("numbering_mode", "aired")
    entry.setdefault("title_preference", "provider")
    entry.setdefault("aliases", [])
    if show.year is not None:
        entry["year"] = show.year
    return entry


def _existing_jellyfin_ids(
    raw: dict[str, Any], show_key: str
) -> tuple[JellyfinProviderIdentifier, ...]:
    entry = _find_show_entry(raw, show_key)
    if entry is None:
        return ()
    values: list[JellyfinProviderIdentifier] = []
    for field, provider in (
        ("tmdb_id", JellyfinProvider.TMDB),
        ("tvdb_id", JellyfinProvider.TVDB),
        ("imdb_id", JellyfinProvider.IMDB),
    ):
        value = entry.get(field)
        if value is not None:
            values.append(JellyfinProviderIdentifier(provider, str(value)))
    return tuple(values)


def _prompt_identifier(
    label: str,
    provider: JellyfinProvider,
    current: str | None,
    *,
    input_fn: InputFn,
    output: TextIO,
) -> JellyfinProviderIdentifier | None:
    while True:
        current_hint = f" [{current}]" if current is not None else ""
        raw = input_fn(
            f"{label}{current_hint} (blank keeps current, '-' clears): "
        ).strip()
        if not raw:
            return (
                JellyfinProviderIdentifier(provider, current)
                if current is not None
                else None
            )
        if raw == "-":
            return None
        try:
            return JellyfinProviderIdentifier(provider, raw)
        except ValueError as exc:
            output.write(f"Invalid {label}: {exc}\n")


def _prompt_jellyfin_ids(
    raw: dict[str, Any],
    show_key: str,
    *,
    input_fn: InputFn,
    output: TextIO,
) -> tuple[JellyfinProviderIdentifier, ...]:
    existing = _existing_jellyfin_ids(raw, show_key)
    by_provider = {identifier.provider: identifier.value for identifier in existing}
    if input_fn(
        "Edit Jellyfin show IDs (TVDB/TMDB/IMDb)? [y/N]: "
    ).strip().casefold() not in {
        "y",
        "yes",
    }:
        return existing
    identifiers = []
    for label, provider in (
        ("TVDB show ID", JellyfinProvider.TVDB),
        ("TMDB show ID", JellyfinProvider.TMDB),
        ("IMDb show ID", JellyfinProvider.IMDB),
    ):
        identifier = _prompt_identifier(
            label,
            provider,
            by_provider.get(provider),
            input_fn=input_fn,
            output=output,
        )
        if identifier is not None:
            identifiers.append(identifier)
    return tuple(identifiers)


def _write_show_metadata(
    raw: dict[str, Any],
    key: str,
    show: ProviderShow,
    identifiers: Sequence[JellyfinProviderIdentifier],
) -> None:
    entry = _upsert_show(raw, key, show)
    for field in ("tmdb_id", "tvdb_id", "imdb_id"):
        entry.pop(field, None)
    field_by_provider = {
        JellyfinProvider.TMDB: "tmdb_id",
        JellyfinProvider.TVDB: "tvdb_id",
        JellyfinProvider.IMDB: "imdb_id",
    }
    for identifier in identifiers:
        entry[field_by_provider[identifier.provider]] = identifier.value


def _add_reviewed_episode(
    raw: dict[str, Any],
    *,
    source: str,
    show: ProviderShow,
    episode: ProviderEpisode,
    lookup_mode: str,
    reason: str,
) -> None:
    if episode.number is None:
        raise ReviewConfigurationError("provider episode has no numeric coordinate")
    _remove_exact_dispositions(raw, (source,))
    _remove_source_entries(raw, "duplicate_preferences", (source,))
    entry: dict[str, Any] = {
        "source": source,
        "show_provider": show.identity.provider,
        "show_provider_id": show.identity.value,
        "episode_provider": episode.identity.provider,
        "episode_provider_id": episode.identity.value,
        "season": episode.season,
        "number": episode.number,
        "title": episode.title,
        "lookup_mode": lookup_mode,
        "reasons": [reason, f"provider episode confirmed:{episode.identity.key}"],
    }
    if episode.airdate is not None:
        entry["airdate"] = episode.airdate
    _table(raw, "reviewed_episode_decisions").append(entry)


def _add_extra_decision(
    raw: dict[str, Any],
    *,
    source: str,
    show: ProviderShow,
    kind: str,
    display_title: str,
) -> None:
    _remove_exact_dispositions(raw, (source,))
    _remove_source_entries(raw, "duplicate_preferences", (source,))
    _table(raw, "extra_decisions").append(
        {
            "source": source,
            "show_provider": show.identity.provider,
            "show_provider_id": show.identity.value,
            "kind": kind,
            "display_title": display_title,
            "reasons": ["manual review classified source as an explicit extra"],
        }
    )


def _select_show(
    record: Mapping[str, object],
    provider: MetadataProvider,
    *,
    input_fn: InputFn,
    output: TextIO,
) -> ProviderShow | None:
    default_query = _record_series_hint(record)
    query = input_fn(f"Show search [{default_query}]: ").strip() or default_query
    snapshot = provider.search_shows(query)
    if not snapshot.resolved:
        output.write(f"Provider search unavailable: {snapshot.unresolved_reason}\n")
        return None
    if not snapshot.shows:
        output.write("Provider search returned no shows.\n")
        return None
    choices = snapshot.shows[:20]
    output.write("Provider show candidates:\n")
    for index, show in enumerate(choices, start=1):
        year = str(show.year) if show.year is not None else "unknown year"
        output.write(
            f"  {index}) {show.title} ({year}) "
            f"[{show.identity.provider}:{show.identity.value}]\n"
        )
    selected = _prompt_int(
        "Choose show number (or cancel)", input_fn=input_fn, output=output
    )
    if selected is None or selected == 0:
        return None
    if selected > len(choices):
        output.write("Selected show is outside the displayed candidate range.\n")
        return None
    return choices[selected - 1]


def _canonical_show(record: Mapping[str, object], show: ProviderShow) -> CanonicalShow:
    return CanonicalShow(
        source_key=_record_group_key(record),
        provider_identity=show.identity,
        title=show.title,
        year=show.year,
        numbering_mode=NumberingMode.AIRED,
    )


def _preview_episode(
    record: Mapping[str, object],
    show: ProviderShow,
    episode: ProviderEpisode,
    provider_ids: Sequence[JellyfinProviderIdentifier],
) -> str | None:
    if episode.number is None:
        return None
    assignment = SourceEpisodeAssignment(
        source_key=_record_source(record),
        status=AssignmentStatus.MATCHED,
        episodes=(episode,),
        evidence=MatchEvidence(
            method="manual-review-provider-confirmation",
            confidence=1.0,
            reasons=(f"provider-episode:{episode.identity.key}",),
        ),
    )
    destination = build_episode_destination(
        _canonical_show(record, show),
        assignment,
        _record_extension(record),
        provider_ids=provider_ids,
    )
    if destination.status is not DestinationStatus.READY:
        return None
    return destination.relative_path


def _find_episode_by_coordinate(
    episodes: Sequence[ProviderEpisode], season: int, number: int
) -> ProviderEpisode | None:
    matches = [
        episode
        for episode in episodes
        if episode.season == season and episode.number == number
    ]
    return matches[0] if len(matches) == 1 else None


def _find_episode_by_absolute(
    episodes: Sequence[ProviderEpisode], absolute: int
) -> ProviderEpisode | None:
    regular = [
        episode
        for episode in episodes
        if episode.season > 0 and episode.number is not None
    ]
    if absolute <= 0 or absolute > len(regular):
        return None
    return regular[absolute - 1]


def _find_episode_by_date(
    episodes: Sequence[ProviderEpisode], airdate: str
) -> ProviderEpisode | None:
    matches = [episode for episode in episodes if episode.airdate == airdate]
    return matches[0] if len(matches) == 1 else None


def _confirm_episode_resolution(
    raw: dict[str, Any],
    record: Mapping[str, object],
    show: ProviderShow,
    episode: ProviderEpisode,
    *,
    lookup_mode: str,
    reason: str,
    input_fn: InputFn,
    output: TextIO,
) -> bool:
    show_key = _record_group_key(record)
    provider_ids = _prompt_jellyfin_ids(
        raw,
        show_key,
        input_fn=input_fn,
        output=output,
    )
    destination = _preview_episode(record, show, episode, provider_ids)
    if destination is None:
        output.write(
            "A safe Jellyfin destination cannot be produced for that provider episode.\n"
        )
        return False
    coordinate = f"S{episode.season:02d}E{episode.number:02d}"
    output.write(
        f"Provider confirmation: {show.title} {coordinate} - {episode.title} "
        f"[{episode.identity.key}]\n"
    )
    output.write(f"Destination preview: {destination}\n")
    if input_fn("Write this decision? [y/N]: ").strip().casefold() not in {
        "y",
        "yes",
    }:
        output.write("Decision deferred.\n")
        return False
    source = _record_source(record)
    _write_show_metadata(raw, show_key, show, provider_ids)
    _add_reviewed_episode(
        raw,
        source=source,
        show=show,
        episode=episode,
        lookup_mode=lookup_mode,
        reason=reason,
    )
    return True


def _review_specific_episode(
    raw: dict[str, Any],
    record: Mapping[str, object],
    provider: MetadataProvider,
    *,
    input_fn: InputFn,
    output: TextIO,
) -> bool:
    show = _select_show(record, provider, input_fn=input_fn, output=output)
    if show is None:
        return False
    catalog = provider.episode_catalog(show.identity)
    if not catalog.resolved or catalog.errors:
        output.write("Provider episode catalog is unavailable or unsafe to use.\n")
        return False
    mode = _prompt_choice(
        "How do you want to identify the episode?",
        {
            "1": "Season / episode coordinate",
            "2": "Absolute episode number",
            "3": "Air date",
            "4": "Cancel / defer",
        },
        input_fn=input_fn,
        output=output,
    )
    episode: ProviderEpisode | None = None
    reason = "manual review provider-confirmed episode"
    lookup_mode = "coordinate"
    if mode == "1":
        default_season = _record_parse_int(record, "season")
        parsed = _record_parse_episodes(record)
        default_episode = parsed[0] if len(parsed) == 1 else None
        season = _prompt_int(
            "Season", input_fn=input_fn, output=output, default=default_season
        )
        if season is None:
            return False
        number = _prompt_int(
            "Episode", input_fn=input_fn, output=output, default=default_episode
        )
        if number is None:
            return False
        episode = _find_episode_by_coordinate(catalog.episodes, season, number)
        reason = (
            f"manual review confirmed provider coordinate S{season:02d}E{number:02d}"
        )
    elif mode == "2":
        absolute = _prompt_int(
            "Absolute episode number", input_fn=input_fn, output=output
        )
        if absolute is None:
            return False
        episode = _find_episode_by_absolute(catalog.episodes, absolute)
        lookup_mode = "absolute"
        reason = (
            f"manual review confirmed absolute episode {absolute} "
            "against provider catalog"
        )
    elif mode == "3":
        airdate = input_fn("Air date (YYYY-MM-DD): ").strip()
        episode = _find_episode_by_date(catalog.episodes, airdate)
        lookup_mode = "date"
        reason = f"manual review confirmed unique provider air date {airdate}"
    else:
        return False
    if episode is None or episode.number is None:
        output.write(
            "No unique numbered provider episode matched that identity. Decision deferred.\n"
        )
        return False
    return _confirm_episode_resolution(
        raw,
        record,
        show,
        episode,
        lookup_mode=lookup_mode,
        reason=reason,
        input_fn=input_fn,
        output=output,
    )


def _review_special(
    raw: dict[str, Any],
    record: Mapping[str, object],
    provider: MetadataProvider,
    *,
    input_fn: InputFn,
    output: TextIO,
) -> bool:
    show = _select_show(record, provider, input_fn=input_fn, output=output)
    if show is None:
        return False
    catalog = provider.episode_catalog(show.identity)
    if not catalog.resolved or catalog.errors:
        output.write("Provider episode catalog is unavailable or unsafe to use.\n")
        return False
    specials = [
        episode
        for episode in catalog.episodes
        if episode.number is not None
        and (episode.season == 0 or (episode.episode_type or "regular") != "regular")
    ]
    if not specials:
        output.write(
            "The provider has no numerically addressable special for this show. "
            "JMO will keep the source held rather than invent a special number.\n"
        )
        return False
    output.write("Provider-confirmed specials:\n")
    for index, episode in enumerate(specials[:50], start=1):
        output.write(
            f"  {index}) S{episode.season:02d}E{episode.number:02d} - "
            f"{episode.title} [{episode.identity.key}]\n"
        )
    selected = _prompt_int(
        "Choose special number (or cancel)", input_fn=input_fn, output=output
    )
    if selected is None or selected == 0 or selected > min(len(specials), 50):
        return False
    episode = specials[selected - 1]
    return _confirm_episode_resolution(
        raw,
        record,
        show,
        episode,
        lookup_mode="special",
        reason="manual review confirmed provider-catalog special",
        input_fn=input_fn,
        output=output,
    )


def _review_extra(
    raw: dict[str, Any],
    record: Mapping[str, object],
    provider: MetadataProvider,
    *,
    input_fn: InputFn,
    output: TextIO,
) -> bool:
    show = _select_show(record, provider, input_fn=input_fn, output=output)
    if show is None:
        return False
    kinds = (
        "trailer",
        "featurette",
        "interview",
        "behind-the-scenes",
        "deleted-scene",
        "clip",
        "creditless-opening",
        "creditless-ending",
        "extra",
    )
    choices = {str(index): kind for index, kind in enumerate(kinds, start=1)}
    selected = _prompt_choice(
        "Choose extra type:",
        choices,
        input_fn=input_fn,
        output=output,
    )
    kind = choices[selected]
    default_title = (
        _record_title_hint(record) or PurePosixPath(_record_source(record)).stem
    )
    display_title = (
        input_fn(f"Extra display title [{default_title}]: ").strip() or default_title
    )
    show_key = _record_group_key(record)
    provider_ids = _prompt_jellyfin_ids(
        raw,
        show_key,
        input_fn=input_fn,
        output=output,
    )
    extra = ExtraDecision(kind=kind, rule="explicit-review-extra")
    destination = build_extra_destination(
        _canonical_show(record, show),
        source_key=_record_source(record),
        extra=extra,
        source_extension=_record_extension(record),
        display_title=display_title,
        provider_ids=provider_ids,
    )
    if destination.status is not DestinationStatus.READY:
        output.write("A safe Jellyfin extra destination cannot be produced.\n")
        return False
    output.write(f"Destination preview: {destination.relative_path}\n")
    if input_fn("Write this extra decision? [y/N]: ").strip().casefold() not in {
        "y",
        "yes",
    }:
        output.write("Decision deferred.\n")
        return False
    _write_show_metadata(raw, show_key, show, provider_ids)
    _add_extra_decision(
        raw,
        source=_record_source(record),
        show=show,
        kind=kind,
        display_title=display_title,
    )
    return True
