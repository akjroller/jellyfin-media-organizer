from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from .inventory import VIDEO_EXTENSIONS, AuthorizedShowsRoot
from .models import SourceFile, SourceFingerprint

SUBTITLE_EXTENSIONS = frozenset({".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx"})
IGNORED_ADJACENT_EXTENSIONS = frozenset(
    {
        ".nfo",
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
    }
)
_LANGUAGE_TAG = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2,4})?$", re.IGNORECASE)
_SUBTITLE_FLAGS = frozenset({"default", "forced", "sdh", "cc"})


class AdjacentDisposition(StrEnum):
    ASSOCIATED = "associated"
    IGNORED = "ignored"
    UNRESOLVED = "unresolved"


class CompanionKind(StrEnum):
    SUBTITLE = "subtitle"
    SUBTITLE_PAIR = "subtitle-pair"


@dataclass(frozen=True, slots=True)
class AdjacentFile:
    relative_path: str
    extension: str
    fingerprint: SourceFingerprint | None
    disposition: AdjacentDisposition
    reason: str


@dataclass(frozen=True, slots=True)
class CompanionGroup:
    source_video: str
    kind: CompanionKind
    suffix: str
    files: tuple[AdjacentFile, ...]

    def __post_init__(self) -> None:
        if not self.source_video:
            raise ValueError("companion group source_video cannot be empty")
        if not self.files:
            raise ValueError("companion groups require at least one file")
        if any(
            file.disposition is not AdjacentDisposition.ASSOCIATED
            for file in self.files
        ):
            raise ValueError("companion group files must be associated")
        if self.kind is CompanionKind.SUBTITLE_PAIR:
            extensions = {file.extension for file in self.files}
            if len(self.files) != 2 or extensions != {".idx", ".sub"}:
                raise ValueError(
                    "subtitle pairs require exactly one .idx and one .sub file"
                )


@dataclass(frozen=True, slots=True)
class SidecarDiscovery:
    companions: tuple[CompanionGroup, ...]
    unresolved: tuple[AdjacentFile, ...]
    ignored: tuple[AdjacentFile, ...]


def _windows_path_key(relative_path: str) -> tuple[str, str]:
    return relative_path.casefold(), relative_path


def _is_junction(path: Path) -> bool:
    checker = getattr(path, "is_junction", None)
    if checker is None:
        return False
    try:
        return bool(checker())
    except OSError:
        return True


def _is_link(path: Path, *, entry_is_symlink: bool = False) -> bool:
    return entry_is_symlink or path.is_symlink() or _is_junction(path)


def _fingerprint(entry: os.DirEntry[str]) -> SourceFingerprint:
    stat = entry.stat(follow_symlinks=False)
    return SourceFingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _normalized_subtitle_suffix(raw_suffix: str) -> str | None:
    if not raw_suffix:
        return ""
    if not raw_suffix.startswith("."):
        return None

    tokens = raw_suffix[1:].split(".")
    if not tokens or any(not token for token in tokens):
        return None

    language_count = 0
    seen_flags: set[str] = set()
    normalized: list[str] = []
    for token in tokens:
        folded = token.casefold()
        if folded in _SUBTITLE_FLAGS:
            if folded in seen_flags:
                return None
            seen_flags.add(folded)
            normalized.append(folded)
            continue
        if _LANGUAGE_TAG.fullmatch(token):
            language_count += 1
            if language_count > 1:
                return None
            normalized.append(folded)
            continue
        return None

    return "." + ".".join(normalized)


def _casefold_prefix_end(value: str, folded_prefix: str) -> int | None:
    """Return the raw prefix boundary whose casefold equals folded_prefix."""

    for end in range(1, len(value) + 1):
        folded_candidate = value[:end].casefold()
        if folded_candidate == folded_prefix:
            return end
        if len(folded_candidate) > len(folded_prefix):
            return None
    return None


