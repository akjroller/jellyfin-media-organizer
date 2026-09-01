from __future__ import annotations

import hashlib
import json
import unicodedata
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import cast
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import __version__
from .destination import (
    DestinationPolicy,
    DestinationStatus,
    build_episode_destination,
    build_extra_destination,
)
from .duplicate_classifier import (
    DuplicateCandidate,
    DuplicateDisposition,
    DuplicatePreference,
    classify_duplicate_candidates,
)
from .episode_assignment import (
    AssignmentStatus,
    SourceEpisodeInput,
    assign_episode_group_with_provider,
)
from .extra_classifier import ExtraClassification, ExtraDisposition, classify_extra
from .extra_naming import derive_extra_display_identity
from .inventory import (
    AuthorizedShowsRoot,
    InventoryStatus,
    authorize_shows_root,
    scan_videos,
)
from .models import (
    CacheSnapshot,
    CompanionPlanRecord,
    CompanionStatus,
    MatchEvidence,
    OrganizerPlan,
    PlanEpisode,
    PlanProvenance,
    PlanRecord,
    SourceFile,
    TerminalStatus,
)
from .overrides import EpisodeDecisionOverride, OverrideCatalog, load_overrides
from .preflight import (
    PreflightRecord,
    PreflightResult,
    PreflightStatus,
    authorize_destination_root,
    preflight_plan,
)
from .providers import MetadataProvider, ProviderEpisode, TvmazeProviderAdapter
from .reports import AuditBundle, write_audit_bundle
from .schema import PLAN_SCHEMA_VERSION, stable_plan_hash
from .show_resolver import (
    ResolutionStatus,
    ShowResolution,
    resolve_show_group_with_provider,
)
from .sidecars import (
    AdjacentFile,
    SidecarDiscovery,
    companion_destinations,
    discover_sidecars,
)
from .tvmaze_cache import (
    CacheRecord,
    CacheState,
    Clock,
    JsonGetter,
    TvmazeCatalogCache,
)


class PlanningConfigurationError(ValueError):
    """Raised when a requested plan cannot be run within the safety contract."""


