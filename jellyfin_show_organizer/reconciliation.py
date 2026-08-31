from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from .inventory import VIDEO_EXTENSIONS, InventoryRecord, InventoryStatus


class ReconciliationStatus(StrEnum):
    PROCESSED = "processed"
    EXCLUDED_SAMPLE = "excluded-sample"
    UNSUPPORTED = "unsupported"
    MISSING = "missing"
    UNREADABLE = "unreadable"


@dataclass(frozen=True, slots=True)
class ReconciliationRecord:
    relative_path: str
    status: ReconciliationStatus
    reason: str


def _normalize_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        raise ValueError("inventory baseline paths must be non-empty relative paths")
    return path.as_posix()


def _path_key(relative_path: str) -> tuple[str, str]:
    return relative_path.casefold(), relative_path


def _status_for_inventory_record(
    record: InventoryRecord,
    *,
    expected: bool,
) -> ReconciliationRecord:
    if record.status is InventoryStatus.INCLUDED:
        reason = "expected-video-present" if expected else "discovered-video"
        status = ReconciliationStatus.PROCESSED
    elif record.status is InventoryStatus.EXCLUDED_SAMPLE:
        reason = record.reason or "sample-excluded"
        status = ReconciliationStatus.EXCLUDED_SAMPLE
    elif record.status is InventoryStatus.BLOCKED_LINK:
        reason = f"blocked-link:{record.reason or 'link-not-followed'}"
        status = ReconciliationStatus.UNREADABLE
    else:
        reason = record.reason or "unreadable-video"
        status = ReconciliationStatus.UNREADABLE

    return ReconciliationRecord(
        relative_path=record.relative_path,
        status=status,
        reason=reason,
    )


def reconcile_inventory(
    inventory: Iterable[InventoryRecord],
    expected_paths: Iterable[str] = (),
) -> tuple[ReconciliationRecord, ...]:
    """Reconcile one scan against an optional private/local baseline.

    The function returns only path-level classifications and reasons. Callers may
    keep real baseline data local while repository tests exercise the same logic
    with synthetic paths.
    """

    current_by_key: dict[str, InventoryRecord] = {}
    for inventory_record in inventory:
        normalized = _normalize_relative_path(inventory_record.relative_path)
        key = normalized.casefold()
        if key in current_by_key:
            raise ValueError("inventory contains case-insensitive duplicate paths")
        current_by_key[key] = inventory_record

    expected_by_key: dict[str, str] = {}
    for value in expected_paths:
        normalized = _normalize_relative_path(value)
        key = normalized.casefold()
        previous = expected_by_key.get(key)
        if previous is not None and previous != normalized:
            raise ValueError("baseline contains case-insensitive duplicate paths")
        expected_by_key[key] = normalized

    reconciled: list[ReconciliationRecord] = []
    for key in set(current_by_key) | set(expected_by_key):
        current_record = current_by_key.get(key)
        expected_path = expected_by_key.get(key)

        if current_record is not None:
            reconciled.append(
                _status_for_inventory_record(
                    current_record,
                    expected=expected_path is not None,
                )
            )
            continue

        assert expected_path is not None
        extension = PurePosixPath(expected_path).suffix.casefold()
        if extension not in VIDEO_EXTENSIONS:
            status = ReconciliationStatus.UNSUPPORTED
            reason = f"unsupported-extension:{extension or '<none>'}"
        else:
            status = ReconciliationStatus.MISSING
            reason = "expected-video-not-found"
        reconciled.append(
            ReconciliationRecord(
                relative_path=expected_path,
                status=status,
                reason=reason,
            )
        )

    reconciled.sort(key=lambda item: _path_key(item.relative_path))
    return tuple(reconciled)
