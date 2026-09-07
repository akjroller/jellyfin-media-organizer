from __future__ import annotations

import copy
import json
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO, cast

from . import review_wizard as _wizard
from .providers import MetadataProvider
from .review_contract import DuplicateGroupAction, compile_active_overrides
from .review_identity import normalize_review_path, stable_duplicate_ref
from .review_session import (
    ReviewCollisionClass,
    ReviewItemKind,
    ReviewItemState,
    ReviewSession,
    atomic_replace,
    atomic_write_new,
    build_review_session,
    load_review_session,
    manifest_sha256,
    render_review_session,
)
from .schema import validate_manifest

InputFn = Callable[[str], str]
ReviewConfigurationError = _wizard.ReviewConfigurationError
ANSWERS_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class DuplicateReviewGroup:
    destination_key: str
    candidates: tuple[str, ...]
    recommended_winner: str | None
    losers: tuple[str, ...]
    evidence: tuple[str, ...]
    collision_class: ReviewCollisionClass

    @property
    def manual_selection_allowed(self) -> bool:
        return self.collision_class is ReviewCollisionClass.SAME_LOGICAL_IDENTITY


@dataclass(frozen=True, slots=True)
class ReviewAnswer:
    review_ref: str
    action: str
    expected_identity_sha256: str
    winner: str | None = None
    responses: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReviewAnswerBundle:
    plan_sha256: str
    base_override_snapshot: str
    session_sha256: str
    answers: tuple[ReviewAnswer, ...]

    def by_ref(self) -> dict[str, ReviewAnswer]:
        return {answer.review_ref: answer for answer in self.answers}


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ReviewConfigurationError(f"{label} must be a SHA-256 string")
    digest = value.casefold()
    if len(digest) != 64:
        raise ReviewConfigurationError(f"{label} must contain 64 hex characters")
    try:
        int(digest, 16)
    except ValueError as exc:
        raise ReviewConfigurationError(
            f"{label} must contain 64 hex characters"
        ) from exc
    return digest


def load_review_answers(payload: bytes) -> ReviewAnswerBundle:
    """Load stable-ref keyed non-interactive review answers."""

    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewConfigurationError("invalid review answers JSON") from exc
    if not isinstance(raw, dict):
        raise ReviewConfigurationError("answers root must be an object")
    expected = {
        "schema_version",
        "plan_sha256",
        "base_override_snapshot",
        "session_sha256",
        "answers",
    }
    if set(raw) != expected or raw.get("schema_version") != ANSWERS_SCHEMA_VERSION:
        raise ReviewConfigurationError("answers file has an unsupported contract")
    raw_answers = raw.get("answers")
    if not isinstance(raw_answers, list):
        raise ReviewConfigurationError("answers must be an array")

    answers: list[ReviewAnswer] = []
    refs: set[str] = set()
    for entry in raw_answers:
        if not isinstance(entry, dict):
            raise ReviewConfigurationError("answer entries must be objects")
        allowed = {
            "review_ref",
            "action",
            "expected_identity_sha256",
            "winner",
            "responses",
        }
        unknown = set(entry) - allowed
        if unknown:
            raise ReviewConfigurationError(
                f"answer contains unsupported fields: {sorted(unknown)}"
            )
        review_ref = entry.get("review_ref")
        action = entry.get("action")
        expected_identity = entry.get("expected_identity_sha256")
        winner = entry.get("winner")
        responses = entry.get("responses", [])
        if not isinstance(review_ref, str) or not review_ref:
            raise ReviewConfigurationError("answer review_ref must be a string")
        if review_ref in refs:
            raise ReviewConfigurationError("answers cannot repeat one review_ref")
        refs.add(review_ref)
        if not isinstance(action, str) or not action:
            raise ReviewConfigurationError("answer action must be a string")
        if winner is not None and not isinstance(winner, str):
            raise ReviewConfigurationError("answer winner must be a string")
        if not isinstance(responses, list) or not all(
            isinstance(response, str) for response in responses
        ):
            raise ReviewConfigurationError("answer responses must be strings")
        answers.append(
            ReviewAnswer(
                review_ref=review_ref,
                action=action.casefold(),
                expected_identity_sha256=_sha256(
                    expected_identity,
                    "answer expected_identity_sha256",
                ),
                winner=winner,
                responses=tuple(responses),
            )
        )

    return ReviewAnswerBundle(
        plan_sha256=_sha256(raw.get("plan_sha256"), "answers plan_sha256"),
        base_override_snapshot=_sha256(
            raw.get("base_override_snapshot"),
            "answers base_override_snapshot",
        ),
        session_sha256=_sha256(
            raw.get("session_sha256"),
            "answers session_sha256",
        ),
        answers=tuple(answers),
    )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ReviewConfigurationError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewConfigurationError(f"{label} must be a non-empty string")
    return value


