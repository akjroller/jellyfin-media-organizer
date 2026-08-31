from __future__ import annotations

import hashlib
import os
import unicodedata
from pathlib import Path

import pytest

from jellyfin_show_organizer.inventory import authorize_shows_root
from jellyfin_show_organizer.models import DuplicateDecision, SourceFingerprint
from jellyfin_show_organizer.preflight import (
    PreflightRecord,
    PreflightStatus,
    authorize_destination_root,
    preflight_plan,
    summarize_preflight,
)

pytestmark = pytest.mark.local

PLAN_HASH = "a" * 64


def _roots(tmp_path: Path):
    shows = tmp_path / "shows"
    destination = tmp_path / "organized"
    shows.mkdir()
    destination.mkdir()
    return (
        shows,
        authorize_shows_root(shows),
        destination,
        authorize_destination_root(destination),
    )


def _source(
    root: Path,
    relative_path: str,
    *,
    payload: bytes = b"fabricated-video",
    include_hash: bool = False,
) -> SourceFingerprint:
    path = root.joinpath(*relative_path.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    stat = path.stat()
    return SourceFingerprint(
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        sha256=hashlib.sha256(payload).hexdigest() if include_hash else None,
    )


def _matched(
    record_id: str,
    source_relative_path: str,
    fingerprint: SourceFingerprint | None,
    destination_relative_path: str | None,
    *,
    group_id: str | None = "group-1",
    provider_identity: str | None = "tvmaze:4242",
    numbering_identity: str | None = "S01E01",
    duplicate: DuplicateDecision | None = None,
) -> PreflightRecord:
    return PreflightRecord(
        record_id=record_id,
        source_relative_path=source_relative_path,
        status=PreflightStatus.MATCHED,
        operation_group_id=group_id,
        provider_identity=provider_identity,
        numbering_identity=numbering_identity,
        destination_relative_path=destination_relative_path,
        source_fingerprint=fingerprint,
        duplicate=duplicate,
    )


def test_clean_operation_set_is_ready_without_creating_destinations(
    tmp_path: Path,
) -> None:
    shows, source_root, destination, destination_root = _roots(tmp_path)
    fingerprint = _source(shows, "Fabricated Show/episode.mkv")
    record = _matched(
        "record-1",
        "Fabricated Show/episode.mkv",
        fingerprint,
        "Fabricated Show/Season 01/Fabricated Show S01E01.mkv",
    )

    result = preflight_plan(
        PLAN_HASH,
        (record,),
        source_root=source_root,
        destination_root=destination_root,
    )

    assert result.ready
    assert result.findings == ()
    assert result.blocked_group_ids == ()
    assert not (destination / "Fabricated Show").exists()
    assert summarize_preflight(result) == (
        f"preflight ready for plan {PLAN_HASH}: 0 blocking findings"
    )


def test_exact_case_and_unicode_destination_collisions_fail_closed(
    tmp_path: Path,
) -> None:
    shows, source_root, _, destination_root = _roots(tmp_path)
    fingerprints = {
        name: _source(shows, name)
        for name in ("a.mkv", "b.mkv", "c.mkv", "d.mkv", "e.mkv", "f.mkv")
    }
    composed = "Caf\u00e9/Season 01/Episode.mkv"
    decomposed = unicodedata.normalize("NFD", composed)
    records = (
        _matched(
            "a",
            "a.mkv",
            fingerprints["a.mkv"],
            "Exact/Season 01/Episode.mkv",
            group_id="ga",
        ),
        _matched(
            "b",
            "b.mkv",
            fingerprints["b.mkv"],
            "Exact/Season 01/Episode.mkv",
            group_id="gb",
        ),
        _matched(
            "c",
            "c.mkv",
            fingerprints["c.mkv"],
            "Case/Season 01/Episode.mkv",
            group_id="gc",
        ),
        _matched(
            "d",
            "d.mkv",
            fingerprints["d.mkv"],
            "case/season 01/episode.mkv",
            group_id="gd",
        ),
        _matched("e", "e.mkv", fingerprints["e.mkv"], composed, group_id="ge"),
        _matched("f", "f.mkv", fingerprints["f.mkv"], decomposed, group_id="gf"),
    )

    result = preflight_plan(
        PLAN_HASH,
        records,
        source_root=source_root,
        destination_root=destination_root,
    )
    codes = {finding.code for finding in result.findings}

    assert not result.ready
    assert "destination-exact-collision" in codes
    assert "destination-case-insensitive-collision" in codes
    assert "destination-unicode-normalization-collision" in codes
    assert "destination-component-not-nfc" in codes


@pytest.mark.parametrize(
    ("destination", "expected_code", "kwargs"),
    [
        ("../escape.mkv", "destination-root-escape", {}),
        ("Fabricated/CON.mkv", "destination-component-windows-reserved", {}),
        ("Fabricated/bad?.mkv", "destination-component-forbidden-character", {}),
        ("Fabricated./Episode.mkv", "destination-component-trailing-dot-space", {}),
        (
            "Fabricated\\Season 01\\Episode.mkv",
            "destination-path-noncanonical-separator",
            {},
        ),
        ("L" * 81, "destination-path-too-long", {"max_path_length": 80}),
        (
            f"{'C' * 33}/Episode.mkv",
            "destination-component-too-long",
            {"max_component_length": 32},
        ),
    ],
)
def test_destination_path_hazards_are_blocking(
    tmp_path: Path,
    destination: str,
    expected_code: str,
    kwargs: dict[str, int],
) -> None:
    shows, source_root, _, destination_root = _roots(tmp_path)
    fingerprint = _source(shows, "episode.mkv")

    result = preflight_plan(
        PLAN_HASH,
        (_matched("record-1", "episode.mkv", fingerprint, destination),),
        source_root=source_root,
        destination_root=destination_root,
        **kwargs,
    )

    assert not result.ready
    assert expected_code in {finding.code for finding in result.findings}


def test_existing_destination_file_blocks_preflight(tmp_path: Path) -> None:
    shows, source_root, destination, destination_root = _roots(tmp_path)
    fingerprint = _source(shows, "episode.mkv")
    existing = destination / "Fabricated" / "Season 01" / "Episode.mkv"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"already-there")

    result = preflight_plan(
        PLAN_HASH,
        (
            _matched(
                "record-1",
                "episode.mkv",
                fingerprint,
                "Fabricated/Season 01/Episode.mkv",
            ),
        ),
        source_root=source_root,
        destination_root=destination_root,
    )

    assert not result.ready
    assert "destination-file-exists" in {finding.code for finding in result.findings}


