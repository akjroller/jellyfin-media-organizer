from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from .filename_parser import parse_video_path
from .models import ExtraDecision, ParseResult


class ExtraKind(StrEnum):
    CREDITLESS_OPENING = "creditless-opening"
    CREDITLESS_ENDING = "creditless-ending"
    TRAILER = "trailer"
    FEATURETTE = "featurette"
    INTERVIEW = "interview"
    BEHIND_THE_SCENES = "behind-the-scenes"
    DELETED_SCENE = "deleted-scene"
    CLIP = "clip"
    EXTRA = "extra"


class ExtraDisposition(StrEnum):
    EPISODE_CANDIDATE = "episode-candidate"
    EXTRA = "extra"
    SUSPICIOUS = "suspicious"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class ExtraClassification:
    disposition: ExtraDisposition
    parse: ParseResult
    decision: ExtraDecision | None = None
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.disposition is ExtraDisposition.EXTRA and self.decision is None:
            raise ValueError("extra classifications require an extra decision")
        if self.disposition is not ExtraDisposition.EXTRA and self.decision is not None:
            raise ValueError("only extra classifications may carry an extra decision")
        if not self.reasons:
            raise ValueError("extra classifications require at least one reason")


_SEPARATORS = re.compile(r"[\s._\-\[\](){}]+")

_STRONG_FILE_MARKERS: tuple[tuple[ExtraKind, re.Pattern[str]], ...] = (
    (
        ExtraKind.CREDITLESS_OPENING,
        re.compile(r"\b(?:ncop|creditless opening|clean opening)\b"),
    ),
    (
        ExtraKind.CREDITLESS_ENDING,
        re.compile(r"\b(?:nced|creditless ending|clean ending)\b"),
    ),
    (ExtraKind.TRAILER, re.compile(r"\b(?:trailer|teaser)\b")),
    (ExtraKind.FEATURETTE, re.compile(r"\bfeaturettes?\b")),
    (ExtraKind.INTERVIEW, re.compile(r"\binterviews?\b")),
    (
        ExtraKind.BEHIND_THE_SCENES,
        re.compile(r"\b(?:behind the scenes|bts)\b"),
    ),
    (ExtraKind.DELETED_SCENE, re.compile(r"\bdeleted scenes?\b")),
)

_GENERIC_EXTRA = re.compile(r"\bextras?\b")
_WEAK_CLIP = re.compile(r"\bclips?\b")
_AMBIGUOUS_EXTRA = re.compile(r"\b(?:bonus|special)\b")
_SPECIAL_NUMBERING = re.compile(r"\b(?:ova|oad)\b")
_STRUCTURAL_SEASON_EXTRA = re.compile(
    r"(?i)(?<![A-Za-z0-9])s\d{1,2}[ ._-]*extras?(?:[ ._-]*\d{1,3})?(?![A-Za-z])"
)

_FOLDER_KINDS: dict[str, ExtraKind] = {
    "creditless openings": ExtraKind.CREDITLESS_OPENING,
    "creditless opening": ExtraKind.CREDITLESS_OPENING,
    "creditless endings": ExtraKind.CREDITLESS_ENDING,
    "creditless ending": ExtraKind.CREDITLESS_ENDING,
    "trailers": ExtraKind.TRAILER,
    "trailer": ExtraKind.TRAILER,
    "featurettes": ExtraKind.FEATURETTE,
    "featurette": ExtraKind.FEATURETTE,
    "interviews": ExtraKind.INTERVIEW,
    "interview": ExtraKind.INTERVIEW,
    "behind the scenes": ExtraKind.BEHIND_THE_SCENES,
    "deleted scenes": ExtraKind.DELETED_SCENE,
    "deleted scene": ExtraKind.DELETED_SCENE,
    "clips": ExtraKind.CLIP,
    "clip": ExtraKind.CLIP,
    "extras": ExtraKind.EXTRA,
    "extra": ExtraKind.EXTRA,
}


def _normalize(value: str) -> str:
    return _SEPARATORS.sub(" ", value).strip().casefold()


def _strong_episode_evidence(parsed: ParseResult) -> bool:
    return bool(
        (parsed.season is not None and parsed.episodes)
        or parsed.absolute_episode is not None
    )


def _append_marker(
    hits: list[tuple[ExtraKind, str]],
    kind: ExtraKind,
    reason: str,
) -> None:
    if all(existing is not kind for existing, _ in hits):
        hits.append((kind, reason))


