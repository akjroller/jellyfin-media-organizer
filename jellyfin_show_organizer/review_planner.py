from __future__ import annotations

from dataclasses import replace

from . import planner as _planner
from .destination import (
    DestinationPolicy,
    DestinationStatus,
    build_episode_destination,
    build_extra_destination,
)
from .episode_assignment_strict import AssignmentStatus, SourceEpisodeAssignment
from .extra_naming import derive_extra_display_identity
from .inventory import InventoryStatus, scan_videos
from .models import (
    ExtraDecision,
    MatchEvidence,
    OrganizerPlan,
    PlanRecord,
    TerminalStatus,
)
from .preflight import preflight_plan
from .providers import ProviderEpisode, TvmazeProviderAdapter
from .reports import write_audit_bundle
from .review_overrides import ReviewOverrideCatalog, load_planning_overrides
from .run_provenance import (
    build_run_provenance,
    detect_source_revision,
    render_run_provenance,
)
from .schema import stable_plan_hash
from .sidecars import discover_sidecars
from .tvmaze_cache import CacheState, Clock, JsonGetter

PlanningConfig = _planner.PlanningConfig
PlanningConfigurationError = _planner.PlanningConfigurationError
PlanningOutcome = _planner.PlanningOutcome
http_json_getter = _planner.http_json_getter


def _review_catalog(overrides: object) -> ReviewOverrideCatalog | None:
    return overrides if isinstance(overrides, ReviewOverrideCatalog) else None


def _provider_episodes(record: PlanRecord) -> tuple[ProviderEpisode, ...]:
    return tuple(
        ProviderEpisode(
            identity=episode.provider_identity,
            season=episode.season,
            number=episode.number,
            title=episode.title,
            airdate=episode.airdate,
        )
        for episode in record.provider_episodes
    )


def _base_duplicate_status(record: PlanRecord) -> TerminalStatus:
    if record.extra is not None:
        return TerminalStatus.EXTRA
    if record.destination is not None and record.provider_episodes:
        return TerminalStatus.MATCHED
    return record.status


def _clear_duplicate_decisions(records: tuple[PlanRecord, ...]) -> list[PlanRecord]:
    """Restore movable candidates before destinations are rebuilt and reclassified."""

    restored: list[PlanRecord] = []
    for record in records:
        if record.duplicate is None:
            restored.append(record)
            continue
        restored.append(
            replace(
                record,
                status=_base_duplicate_status(record),
                duplicate=None,
                reason=None,
            )
        )
    return restored


def _explicit_extra_record(
    record: PlanRecord,
    catalog: ReviewOverrideCatalog,
    destination_policy: DestinationPolicy,
) -> PlanRecord:
    decision = catalog.extra_decision_for(record.source.relative_path)
    if decision is None:
        return record
    if record.show is None:
        raise PlanningConfigurationError(
            "reviewed extra decision could not resolve a verified show identity"
        )
    if record.show.provider_identity != decision.show_provider_identity:
        raise PlanningConfigurationError(
            "reviewed extra decision conflicts with resolved show identity"
        )

    extra = ExtraDecision(kind=decision.kind, rule="explicit-review-extra")
    display_title = decision.display_title
    if display_title is None and record.parse is not None:
        display_title = record.parse.title_hint
    naming = derive_extra_display_identity(
        record.source.relative_path,
        extra.kind,
        show_title=record.show.title,
        title_hint=display_title,
    )
    provider_ids = catalog.jellyfin_identifiers_for(record.show.source_key)
    destination = build_extra_destination(
        record.show,
        source_key=record.source.relative_path,
        extra=extra,
        source_extension=record.source.extension,
        display_title=display_title,
        provider_ids=provider_ids,
        policy=destination_policy,
    )
    if destination.status is not DestinationStatus.READY:
        raise PlanningConfigurationError(
            "reviewed extra decision cannot produce a safe destination"
        )
    evidence = MatchEvidence(
        method="explicit-extra-review+extra-naming",
        confidence=1.0,
        reasons=(
            *decision.reasons,
            *naming.reasons,
        ),
    )
    return replace(
        record,
        status=TerminalStatus.EXTRA,
        evidence=evidence,
        destination=destination.relative_path,
        extra=extra,
        duplicate=None,
        provider_episodes=(),
        reason=None,
    )


