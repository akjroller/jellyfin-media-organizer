from __future__ import annotations

from pathlib import Path

import pytest

from jellyfin_show_organizer import review_session

pytestmark = pytest.mark.local


def test_atomic_write_new_writes_once_and_never_replaces(tmp_path: Path) -> None:
    target = tmp_path / "review.json"
    review_session.atomic_write_new(target, b"first\n")

    assert target.read_bytes() == b"first\n"
    with pytest.raises(FileExistsError, match="review output already exists"):
        review_session.atomic_write_new(target, b"second\n")
    assert target.read_bytes() == b"first\n"


def test_atomic_write_new_fallback_uses_exclusive_create(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "review.json"

    def no_hard_links(_source: Path, _destination: Path) -> None:
        raise OSError("hard links unavailable")

    monkeypatch.setattr(review_session.os, "link", no_hard_links)

    review_session.atomic_write_new(target, b"fallback\n")

    assert target.read_bytes() == b"fallback\n"


def test_atomic_write_new_fallback_race_preserves_competing_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "review.json"

    def no_hard_links(_source: Path, _destination: Path) -> None:
        raise OSError("hard links unavailable")

    original_exclusive = review_session._write_new_exclusive

    def create_competing_target(path: Path, payload: bytes) -> None:
        path.write_bytes(b"competitor\n")
        original_exclusive(path, payload)

    monkeypatch.setattr(review_session.os, "link", no_hard_links)
    monkeypatch.setattr(
        review_session,
        "_write_new_exclusive",
        create_competing_target,
    )

    with pytest.raises(FileExistsError, match="review output already exists"):
        review_session.atomic_write_new(target, b"ours\n")

    assert target.read_bytes() == b"competitor\n"


def test_atomic_write_new_rejects_broken_symlink_when_supported(
    tmp_path: Path,
) -> None:
    target = tmp_path / "review.json"
    try:
        target.symlink_to(tmp_path / "missing-target")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable on this platform")

    with pytest.raises(FileExistsError, match="review output already exists"):
        review_session.atomic_write_new(target, b"ours\n")

    assert target.is_symlink()


def test_atomic_replace_replaces_existing_session_payload(tmp_path: Path) -> None:
    target = tmp_path / "review.json"
    target.write_bytes(b"old\n")

    review_session.atomic_replace(target, b"new\n")

    assert target.read_bytes() == b"new\n"
