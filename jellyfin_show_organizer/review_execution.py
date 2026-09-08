from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

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
    DuplicateDecision,
    ExtraDecision,
    MatchEvidence,
    OrganizerPlan,
    PlanRecord,
    TerminalStatus,
)
from .preflight import preflight_plan
from .providers import MetadataProvider, ProviderEpisode, TvmazeProviderAdapter
from .reports import write_audit_bundle
from .review_contract import (
    DuplicateGroupAction,
    ReviewContractCatalog,
    load_review_contract,
    verify_review_contract_session,
)
from .review_identity import (
    ReviewCandidateBinding,
    ReviewFingerprint,
    ReviewMemberBinding,
    duplicate_candidate_set_hash,
    normalize_review_path,
    source_binding_hash,
    stable_duplicate_ref,
)
from .review_session import ReviewSession, load_review_session
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


def _fingerprint(value) -> ReviewFingerprint:
    return ReviewFingerprint(
        size=value.size,
        mtime_ns=value.mtime_ns,
        sha256=value.sha256,
    )


def _bindings_by_source(plan: OrganizerPlan) -> dict[str, ReviewCandidateBinding]:
    companions: dict[str, list[ReviewMemberBinding]] = defaultdict(list)
    for companion in plan.companions:
        if companion.source_video is None or companion.fingerprint is None:
            continue
        companions[normalize_review_path(companion.source_video)].append(
            ReviewMemberBinding(
                path=companion.relative_path,
                fingerprint=_fingerprint(companion.fingerprint),
            )
        )

    return {
        normalize_review_path(record.source.relative_path): ReviewCandidateBinding(
            source=ReviewMemberBinding(
                path=record.source.relative_path,
                fingerprint=_fingerprint(record.source.fingerprint),
            ),
            companions=tuple(
                sorted(
                    companions.get(
                        normalize_review_path(record.source.relative_path), ()
                    ),
                    key=lambda item: (normalize_review_path(item.path), item.path),
                )
            ),
        )
        for record in plan.records
    }


def _bindings_for_group(
    plan: OrganizerPlan,
    candidates: tuple[str, ...],
) -> tuple[ReviewCandidateBinding, ...]:
    bindings = _bindings_by_source(plan)
    result = []
    for source in candidates:
        binding = bindings.get(normalize_review_path(source))
        if binding is None:
            raise PlanningConfigurationError(
                "reviewed duplicate candidate is missing from the current plan"
            )
        result.append(binding)
    return tuple(result)


def _source_binding(plan: OrganizerPlan, source: str) -> str:
    binding = _bindings_by_source(plan).get(normalize_review_path(source))
    if binding is None:
        raise PlanningConfigurationError(
            "reviewed source is missing from the current plan"
        )
    return source_binding_hash(binding)


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


def _confirmed_provider_episode(
    provider: MetadataProvider,
    decision,
) -> ProviderEpisode:
    catalog = provider.episode_catalog(decision.show_provider_identity)
    if not catalog.resolved or catalog.errors:
        raise PlanningConfigurationError(
            "reviewed episode provider catalog is unavailable or unsafe"
        )
    matches = [
        episode
        for episode in catalog.episodes
        if episode.identity == decision.episode_provider_identity
    ]
    if len(matches) != 1:
        raise PlanningConfigurationError(
            "reviewed provider episode identity is missing or ambiguous"
        )
    episode = matches[0]
    if (
        episode.number is None
        or episode.season != decision.season
        or episode.number != decision.number
        or episode.title != decision.title
        or episode.airdate != decision.airdate
    ):
        raise PlanningConfigurationError(
            "reviewed provider episode metadata changed since manual confirmation"
        )
    return episode


