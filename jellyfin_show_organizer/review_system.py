from __future__ import annotations

import copy
import json
import tomllib
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any, TextIO, cast

from .providers import MetadataProvider
from .review_contract import DuplicateGroupAction
from .review_identity import stable_duplicate_ref
from .review_session import (
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
from .review_wizard import (
    DuplicateReviewGroup,
    InputFn,
    ReviewConfigurationError,
    _record_source,
    _review_extra,
    _review_special,
    _review_specific_episode,
    collect_duplicate_groups,
    collect_held_records,
    parse_overrides,
)
from .schema import validate_manifest


_TABLE_ORDER = (
    "shows",
    "duplicate_preferences",
    "duplicate_group_decisions",
    "episode_decisions",
    "source_holds",
    "reviewed_episode_decisions",
    "extra_decisions",
)

_FIELD_ORDER: dict[str, tuple[str, ...]] = {
    "shows": (
        "key",
        "tvmaze_id",
        "provider",
        "provider_id",
        "aliases",
        "year",
        "numbering_mode",
        "title_preference",
        "preferred_title",
        "tmdb_id",
        "tvdb_id",
        "imdb_id",
    ),
    "duplicate_preferences": ("source", "rank", "reasons"),
    "duplicate_group_decisions": (
        "duplicate_ref",
        "candidate_set_sha256",
        "candidates",
        "action",
        "winner",
        "reasons",
    ),
    "episode_decisions": (
        "source",
        "show_provider",
        "show_provider_id",
        "numbering_mode",
        "season",
        "episodes",
        "absolute_episode",
        "special_kind",
        "special_episode",
        "episode_date",
        "segment_hint",
        "title_hint",
        "reasons",
    ),
    "source_holds": ("source", "reasons"),
    "reviewed_episode_decisions": (
        "source",
        "show_provider",
        "show_provider_id",
        "episode_provider",
        "episode_provider_id",
        "season",
        "number",
        "title",
        "airdate",
        "lookup_mode",
        "reasons",
    ),
    "extra_decisions": (
        "source",
        "show_provider",
        "show_provider_id",
        "kind",
        "display_title",
        "reasons",
    ),
}


def _toml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise ReviewConfigurationError("unsupported active override value")


def _parse_active(payload: bytes) -> dict[str, Any]:
    try:
        raw = tomllib.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ReviewConfigurationError("invalid active override TOML") from exc
    if raw.get("schema_version") not in {4, 5}:
        raise ReviewConfigurationError("review requires schema-4 or schema-5 overrides")
    groups = raw.pop("duplicate_group_decisions", [])
    session_hash = raw.pop("review_session_sha256", None)
    legacy = parse_overrides(
        ("\n".join(_render_legacy_lines(raw)) + "\n").encode("utf-8")
    )
    legacy["duplicate_group_decisions"] = groups
    legacy["review_session_sha256"] = session_hash
    for table in _TABLE_ORDER:
        legacy.setdefault(table, [])
    return legacy


def _render_legacy_lines(raw: Mapping[str, object]) -> list[str]:
    lines = [f"schema_version = {raw.get('schema_version', 4)}"]
    legacy_tables = tuple(
        table for table in _TABLE_ORDER if table != "duplicate_group_decisions"
    )
    for table in legacy_tables:
        values = raw.get(table, [])
        if not isinstance(values, list):
            raise ReviewConfigurationError(f"override {table} must be an array")
        order = _FIELD_ORDER[table]
        for item in values:
            if not isinstance(item, dict):
                raise ReviewConfigurationError(f"override {table} item must be a table")
            lines.extend(("", f"[[{table}]]"))
            for field in order:
                if field in item and item[field] is not None:
                    lines.append(f"{field} = {_toml_value(item[field])}")
    return lines


def render_active_overrides(raw: Mapping[str, object]) -> bytes:
    lines = ["schema_version = 5"]
    session_hash = raw.get("review_session_sha256")
    if session_hash is not None:
        if not isinstance(session_hash, str):
            raise ReviewConfigurationError("review_session_sha256 must be a string")
        lines.append(f"review_session_sha256 = {_toml_value(session_hash)}")
    for table in _TABLE_ORDER:
        values = raw.get(table, [])
        if not isinstance(values, list) or not all(
            isinstance(item, dict) for item in values
        ):
            raise ReviewConfigurationError(
                f"override {table} must be an array of tables"
            )
        order = _FIELD_ORDER[table]
        for item in cast(list[dict[str, object]], values):
            unknown = set(item) - set(order)
            if unknown:
                raise ReviewConfigurationError(
                    f"override {table} contains unsupported fields: {sorted(unknown)}"
                )
            lines.extend(("", f"[[{table}]]"))
            for field in order:
                if field in item and item[field] is not None:
                    lines.append(f"{field} = {_toml_value(item[field])}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _source_key(value: str) -> str:
    return value.replace("\\", "/").casefold()


def _remove_source(raw: dict[str, Any], table: str, source: str) -> None:
    values = cast(list[dict[str, Any]], raw.setdefault(table, []))
    key = _source_key(source)
    values[:] = [
        item
        for item in values
        if not isinstance(item.get("source"), str)
        or _source_key(cast(str, item["source"])) != key
    ]


def _upsert_show(raw: dict[str, Any], show: Mapping[str, object]) -> None:
    key = show.get("key")
    if not isinstance(key, str):
        raise ReviewConfigurationError("reviewed show metadata is missing its key")
    values = cast(list[dict[str, Any]], raw.setdefault("shows", []))
    normalized = key.casefold()
    values[:] = [
        item
        for item in values
        if not isinstance(item.get("key"), str)
        or cast(str, item["key"]).casefold() != normalized
    ]
    values.append(copy.deepcopy(dict(show)))


def _compile_active(base_payload: bytes, session: ReviewSession) -> bytes:
    raw = _parse_active(base_payload)
    for item in session.items:
        if item.state is not ReviewItemState.ANSWERED:
            continue
        data = item.data
        if item.kind is ReviewItemKind.DUPLICATE:
            groups = cast(
                list[dict[str, Any]], raw.setdefault("duplicate_group_decisions", [])
            )
            groups[:] = [
                group
                for group in groups
                if group.get("duplicate_ref") != item.duplicate_ref
            ]
            for candidate in item.candidates:
                _remove_source(raw, "duplicate_preferences", candidate)
            action = data.get("active_action")
            winner = data.get("winner")
            if action not in {
                DuplicateGroupAction.SELECT_WINNER.value,
                DuplicateGroupAction.KEEP_ALL.value,
            }:
                raise ReviewConfigurationError(
                    "answered duplicate item has no active action"
                )
            group: dict[str, object] = {
                "duplicate_ref": item.duplicate_ref,
                "candidate_set_sha256": item.candidate_set_sha256,
                "candidates": list(item.candidates),
                "action": action,
                "reasons": ["human-reviewed duplicate group decision"],
            }
            if winner is not None:
                if not isinstance(winner, str):
                    raise ReviewConfigurationError("reviewed duplicate winner is invalid")
                group["winner"] = winner
            groups.append(group)
            continue

        assert item.source is not None
        action = item.action
        if action == "keep_held":
            continue
        for table in (
            "source_holds",
            "episode_decisions",
            "reviewed_episode_decisions",
            "extra_decisions",
            "duplicate_preferences",
        ):
            _remove_source(raw, table, item.source)
        show = data.get("show")
        if isinstance(show, Mapping):
            _upsert_show(raw, cast(Mapping[str, object], show))
        reviewed_episode = data.get("reviewed_episode")
        if isinstance(reviewed_episode, Mapping):
            cast(list[dict[str, Any]], raw["reviewed_episode_decisions"]).append(
                copy.deepcopy(dict(reviewed_episode))
            )
        extra = data.get("extra")
        if isinstance(extra, Mapping):
            cast(list[dict[str, Any]], raw["extra_decisions"]).append(
                copy.deepcopy(dict(extra))
            )

    raw["schema_version"] = 5
    raw["review_session_sha256"] = session.sha256
    return render_active_overrides(raw)


def _record_maps(
    manifest: object,
) -> tuple[
    dict[str, Mapping[str, object]],
    dict[str, DuplicateReviewGroup],
]:
    validate_manifest(manifest)
    held = {_record_source(record): record for record in collect_held_records(manifest)}
    duplicate = {
        stable_duplicate_ref(group.destination_key, group.candidates): group
        for group in collect_duplicate_groups(manifest)
    }
    return held, duplicate


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
        if pending_only and item.state is ReviewItemState.ANSWERED:
            continue
        selected.append(item.review_ref)
    return tuple(selected)


def _display_name(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).name


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
            data[key] = copy.deepcopy(match)
    decision = data.get("reviewed_episode") or data.get("extra")
    if isinstance(decision, Mapping):
        provider = decision.get("show_provider")
        provider_id = decision.get("show_provider_id")
        for entry in cast(list[dict[str, Any]], raw.get("shows", [])):
            if entry.get("provider") == provider and str(entry.get("provider_id")) == str(
                provider_id
            ):
                data["show"] = copy.deepcopy(entry)
                break
    return data


def _answer_duplicate(
    session: ReviewSession,
    group: DuplicateReviewGroup,
    *,
    input_fn: InputFn,
    output: TextIO,
) -> ReviewSession:
    ref = stable_duplicate_ref(group.destination_key, group.candidates)
    output.write(f"\nDuplicate review {ref}\n")
    for index, candidate in enumerate(group.candidates, start=1):
        marker = " [recommended]" if candidate == group.recommended_winner else ""
        output.write(f"  {index}) {_display_name(candidate)}{marker}\n")
    output.write(
        "  1) Accept recommended winner\n"
        "  2) Select different winner\n"
        "  3) Keep every copy in place\n"
        "  4) Mark current loser(s) as future quarantine candidates\n"
        "  5) Defer\n"
    )
    action = input_fn("> ").strip()
    if action == "5":
        return session.with_answer(
            ref, state=ReviewItemState.DEFERRED, action="defer"
        )
    if action in {"1", "4"}:
        winner = group.recommended_winner
        if winner is None:
            output.write("No safe recommended winner exists; item deferred.\n")
            return session.with_answer(
                ref, state=ReviewItemState.DEFERRED, action="defer"
            )
        data: dict[str, object] = {
            "active_action": DuplicateGroupAction.SELECT_WINNER.value,
            "winner": winner,
        }
        if action == "4":
            data["quarantine_candidates"] = list(group.losers)
            reviewed_action = "quarantine_candidate"
        else:
            reviewed_action = "select_winner"
        return session.with_answer(
            ref,
            state=ReviewItemState.ANSWERED,
            action=reviewed_action,
            data=data,
        )
    if action == "2":
        if group.recommended_winner is None:
            output.write(
                "This collision has no safely classifiable duplicate winner. "
                "Choosing a winner is prohibited; item deferred.\n"
            )
            return session.with_answer(
                ref, state=ReviewItemState.DEFERRED, action="defer"
            )
        selected = input_fn("Candidate number: ").strip()
        try:
            index = int(selected)
        except ValueError:
            index = 0
        if not 1 <= index <= len(group.candidates):
            output.write("Invalid candidate; item deferred.\n")
            return session.with_answer(
                ref, state=ReviewItemState.DEFERRED, action="defer"
            )
        winner = group.candidates[index - 1]
        return session.with_answer(
            ref,
            state=ReviewItemState.ANSWERED,
            action="select_winner",
            data={
                "active_action": DuplicateGroupAction.SELECT_WINNER.value,
                "winner": winner,
            },
        )
    if action == "3":
        return session.with_answer(
            ref,
            state=ReviewItemState.ANSWERED,
            action="keep_all",
            data={"active_action": DuplicateGroupAction.KEEP_ALL.value},
        )
    output.write("Invalid action; item deferred.\n")
    return session.with_answer(ref, state=ReviewItemState.DEFERRED, action="defer")


def _answer_held(
    session: ReviewSession,
    record: Mapping[str, object],
    raw: dict[str, Any],
    provider: MetadataProvider,
    *,
    input_fn: InputFn,
    output: TextIO,
) -> ReviewSession:
    source = _record_source(record)
    ref = next(
        item.review_ref
        for item in session.items
        if item.kind is ReviewItemKind.HELD and item.source == source
    )
    output.write(f"\nHeld review {ref} ({_display_name(source)})\n")
    output.write(
        "  1) Continue leaving untouched\n"
        "  2) Identify as specific episode\n"
        "  3) Identify as provider-confirmed special\n"
        "  4) Classify as explicit extra\n"
        "  5) Defer\n"
    )
    action = input_fn("> ").strip()
    if action == "1":
        return session.with_answer(
            ref, state=ReviewItemState.ANSWERED, action="keep_held"
        )
    if action == "5":
        return session.with_answer(ref, state=ReviewItemState.DEFERRED, action="defer")

    working = copy.deepcopy(raw)
    if action == "2":
        changed = _review_specific_episode(
            working, record, provider, input_fn=input_fn, output=output
        )
        reviewed_action = "episode"
    elif action == "3":
        changed = _review_special(
            working, record, provider, input_fn=input_fn, output=output
        )
        reviewed_action = "special"
    elif action == "4":
        changed = _review_extra(
            working, record, provider, input_fn=input_fn, output=output
        )
        reviewed_action = "extra"
    else:
        changed = False
        reviewed_action = "defer"
    if not changed:
        return session.with_answer(ref, state=ReviewItemState.DEFERRED, action="defer")
    data = _capture_held_delta(working, source)
    if not data:
        raise ReviewConfigurationError("reviewed held decision produced no active state")
    raw.clear()
    raw.update(working)
    return session.with_answer(
        ref,
        state=ReviewItemState.ANSWERED,
        action=reviewed_action,
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
) -> tuple[ReviewSession, bytes]:
    """Run or resume one crash-safe non-mutating review session."""

    validate_manifest(manifest)
    expected_plan = manifest_sha256(manifest)
    if resume:
        session = load_review_session(session_path.read_bytes())
        if session.plan_sha256 != expected_plan:
            raise ReviewConfigurationError("resume plan hash does not match the session")
        if session.base_override_snapshot != base_override_snapshot:
            raise ReviewConfigurationError(
                "resume base override snapshot does not match the session"
            )
    else:
        session = build_review_session(
            manifest,
            base_override_snapshot=base_override_snapshot,
        )
        atomic_write_new(session_path, render_review_session(session))

    held_by_source, duplicate_by_ref = _record_maps(manifest)
    selected = _filter_items(
        session,
        show=show_filter,
        kind=kind_filter,
        review_ref=ref_filter,
        pending_only=pending_only,
    )
    output.write(
        f"Review session {session.sha256[:12]}: {len(selected)} selected items. "
        "No delete or quarantine operation exists.\n"
    )
    raw = _parse_active(base_override_payload)

    if batch_accept_recommended:
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
            output.write(
                "Batch action will accept the displayed recommended winner for "
                f"{len(duplicate_refs)} independently audited groups.\n"
            )
            if input_fn("Proceed with this batch? [y/N]: ").strip().casefold() in {
                "y",
                "yes",
            }:
                accepted = set(duplicate_refs)
                for ref in duplicate_refs:
                    group = duplicate_by_ref[ref]
                    assert group.recommended_winner is not None
                    session = session.with_answer(
                        ref,
                        state=ReviewItemState.ANSWERED,
                        action="select_winner",
                        data={
                            "active_action": DuplicateGroupAction.SELECT_WINNER.value,
                            "winner": group.recommended_winner,
                        },
                    )
                    atomic_replace(session_path, render_review_session(session))
                selected = tuple(ref for ref in selected if ref not in accepted)

    for ref in selected:
        item = session.item(ref)
        if item.state is ReviewItemState.ANSWERED:
            continue
        if item.kind is ReviewItemKind.DUPLICATE:
            session = _answer_duplicate(
                session,
                duplicate_by_ref[ref],
                input_fn=input_fn,
                output=output,
            )
        else:
            assert item.source is not None
            record = held_by_source.get(item.source)
            if record is None:
                raise ReviewConfigurationError("held review source disappeared from plan")
            session = _answer_held(
                session,
                record,
                raw,
                provider,
                input_fn=input_fn,
                output=output,
            )
        atomic_replace(session_path, render_review_session(session))

    active = _compile_active(base_override_payload, session)
    atomic_write_new(output_override_path, active)
    return session, active
