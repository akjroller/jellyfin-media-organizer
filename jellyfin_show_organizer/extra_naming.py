from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True, slots=True)
class ExtraDisplayIdentity:
    """Stable semantic identity used to name and compare one planned extra."""

    display_title: str
    identity_key: str
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.display_title.strip():
            raise ValueError("extra display identity requires a title")
        if not self.identity_key.strip():
            raise ValueError("extra display identity requires an identity key")
        if not self.reasons or any(not reason.strip() for reason in self.reasons):
            raise ValueError("extra display identity requires naming evidence")


_DEFAULT_TITLES: dict[str, str] = {
    "trailer": "Trailer",
    "featurette": "Featurette",
    "interview": "Interview",
    "behind-the-scenes": "Behind the Scenes",
    "deleted-scene": "Deleted Scene",
    "clip": "Clip",
    "extra": "Extra",
    "creditless-opening": "Creditless Opening",
    "creditless-ending": "Creditless Ending",
}

_STRUCTURAL_EXTRA = re.compile(
    r"(?i)(?<![A-Za-z0-9])s\d{1,2}[ ._-]*extras?"
    r"(?:[ ._-]*(?P<variant>\d{1,3}))?(?![A-Za-z0-9])"
)
_GENERIC_EXTRA = re.compile(r"(?i)(?<![A-Za-z0-9])extras?(?![A-Za-z0-9])")
_CREDITLESS_MARKERS: dict[str, re.Pattern[str]] = {
    "creditless-opening": re.compile(
        r"(?i)(?<![A-Za-z0-9])(?:ncop|creditless[ ._-]+opening|clean[ ._-]+opening)"
        r"(?:[ ._-]*(?P<variant>\d{1,3}))?(?![A-Za-z0-9])"
    ),
    "creditless-ending": re.compile(
        r"(?i)(?<![A-Za-z0-9])(?:nced|creditless[ ._-]+ending|clean[ ._-]+ending)"
        r"(?:[ ._-]*(?P<variant>\d{1,3}))?(?![A-Za-z0-9])"
    ),
}
_SPECIFIC_MARKERS: dict[str, re.Pattern[str]] = {
    "trailer": re.compile(r"(?i)(?<![A-Za-z0-9])(?:trailer|teaser)(?![A-Za-z0-9])"),
    "featurette": re.compile(r"(?i)(?<![A-Za-z0-9])featurettes?(?![A-Za-z0-9])"),
    "interview": re.compile(r"(?i)(?<![A-Za-z0-9])interviews?(?![A-Za-z0-9])"),
    "behind-the-scenes": re.compile(
        r"(?i)(?<![A-Za-z0-9])(?:behind[ ._-]+the[ ._-]+scenes|bts)(?![A-Za-z0-9])"
    ),
    "deleted-scene": re.compile(
        r"(?i)(?<![A-Za-z0-9])deleted[ ._-]+scenes?(?![A-Za-z0-9])"
    ),
    "clip": re.compile(r"(?i)(?<![A-Za-z0-9])clips?(?![A-Za-z0-9])"),
}
_RELEASE_NOISE = re.compile(
    r"(?i)(?:^|\s)(?:"
    r"2160p|1080p|720p|576p|480p|"
    r"web[ ._-]?dl|web[ ._-]?rip|blu[ ._-]?ray|bdrip|bdremux|hdtv|dvd(?:[ ._-]?rip)?|remux|"
    r"x264|x265|h264|h265|hevc|av1|10bit|hi10|aac|flac|opus|"
    r"repack|proper|v[2-9]\d*"
    r")(?=$|\s|[-_[({])"
)
_CHECKSUM_TAIL = re.compile(r"(?i)\s+[A-F0-9]{8,64}$")
_SPACES = re.compile(r"\s+")
_LEADING_SEPARATORS = re.compile(r"^[\s._\-]+")
_TRAILING_SEPARATORS = re.compile(r"[\s._\-]+$")


def _stem(relative_path: str) -> str:
    normalized = unicodedata.normalize("NFKC", relative_path.replace("\\", "/"))
    return PurePosixPath(normalized).stem


def _readable(value: str) -> str:
    value = re.sub(r"[._]+", " ", value)
    value = re.sub(r"[\[\](){}]+", " ", value)
    value = _SPACES.sub(" ", value)
    return value.strip(" ._-")


def _trim_release_noise(value: str) -> tuple[str, bool]:
    readable = _readable(value)
    match = _RELEASE_NOISE.search(readable)
    trimmed = False
    if match is not None:
        readable = readable[: match.start()].rstrip(" ._-")
        trimmed = True
    checksum_trimmed = _CHECKSUM_TAIL.sub("", readable).rstrip(" ._-")
    if checksum_trimmed != readable:
        readable = checksum_trimmed
        trimmed = True
    return readable, trimmed