def test_changed_source_fingerprint_blocks_preflight(tmp_path: Path) -> None:
    shows, source_root, _, destination_root = _roots(tmp_path)
    fingerprint = _source(shows, "episode.mkv")
    (shows / "episode.mkv").write_bytes(b"changed-after-plan")

    result = preflight_plan(
        PLAN_HASH,
        (
            _matched(
                "record-1", "episode.mkv", fingerprint, "Show/Season 01/Episode.mkv"
            ),
        ),
        source_root=source_root,
        destination_root=destination_root,
    )

    assert not result.ready
    assert "source-fingerprint-changed" in {finding.code for finding in result.findings}


def test_sha256_revalidation_catches_content_change_with_same_metadata(
    tmp_path: Path,
) -> None:
    shows, source_root, _, destination_root = _roots(tmp_path)
    fingerprint = _source(
        shows,
        "episode.mkv",
        payload=b"AAAA",
        include_hash=True,
    )
    path = shows / "episode.mkv"
    stat = path.stat()
    path.write_bytes(b"BBBB")
    os.utime(path, ns=(stat.st_atime_ns, fingerprint.mtime_ns))

    result = preflight_plan(
        PLAN_HASH,
        (
            _matched(
                "record-1", "episode.mkv", fingerprint, "Show/Season 01/Episode.mkv"
            ),
        ),
        source_root=source_root,
        destination_root=destination_root,
    )

    assert not result.ready
    assert "source-fingerprint-changed" in {finding.code for finding in result.findings}


def test_missing_matched_identity_fields_are_all_blocking(tmp_path: Path) -> None:
    shows, source_root, _, destination_root = _roots(tmp_path)
    fingerprint = _source(shows, "episode.mkv")
    record = _matched(
        "record-1",
        "episode.mkv",
        fingerprint,
        None,
        group_id=None,
        provider_identity=None,
        numbering_identity=None,
    )

    result = preflight_plan(
        PLAN_HASH,
        (record,),
        source_root=source_root,
        destination_root=destination_root,
    )
    codes = {finding.code for finding in result.findings}

    assert not result.ready
    assert "matched-operation-group-missing" in codes
    assert "matched-provider-identity-missing" in codes
    assert "matched-numbering-identity-missing" in codes
    assert "matched-destination-missing" in codes