class ProviderHttpError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class PlanningConfig:
    shows_root: Path
    destination_root: Path
    output_dir: Path
    cache_dir: Path
    overrides_path: Path | None = None
    offline: bool = False
    refresh: bool = False
    max_path_length: int = 240
    max_component_length: int = 180

    def __post_init__(self) -> None:
        if self.offline and self.refresh:
            raise PlanningConfigurationError(
                "offline and refresh modes cannot be enabled together"
            )
        DestinationPolicy(
            max_path_length=self.max_path_length,
            max_component_length=self.max_component_length,
        )

    @property
    def snapshot_id(self) -> str:
        payload = {
            "schema_version": 1,
            "destination_policy": {
                "include_year": True,
                "max_component_length": self.max_component_length,
                "max_path_length": self.max_path_length,
            },
            "provider": "tvmaze",
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class PlanningOutcome:
    plan: OrganizerPlan
    preflight: PreflightResult
    bundle: AuditBundle
    provider_failure: bool


class TrackingTvmazeCatalogCache(TvmazeCatalogCache):
    def __init__(
        self,
        root: Path,
        *,
        offline: bool,
        refresh: bool,
        clock: Clock | None = None,
    ) -> None:
        if clock is None:
            super().__init__(root, offline=offline, refresh=refresh)
        else:
            super().__init__(root, offline=offline, refresh=refresh, clock=clock)
        self.records: dict[tuple[str, str], CacheRecord] = {}

    def _track(self, record: CacheRecord) -> CacheRecord:
        self.records[(record.kind.value, record.request_key)] = record
        return record

    def search_show(self, title: str, getter: JsonGetter) -> CacheRecord:
        return self._track(super().search_show(title, getter))

    def episode_catalog(self, tvmaze_id: int, getter: JsonGetter) -> CacheRecord:
        return self._track(super().episode_catalog(tvmaze_id, getter))


def http_json_getter(
    url: str,
    params: Mapping[str, str] | None = None,
) -> object:
    query = urlencode(sorted((params or {}).items()))
    target = f"{url}?{query}" if query else url
    request = Request(
        target,
        headers={"Accept": "application/json", "User-Agent": f"JMO/{__version__}"},
    )
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310
            payload = response.read()
    except HTTPError as exc:
        raise ProviderHttpError(exc.code, f"provider HTTP {exc.code}") from exc
    return json.loads(payload.decode("utf-8"))


def _path_key(value: str) -> tuple[str, str]:
    return unicodedata.normalize("NFKC", value).casefold(), value


def _operation_group_id(relative_path: str) -> str:
    normalized = unicodedata.normalize("NFKC", relative_path).casefold()
    return "op-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def _show_group_key(source: SourceFile, classification: ExtraClassification) -> str:
    path = PurePosixPath(source.relative_path)
    if len(path.parts) > 1:
        return path.parts[0]
    if classification.parse.series_hint:
        return classification.parse.series_hint
    return path.stem


def _combine_evidence(*values: MatchEvidence) -> MatchEvidence:
    return MatchEvidence(
        method="+".join(value.method for value in values),
        confidence=min(value.confidence for value in values),
        reasons=tuple(reason for value in values for reason in value.reasons),
        candidates=tuple(
            candidate for value in values for candidate in value.candidates
        ),
    )


def _classification_evidence(classification: ExtraClassification) -> MatchEvidence:
    confidence = 1.0 if classification.disposition is ExtraDisposition.EXTRA else 0.0
    return MatchEvidence(
        method="extra-classification",
        confidence=confidence,
        reasons=classification.reasons,
    )


def _episode_decision_evidence(decision: EpisodeDecisionOverride) -> MatchEvidence:
    return MatchEvidence(
        method="episode-decision-override",
        confidence=1.0,
        reasons=(
            f"episode-decision-provider:{decision.show_provider_identity.key}",
            f"episode-decision-numbering-mode:{decision.numbering_mode.value}",
            *decision.reasons,
        ),
    )


def _reason(evidence: MatchEvidence) -> str:
    return "; ".join(evidence.reasons) or evidence.method


def _plan_episode(episode: ProviderEpisode) -> PlanEpisode:
    return PlanEpisode(
        provider_identity=episode.identity,
        season=episode.season,
        number=episode.number,
        title=episode.title,
        airdate=episode.airdate,
    )


def _unresolved_show_records(
    sources: tuple[SourceFile, ...],
    classifications: Mapping[str, ExtraClassification],
    resolution: ShowResolution,
) -> list[PlanRecord]:
    status = (
        TerminalStatus.SUSPICIOUS
        if resolution.status is ResolutionStatus.SUSPICIOUS
        else TerminalStatus.UNRESOLVED
    )
    return [
        PlanRecord(
            source=source,
            status=status,
            parse=classifications[source.relative_path].parse,
            evidence=resolution.evidence,
            operation_group_id=_operation_group_id(source.relative_path),
            reason=_reason(resolution.evidence),
        )
        for source in sources
    ]


def _plan_resolved_group(
    sources: tuple[SourceFile, ...],
    classifications: Mapping[str, ExtraClassification],
    resolution: ShowResolution,
    provider: MetadataProvider,
    destination_policy: DestinationPolicy,
    overrides: OverrideCatalog,
) -> list[PlanRecord]:
    assert resolution.show is not None
    show = resolution.show
    records: list[PlanRecord] = []
    episode_sources: list[SourceEpisodeInput] = []
    decision_evidence: dict[str, MatchEvidence] = {}

    for source in sources:
        classification = classifications[source.relative_path]
        group_id = _operation_group_id(source.relative_path)
        decision = overrides.episode_decision_for(source.relative_path)
        if decision is not None:
            if classification.disposition is not ExtraDisposition.EPISODE_CANDIDATE:
                raise PlanningConfigurationError(
                    "episode decision source is not an episode candidate"
                )
            if decision.show_provider_identity != show.provider_identity:
                raise PlanningConfigurationError(
                    "episode decision provider identity conflicts with resolved show"
                )
            if decision.numbering_mode is not show.numbering_mode:
                raise PlanningConfigurationError(
                    "episode decision numbering mode conflicts with resolved show"
                )
        if classification.disposition in {
            ExtraDisposition.SUSPICIOUS,
            ExtraDisposition.UNRESOLVED,
        }:
            evidence = _combine_evidence(
                resolution.evidence,
                _classification_evidence(classification),
            )
            status = (
                TerminalStatus.SUSPICIOUS
                if classification.disposition is ExtraDisposition.SUSPICIOUS
                else TerminalStatus.UNRESOLVED
            )
            records.append(
                PlanRecord(
                    source=source,
                    status=status,
                    parse=classification.parse,
                    show=show,
                    evidence=evidence,
                    operation_group_id=group_id,
                    reason=_reason(evidence),
                )
            )
            continue

        if classification.disposition is ExtraDisposition.EXTRA:
            assert classification.decision is not None
            naming = derive_extra_display_identity(
                source.relative_path,
                classification.decision.kind,
                show_title=show.title,
                title_hint=classification.parse.title_hint,
            )
            destination = build_extra_destination(
                show,
                source_key=source.relative_path,
                extra=classification.decision,
                source_extension=source.extension,
                display_title=classification.parse.title_hint,
                policy=destination_policy,
            )
            evidence = _combine_evidence(
                resolution.evidence,
                _classification_evidence(classification),
                MatchEvidence(
                    method="extra-naming",
                    confidence=1.0,
                    reasons=naming.reasons,
                ),
            )
            if destination.status is DestinationStatus.READY:
                records.append(
                    PlanRecord(
                        source=source,
                        status=TerminalStatus.EXTRA,
                        parse=classification.parse,
                        show=show,
                        evidence=evidence,
                        destination=destination.relative_path,
                        extra=classification.decision,
                        operation_group_id=group_id,
                    )
                )
            else:
                records.append(
                    PlanRecord(
                        source=source,
                        status=TerminalStatus.UNRESOLVED,
                        parse=classification.parse,
                        show=show,
                        evidence=evidence,
                        extra=classification.decision,
                        operation_group_id=group_id,
                        reason="; ".join(destination.reasons),
                    )
                )
            continue

        effective_parse = classification.parse
        if decision is not None:
            effective_parse = decision.apply_to(effective_parse)
            decision_evidence[source.relative_path] = _episode_decision_evidence(
                decision
            )
        episode_sources.append(
            SourceEpisodeInput(
                source_key=source.relative_path,
                parse=effective_parse,
            )
        )

    if not episode_sources:
        return records

    assignment_group = assign_episode_group_with_provider(
        show, episode_sources, provider
    )
    assignments = {
        assignment.source_key: assignment for assignment in assignment_group.assignments
    }
    sources_by_path = {source.relative_path: source for source in sources}
    for source_input in episode_sources:
        source = sources_by_path[source_input.source_key]
        assignment = assignments[source.relative_path]
        evidence_parts = [resolution.evidence]
        local_decision_evidence = decision_evidence.get(source.relative_path)
        if local_decision_evidence is not None:
            evidence_parts.append(local_decision_evidence)
        evidence_parts.append(assignment.evidence)
        evidence = _combine_evidence(*evidence_parts)
        group_id = _operation_group_id(source.relative_path)
        if assignment.status is not AssignmentStatus.MATCHED:
            status = (
                TerminalStatus.SUSPICIOUS
                if assignment.status is AssignmentStatus.SUSPICIOUS
                else TerminalStatus.UNRESOLVED
            )
            records.append(
                PlanRecord(
                    source=source,
                    status=status,
                    parse=source_input.parse,
                    show=show,
                    evidence=evidence,
                    operation_group_id=group_id,
                    reason=_reason(evidence),
                )
            )
            continue

        destination = build_episode_destination(
            show,
            assignment,
            source.extension,
            policy=destination_policy,
        )
        if destination.status is not DestinationStatus.READY:
            records.append(
                PlanRecord(
                    source=source,
                    status=TerminalStatus.UNRESOLVED,
                    parse=source_input.parse,
                    show=show,
                    evidence=evidence,
                    operation_group_id=group_id,
                    provider_episodes=tuple(
                        _plan_episode(episode) for episode in assignment.episodes
                    ),
                    reason="; ".join(destination.reasons),
                )
            )
            continue

        records.append(
            PlanRecord(
                source=source,
                status=TerminalStatus.MATCHED,
                parse=source_input.parse,
                show=show,
                evidence=evidence,
                destination=destination.relative_path,
                operation_group_id=group_id,
                provider_episodes=tuple(
                    _plan_episode(episode) for episode in assignment.episodes
                ),
            )
        )
    return records


def _logical_identity(record: PlanRecord) -> str:
    assert record.show is not None
    show_identity = record.show.provider_identity.key
    if record.provider_episodes:
        episodes = ",".join(
            episode.provider_identity.key for episode in record.provider_episodes
        )
        return f"{show_identity}:episodes:{episodes}"
    if record.extra is not None:
        naming = derive_extra_display_identity(
            record.source.relative_path,
            record.extra.kind,
            show_title=record.show.title,
            title_hint=record.parse.title_hint if record.parse is not None else None,
        )
        return f"{show_identity}:extra:{record.extra.kind}:{naming.identity_key}"
    return f"{show_identity}:source:{record.source.relative_path}"


def _apply_duplicate_decisions(
    records: list[PlanRecord],
    sidecars: SidecarDiscovery,
    overrides: OverrideCatalog,
) -> list[PlanRecord]:
    companions_by_video: dict[str, list[str]] = defaultdict(list)
    for group in sidecars.companions:
        companions_by_video[group.source_video].extend(
            file.relative_path for file in group.files
        )

    candidate_records = tuple(
        record
        for record in records
        if record.status in {TerminalStatus.MATCHED, TerminalStatus.EXTRA}
        and record.destination is not None
        and record.show is not None
    )
    candidate_keys = {
        _path_key(record.source.relative_path)[0] for record in candidate_records
    }
    configured_keys = {
        _path_key(preference.source)[0]
        for preference in overrides.duplicate_preferences
    }
    if configured_keys - candidate_keys:
        raise PlanningConfigurationError(
            "duplicate preference references an unknown or non-movable source"
        )

    candidates: list[DuplicateCandidate] = []
    for record in candidate_records:
        configured = overrides.duplicate_preference_for(record.source.relative_path)
        preference = (
            DuplicatePreference(
                rank=configured.rank,
                reasons=(
                    f"local duplicate preference for {configured.source}",
                    *configured.reasons,
                ),
            )
            if configured is not None
            else None
        )
        candidates.append(
            DuplicateCandidate(
                operation_key=record.source.relative_path,
                members=(
                    record.source.relative_path,
                    *sorted(
                        companions_by_video.get(record.source.relative_path, ()),
                        key=_path_key,
                    ),
                ),
                destination=cast(str, record.destination),
                logical_identity=_logical_identity(record),
                fingerprint=record.source.fingerprint,
                preference=preference,
            )
        )

    results = classify_duplicate_candidates(candidates)
    collision_keys = {
        _path_key(candidate.operation_key)[0]
        for result in results
        for candidate in result.candidates
    }
    if configured_keys - collision_keys:
        raise PlanningConfigurationError(
            "duplicate preference source is not part of a destination collision"
        )

    by_source = {
        record.source.relative_path: index for index, record in enumerate(records)
    }
    for result in results:
        decision = result.decision
        for candidate in result.candidates:
            index = by_source[candidate.operation_key]
            record = records[index]
            if (
                result.disposition is DuplicateDisposition.SUSPICIOUS
                or decision.winner is None
            ):
                records[index] = replace(
                    record,
                    status=TerminalStatus.SUSPICIOUS,
                    duplicate=decision,
                    reason="; ".join(decision.evidence),
                )
            elif candidate.operation_key == decision.winner:
                records[index] = replace(record, duplicate=decision)
            else:
                records[index] = replace(
                    record,
                    status=TerminalStatus.DUPLICATE,
                    duplicate=decision,
                    reason="non-destructive duplicate loser",
                )
    return records


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


def _plan_companions(
    discovery: SidecarDiscovery,
    records: tuple[PlanRecord, ...],
) -> tuple[CompanionPlanRecord, ...]:
    by_source = {record.source.relative_path: record for record in records}
    planned: list[CompanionPlanRecord] = []
    for group in discovery.companions:
        video = by_source[group.source_video]
        group_id = video.operation_group_id or _operation_group_id(group.source_video)
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

        status = (
            CompanionStatus.DUPLICATE
            if video.status is TerminalStatus.DUPLICATE
            else CompanionStatus.UNRESOLVED
        )
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
    return tuple(sorted(planned, key=lambda record: _path_key(record.relative_path)))


def _cache_snapshots(
    cache: TrackingTvmazeCatalogCache,
) -> tuple[CacheSnapshot, ...]:
    return tuple(
        CacheSnapshot(
            provider=record.provider,
            kind=record.kind.value,
            request_key=record.request_key,
            snapshot_id=record.snapshot_id,
            state=record.state.value,
        )
        for record in sorted(
            cache.records.values(),
            key=lambda item: (item.provider, item.kind.value, item.request_key),
        )
    )


def _configured_episode_decision_keys(overrides: OverrideCatalog) -> set[str]:
    return {_path_key(decision.source)[0] for decision in overrides.episode_decisions}


def _validate_episode_decision_sources(
    sources: tuple[SourceFile, ...],
    overrides: OverrideCatalog,
) -> None:
    configured = _configured_episode_decision_keys(overrides)
    available = {_path_key(source.relative_path)[0] for source in sources}
    if configured - available:
        raise PlanningConfigurationError(
            "episode decision references an unknown source"
        )


def _validate_episode_decision_consumption(
    records: list[PlanRecord],
    overrides: OverrideCatalog,
) -> None:
    configured = _configured_episode_decision_keys(overrides)
    if not configured:
        return
    consumed = {
        _path_key(record.source.relative_path)[0]
        for record in records
        if record.evidence is not None
        and "episode-decision-override" in record.evidence.method.split("+")
    }
    if configured - consumed:
        raise PlanningConfigurationError(
            "episode decision could not be consumed safely"
        )


def _build_plan(
    source_root: AuthorizedShowsRoot,
    config: PlanningConfig,
    overrides: OverrideCatalog,
    cache: TrackingTvmazeCatalogCache,
    provider: MetadataProvider,
) -> OrganizerPlan:
    inventory = scan_videos(source_root)
    blocked = tuple(
        record
        for record in inventory
        if record.status in {InventoryStatus.BLOCKED_LINK, InventoryStatus.UNREADABLE}
    )
    if blocked:
        raise PlanningConfigurationError(
            "inventory contains blocked or unreadable video entries"
        )
    sources = tuple(
        record.to_source_file()
        for record in inventory
        if record.status is InventoryStatus.INCLUDED
    )
    if not sources:
        raise PlanningConfigurationError("no included video files were found")
    _validate_episode_decision_sources(sources, overrides)
    sidecars = discover_sidecars(source_root, sources)
    classifications = {
        source.relative_path: classify_extra(source.relative_path) for source in sources
    }
    groups: dict[str, list[SourceFile]] = defaultdict(list)
    for source in sources:
        groups[_show_group_key(source, classifications[source.relative_path])].append(
            source
        )

    destination_policy = DestinationPolicy(
        max_path_length=config.max_path_length,
        max_component_length=config.max_component_length,
    )
    records: list[PlanRecord] = []
    for source_key in sorted(groups, key=_path_key):
        group = tuple(
            sorted(groups[source_key], key=lambda item: _path_key(item.relative_path))
        )
        resolution = resolve_show_group_with_provider(
            source_key,
            (classifications[source.relative_path].parse for source in group),
            overrides,
            provider,
        )
        if resolution.status is not ResolutionStatus.MATCHED:
            records.extend(_unresolved_show_records(group, classifications, resolution))
            continue
        records.extend(
            _plan_resolved_group(
                group,
                classifications,
                resolution,
                provider,
                destination_policy,
                overrides,
            )
        )

    _validate_episode_decision_consumption(records, overrides)
    records = _apply_duplicate_decisions(records, sidecars, overrides)
    ordered_records = tuple(
        sorted(records, key=lambda item: _path_key(item.source.relative_path))
    )
    companions = _plan_companions(sidecars, ordered_records)
    provenance = PlanProvenance(
        tool_version=__version__,
        config_snapshot_id=config.snapshot_id,
        overrides_snapshot_id=overrides.snapshot_id,
        cache_snapshots=_cache_snapshots(cache),
    )
    return OrganizerPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        overrides_version=overrides.schema_version,
        records=ordered_records,
        provenance=provenance,
        companions=companions,
    )


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


