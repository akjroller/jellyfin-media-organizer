"""The guided first-run wizard: setup, doctor, paper plan, review, apply check.

The wizard never moves media. It stops after the read-only apply check and
prints the exact artifacts for an explicit jmo apply.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from .inventory import VIDEO_EXTENSIONS
from .planner import PlanningConfig
from .provider_aliases import TvmazeAliasProviderAdapter
from .providers import AutoProviderAdapter, MetadataProvider, TmdbProviderAdapter
from .review_execution import execute_plan, http_json_getter
from .tmdb_cache import TmdbCatalogCache, tmdb_http_getter
from .tvmaze_cache import TvmazeCatalogCache
from .user_commands import (
    run_doctor,
    run_init,
)


def _discover_library_candidates() -> list[Path]:
    """Return a small, predictable set of likely library directories.

    Discovery is intentionally shallow: JMO must never walk an entire drive or
    silently choose a media root.  The wizard only offers existing directories
    with at least one common video file below them.
    """

    candidates: list[Path] = []
    roots = [Path.cwd(), Path.home() / "Videos", Path.home() / "Desktop"]
    for root in roots:
        if not root.is_dir():
            continue
        children = [root] + [child for child in root.iterdir() if child.is_dir()]
        for child in children:
            if child.name.casefold() not in {"shows", "tv", "media", "library"}:
                continue
            try:
                has_video = any(
                    item.is_file() and item.suffix.casefold() in VIDEO_EXTENSIONS
                    for item in child.rglob("*")
                )
            except OSError:
                has_video = False
            resolved = child.resolve(strict=False)
            if has_video and resolved not in candidates:
                candidates.append(resolved)
    return sorted(candidates, key=lambda path: str(path).casefold())


def _write_wizard_summary(output, outcome, run_dir: Path) -> None:
    """Print the actionable part of a paper plan in non-technical language."""

    counts = Counter(record.status.value for record in outcome.plan.records)
    total = len(outcome.plan.records)
    movable = counts["matched"] + counts["extra"]
    output.write(
        "\nPaper plan summary\n"
        f"  Found {total} video records.\n"
        f"  Safe to organize now: {movable} (matched {counts['matched']}, extras {counts['extra']})\n"
        f"  Duplicate releases kept in place: {counts['duplicate']}\n"
        f"  Held files kept in place: {counts['held']}\n"
        f"  Suspicious files needing review: {counts['suspicious']}\n"
        f"  Unresolved files needing review: {counts['unresolved']}\n"
        f"  Audit bundle: {run_dir}\n"
        "  Nothing has been moved, deleted, overwritten, or quarantined.\n"
    )


def _build_review_provider(cache_path: Path, mode: str) -> MetadataProvider:
    """Build the same provider stack used by planning for guided review."""

    cache = TvmazeCatalogCache(
        cache_path,
        offline=mode == "offline",
        refresh=mode == "refresh",
    )
    tvmaze_provider = TvmazeAliasProviderAdapter(cache, http_json_getter)
    token = os.environ.get("JMO_TMDB_ACCESS_TOKEN", "").strip()
    if mode != "auto" or not token:
        return tvmaze_provider
    return AutoProviderAdapter(
        tvmaze_provider,
        TmdbProviderAdapter(
            TmdbCatalogCache(
                cache_path / "tmdb",
                offline=mode == "offline",
                refresh=mode == "refresh",
            ),
            tmdb_http_getter(token),
        ),
    )


def run_wizard(*, input_fn=input, output=None) -> int:
    """Guide a first paper run without requiring users to assemble CLI flags."""

    if output is None:
        output = sys.stdout

    def ask(label: str, default: str | None = None) -> str:
        suffix = f" [{default}]" if default else ""
        value = input_fn(f"{label}{suffix}: ").strip()
        return value or (default or "")

    output.write(
        "Jellyfin Media Organizer guided setup\n"
        "This wizard only creates state, checks paths, and prepares a paper plan.\n"
        "It never moves, deletes, quarantines, or overwrites media.\n\n"
    )
    candidates = _discover_library_candidates()
    source_default: str | None = None
    if len(candidates) == 1:
        source_default = str(candidates[0])
        output.write(
            f"Detected one likely Shows directory: {source_default}\n"
            "You can press Enter to use it, or type a different path.\n"
        )
    elif candidates:
        output.write("Likely Shows directories (choose explicitly):\n")
        for candidate in candidates:
            output.write(f"  - {candidate}\n")
    source_text = ask("Shows/library directory", source_default)
    if not source_text:
        output.write("Wizard cancelled: a Shows/library directory is required.\n")
        return 2
    source = Path(source_text).expanduser().resolve(strict=False)
    destination_default = str(source.parent / "OrganizedShows")
    destination_text = ask("Destination directory", destination_default)
    state_default = str(source.parent / f"{source.name}-JMO-State")
    state_text = ask("JMO state directory", state_default)
    provider = ask(
        "Provider mode (auto uses TMDb when configured; online/offline/refresh are TVMaze modes)",
        "auto",
    ).casefold()
    if provider not in {"auto", "online", "offline", "refresh"}:
        output.write(
            "Wizard cancelled: provider mode must be auto, online, offline, or refresh.\n"
        )
        return 2
    destination = Path(destination_text).expanduser().resolve(strict=False)
    state = Path(state_text).expanduser().resolve(strict=False)
    output.write(
        "\nReview these paths before continuing:\n"
        f"  Source:      {source}\n"
        f"  Destination: {destination}\n"
        f"  State:       {state}\n"
        f"  Provider:    {provider}\n"
    )
    if ask("Create this setup", "Y").casefold() not in {"y", "yes"}:
        output.write("Wizard cancelled before changing anything.\n")
        return 0

    # A second invocation should be a safe resume point, not a confusing
    # "state already exists" failure and never an opportunity to overwrite an
    # audit bundle.  The state files are deliberately checked by name here;
    # their contents are still validated by the normal plan/review commands.
    existing_setup = (
        state.is_dir()
        and (state / "planning.toml").is_file()
        and (state / "base-overrides.toml").is_file()
        and (state / "cache").is_dir()
        and (state / "runs").is_dir()
    )
    resume_existing = False
    if existing_setup:
        output.write(
            "\nExisting JMO setup detected; preserving it and its audit bundles.\n"
            f"  Config:   {state / 'planning.toml'}\n"
            f"  Overrides: {state / 'base-overrides.toml'}\n"
            f"  Runs:     {state / 'runs'}\n"
        )
        if ask("Resume with a fresh read-only run now", "Y").casefold() not in {
            "y",
            "yes",
        }:
            output.write(
                "Setup preserved. Nothing touched your media.\n"
                f'Run later with: jmo "{source}"\n'
            )
            return 0
        try:
            has_media = any(
                item.is_file() and item.suffix.casefold() in VIDEO_EXTENSIONS
                for item in source.rglob("*")
            )
        except OSError:
            has_media = False
        if not has_media:
            output.write(
                "No supported video files were found yet. The existing setup was "
                "preserved; add media and run the printed plan command when ready.\n"
                f'  jmo plan "{source}" --config "{state / "planning.toml"}"\n'
            )
            return 0
        resume_existing = True
        run_dir = state / "runs" / f"wizard-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    else:
        result = run_init(source, destination, state, provider_mode=provider)
        if result != 0:
            return result
        run_dir = state / "runs" / "initial"
    if not resume_existing and ask(
        "Run the read-only safety check and paper plan now", "Y"
    ).casefold() not in {
        "y",
        "yes",
    }:
        output.write(
            "\nSetup is ready. The printed doctor and plan commands are available when "
            "you are ready. Nothing touched your media.\n"
        )
        return 0
    if resume_existing:
        output.write(f'  jmo plan "{source}" --config "{state / "planning.toml"}"\n')

    doctor_result = run_doctor(
        source,
        destination,
        run_dir,
        state / "cache",
    )
    if doctor_result != 0:
        output.write("\nWizard stopped because the safety check was not ready.\n")
        return doctor_result
    try:
        outcome = execute_plan(
            PlanningConfig(
                shows_root=source,
                destination_root=destination,
                output_dir=run_dir,
                cache_dir=state / "cache",
                overrides_path=state / "base-overrides.toml",
                offline=provider == "offline",
                refresh=provider == "refresh",
                provider_strategy="auto" if provider == "auto" else "tvmaze",
            )
        )
    except (OSError, UnicodeError, ValueError) as exc:
        output.write(f"\nPaper plan stopped safely: {exc}\n")
        return 2
    status = "ready" if outcome.preflight.ready else "blocked"
    _write_wizard_summary(output, outcome, run_dir)
    duplicate_count = sum(r.status.value == "duplicate" for r in outcome.plan.records)
    held_count = sum(r.status.value == "held" for r in outcome.plan.records)
    review_session_sha: str | None = None
    review_needed = duplicate_count + held_count
    start_review = review_needed == 0 or ask(
        f"Start guided review for {review_needed} duplicate/held items", "Y"
    ).casefold() in {"y", "yes"}
    if start_review:
        try:
            from .review_contract import load_review_contract
            from .review_system import run_review_system

            plan_path = run_dir / "plan.json"
            base_override = state / "base-overrides.toml"
            session_path = run_dir / "review-session.json"
            reviewed_override = run_dir / "reviewed-overrides.toml"
            resume_session = False
            if resume_existing:
                plan_sha = (run_dir / "plan.sha256").read_text(encoding="ascii").strip()
                prior_sessions = sorted(
                    state.joinpath("runs").glob("*/review-session.json"),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                for candidate in prior_sessions:
                    if candidate == session_path:
                        continue
                    try:
                        candidate_payload = json.loads(
                            candidate.read_text(encoding="utf-8")
                        )
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        continue
                    if candidate_payload.get("plan_sha256") == plan_sha:
                        session_path = candidate
                        resume_session = True
                        break
            base_catalog = load_review_contract(base_override)
            review_provider = _build_review_provider(state / "cache", provider)
            session, _ = run_review_system(
                json.loads(plan_path.read_text(encoding="utf-8")),
                base_override.read_bytes(),
                base_override_snapshot=base_catalog.snapshot_id,
                provider=review_provider,
                session_path=session_path,
                output_override_path=reviewed_override,
                resume=resume_session,
                input_fn=input_fn,
                output=output,
            )
            if not session.complete:
                output.write(
                    "Review is resumable. Re-run the wizard or use the printed "
                    f"session at {session_path}.\n"
                )
                return 2
            review_session_sha = session.sha256
            reviewed_run_dir = (
                state
                / "runs"
                / (f"reviewed-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
            )
            reviewed_suffix = 1
            while reviewed_run_dir.exists():
                reviewed_run_dir = (
                    state
                    / "runs"
                    / (
                        f"reviewed-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                        f"-{reviewed_suffix}"
                    )
                )
                reviewed_suffix += 1
            reviewed_outcome = execute_plan(
                PlanningConfig(
                    shows_root=source,
                    destination_root=destination,
                    output_dir=reviewed_run_dir,
                    cache_dir=state / "cache",
                    overrides_path=reviewed_override,
                    offline=provider == "offline",
                    refresh=provider == "refresh",
                    provider_strategy="auto" if provider == "auto" else "tvmaze",
                ),
                review_session_path=session_path,
            )
            outcome = reviewed_outcome
            run_dir = reviewed_run_dir
            status = "ready" if outcome.preflight.ready else "blocked"
            output.write(
                f"\nReviewed plan complete: {status}\n"
                f"  Audit bundle: {run_dir}\n"
                "  No media was moved.\n"
            )
        except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
            output.write(f"\nGuided review stopped safely: {exc}\n")
            return 2
    if outcome.preflight.ready and review_session_sha is not None:
        try:
            from .apply_execution import (
                approval_token,
                execute_apply,
                prepare_apply,
                total_moving_members,
            )
            from .apply_validation import validate_apply_roots
            from .run_provenance import detect_source_revision

            plan_path = run_dir / "plan.json"
            preflight_path = run_dir / "preflight.json"
            provenance_path = run_dir / "run-provenance.json"
            plan_sha = (run_dir / "plan.sha256").read_text(encoding="ascii").strip()
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            recorded_revision = provenance["source_revision"]["commit"]
            source_root, destination_root = validate_apply_roots(source, destination)
            current_revision = detect_source_revision()
            if (
                current_revision.state != "git"
                or current_revision.dirty
                or current_revision.commit != recorded_revision
            ):
                raise ValueError(
                    "apply check requires the same clean Git revision recorded by the plan"
                )
            prepared = prepare_apply(
                plan_path,
                preflight_path,
                provenance_path,
                approved_plan_sha256=plan_sha,
                approved_review_session_sha256=review_session_sha,
                approved_source_revision=recorded_revision,
                separate_roots=source_root != destination_root,
            )
            apply_result = execute_apply(
                prepared,
                source_root,
                destination_root,
                journal_path=None,
                check_only=True,
                resume=False,
            )
            output.write(
                "\nApply check passed. Nothing moved.\n"
                f"  Operation groups: {apply_result.groups_total}\n"
                f"  Files to move:    {total_moving_members(prepared)}\n"
                f"  Confirmation token: {approval_token(prepared, source_root, destination_root)}\n"
                "The wizard stops at the read-only boundary. Use the explicit "
                "`jmo apply` command only after inspecting this exact bundle.\n"
            )
        except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
            output.write(f"\nApply check stopped safely: {exc}\n")
            return 2
    output.write(
        f"\nPaper plan complete: {status}\n"
        f"  Audit bundle: {run_dir}\n"
        f"  Records:       {len(outcome.plan.records)}\n"
        f"  Matched:       {sum(r.status.value == 'matched' for r in outcome.plan.records)}\n"
        f"  Extras:        {sum(r.status.value == 'extra' for r in outcome.plan.records)}\n"
        f"  Held:          {sum(r.status.value == 'held' for r in outcome.plan.records)}\n"
        f"  Duplicates:    {sum(r.status.value == 'duplicate' for r in outcome.plan.records)}\n"
        f"  Suspicious:    {sum(r.status.value == 'suspicious' for r in outcome.plan.records)}\n"
        f"  Unresolved:    {sum(r.status.value == 'unresolved' for r in outcome.plan.records)}\n"
        "\nReview the audit bundle before any future apply step. No media was moved.\n"
        f'Next: jmo inspect "{run_dir}"\n'
    )
    return 0
