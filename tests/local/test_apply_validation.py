from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from jellyfin_show_organizer import apply_validation
from jellyfin_show_organizer.apply_contract import (
    ApplyContract,
    ApplyMember,
    ApplyMemberRole,
    ApplyOperationGroup,
)
from jellyfin_show_organizer.apply_validation import (
    ApplyFilesystemError,
    revalidate_apply_contract,
    revalidate_apply_member,
)
from jellyfin_show_organizer.models import SourceFingerprint

pytestmark = pytest.mark.local


def _fingerprint(path: Path, *, with_hash: bool = False) -> SourceFingerprint:
    stat = path.stat()
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest() if with_hash else None
    return SourceFingerprint(
        size=stat.st_size, mtime_ns=stat.st_mtime_ns, sha256=sha256
    )


def _member(
    source: str,
    destination: str,
    fingerprint: SourceFingerprint,
) -> ApplyMember:
    return ApplyMember(
        role=ApplyMemberRole.VIDEO,
        source_relative_path=source,
        destination_relative_path=destination,
        fingerprint=fingerprint,
    )


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    source_root = tmp_path / "source"
    destination_root = tmp_path / "destination"
    source = source_root / "Example Series" / "episode.mkv"
    source.parent.mkdir(parents=True)
    destination_root.mkdir()
    source.write_bytes(b"synthetic-media")
    return source_root, destination_root, source


def test_revalidation_accepts_matching_source_without_creating_destinations(
    tmp_path: Path,
):
    source_root, destination_root, source = _roots(tmp_path)
    member = _member(
        "Example Series/episode.mkv",
        "Example Series (2024)/Season 01/episode.mkv",
        _fingerprint(source),
    )
    contract = ApplyContract(
        plan_sha256="a" * 64,
        groups=(ApplyOperationGroup(group_id="op-example", members=(member,)),),
    )

    validation = revalidate_apply_contract(contract, source_root, destination_root)

    assert validation.plan_sha256 == contract.plan_sha256
    assert len(validation.observations) == 1
    assert (
        validation.observations[0].source_relative_path == member.source_relative_path
    )
    assert not (destination_root / "Example Series (2024)").exists()


def test_revalidation_rejects_stale_source_fingerprint(tmp_path: Path):
    source_root, destination_root, source = _roots(tmp_path)
    planned = _fingerprint(source)
    source.write_bytes(b"changed-synthetic-media")
    member = _member("Example Series/episode.mkv", "output/episode.mkv", planned)

    with pytest.raises(ApplyFilesystemError, match="fingerprint"):
        revalidate_apply_member("op-example", member, source_root, destination_root)


def test_revalidation_rejects_destination_race(tmp_path: Path):
    source_root, destination_root, source = _roots(tmp_path)
    destination = destination_root / "output" / "episode.mkv"
    destination.parent.mkdir()
    destination.write_bytes(b"already-present")
    member = _member(
        "Example Series/episode.mkv",
        "output/episode.mkv",
        _fingerprint(source),
    )

    with pytest.raises(ApplyFilesystemError, match="destination already exists"):
        revalidate_apply_member("op-example", member, source_root, destination_root)

    assert destination.read_bytes() == b"already-present"
    assert source.exists()


def test_sha256_detects_content_change_even_when_size_and_mtime_are_restored(
    tmp_path: Path,
):
    source_root, destination_root, source = _roots(tmp_path)
    source.write_bytes(b"abc")
    planned = _fingerprint(source, with_hash=True)
    before = source.stat()
    source.write_bytes(b"xyz")
    os.utime(source, ns=(before.st_atime_ns, planned.mtime_ns))
    member = _member("Example Series/episode.mkv", "output/episode.mkv", planned)

    with pytest.raises(ApplyFilesystemError, match="SHA-256"):
        revalidate_apply_member("op-example", member, source_root, destination_root)


@pytest.mark.parametrize(
    "unsafe",
    (
        "../episode.mkv",
        "C:relative.mkv",
        "C:/absolute.mkv",
        "Example Series//episode.mkv",
    ),
)
def test_revalidation_rejects_unsafe_source_references(tmp_path: Path, unsafe: str):
    source_root, destination_root, source = _roots(tmp_path)
    member = _member(unsafe, "output/episode.mkv", _fingerprint(source))

    with pytest.raises(ApplyFilesystemError, match="source"):
        revalidate_apply_member("op-example", member, source_root, destination_root)


def test_revalidation_rejects_cross_filesystem_targets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source_root, destination_root, source = _roots(tmp_path)
    member = _member(
        "Example Series/episode.mkv",
        "output/episode.mkv",
        _fingerprint(source),
    )

    monkeypatch.setattr(
        apply_validation,
        "_device_id",
        lambda path: 1 if path.name == "episode.mkv" else 2,
    )

    with pytest.raises(ApplyFilesystemError, match="cross-filesystem"):
        revalidate_apply_member("op-example", member, source_root, destination_root)


def test_revalidation_rejects_symlinked_source_parent_when_supported(tmp_path: Path):
    source_root, destination_root, source = _roots(tmp_path)
    target = tmp_path / "outside"
    target.mkdir()
    (target / "episode.mkv").write_bytes(source.read_bytes())
    link = source_root / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")

    member = _member("linked/episode.mkv", "output/episode.mkv", _fingerprint(source))

    with pytest.raises(ApplyFilesystemError, match="symlink or junction"):
        revalidate_apply_member("op-example", member, source_root, destination_root)
