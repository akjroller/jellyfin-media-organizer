from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath


class ReleaseSourceFamily(StrEnum):
    WEB_DL = "web-dl"
    WEB_RIP = "web-rip"
    BLURAY = "blu-ray"
    HDTV = "hdtv"
    DVD = "dvd"


@dataclass(frozen=True, slots=True)
class ReleaseQualityEvidence:
    """Deterministic release metadata that may support duplicate comparison."""

    resolution: int | None
    source_family: ReleaseSourceFamily | None
    remux: bool
    revision_rank: int
    revision_markers: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        resolution = f"{self.resolution}p" if self.resolution is not None else "unknown"
        source = (
            self.source_family.value if self.source_family is not None else "unknown"
        )
        markers = ",".join(self.revision_markers) if self.revision_markers else "none"
        errors = ",".join(self.errors) if self.errors else "none"
        mode = "remux" if self.remux else "encode"
        return (
            f"resolution={resolution};source={source};mode={mode};"
            f"revision={self.revision_rank};markers={markers};errors={errors}"
        )


_RESOLUTION_RE = re.compile(
    r"(?<!\d)(2160|1080|720|576|540|480)p(?!\d)", re.IGNORECASE
)
_VERSION_RE = re.compile(
    r"(?<![a-z0-9])v([2-9]\d*)(?![a-z0-9])",
    re.IGNORECASE,
)
_REPACK_RE = re.compile(r"(?<![a-z0-9])repack(?![a-z0-9])", re.IGNORECASE)
_PROPER_RE = re.compile(r"(?<![a-z0-9])proper(?![a-z0-9])", re.IGNORECASE)
_REMUX_RE = re.compile(
    r"(?<![a-z0-9])(?:remux|bdremux)(?![a-z0-9])",
    re.IGNORECASE,
)
_SOURCE_PATTERNS: tuple[tuple[ReleaseSourceFamily, re.Pattern[str]], ...] = (
    (
        ReleaseSourceFamily.WEB_DL,
        re.compile(r"(?<![a-z0-9])web[ ._-]?dl(?![a-z0-9])", re.IGNORECASE),
    ),
    (
        ReleaseSourceFamily.WEB_RIP,
        re.compile(r"(?<![a-z0-9])web[ ._-]?rip(?![a-z0-9])", re.IGNORECASE),
    ),
    (
        ReleaseSourceFamily.BLURAY,
        re.compile(
            r"(?<![a-z0-9])(?:blu[ ._-]?ray|bdrip|bdremux)(?![a-z0-9])",
            re.IGNORECASE,
        ),
    ),
    (
        ReleaseSourceFamily.HDTV,
        re.compile(r"(?<![a-z0-9])hdtv(?![a-z0-9])", re.IGNORECASE),
    ),
    (
        ReleaseSourceFamily.DVD,
        re.compile(r"(?<![a-z0-9])dvd(?:[ ._-]?rip)?(?![a-z0-9])", re.IGNORECASE),
    ),
)


def _release_stem(relative_path: str) -> str:
    normalized = unicodedata.normalize("NFKC", relative_path.replace("\\", "/"))
    return PurePosixPath(normalized).stem


