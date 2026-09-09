from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from jellyfin_show_organizer.apply_contract import (
    ApplyContract,
    ApplyMember,
    ApplyMemberRole,
    ApplyOperationGroup,
)
from jellyfin_show_organizer.apply_execution import (
    ApplyExecutionError,
    PreparedApply,
    approval_token,
    execute_apply,
)
from jellyfin_show_organizer.apply_scope import (
    ApplyScopeError,
    create_apply_scope,
    load_apply_scope,
    prepare_scoped_apply,
    render_apply_scope,
    write_apply_scope,
)
from jellyfin_show_organizer.entrypoint import build_parser
from jellyfin_show_organizer.models import SourceFingerprint
from jellyfin_show_organizer.rollback_execution import (
    execute_rollback,
    prepare_rollback,
)

pytestmark = pytest.mark.local


def _fingerprint(path: Path) -> SourceFingerprint:
    stat = path.stat()
    return SourceFingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    (source / "Release A").mkdir(parents=True)
    (source / "Release B").mkdir(parents=True)
    destination.mkdir()
    (source / "Release A" / "episode.mkv").write_bytes(b"video-a")
    (source / "Release A" / "episode.en.srt").write_bytes(b"subtitle-a")
    (source / "Release B" / "episode.mkv").write_bytes(b"video-b")
    return source, destination


def _prepared(tmp_path: Path) -> tuple[PreparedApply, Path, Path]:
    source, destination = _roots(tmp_path)
    group_a = ApplyOperationGroup(
        group_id="op-a",
        members=(
            ApplyMember(
                role=ApplyMemberRole.VIDEO,
                source_relative_path="Release A/episode.mkv",
                destination_relative_path="Example/Season 01/Episode A.mkv",
                fingerprint=_fingerprint(source / "Release A" / "episode.mkv"),
                separate_roots=True,
            ),
            ApplyMember(
                role=ApplyMemberRole.COMPANION,
                source_relative_path="Release A/episode.en.srt",
                destination_relative_path="Example/Season 01/Episode A.en.srt",
                fingerprint=_fingerprint(source / "Release A" / "episode.en.srt"),
                separate_roots=True,
            ),
        ),
    )
    group_b = ApplyOperationGroup(
        group_id="op-b",
        members=(
            ApplyMember(
                role=ApplyMemberRole.VIDEO,
                source_relative_path="Release B/episode.mkv",
                destination_relative_path="Example/Season 01/Episode B.mkv",
                fingerprint=_fingerprint(source / "Release B" / "episode.mkv"),
                separate_roots=True,
            ),
        ),
    )
    prepared = PreparedApply(
        contract=ApplyContract(
            plan_sha256="a" * 64,
            groups=(group_a, group_b),
            separate_roots=True,
        ),
        review_session_sha256="b" * 64,
        source_revision="c" * 40,
    )
    return prepared, source, destination


def test_apply_scope_is_canonical_and_round_trips(tmp_path: Path):
    prepared, _, _ = _prepared(tmp_path)
    scope = create_apply_scope(prepared, ("op-a",))
    payload = render_apply_scope(scope)

    assert payload.endswith(b"\n")
    assert load_apply_scope(payload) == scope
    assert scope.sha256 == load_apply_scope(payload).sha256
    assert json.loads(payload)["group_ids"] == ["op-a"]


def test_apply_scope_requires_explicit_unique_known_proper_subset(tmp_path: Path):
    prepared, _, _ = _prepared(tmp_path)

    with pytest.raises(ApplyScopeError, match="explicit operation groups"):
        create_apply_scope(prepared, ())
    with pytest.raises(ApplyScopeError, match="unique"):
        create_apply_scope(prepared, ("op-a", "op-a"))
    with pytest.raises(ApplyScopeError, match="unknown"):
        create_apply_scope(prepared, ("op-missing",))
    with pytest.raises(ApplyScopeError, match="proper subset"):
        create_apply_scope(prepared, ("op-a", "op-b"))


def test_apply_scope_rejects_unsorted_tampered_or_stale_artifacts(tmp_path: Path):
    prepared, _, _ = _prepared(tmp_path)
    scope = create_apply_scope(prepared, ("op-a",))
    payload = render_apply_scope(scope)

    wrong_approval = "d" * 64
    with pytest.raises(ApplyScopeError, match="does not match"):
        prepare_scoped_apply(
            prepared,
            payload,
            approved_scope_sha256=wrong_approval,
        )

    stale = replace(scope, plan_sha256="d" * 64)
    with pytest.raises(ApplyScopeError, match="another plan"):
        prepare_scoped_apply(
            prepared,
            render_apply_scope(stale),
            approved_scope_sha256=stale.sha256,
        )

    stale_review = replace(scope, review_session_sha256="e" * 64)
    with pytest.raises(ApplyScopeError, match="another review"):
        prepare_scoped_apply(
            prepared,
            render_apply_scope(stale_review),
            approved_scope_sha256=stale_review.sha256,
        )

    stale_revision = replace(scope, source_revision="d" * 40)
    with pytest.raises(ApplyScopeError, match="another source revision"):
        prepare_scoped_apply(
            prepared,
            render_apply_scope(stale_revision),
            approved_scope_sha256=stale_revision.sha256,
        )

    raw = json.loads(payload)
    raw["unexpected"] = True
    with pytest.raises(ApplyScopeError, match="unexpected fields"):
        load_apply_scope(json.dumps(raw).encode())


