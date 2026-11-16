"""Turn a finished plan into the records whole-plan preflight checks."""

from __future__ import annotations

from .models import CompanionStatus, OrganizerPlan, PlanRecord, TerminalStatus
from .plan_common import path_key
from .preflight import PreflightRecord, PreflightStatus


def _numbering_identity(record: PlanRecord) -> str | None:
    if record.provider_episodes:
        return ",".join(
            f"S{episode.season:02d}E{episode.number:02d}"
            if episode.number is not None
            else f"S{episode.season:02d}:{episode.provider_identity.key}"
            for episode in record.provider_episodes
        )
    if record.extra is not None:
        return f"extra:{record.extra.kind}"
    return None


def video_preflight_status(record: PlanRecord) -> PreflightStatus:
    if record.status is TerminalStatus.SUSPICIOUS:
        return PreflightStatus.SUSPICIOUS
    if record.status is TerminalStatus.UNRESOLVED:
        return PreflightStatus.UNRESOLVED
    if record.status in {TerminalStatus.DUPLICATE, TerminalStatus.HELD}:
        return PreflightStatus.NON_MOVING
    if (
        record.destination is not None
        and path_key(record.destination)[0] == path_key(record.source.relative_path)[0]
    ):
        return PreflightStatus.NON_MOVING
    return PreflightStatus.MATCHED


def preflight_records(plan: OrganizerPlan) -> tuple[PreflightRecord, ...]:
    videos = {record.source.relative_path: record for record in plan.records}
    records: list[PreflightRecord] = []
    for record in plan.records:
        records.append(
            PreflightRecord(
                record_id=f"video:{record.source.relative_path}",
                source_relative_path=record.source.relative_path,
                status=video_preflight_status(record),
                operation_group_id=record.operation_group_id,
                provider_identity=(
                    record.show.provider_identity.key
                    if record.show is not None
                    else None
                ),
                numbering_identity=_numbering_identity(record),
                destination_relative_path=record.destination,
                source_fingerprint=record.source.fingerprint,
                duplicate=record.duplicate,
            )
        )

    for companion in plan.companions:
        video = videos.get(companion.source_video or "")
        if companion.status is CompanionStatus.ASSOCIATED:
            status = (
                PreflightStatus.NON_MOVING
                if companion.destination is not None
                and path_key(companion.destination)[0]
                == path_key(companion.relative_path)[0]
                else PreflightStatus.MATCHED
            )
        elif companion.status in {CompanionStatus.DUPLICATE, CompanionStatus.IGNORED}:
            status = PreflightStatus.NON_MOVING
        else:
            status = PreflightStatus.UNRESOLVED
        records.append(
            PreflightRecord(
                record_id=f"companion:{companion.relative_path}",
                source_relative_path=companion.relative_path,
                status=status,
                operation_group_id=companion.operation_group_id,
                provider_identity=(
                    video.show.provider_identity.key
                    if video is not None and video.show is not None
                    else None
                ),
                numbering_identity=(
                    _numbering_identity(video) if video is not None else None
                ),
                destination_relative_path=companion.destination,
                source_fingerprint=companion.fingerprint,
                duplicate=video.duplicate if video is not None else None,
            )
        )
    return tuple(records)
