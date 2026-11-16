"""Plan which companion files move with each video, and which stay.

Covers subtitles, Jellyfin episode files, and series-level files, which
join the operation group of one anchor episode of their show.
"""

from __future__ import annotations

import os
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import cast

from .models import (
    CanonicalShow,
    CompanionPlanRecord,
    CompanionStatus,
    PlanRecord,
    TerminalStatus,
)
from .plan_common import operation_group_id, path_key
from .sidecars import AdjacentFile, SidecarDiscovery, companion_destinations


def _adjacent_record(
    file: AdjacentFile,
    status: CompanionStatus,
) -> CompanionPlanRecord:
    return CompanionPlanRecord(
        relative_path=file.relative_path,
        extension=file.extension,
        fingerprint=file.fingerprint,
        status=status,
        reason=file.reason,
    )


def _show_folder_anchors(
    records: tuple[PlanRecord, ...],
) -> dict[str, tuple[PlanRecord, str] | str]:
    """Pick, per top-level source folder, one moving episode and its show folder.

    The value is the anchor record and destination show folder, or the reason
    series-level files from that source folder must stay where they are.
    """

    by_folder: dict[str, list[PlanRecord]] = defaultdict(list)
    for record in records:
        parts = PurePosixPath(record.source.relative_path).parts
        if len(parts) > 1:
            by_folder[path_key(parts[0])[0]].append(record)
    anchors: dict[str, tuple[PlanRecord, str] | str] = {}
    for folder, folder_records in by_folder.items():
        moving = [
            record
            for record in folder_records
            if record.status is TerminalStatus.MATCHED
            and record.destination is not None
            and record.operation_group_id is not None
            and record.show is not None
        ]
        if not moving:
            anchors[folder] = "show-folder-has-no-moving-episode"
            continue
        show_folders = {
            PurePosixPath(cast(str, record.destination)).parts[0] for record in moving
        }
        identities = {
            cast(CanonicalShow, record.show).provider_identity for record in moving
        }
        if len(show_folders) != 1 or len(identities) != 1:
            anchors[folder] = "show-folder-has-several-shows"
            continue
        anchor = min(moving, key=lambda record: path_key(record.source.relative_path))
        anchors[folder] = (anchor, next(iter(show_folders)))
    return anchors


def plan_show_files(
    show_files: tuple[AdjacentFile, ...],
    records: tuple[PlanRecord, ...],
    destination_root: Path | None,
) -> list[CompanionPlanRecord]:
    """Move series-level files with one episode of their show, or leave them.

    A file moves only when its source folder resolves to exactly one show with
    a moving episode, no other source folder brings a file with the same name
    to that show, and the organized show folder does not already have it.
    Every other case leaves the file in place with a reason; none blocks a plan.
    """

    anchors = _show_folder_anchors(records)
    proposals: list[tuple[AdjacentFile, PlanRecord, str]] = []
    planned: list[CompanionPlanRecord] = []

    def stay(file: AdjacentFile, reason: str) -> None:
        planned.append(
            CompanionPlanRecord(
                relative_path=file.relative_path,
                extension=file.extension,
                fingerprint=file.fingerprint,
                status=CompanionStatus.IGNORED,
                reason=reason,
            )
        )

    for file in show_files:
        source = PurePosixPath(file.relative_path)
        decision = anchors.get(
            path_key(source.parts[0])[0], "show-folder-has-no-moving-episode"
        )
        if isinstance(decision, str):
            stay(file, decision)
            continue
        anchor, show_folder = decision
        proposals.append((file, anchor, f"{show_folder}/{source.name.casefold()}"))

    destination_counts = Counter(
        destination.casefold() for _, _, destination in proposals
    )
    for file, anchor, destination in proposals:
        if destination_counts[destination.casefold()] > 1:
            stay(file, "show-file-collision")
        elif destination_root is not None and os.path.lexists(
            destination_root / destination
        ):
            stay(file, "destination-show-file-exists")
        else:
            planned.append(
                CompanionPlanRecord(
                    relative_path=file.relative_path,
                    extension=file.extension,
                    fingerprint=file.fingerprint,
                    status=CompanionStatus.ASSOCIATED,
                    reason="series-level-file-moves-with-show",
                    source_video=anchor.source.relative_path,
                    operation_group_id=anchor.operation_group_id,
                    destination=destination,
                    kind="show-metadata"
                    if file.extension == ".nfo"
                    else "show-artwork",
                )
            )
    return planned


def plan_companions(
    discovery: SidecarDiscovery,
    records: tuple[PlanRecord, ...],
    destination_root: Path | None = None,
) -> tuple[CompanionPlanRecord, ...]:
    by_source = {record.source.relative_path: record for record in records}
    planned: list[CompanionPlanRecord] = []
    for group in discovery.companions:
        video = by_source[group.source_video]
        group_id = video.operation_group_id or operation_group_id(group.source_video)
        if (
            video.status in {TerminalStatus.MATCHED, TerminalStatus.EXTRA}
            and video.destination is not None
        ):
            destinations = companion_destinations(video.destination, group)
            for file, destination in zip(group.files, destinations, strict=True):
                planned.append(
                    CompanionPlanRecord(
                        relative_path=file.relative_path,
                        extension=file.extension,
                        fingerprint=file.fingerprint,
                        status=CompanionStatus.ASSOCIATED,
                        reason=file.reason,
                        source_video=group.source_video,
                        operation_group_id=group_id,
                        destination=destination,
                        kind=group.kind.value,
                    )
                )
            continue

        if video.status is TerminalStatus.DUPLICATE:
            status = CompanionStatus.DUPLICATE
        elif video.status is TerminalStatus.HELD:
            status = CompanionStatus.IGNORED
        else:
            status = CompanionStatus.UNRESOLVED
        for file in group.files:
            planned.append(
                CompanionPlanRecord(
                    relative_path=file.relative_path,
                    extension=file.extension,
                    fingerprint=file.fingerprint,
                    status=status,
                    reason=f"source-video-status:{video.status.value}",
                    source_video=group.source_video,
                    operation_group_id=group_id,
                    kind=group.kind.value,
                )
            )

    planned.extend(
        _adjacent_record(file, CompanionStatus.UNRESOLVED)
        for file in discovery.unresolved
    )
    planned.extend(
        _adjacent_record(file, CompanionStatus.IGNORED) for file in discovery.ignored
    )
    planned.extend(plan_show_files(discovery.show_files, records, destination_root))
    return tuple(sorted(planned, key=lambda record: path_key(record.relative_path)))
