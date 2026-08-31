import pytest

from jellyfin_show_organizer.inventory import InventoryRecord, InventoryStatus
from jellyfin_show_organizer.models import SourceFingerprint
from jellyfin_show_organizer.reconciliation import (
    ReconciliationStatus,
    reconcile_inventory,
)

pytestmark = pytest.mark.local


def _record(
    path: str,
    status: InventoryStatus,
    reason: str | None = None,
) -> InventoryRecord:
    fingerprint = (
        SourceFingerprint(size=1, mtime_ns=1)
        if status in {InventoryStatus.INCLUDED, InventoryStatus.EXCLUDED_SAMPLE}
        else None
    )
    return InventoryRecord(
        relative_path=path,
        extension="." + path.rsplit(".", 1)[-1].casefold(),
        status=status,
        fingerprint=fingerprint,
        reason=reason,
    )


def test_reconciliation_assigns_one_explained_terminal_status_per_path():
    inventory = (
        _record("Series/Episode 01.mkv", InventoryStatus.INCLUDED),
        _record("Series/Sample.mkv", InventoryStatus.EXCLUDED_SAMPLE, "sample-name"),
        _record(
            "Series/Linked.mp4",
            InventoryStatus.BLOCKED_LINK,
            "symlink-or-junction",
        ),
        _record("Series/Broken.avi", InventoryStatus.UNREADABLE, "stat-error:OSError"),
    )
    expected = (
        "Series/Episode 01.mkv",
        "Series/Sample.mkv",
        "Series/Linked.mp4",
        "Series/Broken.avi",
        "Series/Missing.mkv",
        "Series/Legacy.wmv",
    )

    reconciled = reconcile_inventory(inventory, expected)
    by_path = {record.relative_path: record for record in reconciled}

    assert {record.status for record in reconciled} == set(ReconciliationStatus)
    assert by_path["Series/Episode 01.mkv"].status is ReconciliationStatus.PROCESSED
    assert by_path["Series/Sample.mkv"].status is ReconciliationStatus.EXCLUDED_SAMPLE
    assert by_path["Series/Linked.mp4"].status is ReconciliationStatus.UNREADABLE
    assert by_path["Series/Broken.avi"].status is ReconciliationStatus.UNREADABLE
    assert by_path["Series/Missing.mkv"].status is ReconciliationStatus.MISSING
    assert by_path["Series/Legacy.wmv"].status is ReconciliationStatus.UNSUPPORTED
    assert all(record.reason for record in reconciled)
    assert len(reconciled) == len(
        {record.relative_path.casefold() for record in reconciled}
    )


def test_reconciliation_is_stable_under_input_ordering():
    inventory = (
        _record("Series/beta.mkv", InventoryStatus.INCLUDED),
        _record("Series/Alpha.mkv", InventoryStatus.INCLUDED),
    )

    forward = reconcile_inventory(inventory, ("Series/Missing.mkv",))
    reverse = reconcile_inventory(reversed(inventory), ("Series/Missing.mkv",))

    assert forward == reverse
    assert [record.relative_path for record in forward] == [
        "Series/Alpha.mkv",
        "Series/beta.mkv",
        "Series/Missing.mkv",
    ]


def test_reconciliation_rejects_unsafe_or_ambiguous_baseline_paths():
    with pytest.raises(ValueError, match="relative paths"):
        reconcile_inventory((), ("../outside.mkv",))

    with pytest.raises(ValueError, match="duplicate"):
        reconcile_inventory((), ("Series/Episode.mkv", "series/episode.mkv"))