def _with_jellyfin_ids(
    record: PlanRecord,
    catalog: ReviewOverrideCatalog,
    destination_policy: DestinationPolicy,
) -> PlanRecord:
    if (
        record.show is None
        or record.destination is None
        or record.status not in {TerminalStatus.MATCHED, TerminalStatus.EXTRA}
    ):
        return record
    provider_ids = catalog.jellyfin_identifiers_for(record.show.source_key)
    if not provider_ids:
        return record

    if record.status is TerminalStatus.EXTRA:
        if record.extra is None:
            raise PlanningConfigurationError("extra record is missing an extra decision")
        display_title = record.parse.title_hint if record.parse is not None else None
        reviewed = catalog.extra_decision_for(record.source.relative_path)
        if reviewed is not None and reviewed.display_title is not None:
            display_title = reviewed.display_title
        destination = build_extra_destination(
            record.show,
            source_key=record.source.relative_path,
            extra=record.extra,
            source_extension=record.source.extension,
            display_title=display_title,
            provider_ids=provider_ids,
            policy=destination_policy,
        )
    else:
        episodes = _provider_episodes(record)
        if not episodes:
            raise PlanningConfigurationError(
                "matched record is missing provider episodes for destination rebuild"
            )
        assignment = SourceEpisodeAssignment(
            source_key=record.source.relative_path,
            status=AssignmentStatus.MATCHED,
            episodes=episodes,
            evidence=record.evidence
            or MatchEvidence(
                method="reviewed-destination-rebuild",
                confidence=1.0,
            ),
        )
        destination = build_episode_destination(
            record.show,
            assignment,
            record.source.extension,
            provider_ids=provider_ids,
            policy=destination_policy,
        )

    if destination.status is not DestinationStatus.READY:
        raise PlanningConfigurationError(
            "Jellyfin provider identifiers make a destination unsafe"
        )
    return replace(record, destination=destination.relative_path)


def _apply_review_extensions(
    plan: OrganizerPlan,
    source_root,
    overrides,
    config: PlanningConfig,
) -> OrganizerPlan:
    catalog = _review_catalog(overrides)
    if catalog is None:
        return plan

    destination_policy = DestinationPolicy(
        max_path_length=config.max_path_length,
        max_component_length=config.max_component_length,
    )
    records = _clear_duplicate_decisions(plan.records)
    sources_by_key = {
        _planner._path_key(record.source.relative_path)[0]: record for record in records
    }
    configured_extras = {
        _planner._path_key(decision.source)[0] for decision in catalog.extra_decisions
    }
    if configured_extras - set(sources_by_key):
        raise PlanningConfigurationError("extra decision references an unknown source")

    reviewed_records: list[PlanRecord] = []
    consumed_extras: set[str] = set()
    for record in records:
        updated = _explicit_extra_record(record, catalog, destination_policy)
        if updated is not record:
            consumed_extras.add(_planner._path_key(record.source.relative_path)[0])
        updated = _with_jellyfin_ids(updated, catalog, destination_policy)
        reviewed_records.append(updated)
    if configured_extras - consumed_extras:
        raise PlanningConfigurationError("extra decision could not be consumed safely")

    inventory = scan_videos(source_root)
    sources = tuple(
        item.to_source_file()
        for item in inventory
        if item.status is InventoryStatus.INCLUDED
    )
    sidecars = discover_sidecars(source_root, sources)
    reviewed_records = _planner._apply_duplicate_decisions(
        reviewed_records,
        sidecars,
        overrides,
    )
    ordered_records = tuple(
        sorted(
            reviewed_records,
            key=lambda item: _planner._path_key(item.source.relative_path),
        )
    )
    companions = _planner._plan_companions(sidecars, ordered_records)
    return replace(plan, records=ordered_records, companions=companions)


def execute_plan(
    config: PlanningConfig,
    getter: JsonGetter = http_json_getter,
    *,
    clock: Clock | None = None,
) -> PlanningOutcome:
    """Execute plan-only mode with schema-5 reviewed decisions when configured."""

    source_root = _planner.authorize_shows_root(config.shows_root)
    destination_root = _planner.authorize_destination_root(config.destination_root)
    roots = tuple({source_root.path, destination_root.path})
    output_dir = _planner._external_state_path(
        config.output_dir, roots, "output directory"
    )
    cache_dir = _planner._external_state_path(config.cache_dir, roots, "cache directory")
    if output_dir.exists():
        raise PlanningConfigurationError("output directory already exists")
    if not output_dir.parent.is_dir():
        raise PlanningConfigurationError("output directory parent does not exist")

    overrides = load_planning_overrides(config.overrides_path)
    cache = _planner.TrackingTvmazeCatalogCache(
        cache_dir,
        offline=config.offline,
        refresh=config.refresh,
        clock=clock,
    )
    provider = TvmazeProviderAdapter(cache, getter)
    plan = _planner._build_plan(source_root, config, overrides, cache, provider)
    plan = _apply_review_extensions(plan, source_root, overrides, config)
    plan_hash = stable_plan_hash(plan)
    preflight = preflight_plan(
        plan_hash,
        _planner._preflight_records(plan),
        source_root=source_root,
        destination_root=destination_root,
        max_path_length=config.max_path_length,
        max_component_length=config.max_component_length,
    )
    provider_failure = any(
        record.state is not CacheState.OK for record in cache.records.values()
    )
    provider_mode = (
        "offline" if config.offline else "refresh" if config.refresh else "online"
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
    )
    bundle = write_audit_bundle(
        output_dir,
        plan,
        preflight,
        run_provenance_json=render_run_provenance(run_provenance),
    )
    return PlanningOutcome(
        plan=plan,
        preflight=preflight,
        bundle=bundle,
        provider_failure=provider_failure,
    )