def _record_source(record: Mapping[str, object]) -> str:
    source = _mapping(record.get("source"), "record.source")
    return _string(source.get("relative_path"), "record.source.relative_path")


def _record_show_key(record: Mapping[str, object]) -> str:
    show = record.get("show")
    if isinstance(show, Mapping):
        source_key = show.get("source_key")
        if isinstance(source_key, str) and source_key:
            return source_key
    parse = record.get("parse")
    if isinstance(parse, Mapping):
        series_hint = parse.get("series_hint")
        if isinstance(series_hint, str) and series_hint:
            return series_hint
    return _record_source(record).replace("\\", "/").split("/", 1)[0]


def _record_collision_class(
    record: Mapping[str, object],
) -> ReviewCollisionClass:
    status = record.get("status")
    if status == "duplicate":
        return ReviewCollisionClass.SAME_LOGICAL_IDENTITY
    if status == "suspicious":
        return ReviewCollisionClass.DESTINATION_CONFLICT
    raise ReviewConfigurationError(
        "duplicate review data must belong to a duplicate or suspicious record"
    )


def _collect_duplicate_groups(manifest: object) -> tuple[DuplicateReviewGroup, ...]:
    validate_manifest(manifest)
    root = cast(Mapping[str, object], manifest)
    records = root.get("records")
    assert isinstance(records, list | tuple)
    groups: dict[str, DuplicateReviewGroup] = {}
    for raw_record in records:
        record = cast(Mapping[str, object], raw_record)
        duplicate = record.get("duplicate")
        if not isinstance(duplicate, Mapping):
            continue
        destination_key = _string(
            duplicate.get("destination_key"),
            "duplicate.destination_key",
        )
        candidates_raw = duplicate.get("candidates")
        losers_raw = duplicate.get("losers")
        evidence_raw = duplicate.get("evidence")
        winner = duplicate.get("winner")
        if not isinstance(candidates_raw, list | tuple) or not all(
            isinstance(candidate, str) and candidate for candidate in candidates_raw
        ):
            raise ReviewConfigurationError("duplicate candidates are invalid")
        if not isinstance(losers_raw, list | tuple) or not all(
            isinstance(loser, str) and loser for loser in losers_raw
        ):
            raise ReviewConfigurationError("duplicate losers are invalid")
        if not isinstance(evidence_raw, list | tuple) or not all(
            isinstance(reason, str) and reason for reason in evidence_raw
        ):
            raise ReviewConfigurationError("duplicate evidence is invalid")
        if winner is not None and not isinstance(winner, str):
            raise ReviewConfigurationError("duplicate winner is invalid")
        group = DuplicateReviewGroup(
            destination_key=destination_key,
            candidates=tuple(cast(Sequence[str], candidates_raw)),
            recommended_winner=cast(str | None, winner),
            losers=tuple(cast(Sequence[str], losers_raw)),
            evidence=tuple(cast(Sequence[str], evidence_raw)),
            collision_class=_record_collision_class(record),
        )
        ref = stable_duplicate_ref(destination_key, group.candidates)
        existing = groups.get(ref)
        if existing is not None and existing != group:
            raise ReviewConfigurationError(
                "plan contains inconsistent duplicate decisions for one review group"
            )
        groups[ref] = group
    return tuple(groups[ref] for ref in sorted(groups))