def _video_preflight_status(record: PlanRecord) -> PreflightStatus:
    if record.status is TerminalStatus.SUSPICIOUS:
        return PreflightStatus.SUSPICIOUS
    if record.status is TerminalStatus.UNRESOLVED:
        return PreflightStatus.UNRESOLVED
    if record.status is TerminalStatus.DUPLICATE:
        return PreflightStatus.NON_MOVING
    if (
        record.destination is not None
        and _path_key(record.destination)[0]
        == _path_key(record.source.relative_path)[0]
    ):
        return PreflightStatus.NON_MOVING
    return PreflightStatus.MATCHED


def _preflight_records(plan: OrganizerPlan) -> tuple[PreflightRecord, ...]:
    videos = {record.source.relative_path: record for record in plan.records}
    records: list[PreflightRecord] = []
    for record in plan.records:
        records.append(
            PreflightRecord(
                record_id=f"video:{record.source.relative_path}",
                source_relative_path=record.source.relative_path,
                status=_video_preflight_status(record),
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
                and _path_key(companion.destination)[0]
                == _path_key(companion.relative_path)[0]
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


def _is_inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _external_state_path(path: Path, roots: tuple[Path, ...], label: str) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    if any(_is_inside(resolved, root) for root in roots):
        raise PlanningConfigurationError(f"{label} must be outside media roots")
    return resolved


def execute_plan(
    config: PlanningConfig,
    getter: JsonGetter = http_json_getter,
    *,
    clock: Clock | None = None,
) -> PlanningOutcome:
    source_root = authorize_shows_root(config.shows_root)
    destination_root = authorize_destination_root(config.destination_root)
    roots = tuple({source_root.path, destination_root.path})
    output_dir = _external_state_path(config.output_dir, roots, "output directory")
    cache_dir = _external_state_path(config.cache_dir, roots, "cache directory")
    if output_dir.exists():
        raise PlanningConfigurationError("output directory already exists")
    if not output_dir.parent.is_dir():
        raise PlanningConfigurationError("output directory parent does not exist")

    overrides = load_overrides(config.overrides_path)
    cache = TrackingTvmazeCatalogCache(
        cache_dir,
        offline=config.offline,
        refresh=config.refresh,
        clock=clock,
    )
    provider = TvmazeProviderAdapter(cache, getter)
    plan = _build_plan(source_root, config, overrides, cache, provider)
    plan_hash = stable_plan_hash(plan)
    preflight = preflight_plan(
        plan_hash,
        _preflight_records(plan),
        source_root=source_root,
        destination_root=destination_root,
        max_path_length=config.max_path_length,
        max_component_length=config.max_component_length,
    )
    bundle = write_audit_bundle(output_dir, plan, preflight)
    provider_failure = any(
        record.state is not CacheState.OK for record in cache.records.values()
    )
    return PlanningOutcome(
        plan=plan,
        preflight=preflight,
        bundle=bundle,
        provider_failure=provider_failure,
    )
