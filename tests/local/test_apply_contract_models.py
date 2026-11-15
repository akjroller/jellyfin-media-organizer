"""Apply-contract value objects must refuse every malformed approval or group."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from jellyfin_show_organizer.apply_contract import (
    ApplyApproval,
    ApplyContract,
    ApplyMember,
    ApplyMemberRole,
    ApplyOperationGroup,
    ApplyReviewApproval,
    ApplyReviewMode,
)
from jellyfin_show_organizer.models import CacheSnapshot, SourceFingerprint

pytestmark = pytest.mark.local

HASH = "a" * 64
FINGERPRINT = SourceFingerprint(size=1, mtime_ns=1)


def _member(
    source: str = "Release/episode.mkv",
    destination: str = "Show/Season 01/episode.mkv",
    role: ApplyMemberRole = ApplyMemberRole.VIDEO,
) -> ApplyMember:
    return ApplyMember(
        role=role,
        source_relative_path=source,
        destination_relative_path=destination,
        fingerprint=FINGERPRINT,
    )


def _subtitle(source: str, destination: str) -> ApplyMember:
    return _member(source, destination, ApplyMemberRole.COMPANION)


def _approval(**overrides: object) -> ApplyApproval:
    values: dict[str, object] = {
        "plan_sha256": HASH,
        "schema_version": 3,
        "tool_version": "0.4.1",
        "config_snapshot_id": HASH,
        "overrides_snapshot_id": HASH,
        "cache_snapshots": (),
    }
    values.update(overrides)
    return ApplyApproval(**values)  # type: ignore[arg-type]


def _snapshot(request_key: str) -> CacheSnapshot:
    return CacheSnapshot(
        kind="search",
        provider="tvmaze",
        request_key=request_key,
        snapshot_id=HASH,
        state="ok",
    )


INVALID: dict[str, Callable[[], object]] = {
    "review session hash malformed": lambda: ApplyReviewApproval(
        session_sha256="nope", mode=ApplyReviewMode.COMPLETE
    ),
    "review scope refs unsorted": lambda: ApplyReviewApproval(
        session_sha256=HASH,
        mode=ApplyReviewMode.APPROVED_PARTIAL,
        approved_scope_refs=("b", "a"),
    ),
    "review scope refs duplicated": lambda: ApplyReviewApproval(
        session_sha256=HASH,
        mode=ApplyReviewMode.APPROVED_PARTIAL,
        approved_scope_refs=("a", "a"),
    ),
    "review scope ref empty": lambda: ApplyReviewApproval(
        session_sha256=HASH,
        mode=ApplyReviewMode.APPROVED_PARTIAL,
        approved_scope_refs=("",),
    ),
    "partial review without scope": lambda: ApplyReviewApproval(
        session_sha256=HASH, mode=ApplyReviewMode.APPROVED_PARTIAL
    ),
    "approval plan hash malformed": lambda: _approval(plan_sha256="A" * 64),
    "approval config id malformed": lambda: _approval(config_snapshot_id="x"),
    "approval schema version zero": lambda: _approval(schema_version=0),
    "approval tool version empty": lambda: _approval(tool_version=""),
    "approval duplicate cache snapshot": lambda: _approval(
        cache_snapshots=(_snapshot("search:a"), _snapshot("search:a"))
    ),
    "approval duplicate group ids": lambda: _approval(
        authorized_group_ids=("op-1", "op-1")
    ),
    "approval empty group id": lambda: _approval(authorized_group_ids=("",)),
    "member without source": lambda: _member(source=""),
    "member without destination": lambda: _member(destination=""),
    "group without id": lambda: ApplyOperationGroup(group_id="", members=(_member(),)),
    "group without members": lambda: ApplyOperationGroup(group_id="op", members=()),
    "group without a video": lambda: ApplyOperationGroup(
        group_id="op", members=(_subtitle("Release/a.srt", "Show/a.srt"),)
    ),
    "group with two videos": lambda: ApplyOperationGroup(
        group_id="op",
        members=(_member(), _member("Release/other.mkv", "Show/other.mkv")),
    ),
    "group with a repeated source": lambda: ApplyOperationGroup(
        group_id="op",
        members=(
            _member(),
            _subtitle("Release/EPISODE.MKV", "Show/Season 01/episode.en.srt"),
        ),
    ),
    "group with a repeated destination": lambda: ApplyOperationGroup(
        group_id="op",
        members=(
            _member(),
            _subtitle("Release/episode.en.srt", "Show/Season 01/Episode.mkv"),
        ),
    ),
}


@pytest.mark.parametrize("case", sorted(INVALID))
def test_malformed_apply_values_are_refused(case: str) -> None:
    with pytest.raises(ValueError):
        INVALID[case]()


def test_valid_values_normalize_ordering() -> None:
    approval = _approval(
        cache_snapshots=(_snapshot("search:b"), _snapshot("search:a")),
        authorized_group_ids=("op-B", "op-a"),
    )
    assert [item.request_key for item in approval.cache_snapshots] == [
        "search:a",
        "search:b",
    ]
    assert approval.authorized_group_ids == ("op-a", "op-B")

    group = ApplyOperationGroup(
        group_id="op",
        members=(
            _member(),
            _subtitle("Release/episode.en.srt", "Show/Season 01/episode.en.srt"),
        ),
    )
    contract = ApplyContract(plan_sha256=HASH, groups=(group,))
    assert contract.groups == (group,)
    assert all(member.moving for member in group.members)
    assert not _member("Same/x.mkv", "Same/x.mkv").moving