def _extra_markers(
    path: PurePosixPath,
    *,
    strong_episode: bool,
) -> tuple[tuple[ExtraKind, str], ...]:
    stem = _normalize(path.stem)
    hits: list[tuple[ExtraKind, str]] = []

    for kind, pattern in _STRONG_FILE_MARKERS:
        if pattern.search(stem):
            _append_marker(hits, kind, f"filename marker: {kind.value}")

    if _STRUCTURAL_SEASON_EXTRA.search(path.stem):
        _append_marker(hits, ExtraKind.EXTRA, "structural season-extra marker")
    elif not strong_episode:
        if _WEAK_CLIP.search(stem):
            _append_marker(hits, ExtraKind.CLIP, "filename marker: clip")
        if not hits and _GENERIC_EXTRA.search(stem):
            _append_marker(hits, ExtraKind.EXTRA, "filename marker: extra")

    parent_normalized = _normalize(path.parent.name)
    parent_kind = _FOLDER_KINDS.get(parent_normalized)
    if parent_kind is not None:
        _append_marker(hits, parent_kind, f"extra folder: {parent_kind.value}")
    elif _GENERIC_EXTRA.search(parent_normalized):
        _append_marker(hits, ExtraKind.EXTRA, "embedded extra folder marker")

    if any(kind is ExtraKind.DELETED_SCENE for kind, _ in hits):
        hits = [(kind, reason) for kind, reason in hits if kind is not ExtraKind.CLIP]

    return tuple(hits)


def classify_extra(relative_path: str) -> ExtraClassification:
    """Classify explicit video extras before provider-backed episode matching.

    The classifier is pure string processing. It performs no filesystem access and
    no provider/network calls. Ambiguous or conflicting evidence fails closed.
    """

    path = PurePosixPath(relative_path.replace("\\", "/"))
    parsed = parse_video_path(relative_path)
    strong_episode = _strong_episode_evidence(parsed)
    markers = _extra_markers(path, strong_episode=strong_episode)
    normalized_stem = _normalize(path.stem)
    special_numbering_match = _SPECIAL_NUMBERING.search(normalized_stem)

    unique_kinds = tuple(dict.fromkeys(kind for kind, _ in markers))
    marker_reasons = tuple(reason for _, reason in markers)

    if len(unique_kinds) > 1:
        return ExtraClassification(
            disposition=ExtraDisposition.SUSPICIOUS,
            parse=parsed,
            reasons=(
                "conflicting explicit extra markers",
                *marker_reasons,
            ),
        )

    if unique_kinds:
        kind = unique_kinds[0]
        if kind is ExtraKind.EXTRA and "structural season-extra marker" in marker_reasons:
            rule = "structural season-extra marker"
            return ExtraClassification(
                disposition=ExtraDisposition.EXTRA,
                parse=parsed,
                decision=ExtraDecision(kind=kind.value, rule=rule),
                reasons=(rule,),
            )

        if strong_episode or special_numbering_match is not None:
            conflict = "strong episode evidence"
            if special_numbering_match is not None and not strong_episode:
                conflict = "special-numbering evidence"
            return ExtraClassification(
                disposition=ExtraDisposition.SUSPICIOUS,
                parse=parsed,
                reasons=(
                    f"{conflict} conflicts with explicit extra evidence",
                    *marker_reasons,
                ),
            )

        rule = marker_reasons[0]
        return ExtraClassification(
            disposition=ExtraDisposition.EXTRA,
            parse=parsed,
            decision=ExtraDecision(kind=kind.value, rule=rule),
            reasons=(rule,),
        )

    if special_numbering_match is not None:
        marker = special_numbering_match.group(0)
        return ExtraClassification(
            disposition=ExtraDisposition.EPISODE_CANDIDATE,
            parse=parsed,
            reasons=(f"special-numbering marker: {marker}",),
        )

    ambiguous_match = _AMBIGUOUS_EXTRA.search(normalized_stem)
    if ambiguous_match is not None and not strong_episode:
        marker = ambiguous_match.group(0)
        return ExtraClassification(
            disposition=ExtraDisposition.UNRESOLVED,
            parse=parsed,
            reasons=(f"ambiguous extra marker requires review: {marker}",),
        )

    return ExtraClassification(
        disposition=ExtraDisposition.EPISODE_CANDIDATE,
        parse=parsed,
        reasons=("no explicit extra evidence",),
    )
