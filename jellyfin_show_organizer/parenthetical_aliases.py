from __future__ import annotations

import re
import unicodedata

_COORDINATE = re.compile(
    r"(?ix)(?:"
    r"\bS\d{1,2}[ ._-]*E\d{1,3}\b|"
    r"\b\d{1,2}x\d{1,3}\b|"
    r"\bepisode[ ._-]*\d{1,3}\b|"
    r"\b(?:OVA|OAD)[ ._-]*\d{1,3}\b|"
    r"\b(?:18|19|20|21)\d{2}[-._](?:0[1-9]|1[0-2])[-._](?:0[1-9]|[12]\d|3[01])\b|"
    r"[ ._]+-[ ._]+\d{1,3}(?:v\d+)?(?=$|[ ._\-\[(])"
    r")"
)
_PAREN_GROUP = re.compile(r"\((?P<inner>[^()]*)\)")
_YEAR_ONLY = re.compile(r"^(?:18|19|20|21)\d{2}$")
_NUMBER_ONLY = re.compile(r"^\d{1,4}$")
_CHECKSUM_ONLY = re.compile(r"^[A-Fa-f0-9]{8,64}$")
_TECHNICAL_TOKEN = re.compile(
    r"(?ix)(?:^|\b)(?:"
    r"2160p|1080p|720p|576p|480p|"
    r"web[ ._-]?dl|webrip|bluray|bdrip|hdtv|dvdrip|remux|"
    r"x264|x265|h264|h265|hevc|av1|10bit|hi10|"
    r"aac|flac|opus|ddp(?:\d(?:\.\d)?)?|ac3|eac3|"
    r"dual[ ._-]?audio|multi[ ._-]?audio|"
    r"english|eng|japanese|jpn|dual|dub|dubbed|sub|subs|subbed|"
    r"repack|proper|batch|complete|season|seasons"
    r")(?:\b|$)"
)


def _normalize_fragment(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("_", " ").replace(".", " ")
    normalized = re.sub(r"[\[\]{}]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip(" -_.[]{}()")


def _alphabetic_tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[^\W\d_]+", value, flags=re.UNICODE))


def _title_prefix(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    coordinate = _COORDINATE.search(normalized)
    if coordinate is not None:
        normalized = normalized[: coordinate.start()]
    return normalized.strip(" -_.[]{}")


def parenthetical_show_aliases(value: str) -> tuple[str, ...]:
    """Return one conservative outer/inner show-title alias pair.

    Only a single parenthetical group before episode coordinates is considered.
    Technical, numeric, year, checksum, and ambiguous multi-parenthetical forms are
    deliberately rejected rather than guessed.
    """

    prefix = _title_prefix(value)
    if not prefix:
        return ()

    groups = tuple(_PAREN_GROUP.finditer(prefix))
    if len(groups) != 1:
        return ()

    group = groups[0]
    trailing = prefix[group.end() :].strip(" -_.[]{}")
    if trailing:
        return ()

    outer = _normalize_fragment(prefix[: group.start()])
    inner = _normalize_fragment(group.group("inner"))
    if not outer or not inner:
        return ()

    outer_tokens = _alphabetic_tokens(outer)
    inner_tokens = _alphabetic_tokens(inner)
    if not outer_tokens or len(inner_tokens) < 2:
        return ()

    compact_inner = re.sub(r"\s+", "", inner)
    if (
        _YEAR_ONLY.fullmatch(inner)
        or _NUMBER_ONLY.fullmatch(inner)
        or _CHECKSUM_ONLY.fullmatch(compact_inner)
        or _TECHNICAL_TOKEN.search(inner)
    ):
        return ()

    outer_identity = outer.casefold()
    inner_identity = inner.casefold()
    if outer_identity == inner_identity:
        return ()
    return outer, inner