def test_unresolved_or_suspicious_records_block_the_whole_plan(tmp_path: Path) -> None:
    _, source_root, _, destination_root = _roots(tmp_path)
    records = (
        PreflightRecord(
            record_id="unresolved",
            source_relative_path="unresolved.mkv",
            status=PreflightStatus.UNRESOLVED,
        ),
        PreflightRecord(
            record_id="suspicious",
            source_relative_path="suspicious.mkv",
            status=PreflightStatus.SUSPICIOUS,
        ),
    )

    result = preflight_plan(
        PLAN_HASH,
        records,
        source_root=source_root,
        destination_root=destination_root,
    )

    assert not result.ready
    assert {finding.code for finding in result.findings} == {
        "blocking-plan-status:suspicious",
        "blocking-plan-status:unresolved",
    }


def test_one_bad_companion_member_blocks_its_entire_operation_group(
    tmp_path: Path,
) -> None:
    shows, source_root, _, destination_root = _roots(tmp_path)
    video_fingerprint = _source(shows, "episode.mkv")
    subtitle_fingerprint = _source(shows, "episode.en.srt", payload=b"subtitle")
    records = (
        _matched(
            "video",
            "episode.mkv",
            video_fingerprint,
            "Show/Season 01/Episode.mkv",
            group_id="episode-group",
        ),
        _matched(
            "subtitle",
            "episode.en.srt",
            subtitle_fingerprint,
            "../Episode.en.srt",
            group_id="episode-group",
        ),
    )

    result = preflight_plan(
        PLAN_HASH,
        records,
        source_root=source_root,
        destination_root=destination_root,
    )

    assert not result.ready
    assert result.blocked_group_ids == ("episode-group",)
    assert "destination-root-escape" in {finding.code for finding in result.findings}


def test_mixed_identity_inside_one_operation_group_is_blocking(tmp_path: Path) -> None:
    shows, source_root, _, destination_root = _roots(tmp_path)
    first = _source(shows, "first.mkv")
    second = _source(shows, "second.mkv")
    records = (
        _matched(
            "first",
            "first.mkv",
            first,
            "Show/Season 01/First.mkv",
            group_id="shared",
            provider_identity="tvmaze:1",
            numbering_identity="S01E01",
        ),
        _matched(
            "second",
            "second.mkv",
            second,
            "Show/Season 01/Second.mkv",
            group_id="shared",
            provider_identity="tvmaze:2",
            numbering_identity="S01E02",
        ),
    )

    result = preflight_plan(
        PLAN_HASH,
        records,
        source_root=source_root,
        destination_root=destination_root,
    )
    codes = {finding.code for finding in result.findings}

    assert "operation-group-mixed-provider-identity" in codes
    assert "operation-group-mixed-numbering-identity" in codes
    assert result.blocked_group_ids == ("shared",)


def test_duplicate_loser_cannot_remain_matched(tmp_path: Path) -> None:
    shows, source_root, _, destination_root = _roots(tmp_path)
    first = _source(shows, "a.mkv")
    second = _source(shows, "b.mkv")
    decision = DuplicateDecision(
        destination_key="show/season 01/episode.mkv",
        candidates=("a.mkv", "b.mkv"),
        winner="a.mkv",
        losers=("b.mkv",),
        confidence=1.0,
        evidence=("fabricated-equality",),
    )
    records = (
        _matched(
            "winner",
            "a.mkv",
            first,
            "Show/Season 01/Episode.mkv",
            group_id="winner-group",
            duplicate=decision,
        ),
        _matched(
            "loser",
            "b.mkv",
            second,
            "Show/Season 01/Episode-copy.mkv",
            group_id="loser-group",
            duplicate=decision,
        ),
    )

    result = preflight_plan(
        PLAN_HASH,
        records,
        source_root=source_root,
        destination_root=destination_root,
    )
    codes = {finding.code for finding in result.findings}

    assert "duplicate-loser-marked-matched" in codes
    assert "duplicate-loser-also-marked-matched" in codes


def test_preflight_output_is_deterministic_across_input_order(tmp_path: Path) -> None:
    shows, source_root, _, destination_root = _roots(tmp_path)
    first = _source(shows, "a.mkv")
    second = _source(shows, "b.mkv")
    records = (
        _matched("z", "a.mkv", first, "Show/Season 01/Episode.mkv", group_id="z-group"),
        _matched(
            "a", "b.mkv", second, "show/season 01/episode.mkv", group_id="a-group"
        ),
    )

    first_result = preflight_plan(
        PLAN_HASH,
        records,
        source_root=source_root,
        destination_root=destination_root,
    )
    second_result = preflight_plan(
        PLAN_HASH,
        tuple(reversed(records)),
        source_root=source_root,
        destination_root=destination_root,
    )

    assert first_result == second_result
    assert first_result.to_dict() == second_result.to_dict()