def _collect_held_records(manifest: object) -> tuple[Mapping[str, object], ...]:
    validate_manifest(manifest)
    root = cast(Mapping[str, object], manifest)
    records = root.get("records")
    assert isinstance(records, list | tuple)
    held = [
        cast(Mapping[str, object], record)
        for record in records
        if cast(Mapping[str, object], record).get("status") == "held"
    ]
    return tuple(
        sorted(
            held,
            key=lambda record: (
                normalize_review_path(_record_source(record)),
                _record_source(record),
            ),
        )
    )


def _record_context(
    manifest: object,
) -> tuple[
    dict[str, Mapping[str, object]],
    dict[str, tuple[Mapping[str, object], ...]],
]:
    root = cast(Mapping[str, object], manifest)
    records_raw = root.get("records")
    companions_raw = root.get("companions")
    assert isinstance(records_raw, list | tuple)
    assert isinstance(companions_raw, list | tuple)
    records = {
        normalize_review_path(_record_source(cast(Mapping[str, object], record))): cast(
            Mapping[str, object], record
        )
        for record in records_raw
    }
    companions: dict[str, list[Mapping[str, object]]] = {}
    for raw in companions_raw:
        companion = cast(Mapping[str, object], raw)
        source_video = companion.get("source_video")
        if isinstance(source_video, str) and source_video:
            companions.setdefault(normalize_review_path(source_video), []).append(
                companion
            )
    return records, {
        key: tuple(
            sorted(
                values,
                key=lambda value: normalize_review_path(
                    cast(str, value.get("relative_path", ""))
                ),
            )
        )
        for key, values in companions.items()
    }


def _parse_scratch(payload: bytes) -> dict[str, Any]:
    try:
        raw = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ReviewConfigurationError("invalid base override TOML") from exc
    if not isinstance(raw, dict):
        raise ReviewConfigurationError("base override root must be a table")
    for table in (
        "shows",
        "duplicate_preferences",
        "episode_decisions",
        "source_holds",
        "reviewed_episode_decisions",
        "extra_decisions",
    ):
        raw.setdefault(table, [])
    return cast(dict[str, Any], raw)


def _source_key(value: str) -> str:
    return value.replace("\\", "/").casefold()


def _capture_held_delta(raw: dict[str, Any], source: str) -> dict[str, object]:
    data: dict[str, object] = {}
    for table, key in (
        ("reviewed_episode_decisions", "reviewed_episode"),
        ("extra_decisions", "extra"),
    ):
        values = cast(list[dict[str, Any]], raw.get(table, []))
        match = next(
            (
                entry
                for entry in reversed(values)
                if isinstance(entry.get("source"), str)
                and _source_key(cast(str, entry["source"])) == _source_key(source)
            ),
            None,
        )
        if match is not None:
            captured = copy.deepcopy(match)
            captured.pop("source_binding_sha256", None)
            data[key] = captured
    disposition = data.get("reviewed_episode") or data.get("extra")
    if isinstance(disposition, Mapping):
        provider = disposition.get("show_provider")
        provider_id = disposition.get("show_provider_id")
        for entry in cast(list[dict[str, Any]], raw.get("shows", [])):
            if entry.get("provider") == provider and str(
                entry.get("provider_id")
            ) == str(provider_id):
                data["show"] = copy.deepcopy(entry)
                break
    return data


def _filter_items(
    session: ReviewSession,
    *,
    show: str | None,
    kind: str | None,
    review_ref: str | None,
    pending_only: bool,
) -> tuple[str, ...]:
    selected = []
    for item in session.items:
        if show is not None and item.show_key.casefold() != show.casefold():
            continue
        if kind is not None and item.kind.value != kind:
            continue
        if review_ref is not None and item.review_ref != review_ref:
            continue
        if pending_only and item.state is not ReviewItemState.PENDING:
            continue
        selected.append(item.review_ref)
    return tuple(selected)


def _fingerprint_summary(record: Mapping[str, object]) -> tuple[int, str | None]:
    source = _mapping(record.get("source"), "record.source")
    fingerprint = _mapping(source.get("fingerprint"), "record.source.fingerprint")
    size = fingerprint.get("size")
    digest = fingerprint.get("sha256")
    if isinstance(size, bool) or not isinstance(size, int):
        raise ReviewConfigurationError("record source size is invalid")
    if digest is not None and not isinstance(digest, str):
        raise ReviewConfigurationError("record source digest is invalid")
    return size, cast(str | None, digest)


