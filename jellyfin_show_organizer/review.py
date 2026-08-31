from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any, cast

from .schema import validate_manifest

_REVIEW_STATUSES = frozenset({"suspicious", "unresolved"})


def _normalized_identity(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def stable_review_ref(relative_path: str) -> str:
    """Return a stable local review reference for one relative media path."""

    normalized = PurePosixPath(relative_path.replace("\\", "/")).as_posix()
    identity = _normalized_identity(normalized)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"review-{digest}"


def _mapping(value: object) -> Mapping[str, object] | None:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _record_source_path(record: Mapping[str, object]) -> str:
    source = _mapping(record.get("source"))
    if source is None:
        raise ValueError("validated plan record is missing source identity")
    relative_path = _string(source.get("relative_path"))
    if relative_path is None:
        raise ValueError("validated plan record is missing source relative path")
    return relative_path


def _record_key(record: Mapping[str, object], relative_path: str) -> str:
    show = _mapping(record.get("show"))
    if show is not None:
        source_key = _string(show.get("source_key"))
        if source_key is not None:
            return source_key

    parse = _mapping(record.get("parse"))
    if parse is not None:
        series_hint = _string(parse.get("series_hint"))
        if series_hint is not None:
            return series_hint

    path = PurePosixPath(relative_path.replace("\\", "/"))
    if len(path.parts) > 1:
        return path.parts[0]
    return path.stem


def _numbering_mode(record: Mapping[str, object]) -> str:
    show = _mapping(record.get("show"))
    if show is not None:
        mode = _string(show.get("numbering_mode"))
        if mode is not None:
            return mode

    parse = _mapping(record.get("parse"))
    if parse is None:
        return "aired"
    if _string(parse.get("special_kind")) is not None:
        return "special"
    if _string(parse.get("episode_date")) is not None:
        return "date"
    if _integer(parse.get("absolute_episode")) is not None:
        return "absolute"
    if _string(parse.get("segment_hint")) is not None:
        return "segment-title"
    return "aired"


def _year(record: Mapping[str, object]) -> int | None:
    show = _mapping(record.get("show"))
    if show is not None:
        value = _integer(show.get("year"))
        if value is not None:
            return value
    parse = _mapping(record.get("parse"))
    return _integer(parse.get("year")) if parse is not None else None


def _observed_tvmaze_id(record: Mapping[str, object]) -> int | None:
    show = _mapping(record.get("show"))
    if show is not None:
        value = _integer(show.get("tvmaze_id"))
        if value is not None:
            return value
    parse = _mapping(record.get("parse"))
    return _integer(parse.get("embedded_tvmaze_id")) if parse is not None else None


def _reason(record: Mapping[str, object]) -> str:
    reason = _string(record.get("reason"))
    if reason is not None:
        return reason
    evidence = _mapping(record.get("evidence"))
    if evidence is None:
        return "review required"
    reasons = evidence.get("reasons")
    if isinstance(reasons, list | tuple):
        usable = [value for value in reasons if isinstance(value, str) and value]
        if usable:
            return "; ".join(usable)
    return "review required"


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_override_stub(manifest: object) -> bytes:
    """Render a valid local override starter from unresolved plan records.

    The input must be a current validated plan manifest. Generated provider IDs are
    comments only so the helper never turns observed evidence into an implicit
    override decision. Users must review and deliberately promote such values.
    """

    validate_manifest(manifest)
    root = cast(Mapping[str, object], manifest)
    raw_records = root["records"]
    assert isinstance(raw_records, list | tuple)

    grouped: dict[str, dict[str, Any]] = {}
    records = sorted(
        (cast(Mapping[str, object], record) for record in raw_records),
        key=lambda record: (
            _normalized_identity(_record_source_path(record)),
            _record_source_path(record),
        ),
    )
    for record in records:
        status = _string(record.get("status"))
        if status not in _REVIEW_STATUSES:
            continue
        relative_path = _record_source_path(record)
        key = _record_key(record, relative_path)
        normalized_key = _normalized_identity(key)
        entry = grouped.setdefault(
            normalized_key,
            {
                "keys": set(),
                "refs": set(),
                "reasons": set(),
                "modes": set(),
                "years": set(),
                "tvmaze_ids": set(),
            },
        )
        entry["keys"].add(key)
        entry["refs"].add(stable_review_ref(relative_path))
        entry["reasons"].add(_reason(record))
        entry["modes"].add(_numbering_mode(record))
        year = _year(record)
        if year is not None:
            entry["years"].add(year)
        tvmaze_id = _observed_tvmaze_id(record)
        if tvmaze_id is not None:
            entry["tvmaze_ids"].add(tvmaze_id)

    lines = [
        "schema_version = 2",
        "",
        "# Generated from unresolved/suspicious plan records.",
        "# Review and edit deliberately before passing this file to `plan --overrides`.",
        "# Observed provider IDs remain comments and are never promoted automatically.",
    ]
    for normalized_key in sorted(grouped):
        entry = grouped[normalized_key]
        keys = sorted(
            entry["keys"], key=lambda value: (_normalized_identity(value), value)
        )
        key = keys[0]
        refs = sorted(entry["refs"])
        reasons = sorted(entry["reasons"])
        modes = sorted(entry["modes"])
        years = sorted(entry["years"])
        tvmaze_ids = sorted(entry["tvmaze_ids"])

        lines.extend(["", "[[shows]]"])
        lines.append("# review_refs = " + ", ".join(refs))
        for reason in reasons:
            compact = " ".join(reason.split())
            lines.append(f"# reason = {compact}")
        if tvmaze_ids:
            lines.append(
                "# observed_tvmaze_id = "
                + ", ".join(str(value) for value in tvmaze_ids)
            )
        lines.append(f"key = {_toml_string(key)}")
        lines.append("aliases = []")
        if len(years) == 1:
            lines.append(f"year = {years[0]}")
        elif years:
            lines.append(
                "# observed_years = " + ", ".join(str(value) for value in years)
            )
        if len(modes) == 1:
            lines.append(f"numbering_mode = {_toml_string(modes[0])}")
        else:
            lines.append("# observed_numbering_modes = " + ", ".join(modes))
        lines.append('title_preference = "provider"')
        lines.append("# Optional: tvmaze_id = 12345")
        lines.append('# Optional: preferred_title = "Reviewed Series Title"')
        lines.append(
            '# If preferred_title is set, change title_preference to "override".'
        )

    if not grouped:
        lines.extend(["", "# No unresolved or suspicious video records were present."])
    return ("\n".join(lines) + "\n").encode("utf-8")
