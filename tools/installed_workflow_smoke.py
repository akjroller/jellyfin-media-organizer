"""Exercise an installed artifact, offline, using only temporary synthetic files.

Run with the installed environment's Python from outside the source checkout.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import sysconfig
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from jellyfin_show_organizer.providers import TvmazeProviderAdapter
from jellyfin_show_organizer.review_contract import load_review_contract
from jellyfin_show_organizer.review_execution import PlanningConfig, execute_plan
from jellyfin_show_organizer.review_system import run_review_system
from jellyfin_show_organizer.run_provenance import detect_source_revision
from jellyfin_show_organizer.schema import plan_to_manifest
from jellyfin_show_organizer.tvmaze_cache import TvmazeCatalogCache


def getter(url: str, params: Mapping[str, str] | None = None) -> object:
    if "search/shows" in url:
        return [
            {
                "show": {
                    "id": 4242,
                    "name": "Example Aired Series",
                    "premiered": "2024-01-01",
                }
            }
        ]
    if "/episodes" in url:
        return [
            {
                "id": 1001,
                "season": 1,
                "number": 1,
                "name": "Pilot",
                "airdate": "2024-01-01",
                "type": "regular",
            }
        ]
    raise AssertionError(f"Unexpected provider request: {url}")


def cli(args: list[str]) -> str:
    executable = Path(sysconfig.get_path("scripts")) / (
        "jmo.exe" if sys.platform == "win32" else "jmo"
    )
    result = subprocess.run(
        [str(executable), *args], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, (
        args[0],
        result.returncode,
        result.stdout,
        result.stderr,
    )
    return result.stdout


def smoke(root: Path, *, duplicate: bool = False) -> None:
    revision = detect_source_revision()
    assert revision.state == "git" and revision.dirty is False, revision
    source, destination = root / "source", root / "destination"
    source.mkdir()
    destination.mkdir()
    series = source / "Example Aired Series"
    series.mkdir()
    (series / "Example Aired Series S01E01.mkv").write_bytes(b"synthetic-video")
    (series / "Example Aired Series S01E01.en.srt").write_bytes(b"synthetic-subtitle")
    base = root / "base.toml"
    base.write_text("schema_version = 4\n", encoding="utf-8")
    cache = root / "cache"
    config = PlanningConfig(
        shows_root=source,
        destination_root=destination,
        output_dir=root / "seed",
        cache_dir=cache,
        overrides_path=base,
    )
    seed = execute_plan(config, getter)
    assert seed.preflight.ready
    # Recreate the common import case: correctly named files in another root.
    records = [(r.source.relative_path, r.destination) for r in seed.plan.records]
    records += [(r.relative_path, r.destination) for r in seed.plan.companions]
    for old, new in records:
        assert new is not None
        target = source / new
        target.parent.mkdir(parents=True, exist_ok=True)
        (source / old).rename(target)
    winner_path = seed.plan.records[0].destination
    assert winner_path is not None
    loser = series / "Example Aired Series S01E01.mkv"
    if duplicate:
        loser.write_bytes(b"synthetic-loser")
    first = execute_plan(
        replace(config, output_dir=root / "base-plan", offline=True), getter
    )
    if not duplicate:
        assert first.preflight.ready
        assert all(r.source.relative_path == r.destination for r in first.plan.records)
    answers: list[str] = []
    if duplicate:
        decision = next(
            r.duplicate for r in first.plan.records if r.duplicate is not None
        )
        answers = ["s", f"C{decision.candidates.index(winner_path) + 1}"]
    responses = iter(answers)
    base_catalog = load_review_contract(base)
    session_path = root / "session.json"
    reviewed = root / "reviewed.toml"
    provider = TvmazeProviderAdapter(TvmazeCatalogCache(cache, offline=True), getter)
    session, _ = run_review_system(
        plan_to_manifest(first.plan),
        base.read_bytes(),
        base_override_snapshot=base_catalog.snapshot_id,
        provider=provider,
        session_path=session_path,
        output_override_path=reviewed,
        resume=False,
        input_fn=lambda _: next(responses),
        output=io.StringIO(),
    )
    final = root / "reviewed-plan"
    cli(
        [
            "plan",
            str(source),
            "--destination-root",
            str(destination),
            "--output-dir",
            str(final),
            "--cache-dir",
            str(cache),
            "--overrides",
            str(reviewed),
            "--review-session",
            str(session_path),
            "--offline",
            "--json",
        ]
    )
    manifest = json.loads((final / "plan.json").read_text(encoding="utf-8"))
    plan_hash = (final / "plan.sha256").read_text().strip()
    args = [
        "apply",
        str(final / "plan.json"),
        "--preflight",
        str(final / "preflight.json"),
        "--run-provenance",
        str(final / "run-provenance.json"),
        "--source-root",
        str(source),
        "--destination-root",
        str(destination),
        "--approve-plan-sha256",
        plan_hash,
        "--approve-review-session-sha256",
        session.sha256,
        "--approve-source-revision",
        str(revision.commit),
        "--json",
    ]
    check = json.loads(cli([*args, "--check-only"]))
    assert check["groups_total"] == 1 and check["members_moved"] == 0
    assert list(destination.iterdir()) == []
    mutation = [
        *args,
        "--journal",
        str(root / "journal.jsonl"),
        "--confirm-apply",
        check["confirmation_token"],
    ]
    result = json.loads(cli(mutation))
    assert result["members_moved"] == 2 and result["groups_completed"] == 1
    resumed = json.loads(cli([*mutation, "--resume"]))
    assert resumed["members_moved"] == 0
    for record in manifest["records"]:
        if record["status"] == "duplicate":
            continue
        assert not (source / record["source"]["relative_path"]).exists()
        assert (destination / record["destination"]).read_bytes() == b"synthetic-video"
    for companion in manifest["companions"]:
        assert not (source / companion["relative_path"]).exists()
        assert (
            destination / companion["destination"]
        ).read_bytes() == b"synthetic-subtitle"

    if duplicate:
        quarantine_workflow(root, args, loser)

    rollback = ["rollback", *args[1:], "--apply-journal", str(root / "journal.jsonl")]
    checked = json.loads(cli([*rollback, "--check-only"]))
    assert checked["groups_total"] == 1 and checked["members_restored"] == 0
    restore = [
        *rollback,
        "--rollback-journal",
        str(root / "rollback.jsonl"),
        "--confirm-rollback",
        checked["confirmation_token"],
    ]
    assert json.loads(cli(restore))["members_restored"] == 2
    assert json.loads(cli([*restore, "--resume"]))["members_restored"] == 0
    for record in manifest["records"]:
        if record["status"] == "duplicate":
            continue
        assert (
            source / record["source"]["relative_path"]
        ).read_bytes() == b"synthetic-video"
        assert not (destination / record["destination"]).exists()
    for companion in manifest["companions"]:
        assert (
            source / companion["relative_path"]
        ).read_bytes() == b"synthetic-subtitle"
        assert not (destination / companion["destination"]).exists()


def quarantine_workflow(root: Path, apply_args: list[str], loser: Path) -> None:
    shared = [apply_args[1]]
    for flag in (
        "--preflight",
        "--run-provenance",
        "--approve-plan-sha256",
        "--approve-review-session-sha256",
        "--approve-source-revision",
    ):
        shared.extend([flag, apply_args[apply_args.index(flag) + 1]])
    qplan = root / "quarantine-plan.json"
    created = json.loads(
        cli(["quarantine-plan", *shared, "--output", str(qplan), "--json"])
    )
    assert created["members"] == 1
    quarantine_root = root / "quarantine"
    quarantine_root.mkdir()
    for flag in ("--source-root", "--destination-root"):
        shared.extend([flag, apply_args[apply_args.index(flag) + 1]])
    shared.extend(
        [
            "--quarantine-root",
            str(quarantine_root),
            "--quarantine-plan",
            str(qplan),
            "--approve-quarantine-plan-sha256",
            created["quarantine_plan_sha256"],
            "--json",
        ]
    )
    checked = json.loads(cli(["quarantine", *shared, "--check-only"]))
    assert checked["members_moved"] == 0 and loser.read_bytes() == b"synthetic-loser"
    journal = root / "quarantine.jsonl"
    move = [
        "quarantine",
        *shared,
        "--journal",
        str(journal),
        "--confirm-quarantine",
        checked["confirmation_token"],
    ]
    assert json.loads(cli(move))["members_moved"] == 1
    assert not loser.exists()
    assert json.loads(cli([*move, "--resume"]))["members_moved"] == 0
    restore = ["quarantine-restore", *shared, "--quarantine-journal", str(journal)]
    checked = json.loads(cli([*restore, "--check-only"]))
    assert checked["members_restored"] == 0 and not loser.exists()
    restore += [
        "--restore-journal",
        str(root / "restore.jsonl"),
        "--confirm-restore",
        checked["confirmation_token"],
    ]
    assert json.loads(cli(restore))["members_restored"] == 1
    assert json.loads(cli([*restore, "--resume"]))["members_restored"] == 0
    assert loser.read_bytes() == b"synthetic-loser"
    assert not list(quarantine_root.rglob("*.mkv"))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="jmo-smoke-") as temporary:
        smoke(Path(temporary))
    with tempfile.TemporaryDirectory(prefix="jmo-quarantine-smoke-") as temporary:
        smoke(Path(temporary), duplicate=True)
    print(
        "Installed plan/review/apply/rollback/quarantine/restore/resume workflows passed"
    )