def _display_duplicate(
    group: DuplicateReviewGroup,
    records: Mapping[str, Mapping[str, object]],
    companions: Mapping[str, tuple[Mapping[str, object], ...]],
    output: TextIO,
) -> None:
    ref = stable_duplicate_ref(group.destination_key, group.candidates)
    output.write(f"\nDuplicate review {ref}\n")
    output.write(f"Collision class: {group.collision_class.value}\n")
    output.write(f"Planned destination: {group.destination_key}\n")
    output.write("Group evidence:\n")
    for reason in group.evidence:
        output.write(f"  - {reason}\n")
    output.write("Candidates:\n")
    for index, candidate in enumerate(group.candidates, start=1):
        record = records.get(normalize_review_path(candidate))
        if record is None:
            raise ReviewConfigurationError("duplicate candidate record disappeared")
        marker = " [recommended]" if candidate == group.recommended_winner else ""
        size, digest = _fingerprint_summary(record)
        digest_text = digest if digest is not None else "not captured"
        output.write(f"  [C{index}] {candidate}{marker}\n")
        output.write(f"       size: {size} bytes; sha256: {digest_text}\n")
        destination = record.get("destination")
        if isinstance(destination, str):
            output.write(f"       destination: {destination}\n")
        evidence = record.get("evidence")
        if isinstance(evidence, Mapping):
            method = evidence.get("method")
            confidence = evidence.get("confidence")
            if method is not None:
                output.write(f"       resolution: {method}; confidence: {confidence}\n")
            reasons = evidence.get("reasons")
            if isinstance(reasons, list | tuple):
                for reason in reasons:
                    if isinstance(reason, str):
                        output.write(f"         evidence: {reason}\n")
        members = companions.get(normalize_review_path(candidate), ())
        if members:
            output.write("       companions:\n")
            for member in members:
                path = member.get("relative_path")
                fingerprint = member.get("fingerprint")
                size_text = "unknown size"
                if isinstance(fingerprint, Mapping):
                    member_size = fingerprint.get("size")
                    if isinstance(member_size, int) and not isinstance(
                        member_size, bool
                    ):
                        size_text = f"{member_size} bytes"
                output.write(f"         - {path} ({size_text})\n")
        else:
            output.write("       companions: none\n")


def _scoped_input(responses: tuple[str, ...]) -> tuple[InputFn, Callable[[], None]]:
    iterator = iter(responses)
    used = 0

    def read(_prompt: str) -> str:
        nonlocal used
        try:
            value = next(iterator)
        except StopIteration as exc:
            raise ReviewConfigurationError(
                "item-bound answer ran out of provider prompt responses"
            ) from exc
        used += 1
        return value

    def finish() -> None:
        if used != len(responses):
            raise ReviewConfigurationError(
                "item-bound answer contains unused provider prompt responses"
            )

    return read, finish


def _validate_answer_identity(session: ReviewSession, answer: ReviewAnswer) -> None:
    item = session.item(answer.review_ref)
    if item.identity_sha256 != answer.expected_identity_sha256:
        raise ReviewConfigurationError(
            f"answer identity changed for review item {answer.review_ref}"
        )