def parse_release_quality(relative_path: str) -> ReleaseQualityEvidence:
    """Parse only documented, token-delimited release-quality signals."""

    stem = _release_stem(relative_path)
    errors: list[str] = []

    resolutions = {int(match) for match in _RESOLUTION_RE.findall(stem)}
    resolution = next(iter(resolutions)) if len(resolutions) == 1 else None
    if len(resolutions) > 1:
        errors.append(
            "multiple-resolutions:"
            + ",".join(str(value) for value in sorted(resolutions))
        )

    source_families = {
        family
        for family, pattern in _SOURCE_PATTERNS
        if pattern.search(stem) is not None
    }
    source_family = next(iter(source_families)) if len(source_families) == 1 else None
    if len(source_families) > 1:
        errors.append(
            "multiple-source-families:"
            + ",".join(family.value for family in sorted(source_families, key=str))
        )

    version_values = {int(value) for value in _VERSION_RE.findall(stem)}
    if len(version_values) > 1:
        errors.append(
            "multiple-release-versions:"
            + ",".join(str(value) for value in sorted(version_values))
        )

    markers: list[str] = []
    revision_rank = 1
    if _REPACK_RE.search(stem) is not None:
        markers.append("repack")
        revision_rank = max(revision_rank, 2)
    if _PROPER_RE.search(stem) is not None:
        markers.append("proper")
        revision_rank = max(revision_rank, 2)
    if len(version_values) == 1:
        version = next(iter(version_values))
        markers.append(f"v{version}")
        revision_rank = max(revision_rank, version)

    return ReleaseQualityEvidence(
        resolution=resolution,
        source_family=source_family,
        remux=_REMUX_RE.search(stem) is not None,
        revision_rank=revision_rank,
        revision_markers=tuple(markers),
        errors=tuple(errors),
    )


def _dominates(left: ReleaseQualityEvidence, right: ReleaseQualityEvidence) -> bool:
    assert left.resolution is not None
    assert right.resolution is not None
    return (
        left.resolution >= right.resolution
        and left.revision_rank >= right.revision_rank
        and (
            left.resolution > right.resolution
            or left.revision_rank > right.revision_rank
        )
    )


def select_unique_release_quality_winner(
    evidence: tuple[ReleaseQualityEvidence, ...],
) -> tuple[int | None, str]:
    """Select a unique Pareto-dominant release only within compatible evidence.

    A missing release-source token is not itself a quality signal. Resolution and
    revision may still be compared when every candidate has an unknown source. In a
    mixed known/unknown group, automatic selection is allowed only when the unique
    Pareto winner has a known source and every unknown-source candidate is strictly
    lower resolution. That prevents an unknown source from winning or tying a known
    source while still resolving obvious lower-resolution alternates.
    """

    if len(evidence) < 2:
        return None, "release-quality comparison requires at least two candidates"

    if any(item.errors for item in evidence):
        return None, "release-quality evidence contains conflicting parsed signals"
    if any(item.resolution is None for item in evidence):
        return None, "release-quality evidence is incomplete"

    modes = {item.remux for item in evidence}
    if len(modes) != 1:
        return None, "remux and encode candidates are incomparable"

    known_families = {
        item.source_family for item in evidence if item.source_family is not None
    }
    if len(known_families) > 1:
        return None, "release source families are incomparable"

    winners = tuple(
        index
        for index, item in enumerate(evidence)
        if all(
            index == other_index or _dominates(item, other)
            for other_index, other in enumerate(evidence)
        )
    )
    if len(winners) != 1:
        return None, "no unique candidate dominates every comparable release dimension"

    winner_index = winners[0]
    winner = evidence[winner_index]
    assert winner.resolution is not None

    unknown_indexes = tuple(
        index for index, item in enumerate(evidence) if item.source_family is None
    )
    source_reason: str | None = None
    if len(unknown_indexes) == len(evidence):
        source_reason = "all release source families are unknown"
    elif unknown_indexes:
        if winner.source_family is None:
            return None, "release-quality evidence is incomplete"
        for index in unknown_indexes:
            resolution = evidence[index].resolution
            if resolution is None or resolution >= winner.resolution:
                return None, "release-quality evidence is incomplete"
        source_reason = (
            "known-source winner exceeds lower-resolution unknown-source candidates"
        )

    dimensions: list[str] = []
    if any(
        other.resolution is not None and winner.resolution > other.resolution
        for index, other in enumerate(evidence)
        if index != winner_index
    ):
        dimensions.append("resolution")
    if any(
        winner.revision_rank > other.revision_rank
        for index, other in enumerate(evidence)
        if index != winner_index
    ):
        dimensions.append("revision")

    reason = "unique release-quality dominance via " + "+".join(dimensions)
    if source_reason is not None:
        reason += f" ({source_reason})"
    return winner_index, reason
