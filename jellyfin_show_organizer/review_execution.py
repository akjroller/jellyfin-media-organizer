from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import replace

from . import planner as _planner
from . import review_planner as _review
from .inventory import InventoryStatus, scan_videos
from .models import DuplicateDecision, OrganizerPlan, PlanRecord, TerminalStatus
from .preflight import preflight_plan
from .providers import TvmazeProviderAdapter
from .reports import write_audit_bundle
from .review_contract import (
    DuplicateGroupAction,
    ReviewContractCatalog,
    load_review_contract,
)
from .review_identity import (
    ReviewCandidateBinding,
    ReviewFingerprint,
    ReviewMemberBinding,
    duplicate_candidate_set_hash,
    normalize_review_path,
    stable_duplicate_ref,
)
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


def _bindings_for_group(
    plan: OrganizerPlan,
    candidates: tuple[str, ...],
) -> tuple[ReviewCandidateBinding, ...]:
    records = {
        normalize_review_path(record.source.relative_path): record
        for record in plan.records
    }
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

    bindings = []
    for source in candidates:
        record = records.get(normalize_review_path(source))
        if record is None:
            raise PlanningConfigurationError(
                "reviewed duplicate candidate is missing from the current plan"
            )
        bindings.append(
            ReviewCandidateBinding(
                source=ReviewMemberBinding(
                    path=record.source.relative_path,
                    fingerprint=_fingerprint(record.source.fingerprint),
                ),
                companions=tuple(
                    sorted(
                        companions.get(normalize_review_path(source), ()),
                        key=lambda item: (
                            normalize_review_path(item.path),
                            item.path,
                        ),
                    )
                ),
            )
        )
    return tuple(bindings)


def _duplicate_groups(plan: OrganizerPlan) -> dict[str, tuple[DuplicateDecision, tuple[PlanRecord, ...]]]:
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
        ref: (decision_by_ref[ref], tuple(records))
        for ref, records in grouped.items()
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

        # Keep-all is a first-class group decision. Preserve the duplicate evidence
        # and destination identity while making every candidate explicitly non-moving.
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
        sorted(by_source.values(), key=lambda record: _planner._path_key(record.source.relative_path))
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


def _run_provenance_bytes(run_provenance, catalog) -> bytes:
    rendered = render_run_provenance(run_provenance)
    if not isinstance(catalog, ReviewContractCatalog):
        return rendered
    if catalog.review_session_sha256 is None:
        return rendered
    payload = json.loads(rendered.decode("utf-8"))
    payload["review_session_sha256"] = catalog.review_session_sha256
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
) -> PlanningOutcome:
    """Execute plan-only mode with the full reviewed-state safety contract."""

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

    overrides = load_review_contract(config.overrides_path)
    cache = _planner.TrackingTvmazeCatalogCache(
        cache_dir,
        offline=config.offline,
        refresh=config.refresh,
        clock=clock,
    )
    provider = TvmazeProviderAdapter(cache, getter)
    plan = _planner._build_plan(source_root, config, overrides, cache, provider)
    plan = _review._apply_review_extensions(
        plan,
        source_root,
        overrides,
        config,
        provider,
    )
    if isinstance(overrides, ReviewContractCatalog):
        plan = _apply_duplicate_group_contract(plan, overrides)
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
        run_provenance_json=_run_provenance_bytes(run_provenance, overrides),
    )
    return PlanningOutcome(
        plan=plan,
        preflight=preflight,
        bundle=bundle,
        provider_failure=provider_failure,
    )