def _answer_duplicate(
    session: ReviewSession,
    group: DuplicateReviewGroup,
    records: Mapping[str, Mapping[str, object]],
    companions: Mapping[str, tuple[Mapping[str, object], ...]],
    *,
    answer: ReviewAnswer | None,
    input_fn: InputFn,
    output: TextIO,
) -> ReviewSession:
    ref = stable_duplicate_ref(group.destination_key, group.candidates)
    session_item = session.item(ref)
    if session_item.collision_class is not group.collision_class:
        raise ReviewConfigurationError("duplicate collision class changed within the plan")
    _display_duplicate(group, records, companions, output)

    if answer is None:
        output.write("Actions:\n")
        if group.recommended_winner is not None:
            output.write("  [A] Accept recommended winner\n")
        if group.manual_selection_allowed:
            output.write("  [S] Select a candidate as the explicit winner\n")
        output.write("  [K] Keep every copy in place\n")
        if group.recommended_winner is not None and group.losers:
            output.write("  [Q] Mark current loser(s) for future quarantine review\n")
        output.write("  [D] Defer\n")
        selected_action = input_fn("Action: ").strip().casefold()
        action = {
            "a": "accept_recommended",
            "s": "select_winner",
            "k": "keep_all",
            "q": "quarantine_candidate",
            "d": "defer",
        }.get(selected_action, "defer")
        winner_override: str | None = None
    else:
        _validate_answer_identity(session, answer)
        action = answer.action
        winner_override = answer.winner
        if answer.responses:
            raise ReviewConfigurationError(
                "duplicate answers cannot contain provider prompt responses"
            )

    if action == "defer":
        return session.with_answer(ref, state=ReviewItemState.DEFERRED, action="defer")
    if action == "accept_recommended":
        if group.recommended_winner is None:
            raise ReviewConfigurationError(
                "accept_recommended requires a displayed recommended winner"
            )
        return session.with_answer(
            ref,
            state=ReviewItemState.ANSWERED,
            action="select_winner",
            data={
                "active_action": DuplicateGroupAction.SELECT_WINNER.value,
                "winner": group.recommended_winner,
            },
        )
    if action == "quarantine_candidate":
        if not group.manual_selection_allowed:
            raise ReviewConfigurationError(
                "quarantine-candidate winner selection is prohibited for destination conflicts"
            )
        if group.recommended_winner is None or not group.losers:
            raise ReviewConfigurationError(
                "quarantine candidate review requires a displayed winner and losers"
            )
        return session.with_answer(
            ref,
            state=ReviewItemState.ANSWERED,
            action="quarantine_candidate",
            data={
                "active_action": DuplicateGroupAction.SELECT_WINNER.value,
                "winner": group.recommended_winner,
                "quarantine_candidates": list(group.losers),
            },
        )
    if action == "keep_all":
        return session.with_answer(
            ref,
            state=ReviewItemState.ANSWERED,
            action="keep_all",
            data={"active_action": DuplicateGroupAction.KEEP_ALL.value},
        )
    if action == "select_winner":
        if not group.manual_selection_allowed:
            raise ReviewConfigurationError(
                "manual winner selection is prohibited for destination conflicts"
            )
        selected_winner: str
        if answer is not None:
            if winner_override is None:
                raise ReviewConfigurationError(
                    "select_winner answer requires an explicit winner source"
                )
            matches = [
                candidate
                for candidate in group.candidates
                if normalize_review_path(candidate)
                == normalize_review_path(winner_override)
            ]
            if len(matches) != 1:
                raise ReviewConfigurationError(
                    "selected duplicate winner is not one current candidate"
                )
            selected_winner = matches[0]
        else:
            selected = input_fn("Candidate label (for example C2): ").strip().casefold()
            if selected.startswith("c"):
                selected = selected[1:]
            try:
                index = int(selected)
            except ValueError as exc:
                raise ReviewConfigurationError(
                    "invalid duplicate candidate label"
                ) from exc
            if not 1 <= index <= len(group.candidates):
                raise ReviewConfigurationError(
                    "duplicate candidate label is out of range"
                )
            selected_winner = group.candidates[index - 1]
        return session.with_answer(
            ref,
            state=ReviewItemState.ANSWERED,
            action="select_winner",
            data={
                "active_action": DuplicateGroupAction.SELECT_WINNER.value,
                "winner": selected_winner,
            },
        )
    raise ReviewConfigurationError(f"unsupported duplicate review action: {action}")


def _no_op() -> None:
    return None