def _reviewed_episode_record(
    record: PlanRecord,
    original_plan: OrganizerPlan,
    catalog: ReviewContractCatalog,
    provider: MetadataProvider,
    destination_policy: DestinationPolicy,
) -> PlanRecord:
    decisions = catalog.reviewed_episodes_for(record.source.relative_path)
    if not decisions:
        return record

    source_binding = _source_binding(original_plan, record.source.relative_path)
    if any(decision.source_binding_sha256 != source_binding for decision in decisions):
        raise PlanningConfigurationError(
            "reviewed episode source fingerprint or companion set changed"
        )
    if record.show is None:
        raise PlanningConfigurationError(
            "reviewed episode could not resolve a verified show identity"
        )
    if any(
        record.show.provider_identity != decision.show_provider_identity
        for decision in decisions
    ):
        raise PlanningConfigurationError(
            "reviewed episode conflicts with resolved show identity"
        )

    episodes = tuple(
        _confirmed_provider_episode(provider, decision) for decision in decisions
    )
    if len({episode.identity for episode in episodes}) != len(episodes):
        raise PlanningConfigurationError(
            "reviewed provider episode set contains duplicate identities"
        )
    if len({(episode.season, episode.number) for episode in episodes}) != len(episodes):
        raise PlanningConfigurationError(
            "reviewed provider episode set contains duplicate coordinates"
        )

    reasons: list[str] = []
    for decision, episode in zip(decisions, episodes, strict=True):
        for reason in (
            f"manual-review-lookup-mode:{decision.lookup_mode}",
            f"reviewed-provider-episode:{episode.identity.key}",
            *decision.reasons,
        ):
            if reason not in reasons:
                reasons.append(reason)
    reasons.append(f"reviewed-source-binding:{source_binding}")
    evidence = MatchEvidence(
        method=(
            "reviewed-provider-episode"
            if len(episodes) == 1
            else "reviewed-provider-episode-set"
        ),
        confidence=1.0,
        reasons=tuple(reasons),
    )
    assignment = SourceEpisodeAssignment(
        source_key=record.source.relative_path,
        status=AssignmentStatus.MATCHED,
        episodes=episodes,
        evidence=evidence,
    )
    provider_ids = catalog.jellyfin_identifiers_for(record.show.source_key)
    destination = build_episode_destination(
        record.show,
        assignment,
        record.source.extension,
        provider_ids=provider_ids,
        policy=destination_policy,
    )
    if destination.status is not DestinationStatus.READY:
        raise PlanningConfigurationError(
            "reviewed episode cannot produce a safe Jellyfin destination"
        )
    return replace(
        record,
        status=TerminalStatus.MATCHED,
        evidence=evidence,
        destination=destination.relative_path,
        extra=None,
        duplicate=None,
        provider_episodes=tuple(
            _planner._plan_episode(episode) for episode in episodes
        ),
        reason=None,
    )


def _explicit_extra_record(
    record: PlanRecord,
    original_plan: OrganizerPlan,
    catalog: ReviewContractCatalog,
    destination_policy: DestinationPolicy,
) -> PlanRecord:
    decision = catalog.extra_decision_for(record.source.relative_path)
    if decision is None:
        return record
    if _source_binding(original_plan, record.source.relative_path) != (
        decision.source_binding_sha256
    ):
        raise PlanningConfigurationError(
            "reviewed extra source fingerprint or companion set changed"
        )
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
            f"reviewed-source-binding:{decision.source_binding_sha256}",
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
    catalog: ReviewContractCatalog,
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
            raise PlanningConfigurationError(
                "extra record is missing an extra decision"
            )
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
    catalog: ReviewContractCatalog,
    config: PlanningConfig,
    provider: MetadataProvider,
) -> OrganizerPlan:
    destination_policy = DestinationPolicy(
        max_path_length=config.max_path_length,
        max_component_length=config.max_component_length,
    )
    records = _clear_duplicate_decisions(plan.records)
    source_keys = {
        _planner._path_key(record.source.relative_path)[0] for record in records
    }
    configured_episodes = {
        _planner._path_key(decision.source)[0]
        for decision in catalog.reviewed_episode_decisions
    }
    configured_extras = {
        _planner._path_key(decision.source)[0] for decision in catalog.extra_decisions
    }
    if configured_episodes - source_keys:
        raise PlanningConfigurationError(
            "reviewed episode decision references an unknown source"
        )
    if configured_extras - source_keys:
        raise PlanningConfigurationError("extra decision references an unknown source")

    reviewed_records: list[PlanRecord] = []
    consumed_episodes: set[str] = set()
    consumed_extras: set[str] = set()
    for record in records:
        updated = _reviewed_episode_record(
            record,
            plan,
            catalog,
            provider,
            destination_policy,
        )
        if updated is not record:
            consumed_episodes.add(_planner._path_key(record.source.relative_path)[0])
        extra_updated = _explicit_extra_record(
            updated,
            plan,
            catalog,
            destination_policy,
        )
        if extra_updated is not updated:
            consumed_extras.add(_planner._path_key(record.source.relative_path)[0])
        updated = _with_jellyfin_ids(extra_updated, catalog, destination_policy)
        reviewed_records.append(updated)
    if configured_episodes - consumed_episodes:
        raise PlanningConfigurationError(
            "reviewed episode decision could not be consumed safely"
        )
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
        catalog,
    )
    ordered_records = tuple(
        sorted(
            reviewed_records,
            key=lambda item: _planner._path_key(item.source.relative_path),
        )
    )
    companions = _planner._plan_companions(sidecars, ordered_records)
    return replace(plan, records=ordered_records, companions=companions)