def _strip_show_prefix(value: str, show_title: str | None) -> str:
    if not show_title:
        return value
    readable_title = _readable(show_title)
    if not readable_title:
        return value
    if value.casefold() == readable_title.casefold():
        return ""
    prefix = readable_title + " "
    if value.casefold().startswith(prefix.casefold()):
        return value[len(prefix) :].lstrip(" ._-")
    return value


def _identity_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", _readable(value)).casefold()
    return _SPACES.sub(" ", normalized).strip()


def _variant_label(value: str) -> str:
    number = int(value)
    return f"{number:02d}" if number < 100 else str(number)


def _finalize(
    title: str,
    *,
    source_reason: str,
    release_noise_trimmed: bool,
    additional_reasons: tuple[str, ...] = (),
) -> ExtraDisplayIdentity:
    readable = _readable(title)
    reasons = [source_reason, *additional_reasons]
    if release_noise_trimmed:
        reasons.append("extra-naming-release-noise-trimmed")
    reasons.append(f"extra-display-title:{readable}")
    return ExtraDisplayIdentity(
        display_title=readable,
        identity_key=_identity_key(readable),
        reasons=tuple(reasons),
    )


def derive_extra_display_identity(
    relative_path: str,
    extra_kind: str,
    *,
    show_title: str | None = None,
    title_hint: str | None = None,
) -> ExtraDisplayIdentity:
    """Derive a stable extra title while excluding structured release noise."""

    kind = extra_kind.casefold().strip()
    default_title = _DEFAULT_TITLES.get(kind, "Extra")
    stem = _stem(relative_path)

    creditless_pattern = _CREDITLESS_MARKERS.get(kind)
    if creditless_pattern is not None:
        match = creditless_pattern.search(stem)
        if match is not None:
            variant = match.group("variant")
            title = default_title
            reasons: tuple[str, ...] = ()
            if variant is not None:
                label = _variant_label(variant)
                title = f"{default_title} {label}"
                reasons = (f"extra-naming-{kind}-variant:{label}",)
            _, trimmed = _trim_release_noise(stem[match.end() :])
            return _finalize(
                title,
                source_reason=f"extra-naming-source:{kind}-marker",
                release_noise_trimmed=trimmed,
                additional_reasons=reasons,
            )

    if title_hint is not None and title_hint.strip():
        hinted, trimmed = _trim_release_noise(title_hint)
        if hinted:
            return _finalize(
                hinted,
                source_reason="extra-naming-source:parser-title-hint",
                release_noise_trimmed=trimmed,
            )

    structural_match = _STRUCTURAL_EXTRA.search(stem)
    if structural_match is not None:
        variant = structural_match.group("variant")
        tail, trimmed = _trim_release_noise(stem[structural_match.end() :])
        if tail:
            if variant is not None:
                label = _variant_label(variant)
                tail = f"Extra {label} - {tail}"
                reasons = (f"extra-naming-structural-variant:{label}",)
            else:
                reasons = ()
            return _finalize(
                tail,
                source_reason="extra-naming-source:structural-extra-tail",
                release_noise_trimmed=trimmed,
                additional_reasons=reasons,
            )
        if variant is not None:
            label = _variant_label(variant)
            return _finalize(
                f"Extra {label}",
                source_reason="extra-naming-source:structural-extra-variant",
                release_noise_trimmed=trimmed,
                additional_reasons=(f"extra-naming-structural-variant:{label}",),
            )

    specific_pattern = _SPECIFIC_MARKERS.get(kind)
    if specific_pattern is not None:
        match = specific_pattern.search(stem)
        if match is not None:
            tail, trimmed = _trim_release_noise(stem[match.end() :])
            if tail:
                return _finalize(
                    f"{default_title} - {tail}",
                    source_reason=f"extra-naming-source:{kind}-marker-tail",
                    release_noise_trimmed=trimmed,
                )
            return _finalize(
                default_title,
                source_reason=f"extra-naming-source:{kind}-marker",
                release_noise_trimmed=trimmed,
            )

    if kind == "extra":
        generic_match = _GENERIC_EXTRA.search(stem)
        if generic_match is not None:
            tail, trimmed = _trim_release_noise(stem[generic_match.end() :])
            if tail:
                return _finalize(
                    tail,
                    source_reason="extra-naming-source:generic-extra-tail",
                    release_noise_trimmed=trimmed,
                )

        candidate, trimmed = _trim_release_noise(stem)
        candidate = _strip_show_prefix(candidate, show_title)
        candidate = _LEADING_SEPARATORS.sub("", candidate)
        candidate = _TRAILING_SEPARATORS.sub("", candidate)
        if candidate and candidate.casefold() not in {"extra", "extras"}:
            return _finalize(
                candidate,
                source_reason="extra-naming-source:extra-filename",
                release_noise_trimmed=trimmed,
            )

    return _finalize(
        default_title,
        source_reason="extra-naming-source:kind-fallback",
        release_noise_trimmed=False,
    )