def _answer_held(
    session: ReviewSession,
    record: Mapping[str, object],
    scratch: dict[str, Any],
    provider: MetadataProvider,
    *,
    answer: ReviewAnswer | None,
    input_fn: InputFn,
    output: TextIO,
) -> ReviewSession:
    source = _record_source(record)
    item = next(
        item
        for item in session.items
        if item.kind is ReviewItemKind.HELD
        and item.source is not None
        and normalize_review_path(item.source) == normalize_review_path(source)
    )
    ref = item.review_ref
    output.write(f"\nHeld review {ref}\nSource: {source}\n")
    output.write(f"Reviewed source/member identity: {item.identity_sha256}\n")

    if answer is None:
        output.write(
            "Actions:\n"
            "  [H] Continue leaving untouched\n"
            "  [E] Identify as specific episode\n"
            "  [S] Identify as provider-confirmed special\n"
            "  [X] Classify as explicit extra\n"
            "  [D] Defer\n"
        )
        selected = input_fn("Action: ").strip().casefold()
        action = {
            "h": "keep_held",
            "e": "episode",
            "s": "special",
            "x": "extra",
            "d": "defer",
        }.get(selected, "defer")
        provider_input = input_fn
        finish = _no_op
    else:
        _validate_answer_identity(session, answer)
        action = answer.action
        if answer.winner is not None:
            raise ReviewConfigurationError("held answers cannot contain a winner")
        provider_input, finish = _scoped_input(answer.responses)

    if action == "keep_held":
        finish()
        return session.with_answer(
            ref,
            state=ReviewItemState.ANSWERED,
            action="keep_held",
        )
    if action == "defer":
        finish()
        return session.with_answer(ref, state=ReviewItemState.DEFERRED, action="defer")

    working = copy.deepcopy(scratch)
    if action == "episode":
        changed = _wizard._review_specific_episode(
            working,
            record,
            provider,
            input_fn=provider_input,
            output=output,
        )
    elif action == "special":
        changed = _wizard._review_special(
            working,
            record,
            provider,
            input_fn=provider_input,
            output=output,
        )
    elif action == "extra":
        changed = _wizard._review_extra(
            working,
            record,
            provider,
            input_fn=provider_input,
            output=output,
        )
    else:
        raise ReviewConfigurationError(f"unsupported held review action: {action}")
    finish()
    if not changed:
        return session.with_answer(ref, state=ReviewItemState.DEFERRED, action="defer")
    data = _capture_held_delta(working, source)
    if not data:
        raise ReviewConfigurationError(
            "reviewed held decision produced no active state"
        )
    scratch.clear()
    scratch.update(working)
    return session.with_answer(
        ref,
        state=ReviewItemState.ANSWERED,
        action=action,
        data=data,
    )