def _duplicate_groups(
    plan: OrganizerPlan,
) -> dict[str, tuple[DuplicateDecision, tuple[PlanRecord, ...]]]:
    grouped: dict[str, list[PlanRecord]] = defaultdict(list)
    decision_by_ref: dict[str, DuplicateDecision] = {}
    for record in plan.records:
        decision = record.duplicate
        if decision is None:
            continue
        ref = stable_duplicate_ref(decision.destination_key, decision.candidates)
        previous = decision_by_ref.get(ref)
        if previous is not None and previous != decision:
            raise PlanningConfigurationError(
                "current plan contains inconsistent duplicate-group decisions"
            )
        decision_by_ref[ref] = decision
        grouped[ref].append(record)
    return {
        ref: (decision_by_ref[ref], tuple(records)) for ref, records in grouped.items()
    }


def _apply_duplicate_group_contract(
    plan: OrganizerPlan,
    catalog: ReviewContractCatalog,
) -> OrganizerPlan:
    if not catalog.duplicate_group_decisions:
        return plan

    current = _duplicate_groups(plan)
    by_source = {record.source.relative_path: record for record in plan.records}
    changed = False

    for reviewed in catalog.duplicate_group_decisions:
        group = current.get(reviewed.duplicate_ref)
        if group is None:
            raise PlanningConfigurationError(
                "reviewed duplicate group no longer exists with the same candidate set"
            )
        decision, records = group
        current_candidates = {
            normalize_review_path(candidate) for candidate in decision.candidates
        }
        reviewed_candidates = {
            normalize_review_path(candidate) for candidate in reviewed.candidates
        }
        if current_candidates != reviewed_candidates:
            raise PlanningConfigurationError(
                "reviewed duplicate candidate set changed since manual review"
            )
        candidate_hash = duplicate_candidate_set_hash(
            decision.destination_key,
            _bindings_for_group(plan, reviewed.candidates),
        )
        if candidate_hash != reviewed.candidate_set_sha256:
            raise PlanningConfigurationError(
                "reviewed duplicate candidate fingerprints or companion set changed"
            )

        if reviewed.action is DuplicateGroupAction.SELECT_WINNER:
            if decision.winner is None or reviewed.winner is None:
                raise PlanningConfigurationError(
                    "reviewed duplicate winner could not be honored safely"
                )
            if normalize_review_path(decision.winner) != normalize_review_path(
                reviewed.winner
            ):
                raise PlanningConfigurationError(
                    "planner selected a different winner than the reviewed duplicate decision"
                )
            continue

        keep_all = DuplicateDecision(
            destination_key=decision.destination_key,
            candidates=decision.candidates,
            winner=None,
            losers=(),
            confidence=1.0,
            evidence=(
                *decision.evidence,
                "human-reviewed duplicate-group action:keep-all",
                f"reviewed-duplicate-ref:{reviewed.duplicate_ref}",
                f"reviewed-candidate-set:{reviewed.candidate_set_sha256}",
                *reviewed.reasons,
                "all candidates remain in place; no delete or quarantine operation is authorized",
            ),
            collision_class=decision.collision_class,
        )
        for record in records:
            by_source[record.source.relative_path] = replace(
                record,
                status=TerminalStatus.DUPLICATE,
                duplicate=keep_all,
                reason="human-reviewed keep-all duplicate decision",
            )
        changed = True

    if not changed:
        return plan
    ordered = tuple(
        sorted(
            by_source.values(),
            key=lambda record: _planner._path_key(record.source.relative_path),
        )
    )
    return replace(plan, records=ordered)


