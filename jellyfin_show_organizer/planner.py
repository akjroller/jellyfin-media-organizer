from __future__ import annotations

import hashlib
import json
import os
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from . import __version__
from .companion_planning import plan_companions
from .destination import (
    DestinationPolicy,
    DestinationStatus,
    build_episode_destination,
    build_extra_destination,
)
from .duplicate_planning import (
    apply_duplicate_decisions,
    recover_equivalent_provider_episode_duplicates,
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
    InventoryRecord,
    InventoryStatus,
    authorize_shows_root,
    scan_videos,
    skipped_inventory_records,
)
from .models import (
    CacheSnapshot,
    MatchEvidence,
    OrganizerPlan,
    ParseResult,
    PlanEpisode,
    PlanProvenance,
    PlanRecord,
    SourceFile,
    TerminalStatus,
)
from .overrides import EpisodeDecisionOverride, OverrideCatalog, load_overrides
from .plan_common import (
    PlanningConfigurationError,
    operation_group_id,
    path_key,
)
from .plan_preflight import preflight_records
from .preflight import (
    PreflightResult,
    authorize_destination_root,
    preflight_plan,
)
from .provider_aliases import TvmazeAliasProviderAdapter
from .provider_http import (  # re-exported for existing callers
    TVMAZE_MIN_INTERVAL_SECONDS,
    ProviderHttpError as ProviderHttpError,
    ThrottledJsonGetter,
)
from .providers import (
    AutoProviderAdapter,
    MetadataProvider,
    ProviderEpisode,
    TmdbProviderAdapter,
)
from .reports import AuditBundle, write_audit_bundle
from .run_provenance import (
    build_run_provenance,
    detect_source_revision,
    render_run_provenance,
)
from .schema import PLAN_SCHEMA_VERSION, stable_plan_hash
from .show_resolver import (
    ResolutionStatus,
    ShowResolution,
    resolve_show_group_with_provider,
)
from .sidecars import (
    discover_sidecars,
)
from .tmdb_cache import TmdbCatalogCache, tmdb_http_getter
from .tvmaze_cache import (
    CacheRecord,
    CacheState,
    Clock,
    JsonGetter,
    TvmazeCatalogCache,
)


@dataclass(frozen=True, slots=True)
class PlanningConfig:
    shows_root: Path
    destination_root: Path
    output_dir: Path
    cache_dir: Path
    overrides_path: Path | None = None
    offline: bool = False
    refresh: bool = False
    provider_strategy: str = "auto"
    max_path_length: int = 240
    max_component_length: int = 180

    def __post_init__(self) -> None:
        if self.offline and self.refresh:
            raise PlanningConfigurationError(
                "offline and refresh modes cannot be enabled together"
            )
        if self.provider_strategy not in {"auto", "tvmaze"}:
            raise PlanningConfigurationError("provider_strategy must be auto or tvmaze")
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
            # Keep the existing config identity stable.  Auto mode only adds
            # optional evidence when a TMDb token is configured; the actual
            # cache snapshots and plan hash still bind the resulting run.
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
    provider_strategy: str = "tvmaze"
    provider_names: tuple[str, ...] = ("tvmaze",)
    tmdb_available: bool = False
    skipped_inventory: tuple[InventoryRecord, ...] = ()


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