def run_review_system(
    manifest: object,
    base_override_payload: bytes,
    *,
    base_override_snapshot: str,
    provider: MetadataProvider,
    session_path: Path,
    output_override_path: Path,
    resume: bool,
    input_fn: InputFn,
    output: TextIO,
    show_filter: str | None = None,
    kind_filter: str | None = None,
    ref_filter: str | None = None,
    pending_only: bool = False,
    batch_accept_recommended: bool = False,
    approve_partial: bool = False,
    answers: ReviewAnswerBundle | None = None,
) -> tuple[ReviewSession, bytes]:
    """Run or resume one crash-safe, session-bound, non-mutating review."""

    validate_manifest(manifest)
    expected_plan = manifest_sha256(manifest)
    if resume:
        session = load_review_session(session_path.read_bytes())
        if session.plan_sha256 != expected_plan:
            raise ReviewConfigurationError(
                "resume plan hash does not match the session"
            )
        if session.base_override_snapshot != base_override_snapshot:
            raise ReviewConfigurationError(
                "resume base override snapshot does not match the session"
            )
    else:
        session = build_review_session(
            manifest,
            base_override_snapshot=base_override_snapshot,
            base_override_payload=base_override_payload,
        )
        atomic_write_new(session_path, render_review_session(session))

    starting_session_sha = session.sha256
    answer_by_ref: dict[str, ReviewAnswer] = {}
    if answers is not None:
        if answers.plan_sha256 != session.plan_sha256:
            raise ReviewConfigurationError("answers are not bound to this exact plan")
        if answers.base_override_snapshot != session.base_override_snapshot:
            raise ReviewConfigurationError(
                "answers are not bound to this base override snapshot"
            )
        if answers.session_sha256 != starting_session_sha:
            raise ReviewConfigurationError(
                "answers are not bound to this exact starting review session"
            )
        answer_by_ref = answers.by_ref()

    held_by_source = {
        normalize_review_path(_record_source(record)): record
        for record in _collect_held_records(manifest)
    }
    duplicate_by_ref = {
        stable_duplicate_ref(group.destination_key, group.candidates): group
        for group in _collect_duplicate_groups(manifest)
    }
    records, companions = _record_context(manifest)
    selected = _filter_items(
        session,
        show=show_filter,
        kind=kind_filter,
        review_ref=ref_filter,
        pending_only=pending_only,
    )
    selected_scope = selected
    if answers is not None:
        unexpected = set(answer_by_ref) - set(selected)
        if unexpected:
            raise ReviewConfigurationError(
                "answers contain review refs outside the selected review scope"
            )
        missing = {
            ref
            for ref in selected
            if session.item(ref).state is not ReviewItemState.ANSWERED
        } - set(answer_by_ref)
        if missing:
            raise ReviewConfigurationError(
                "answers do not cover every unresolved item in the selected scope"
            )

    output.write(
        f"Review session {starting_session_sha[:12]}: {len(selected)} selected items. "
        "No delete or quarantine operation exists.\n"
    )
    scratch = _parse_scratch(session.base_override_toml.encode("utf-8"))

    if batch_accept_recommended:
        if answers is not None:
            raise ReviewConfigurationError(
                "--batch-accept-recommended cannot be combined with --answers"
            )
        duplicate_refs = [
            ref
            for ref in selected
            if session.item(ref).kind is ReviewItemKind.DUPLICATE
            and session.item(ref).state is not ReviewItemState.ANSWERED
        ]
        if duplicate_refs:
            if any(
                duplicate_by_ref[ref].recommended_winner is None
                for ref in duplicate_refs
            ):
                raise ReviewConfigurationError(
                    "batch recommended acceptance requires a winner for every selected group"
                )
            output.write("Batch recommended winners:\n")
            for ref in duplicate_refs:
                batch_group = duplicate_by_ref[ref]
                output.write(
                    f"  {ref}: {batch_group.recommended_winner} -> "
                    f"{batch_group.destination_key}\n"
                )
            if input_fn(
                "Accept every displayed recommended winner? [y/N]: "
            ).strip().casefold() in {"y", "yes"}:
                accepted = set(duplicate_refs)
                for ref in duplicate_refs:
                    batch_group = duplicate_by_ref[ref]
                    assert batch_group.recommended_winner is not None
                    session = session.with_answer(
                        ref,
                        state=ReviewItemState.ANSWERED,
                        action="select_winner",
                        data={
                            "active_action": DuplicateGroupAction.SELECT_WINNER.value,
                            "winner": batch_group.recommended_winner,
                        },
                    )
                    atomic_replace(session_path, render_review_session(session))
                selected = tuple(ref for ref in selected if ref not in accepted)

    for ref in selected:
        item = session.item(ref)
        if item.state is ReviewItemState.ANSWERED:
            continue
        answer = answer_by_ref.get(ref)
        if item.kind is ReviewItemKind.DUPLICATE:
            current_group = duplicate_by_ref.get(ref)
            if current_group is None:
                raise ReviewConfigurationError("duplicate review group disappeared")
            session = _answer_duplicate(
                session,
                current_group,
                records,
                companions,
                answer=answer,
                input_fn=input_fn,
                output=output,
            )
        else:
            assert item.source is not None
            record = held_by_source.get(normalize_review_path(item.source))
            if record is None:
                raise ReviewConfigurationError(
                    "held review source disappeared from plan"
                )
            session = _answer_held(
                session,
                record,
                scratch,
                provider,
                answer=answer,
                input_fn=input_fn,
                output=output,
            )
        atomic_replace(session_path, render_review_session(session))

    if approve_partial and not session.complete:
        if show_filter is None and kind_filter is None and ref_filter is None:
            raise ReviewConfigurationError(
                "partial approval requires an explicit --show, --kind, or --ref scope"
            )
        if not selected_scope:
            raise ReviewConfigurationError(
                "partial approval scope selected no review items"
            )
        try:
            session = session.with_approved_scope(selected_scope)
        except ValueError as exc:
            raise ReviewConfigurationError(str(exc)) from exc
        atomic_replace(session_path, render_review_session(session))

    active = compile_active_overrides(session)
    atomic_write_new(output_override_path, active)
    return session, active