def _subtitle_candidates(
    sidecar_stem: str,
    videos: tuple[SourceFile, ...],
) -> tuple[tuple[SourceFile, str], ...]:
    folded_sidecar = sidecar_stem.casefold()
    candidates: list[tuple[SourceFile, str]] = []

    for video in videos:
        video_stem = PurePosixPath(video.relative_path).stem
        folded_video = video_stem.casefold()
        if folded_sidecar == folded_video:
            candidates.append((video, ""))
            continue

        prefix_end = _casefold_prefix_end(sidecar_stem, folded_video)
        if prefix_end is None:
            continue
        raw_suffix = sidecar_stem[prefix_end:]
        suffix = _normalized_subtitle_suffix(raw_suffix)
        if suffix is not None:
            candidates.append((video, suffix))

    return tuple(candidates)


def discover_sidecars(
    root: AuthorizedShowsRoot,
    videos: tuple[SourceFile, ...],
) -> SidecarDiscovery:
    """Discover deterministic same-directory companions for video-led inventory.

    Only directories containing supplied source videos are inspected. No files are
    created, modified, deleted, renamed, or moved.
    """

    if not isinstance(root, AuthorizedShowsRoot):
        raise TypeError(
            "discover_sidecars requires an explicitly authorized Shows root"
        )

    by_directory: dict[str, list[SourceFile]] = {}
    seen_video_paths: set[str] = set()
    for video in videos:
        relative = PurePosixPath(video.relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("source video path must stay within the authorized root")
        key = video.relative_path.casefold()
        if key in seen_video_paths:
            raise ValueError("source video paths must be unique case-insensitively")
        seen_video_paths.add(key)
        by_directory.setdefault(relative.parent.as_posix(), []).append(video)

    companions: list[CompanionGroup] = []
    unresolved: list[AdjacentFile] = []
    ignored: list[AdjacentFile] = []
    subtitle_files: dict[tuple[str, str], list[AdjacentFile]] = {}

    for directory_name in sorted(
        by_directory,
        key=lambda value: (value.casefold(), value),
    ):
        directory = root.path / directory_name
        videos_here = tuple(by_directory[directory_name])
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(
                    iterator,
                    key=lambda entry: (entry.name.casefold(), entry.name),
                )
        except OSError as exc:
            raise RuntimeError("cannot enumerate authorized sidecar directory") from exc

        for entry in entries:
            path = Path(entry.path)
            extension = path.suffix.casefold()
            if extension in VIDEO_EXTENSIONS:
                continue

            relative_path = path.relative_to(root.path).as_posix()
            try:
                is_directory = entry.is_dir(follow_symlinks=False)
            except OSError as exc:
                unresolved.append(
                    AdjacentFile(
                        relative_path=relative_path,
                        extension=extension,
                        fingerprint=None,
                        disposition=AdjacentDisposition.UNRESOLVED,
                        reason=f"entry-type-error:{type(exc).__name__}",
                    )
                )
                continue
            if is_directory:
                continue

            if _is_link(path, entry_is_symlink=entry.is_symlink()):
                unresolved.append(
                    AdjacentFile(
                        relative_path=relative_path,
                        extension=extension,
                        fingerprint=None,
                        disposition=AdjacentDisposition.UNRESOLVED,
                        reason="symlink-or-junction",
                    )
                )
                continue

            try:
                if not entry.is_file(follow_symlinks=False):
                    fingerprint = None
                else:
                    fingerprint = _fingerprint(entry)
            except OSError as exc:
                unresolved.append(
                    AdjacentFile(
                        relative_path=relative_path,
                        extension=extension,
                        fingerprint=None,
                        disposition=AdjacentDisposition.UNRESOLVED,
                        reason=f"stat-error:{type(exc).__name__}",
                    )
                )
                continue

            if fingerprint is None:
                unresolved.append(
                    AdjacentFile(
                        relative_path=relative_path,
                        extension=extension,
                        fingerprint=None,
                        disposition=AdjacentDisposition.UNRESOLVED,
                        reason="not-a-regular-file",
                    )
                )
                continue

            if extension in IGNORED_ADJACENT_EXTENSIONS:
                ignored.append(
                    AdjacentFile(
                        relative_path=relative_path,
                        extension=extension,
                        fingerprint=fingerprint,
                        disposition=AdjacentDisposition.IGNORED,
                        reason="explicitly-ignored-adjacent-file",
                    )
                )
                continue

            if extension not in SUBTITLE_EXTENSIONS:
                unresolved.append(
                    AdjacentFile(
                        relative_path=relative_path,
                        extension=extension,
                        fingerprint=fingerprint,
                        disposition=AdjacentDisposition.UNRESOLVED,
                        reason="unsupported-adjacent-file",
                    )
                )
                continue

            candidates = _subtitle_candidates(path.stem, videos_here)
            if len(candidates) != 1:
                unresolved.append(
                    AdjacentFile(
                        relative_path=relative_path,
                        extension=extension,
                        fingerprint=fingerprint,
                        disposition=AdjacentDisposition.UNRESOLVED,
                        reason=(
                            "ambiguous-subtitle-association"
                            if candidates
                            else "no-deterministic-video-association"
                        ),
                    )
                )
                continue

            video, suffix = candidates[0]
            adjacent = AdjacentFile(
                relative_path=relative_path,
                extension=extension,
                fingerprint=fingerprint,
                disposition=AdjacentDisposition.ASSOCIATED,
                reason="deterministic-subtitle-association",
            )
            subtitle_files.setdefault((video.relative_path, suffix), []).append(
                adjacent
            )

    for (source_video, suffix), files in sorted(
        subtitle_files.items(),
        key=lambda item: (item[0][0].casefold(), item[0][1], item[0][0]),
    ):
        by_extension: dict[str, list[AdjacentFile]] = {}
        for file in files:
            by_extension.setdefault(file.extension, []).append(file)

        duplicate_extensions = {
            extension for extension, matches in by_extension.items() if len(matches) > 1
        }
        if duplicate_extensions:
            for file in files:
                unresolved.append(
                    AdjacentFile(
                        relative_path=file.relative_path,
                        extension=file.extension,
                        fingerprint=file.fingerprint,
                        disposition=AdjacentDisposition.UNRESOLVED,
                        reason="duplicate-companion-extension",
                    )
                )
            continue

        idx = by_extension.pop(".idx", [])
        sub = by_extension.pop(".sub", [])
        if idx:
            if sub:
                pair_files = tuple(
                    sorted((idx[0], sub[0]), key=lambda file: file.extension)
                )
                companions.append(
                    CompanionGroup(
                        source_video=source_video,
                        kind=CompanionKind.SUBTITLE_PAIR,
                        suffix=suffix,
                        files=pair_files,
                    )
                )
            else:
                file = idx[0]
                unresolved.append(
                    AdjacentFile(
                        relative_path=file.relative_path,
                        extension=file.extension,
                        fingerprint=file.fingerprint,
                        disposition=AdjacentDisposition.UNRESOLVED,
                        reason="idx-subtitle-missing-sub-pair",
                    )
                )
        elif sub:
            by_extension[".sub"] = sub

        for _extension, matches in sorted(by_extension.items()):
            file = matches[0]
            companions.append(
                CompanionGroup(
                    source_video=source_video,
                    kind=CompanionKind.SUBTITLE,
                    suffix=suffix,
                    files=(file,),
                )
            )

    companions.sort(
        key=lambda group: (
            group.source_video.casefold(),
            group.source_video,
            group.suffix,
            tuple(file.extension for file in group.files),
        )
    )
    unresolved.sort(key=lambda file: _windows_path_key(file.relative_path))
    ignored.sort(key=lambda file: _windows_path_key(file.relative_path))
    return SidecarDiscovery(
        companions=tuple(companions),
        unresolved=tuple(unresolved),
        ignored=tuple(ignored),
    )


def companion_destinations(
    video_destination: str,
    group: CompanionGroup,
) -> tuple[str, ...]:
    """Derive companion destinations from an already-chosen video destination."""

    destination = PurePosixPath(video_destination.replace("\\", "/"))
    if not destination.name or not destination.suffix:
        raise ValueError("video destination must include a filename extension")

    base = destination.with_suffix("")
    return tuple(
        base.with_name(f"{base.name}{group.suffix}{file.extension}").as_posix()
        for file in group.files
    )