def test_scope_write_never_overwrites(tmp_path: Path):
    prepared, _, _ = _prepared(tmp_path)
    scope = create_apply_scope(prepared, ("op-a",))
    output = tmp_path / "scope.json"

    write_apply_scope(output, scope)
    with pytest.raises(ApplyScopeError, match="already exists"):
        write_apply_scope(output, scope)

    assert load_apply_scope(output.read_bytes()) == scope


def test_scope_hash_changes_confirmation_and_filters_contract(tmp_path: Path):
    prepared, source, destination = _prepared(tmp_path)
    full_token = approval_token(prepared, source, destination)
    scope = create_apply_scope(prepared, ("op-a",))
    scoped = prepare_scoped_apply(
        prepared,
        render_apply_scope(scope),
        approved_scope_sha256=scope.sha256,
    )

    assert scoped.apply_scope_sha256 == scope.sha256
    assert [group.group_id for group in scoped.contract.groups] == ["op-a"]
    assert approval_token(scoped, source, destination) != full_token
    assert f":SCOPE:{scope.sha256}:" in approval_token(scoped, source, destination)


def test_scoped_apply_and_rollback_round_trip_without_touching_other_group(
    tmp_path: Path,
):
    prepared, source, destination = _prepared(tmp_path)
    scope = create_apply_scope(prepared, ("op-a",))
    scoped = prepare_scoped_apply(
        prepared,
        render_apply_scope(scope),
        approved_scope_sha256=scope.sha256,
    )
    journal = tmp_path / "apply.jsonl"

    checked = execute_apply(
        scoped,
        source,
        destination,
        journal_path=None,
        check_only=True,
    )
    assert checked.groups_total == 1
    assert checked.members_moved == 0

    applied = execute_apply(scoped, source, destination, journal_path=journal)
    assert applied.groups_completed == 1
    assert applied.members_moved == 2
    assert not (source / "Release A" / "episode.mkv").exists()
    assert not (source / "Release A" / "episode.en.srt").exists()
    assert (destination / "Example" / "Season 01" / "Episode A.mkv").is_file()
    assert (destination / "Example" / "Season 01" / "Episode A.en.srt").is_file()
    assert (source / "Release B" / "episode.mkv").read_bytes() == b"video-b"
    assert not (destination / "Example" / "Season 01" / "Episode B.mkv").exists()

    prepared_rollback = prepare_rollback(scoped, journal)
    rollback_check = execute_rollback(
        prepared_rollback,
        source,
        destination,
        rollback_journal_path=None,
        check_only=True,
    )
    assert rollback_check.groups_total == 1
    rollback_journal = tmp_path / "rollback.jsonl"
    rolled_back = execute_rollback(
        prepared_rollback,
        source,
        destination,
        rollback_journal_path=rollback_journal,
    )
    assert rolled_back.members_restored == 2
    assert (source / "Release A" / "episode.mkv").read_bytes() == b"video-a"
    assert (source / "Release A" / "episode.en.srt").read_bytes() == b"subtitle-a"
    assert not (destination / "Example" / "Season 01" / "Episode A.mkv").exists()
    assert (source / "Release B" / "episode.mkv").read_bytes() == b"video-b"


def test_apply_journal_rejects_different_or_missing_scope_on_resume(tmp_path: Path):
    prepared, source, destination = _prepared(tmp_path)
    scope_a = create_apply_scope(prepared, ("op-a",))
    scoped_a = prepare_scoped_apply(
        prepared,
        render_apply_scope(scope_a),
        approved_scope_sha256=scope_a.sha256,
    )
    journal = tmp_path / "apply.jsonl"
    execute_apply(scoped_a, source, destination, journal_path=journal)

    with pytest.raises(ApplyExecutionError, match="another apply scope"):
        execute_apply(
            prepared,
            source,
            destination,
            journal_path=journal,
            resume=True,
        )

    scope_b = create_apply_scope(prepared, ("op-b",))
    scoped_b = prepare_scoped_apply(
        prepared,
        render_apply_scope(scope_b),
        approved_scope_sha256=scope_b.sha256,
    )
    with pytest.raises(ApplyExecutionError, match="another apply scope"):
        execute_apply(
            scoped_b,
            source,
            destination,
            journal_path=journal,
            resume=True,
        )


def test_public_parser_has_explicit_scope_controls_only():
    parser = build_parser()
    args = parser.parse_args(
        [
            "apply-scope",
            "create",
            "plan.json",
            "--preflight",
            "preflight.json",
            "--run-provenance",
            "run-provenance.json",
            "--source-root",
            "source",
            "--destination-root",
            "destination",
            "--approve-plan-sha256",
            "a" * 64,
            "--approve-review-session-sha256",
            "b" * 64,
            "--approve-source-revision",
            "c" * 40,
            "--group-id",
            "op-a",
            "--output",
            "scope.json",
        ]
    )
    assert args.group_ids == ["op-a"]
    assert not hasattr(args, "limit")
    assert not hasattr(args, "first")