class TrackingTmdbCatalogCache(TmdbCatalogCache):
    def __init__(
        self, root: Path, *, offline: bool, refresh: bool, clock: Clock | None = None
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

    def episode_catalog(self, tmdb_id: int, getter: JsonGetter) -> CacheRecord:
        return self._track(super().episode_catalog(tmdb_id, getter))


# One shared, throttled getter keeps every planning, review, and wizard call
# within TVMaze's published rate limit for the whole process.
http_json_getter: JsonGetter = ThrottledJsonGetter(
    headers={"Accept": "application/json", "User-Agent": f"JMO/{__version__}"},
    min_interval=TVMAZE_MIN_INTERVAL_SECONDS,
)


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


def _provider_season_parse(  # pragma: no cover - exercised through planning integration
    parse: ParseResult,
    season_map: Mapping[int, int],
) -> tuple[ParseResult, tuple[str, ...]]:
    """Translate an explicitly configured source season to provider numbering."""

    if parse.season is None or parse.season not in season_map:
        return parse, ()
    target = season_map[parse.season]
    return (
        replace(parse, season=target),
        (f"provider-season-remap:S{parse.season:02d}->S{target:02d}",),
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
            operation_group_id=operation_group_id(source.relative_path),
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
    show_override = overrides.matching_show(
        show.source_key,
        tuple(
            classification.parse.series_hint
            for source in sources
            if (
                classification := classifications[source.relative_path]
            ).parse.series_hint
        ),
    )
    season_map = dict(show_override.season_map) if show_override is not None else {}
    records: list[PlanRecord] = []
    episode_sources: list[SourceEpisodeInput] = []
    decision_evidence: dict[str, MatchEvidence] = {}

    for source in sources:
        classification = classifications[source.relative_path]
        group_id = operation_group_id(source.relative_path)
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

        effective_parse, season_remap_reasons = _provider_season_parse(
            classification.parse, season_map
        )
        if season_remap_reasons:
            decision_evidence[source.relative_path] = MatchEvidence(
                method="provider-season-remap",
                confidence=1.0,
                reasons=season_remap_reasons,
            )
        if decision is not None:
            effective_parse = decision.apply_to(effective_parse)
            decision_evidence[source.relative_path] = _episode_decision_evidence(
                decision
            )
        episode_sources.append(
            SourceEpisodeInput(
                source_key=source.relative_path,
                parse=effective_parse,
                explicit_decision=decision is not None,
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
    assignments.update(
        recover_equivalent_provider_episode_duplicates(
            show,
            tuple(episode_sources),
            assignments,
            provider,
        )
    )
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
        group_id = operation_group_id(source.relative_path)
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


def _cache_snapshots(
    cache: TrackingTvmazeCatalogCache | TrackingTmdbCatalogCache,
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
    return {path_key(decision.source)[0] for decision in overrides.episode_decisions}


def _configured_source_hold_keys(overrides: OverrideCatalog) -> set[str]:
    return {path_key(hold.source)[0] for hold in overrides.source_holds}


def _validate_source_hold_sources(
    sources: tuple[SourceFile, ...],
    overrides: OverrideCatalog,
) -> None:
    configured = _configured_source_hold_keys(overrides)
    available = {path_key(source.relative_path)[0] for source in sources}
    if configured - available:
        raise PlanningConfigurationError("source hold references an unknown source")


def _validate_episode_decision_sources(
    sources: tuple[SourceFile, ...],
    overrides: OverrideCatalog,
) -> None:
    configured = _configured_episode_decision_keys(overrides)
    available = {path_key(source.relative_path)[0] for source in sources}
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
        path_key(record.source.relative_path)[0]
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
    *,
    extra_cache_snapshots: tuple[CacheSnapshot, ...] = (),
    progress: Callable[[str, int, int], None] | None = None,
    inventory: tuple[InventoryRecord, ...] | None = None,
) -> OrganizerPlan:
    if inventory is None:
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
    if progress is not None:
        progress("inventory", len(sources), len(inventory))
    _validate_episode_decision_sources(sources, overrides)
    _validate_source_hold_sources(sources, overrides)
    sidecars = discover_sidecars(source_root, sources)
    classifications = {
        source.relative_path: classify_extra(source.relative_path) for source in sources
    }
    groups: dict[str, list[SourceFile]] = defaultdict(list)
    held_records: list[PlanRecord] = []
    for source in sources:
        hold = overrides.source_hold_for(source.relative_path)
        if hold is not None:
            evidence = MatchEvidence(
                method="source-hold-override",
                confidence=1.0,
                reasons=hold.reasons,
            )
            held_records.append(
                PlanRecord(
                    source=source,
                    status=TerminalStatus.HELD,
                    parse=classifications[source.relative_path].parse,
                    evidence=evidence,
                    operation_group_id=operation_group_id(source.relative_path),
                    reason=_reason(evidence),
                )
            )
            continue
        groups[_show_group_key(source, classifications[source.relative_path])].append(
            source
        )

    destination_policy = DestinationPolicy(
        max_path_length=config.max_path_length,
        max_component_length=config.max_component_length,
    )
    records: list[PlanRecord] = list(held_records)
    ordered_groups = sorted(groups, key=path_key)
    for index, source_key in enumerate(ordered_groups, start=1):
        group = tuple(
            sorted(groups[source_key], key=lambda item: path_key(item.relative_path))
        )
        resolution = resolve_show_group_with_provider(
            source_key,
            (classifications[source.relative_path].parse for source in group),
            overrides,
            provider,
        )
        if progress is not None:
            progress("resolving", index, len(ordered_groups))
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
    records = apply_duplicate_decisions(records, sidecars, overrides)
    ordered_records = tuple(
        sorted(records, key=lambda item: path_key(item.source.relative_path))
    )
    companions = plan_companions(
        sidecars,
        ordered_records,
        config.destination_root.expanduser().resolve(strict=False),
    )
    provenance = PlanProvenance(
        tool_version=__version__,
        config_snapshot_id=config.snapshot_id,
        overrides_snapshot_id=overrides.snapshot_id,
        cache_snapshots=(*_cache_snapshots(cache), *extra_cache_snapshots),
    )
    return OrganizerPlan(
        schema_version=PLAN_SCHEMA_VERSION,
        overrides_version=overrides.schema_version,
        records=ordered_records,
        provenance=provenance,
        companions=companions,
    )


def _is_inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def _external_state_path(path: Path, roots: tuple[Path, ...], label: str) -> Path:
    resolved = path.expanduser().resolve(strict=False)
    if any(_is_inside(resolved, root) for root in roots):
        raise PlanningConfigurationError(f"{label} must be outside media roots")
    return resolved


# Public orchestration seams used by the review pipeline.  The implementation
# remains in this module for now, but callers should depend on these names
# rather than reaching through planner's private namespace.  The underscored
# names remain as compatibility aliases for older local integrations/tests.
plan_episode = _plan_episode
build_plan = _build_plan
external_state_path = _external_state_path


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
    tvmaze_provider = TvmazeAliasProviderAdapter(cache, getter)
    tmdb_cache: TrackingTmdbCatalogCache | None = None
    tmdb_token = os.environ.get("JMO_TMDB_ACCESS_TOKEN", "").strip()
    if config.provider_strategy == "auto" and tmdb_token:
        tmdb_cache = TrackingTmdbCatalogCache(
            cache_dir / "tmdb",
            offline=config.offline,
            refresh=config.refresh,
            clock=clock,
        )
        provider: MetadataProvider = AutoProviderAdapter(
            tvmaze_provider,
            TmdbProviderAdapter(tmdb_cache, tmdb_http_getter(tmdb_token)),
        )
    else:
        provider = tvmaze_provider
    tmdb_snapshots = _cache_snapshots(tmdb_cache) if tmdb_cache is not None else ()
    inventory = scan_videos(source_root)
    skipped = skipped_inventory_records(inventory)
    plan = _build_plan(
        source_root,
        config,
        overrides,
        cache,
        provider,
        extra_cache_snapshots=tmdb_snapshots,
        inventory=inventory,
    )
    plan_hash = stable_plan_hash(plan)
    preflight = preflight_plan(
        plan_hash,
        preflight_records(plan),
        source_root=source_root,
        destination_root=destination_root,
        max_path_length=config.max_path_length,
        max_component_length=config.max_component_length,
    )
    cache_records = list(cache.records.values())
    if tmdb_cache is not None:
        cache_records.extend(tmdb_cache.records.values())
    provider_failure = any(
        record.state is not CacheState.OK for record in cache_records
    )
    provider_mode = (
        "offline" if config.offline else "refresh" if config.refresh else "online"
    )
    actual_provider_strategy = config.provider_strategy
    actual_provider_names = (
        ("tvmaze", "tmdb") if tmdb_cache is not None else ("tvmaze",)
    )
    run_provenance = build_run_provenance(
        plan,
        source_revision=detect_source_revision(),
        provider_mode=provider_mode,
        provider_failure=provider_failure,
        max_path_length=config.max_path_length,
        max_component_length=config.max_component_length,
        overrides_configured=config.overrides_path is not None,
        preflight_ready=preflight.ready,
        preflight_finding_count=len(preflight.findings),
        provider_strategy=actual_provider_strategy,
        provider_names=actual_provider_names,
    )
    bundle = write_audit_bundle(
        output_dir,
        plan,
        preflight,
        run_provenance_json=render_run_provenance(run_provenance),
        skipped=skipped,
    )
    return PlanningOutcome(
        plan=plan,
        preflight=preflight,
        bundle=bundle,
        provider_failure=provider_failure,
        provider_strategy=actual_provider_strategy,
        provider_names=actual_provider_names,
        tmdb_available=tmdb_cache is not None,
        skipped_inventory=skipped,
    )
