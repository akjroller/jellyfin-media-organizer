"""Duplicate handling during planning.

Two parts: recovering exact-set duplicate claims after the strict episode
overlap guard fires, and classifying every destination collision into a
winner, losers, or a suspicious group. Nothing here moves or deletes media.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from typing import cast

from .duplicate_classifier import (
    DuplicateCandidate,
    DuplicateDisposition,
    DuplicatePreference,
    classify_duplicate_candidates,
)
from .episode_assignment import (
    AssignmentStatus,
    SourceEpisodeAssignment,
    SourceEpisodeInput,
    assign_episode_group_with_provider,
)
from .extra_naming import derive_extra_display_identity
from .models import CanonicalShow, PlanRecord, TerminalStatus
from .overrides import OverrideCatalog
from .plan_common import PlanningConfigurationError, path_key
from .providers import MetadataProvider
from .sidecars import SidecarDiscovery

_DUPLICATE_PROVIDER_EPISODE_PREFIX = "duplicate-provider-episode-assignment:"


_PROVIDER_EPISODE_DUPLICATE_CANDIDATE = "provider-episode-duplicate-candidate"


def _duplicate_provider_claims(
    assignment: SourceEpisodeAssignment,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            reason.removeprefix(_DUPLICATE_PROVIDER_EPISODE_PREFIX)
            for reason in assignment.evidence.reasons
            if reason.startswith(_DUPLICATE_PROVIDER_EPISODE_PREFIX)
        )
    )


def _duplicate_claim_components(
    claims_by_source: Mapping[str, tuple[str, ...]],
) -> tuple[tuple[str, ...], ...]:
    remaining = set(claims_by_source)
    components: list[tuple[str, ...]] = []
    while remaining:
        seed = min(remaining, key=path_key)
        component = {seed}
        frontier = [seed]
        remaining.remove(seed)
        while frontier:
            current = frontier.pop()
            current_claims = set(claims_by_source[current])
            connected = sorted(
                (
                    source
                    for source in remaining
                    if current_claims.intersection(claims_by_source[source])
                ),
                key=path_key,
            )
            for source in connected:
                remaining.remove(source)
                component.add(source)
                frontier.append(source)
        components.append(tuple(sorted(component, key=path_key)))
    return tuple(components)


def recover_equivalent_provider_episode_duplicates(
    show: CanonicalShow,
    sources: tuple[SourceEpisodeInput, ...],
    assignments: Mapping[str, SourceEpisodeAssignment],
    provider: MetadataProvider,
) -> dict[str, SourceEpisodeAssignment]:
    """Recover only exact-set duplicate claims after the strict overlap guard fires.

    The normal group assignment remains fail-closed. Sources are re-evaluated one at
    a time solely to recover the provider episodes that the strict duplicate guard
    deliberately removed. A connected overlap component is promoted only when every
    member independently matches and every member has the exact same complete
    provider-episode identity set. Partial overlaps therefore remain suspicious.
    """

    claims_by_source = {
        source.source_key: claims
        for source in sources
        if (assignment := assignments.get(source.source_key)) is not None
        and assignment.status is AssignmentStatus.SUSPICIOUS
        and (claims := _duplicate_provider_claims(assignment))
    }
    if len(claims_by_source) < 2:
        return {}

    source_by_key = {source.source_key: source for source in sources}
    recovered: dict[str, SourceEpisodeAssignment] = {}
    for component in _duplicate_claim_components(claims_by_source):
        if len(component) < 2:
            continue

        individual: dict[str, SourceEpisodeAssignment] = {}
        complete_sets: set[tuple[str, ...]] = set()
        valid = True
        for source_key in component:
            result = assign_episode_group_with_provider(
                show,
                (source_by_key[source_key],),
                provider,
            )
            if len(result.assignments) != 1:
                valid = False
                break
            assignment = result.assignments[0]
            if (
                assignment.status is not AssignmentStatus.MATCHED
                or not assignment.episodes
            ):
                valid = False
                break
            complete_set = tuple(
                sorted(episode.identity.key for episode in assignment.episodes)
            )
            if len(complete_set) != len(set(complete_set)):
                valid = False
                break
            individual[source_key] = assignment
            complete_sets.add(complete_set)

        if not valid or len(individual) != len(component) or len(complete_sets) != 1:
            continue

        episode_set = next(iter(complete_sets))
        set_reason = "provider-episode-duplicate-set:" + ",".join(episode_set)
        for source_key in component:
            assignment = individual[source_key]
            original = assignments[source_key]
            duplicate_reasons = tuple(
                reason
                for reason in original.evidence.reasons
                if reason.startswith(_DUPLICATE_PROVIDER_EPISODE_PREFIX)
            )
            recovered[source_key] = replace(
                assignment,
                evidence=replace(
                    assignment.evidence,
                    reasons=(
                        *assignment.evidence.reasons,
                        *duplicate_reasons,
                        _PROVIDER_EPISODE_DUPLICATE_CANDIDATE,
                        set_reason,
                    ),
                ),
            )
    return recovered


def logical_identity(record: PlanRecord) -> str:
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


def _provider_episode_duplicate_collision_key(record: PlanRecord) -> str | None:
    # Every resolved provider episode is a potential logical destination
    # collision, regardless of whether the resolver happened to annotate the
    # source with the duplicate-candidate evidence marker.  The marker is an
    # optimization used by the resolver for ambiguous assignment families;
    # relying on it here allowed two independently resolved releases (for
    # example an MKV and an MP4 of the same episode) to pass apply and only be
    # discovered as a collision on the next scan after they were managed.
    # Grouping all resolved episode sets is fail-closed: singleton groups do
    # not produce a duplicate result, while repeated provider coordinates are
    # forced through the normal reviewed duplicate decision path.
    if record.show is None or not record.provider_episodes:
        return None
    episode_ids = tuple(
        sorted(episode.provider_identity.key for episode in record.provider_episodes)
    )
    return (
        "provider-episode-collision:"
        f"{record.show.provider_identity.key}:" + ",".join(episode_ids)
    )


def apply_duplicate_decisions(
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
        path_key(record.source.relative_path)[0] for record in candidate_records
    }
    record_keys = {path_key(record.source.relative_path)[0] for record in records}
    configured_keys = {
        path_key(preference.source)[0] for preference in overrides.duplicate_preferences
    }
    # Reviewed duplicate-group winners are historical inputs. Once a winner
    # has been moved into the managed layout, its original source path is no
    # longer present on a same-root re-scan. Keep legacy/manual references
    # fail-closed, but allow these session-bound, already-consumed decisions to
    # become inactive instead of blocking every subsequent paper run.
    stale_reviewed_keys = {
        path_key(preference.source)[0]
        for preference in overrides.duplicate_preferences
        if any(
            reason.startswith("reviewed-duplicate-ref:")
            for reason in preference.reasons
        )
        and path_key(preference.source)[0] not in record_keys
    }
    if configured_keys - record_keys - stale_reviewed_keys:
        raise PlanningConfigurationError(
            "duplicate preference references an unknown source"
        )
    movable_configured_keys = configured_keys & candidate_keys

    provider_collision_counts: dict[str, int] = defaultdict(int)
    provider_collision_keys: dict[str, str] = {}
    for record in candidate_records:
        collision_key = _provider_episode_duplicate_collision_key(record)
        if collision_key is not None:
            source_key = path_key(record.source.relative_path)[0]
            provider_collision_counts[collision_key] += 1
            provider_collision_keys[source_key] = collision_key

    provider_duplicate_keys: set[str] = set()
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
            else (
                DuplicatePreference(
                    rank=2_000_000,
                    reasons=(
                        "already-managed destination is the canonical duplicate winner",
                    ),
                )
                if path_key(record.source.relative_path)[0]
                == path_key(record.destination or "")[0]
                else None
            )
        )
        source_key = path_key(record.source.relative_path)[0]
        collision_key = provider_collision_keys.get(source_key)
        if collision_key is not None and provider_collision_counts[collision_key] > 1:
            provider_duplicate_keys.add(source_key)
        else:
            collision_key = None
        candidates.append(
            DuplicateCandidate(
                operation_key=record.source.relative_path,
                members=(
                    record.source.relative_path,
                    *sorted(
                        companions_by_video.get(record.source.relative_path, ()),
                        key=path_key,
                    ),
                ),
                destination=collision_key or cast(str, record.destination),
                logical_identity=logical_identity(record),
                fingerprint=record.source.fingerprint,
                preference=preference,
            )
        )

    results = classify_duplicate_candidates(candidates)
    collision_keys = {
        path_key(candidate.operation_key)[0]
        for result in results
        for candidate in result.candidates
    }
    if provider_duplicate_keys - collision_keys:
        raise PlanningConfigurationError(
            "provider-episode duplicate candidate did not form a complete collision"
        )
    if movable_configured_keys - collision_keys:
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