def _rebuild_companions(source_root, plan: OrganizerPlan) -> OrganizerPlan:
    inventory = scan_videos(source_root)
    sources = tuple(
        item.to_source_file()
        for item in inventory
        if item.status is InventoryStatus.INCLUDED
    )
    sidecars = discover_sidecars(source_root, sources)
    companions = _planner._plan_companions(sidecars, plan.records)
    return replace(plan, companions=companions)


def _run_provenance_bytes(
    run_provenance,
    catalog: ReviewContractCatalog | None,
    session: ReviewSession | None,
) -> bytes:
    rendered = render_run_provenance(run_provenance)
    if catalog is None or session is None:
        return rendered
    payload = json.loads(rendered.decode("utf-8"))
    payload["review"] = {
        "session_sha256": session.sha256,
        "base_plan_sha256": session.plan_sha256,
        "base_override_snapshot": session.base_override_snapshot,
        "scope_state": "complete" if session.complete else "approved-partial",
        "approved_scope_refs": list(session.approved_scope_refs),
        "authorization": "review-state-only",
        "movement_authorized": False,
        "full_plan_approval_required": True,
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def execute_plan(
    config: PlanningConfig,
    getter: JsonGetter = http_json_getter,
    *,
    clock: Clock | None = None,
    review_session_path: Path | None = None,
) -> PlanningOutcome:
    """Execute the single plan-only path, including verified reviewed state."""

    source_root = _planner.authorize_shows_root(config.shows_root)
    destination_root = _planner.authorize_destination_root(config.destination_root)
    roots = tuple({source_root.path, destination_root.path})
    output_dir = _planner._external_state_path(
        config.output_dir,
        roots,
        "output directory",
    )
    cache_dir = _planner._external_state_path(
        config.cache_dir, roots, "cache directory"
    )
    if output_dir.exists():
        raise PlanningConfigurationError("output directory already exists")
    if not output_dir.parent.is_dir():
        raise PlanningConfigurationError("output directory parent does not exist")

    overrides = load_review_contract(config.overrides_path)
    review_catalog = overrides if isinstance(overrides, ReviewContractCatalog) else None
    review_session: ReviewSession | None = None
    if review_catalog is not None:
        if review_session_path is None:
            raise PlanningConfigurationError(
                "schema-5 reviewed overrides require --review-session"
            )
        session_file = _planner._external_state_path(
            review_session_path,
            roots,
            "review session",
        )
        if not session_file.is_file():
            raise PlanningConfigurationError("review session file does not exist")
        review_session = load_review_session(session_file.read_bytes())
        try:
            verify_review_contract_session(review_catalog, review_session)
        except ValueError as exc:
            raise PlanningConfigurationError(str(exc)) from exc
    elif review_session_path is not None:
        raise PlanningConfigurationError(
            "--review-session is only valid with schema-5 reviewed overrides"
        )

    cache = _planner.TrackingTvmazeCatalogCache(
        cache_dir,
        offline=config.offline,
        refresh=config.refresh,
        clock=clock,
    )
    provider = TvmazeProviderAdapter(cache, getter)
    plan = _planner._build_plan(source_root, config, overrides, cache, provider)
    if review_catalog is not None:
        plan = _apply_review_extensions(
            plan,
            source_root,
            review_catalog,
            config,
            provider,
        )
        plan = _apply_duplicate_group_contract(plan, review_catalog)
        plan = _rebuild_companions(source_root, plan)

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
        run_provenance_json=_run_provenance_bytes(
            run_provenance,
            review_catalog,
            review_session,
        ),
    )
    return PlanningOutcome(
        plan=plan,
        preflight=preflight,
        bundle=bundle,
        provider_failure=provider_failure,
    )
