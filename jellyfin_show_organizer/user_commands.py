"""Small, read-only commands that make the core workflow approachable."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import textwrap
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from . import __version__
from .planner import PlanningConfig
from .privacy import path_free_text
from .review_execution import execute_plan
from .review_session import (
    ReviewItemKind,
    ReviewItemState,
    ReviewSession,
    load_review_session,
)
from .summary_io import read_summary, summary_int

CONFIG_EXAMPLE = """schema_version = 1

[plan]
source_root = "../Shows"
destination_root = "../OrganizedShows"
output_dir = "./audit"
cache_dir = "./cache"
provider_mode = "auto"
max_path_length = 240
max_component_length = 180
"""

OVERRIDES_EXAMPLE = "schema_version = 4\n"


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
                    item.is_file()
                    and item.suffix.casefold()
                    in {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov"}
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


def run_init(
    shows_root: Path,
    destination_root: Path,
    state_dir: Path,
    *,
    provider_mode: str = "auto",
) -> int:
    """Create a local, non-overwriting JMO state directory and config."""

    source = shows_root.expanduser().resolve(strict=False)
    destination = destination_root.expanduser().resolve(strict=False)
    state = state_dir.expanduser().resolve(strict=False)
    if not source.is_dir() or source.is_symlink():
        print(f"Init failed: source must be a real existing directory: {source}")
        return 2
    if not destination.is_dir() or destination.is_symlink():
        print(
            f"Init failed: destination must be a real existing directory: {destination}"
        )
        return 2
    try:
        if os.stat(source).st_dev != os.stat(destination).st_dev:
            print("Init failed: source and destination must be on the same filesystem")
            return 2
    except OSError as exc:
        print(f"Init failed: cannot inspect source and destination filesystems: {exc}")
        return 2
    if not _outside(state, (source, destination)):
        print(
            "Init failed: state directory must be outside source and destination roots"
        )
        return 2
    if provider_mode not in {"auto", "online", "offline", "refresh"}:
        print(f"Init failed: unsupported provider mode: {provider_mode}")
        return 2
    if state.exists():
        print(f"Init failed: refusing to use existing state directory: {state}")
        return 2
    state.mkdir(parents=True)
    (state / "cache").mkdir()
    (state / "runs").mkdir()
    source_value = os.path.relpath(source, state).replace(os.sep, "/")
    destination_value = os.path.relpath(destination, state).replace(os.sep, "/")
    config = textwrap.dedent(
        f'''\
        schema_version = 1

        [plan]
        source_root = "{source_value}"
        destination_root = "{destination_value}"
        output_dir = "runs/initial"
        cache_dir = "cache"
        overrides = "base-overrides.toml"
        provider_mode = "{provider_mode}"
        max_path_length = 240
        max_component_length = 180
        '''
    )
    (state / "planning.toml").write_text(config, encoding="utf-8", newline="\n")
    (state / "base-overrides.toml").write_text(
        OVERRIDES_EXAMPLE, encoding="utf-8", newline="\n"
    )
    print(f"Initialized JMO state: {state}")
    print(
        f'Next step: jmo doctor "{source}" --destination-root "{destination}" --output-dir "{state / "runs" / "initial"}" --cache-dir "{state / "cache"}"'
    )
    print(f'Then:      jmo plan "{source}" --config "{state / "planning.toml"}"')
    return 0


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
    provider = ask("Provider mode (auto/online/offline/refresh)", "auto").casefold()
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
                item.is_file()
                and item.suffix.casefold()
                in {".mkv", ".mp4", ".avi", ".m4v", ".ts", ".mov"}
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
            from .providers import TvmazeProviderAdapter
            from .review_contract import load_review_contract
            from .review_execution import http_json_getter
            from .review_system import run_review_system
            from .tvmaze_cache import TvmazeCatalogCache

            plan_path = run_dir / "plan.json"
            base_override = state / "base-overrides.toml"
            session_path = state / "review-session.json"
            reviewed_override = state / "reviewed-overrides.toml"
            base_catalog = load_review_contract(base_override)
            cache = TvmazeCatalogCache(
                state / "cache",
                offline=provider == "offline",
                refresh=provider == "refresh",
            )
            session, _ = run_review_system(
                json.loads(plan_path.read_text(encoding="utf-8")),
                base_override.read_bytes(),
                base_override_snapshot=base_catalog.snapshot_id,
                provider=TvmazeProviderAdapter(cache, http_json_getter),
                session_path=session_path,
                output_override_path=reviewed_override,
                resume=session_path.is_file(),
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
            reviewed_outcome = execute_plan(
                PlanningConfig(
                    shows_root=source,
                    destination_root=destination,
                    output_dir=state / "runs" / "reviewed",
                    cache_dir=state / "cache",
                    overrides_path=reviewed_override,
                    offline=provider == "offline",
                    refresh=provider == "refresh",
                    provider_strategy="auto" if provider == "auto" else "tvmaze",
                ),
                review_session_path=session_path,
            )
            outcome = reviewed_outcome
            run_dir = state / "runs" / "reviewed"
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
        if ask("Run the read-only apply check now", "Y").casefold() in {"y", "yes"}:
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
                source_root, destination_root = validate_apply_roots(
                    source, destination
                )
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
                )
                if (
                    input_fn(
                        "Type APPLY to execute this exact reviewed plan now "
                        "(anything else keeps files untouched): "
                    ).strip()
                    == "APPLY"
                ):
                    journal_path = run_dir / "apply-journal.jsonl"
                    applied = execute_apply(
                        prepared,
                        source_root,
                        destination_root,
                        journal_path=journal_path,
                        check_only=False,
                        resume=False,
                    )
                    output.write(
                        "\nApply completed successfully.\n"
                        f"  Groups completed: {applied.groups_completed}\n"
                        f"  Files moved:      {applied.members_moved}\n"
                        f"  Journal:          {journal_path}\n"
                    )
                else:
                    output.write("\nApply not started. Your media remains untouched.\n")
            except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
                output.write(f"\nApply check stopped safely: {exc}\n")
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


def run_demo(output_dir: Path | None) -> int:
    """Create a disposable synthetic library and run a complete offline rehearsal."""

    if output_dir is None:
        output = Path.cwd() / f"jmo-demo-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    else:
        output = output_dir.expanduser().resolve(strict=False)
    if output.exists():
        print(f"Demo failed: refusing to overwrite existing directory: {output}")
        return 2
    shows = output / "Shows"
    organized = output / "OrganizedShows"
    state = output / "State"
    episode_dir = shows / "Example Show" / "Season 01"
    episode_dir.mkdir(parents=True)
    organized.mkdir(parents=True)
    (state / "cache").mkdir(parents=True)
    (state / "runs").mkdir()
    demo_overrides = (
        "schema_version = 4\n\n"
        "[[source_holds]]\n"
        'source = "Held Show/Season 01/Held Show - S01E01.mkv"\n'
        'reasons = ["synthetic demo hold for the guided review workflow"]\n'
    )
    (state / "base-overrides.toml").write_text(
        demo_overrides, encoding="utf-8", newline="\n"
    )
    (episode_dir / "Example Show - S01E01.mkv").write_bytes(b"synthetic demo video")
    duplicate_dir = episode_dir / "Release 2"
    duplicate_dir.mkdir()
    (duplicate_dir / "Example Show - S01E01.mkv").write_bytes(
        b"synthetic duplicate video"
    )
    held_dir = shows / "Held Show" / "Season 01"
    held_dir.mkdir(parents=True)
    (held_dir / "Held Show - S01E01.mkv").write_bytes(b"synthetic held video")
    (episode_dir / "Example Show - S01E01.en.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nDemo subtitle\n", encoding="utf-8"
    )
    (shows / "README.txt").write_text(
        "Synthetic JMO demo media. It is safe to delete this entire directory.\n",
        encoding="utf-8",
    )
    # Seed the normal TVMaze cache through its public cache API, using a local
    # deterministic getter. The subsequent planner run is genuinely offline.
    from .review_execution import execute_plan
    from .tvmaze_cache import TVMAZE_EPISODES_URL, TVMAZE_SEARCH_URL, TvmazeCatalogCache

    def demo_getter(url: str, params: Mapping[str, str] | None = None) -> object:
        if url == TVMAZE_SEARCH_URL:
            return [
                {
                    "score": 1.0,
                    "show": {
                        "id": 9001,
                        "name": "Example Show",
                        "premiered": "2020-01-01",
                    },
                }
            ]
        if url == TVMAZE_EPISODES_URL.format(tvmaze_id=9001):
            return [
                {
                    "id": 900101,
                    "season": 1,
                    "number": 1,
                    "name": "Pilot",
                    "airdate": "2020-01-01",
                    "type": "regular",
                }
            ]
        raise AssertionError(f"unexpected demo provider URL: {url}")

    cache = TvmazeCatalogCache(state / "cache")
    cache.search_show("Example Show", demo_getter)
    cache.episode_catalog(9001, demo_getter)
    demo_run = state / "runs" / "demo-run"
    outcome = execute_plan(
        PlanningConfig(
            shows_root=shows,
            destination_root=organized,
            output_dir=demo_run,
            cache_dir=state / "cache",
            overrides_path=state / "base-overrides.toml",
            offline=True,
        )
    )
    (output / "README.txt").write_text(
        f"""JMO synthetic demo workspace

This contains fabricated media only. It is safe to delete.

Run from the repository or installed environment:

jmo doctor "{shows}" --destination-root "{organized}" --output-dir "{state / "runs" / "initial"}" --cache-dir "{state / "cache"}"
jmo inspect "{demo_run}"

The demo plan is created from a local synthetic provider cache and never makes
a network request. It intentionally includes one duplicate and one held video
so you can exercise the review workflow before trying a real library. It is
safe to delete this entire directory.
""",
        encoding="utf-8",
    )
    print(f"Created disposable demo workspace: {output}")
    print(f"Demo audit bundle: {demo_run}")
    print(f"Plan status: {'ready' if outcome.preflight.ready else 'blocked'}")
    print(f"Read the instructions in: {output / 'README.txt'}")
    return 0


def _outside(path: Path, roots: tuple[Path, ...]) -> bool:
    resolved = path.expanduser().resolve(strict=False)
    return all(resolved != root and not resolved.is_relative_to(root) for root in roots)


def run_doctor(
    shows_root: Path,
    destination_root: Path,
    output_dir: Path,
    cache_dir: Path,
    *,
    json_output: bool = False,
) -> int:
    """Check common first-run conditions without scanning or changing media."""

    source = shows_root.expanduser().resolve(strict=False)
    destination = destination_root.expanduser().resolve(strict=False)
    output = output_dir.expanduser().resolve(strict=False)
    cache = cache_dir.expanduser().resolve(strict=False)
    checks: list[dict[str, object]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    check("source_exists", source.is_dir(), str(source))
    check(
        "source_is_not_link", source.is_dir() and not source.is_symlink(), str(source)
    )
    check("destination_exists", destination.is_dir(), str(destination))
    same_filesystem = False
    if source.is_dir() and destination.is_dir():
        try:
            same_filesystem = os.stat(source).st_dev == os.stat(destination).st_dev
        except OSError:
            same_filesystem = False
    check("same_filesystem", same_filesystem, "source and destination device match")
    roots = (source, destination)
    check("output_outside_media", _outside(output, roots), str(output))
    check("cache_outside_media", _outside(cache, roots), str(cache))
    if source.is_dir():
        try:
            entries = sum(1 for item in source.rglob("*") if item.is_file())
        except OSError:
            entries = -1
        check(
            "source_readable",
            entries >= 0,
            f"{entries if entries >= 0 else 'unreadable'} entries visible",
        )
    ready = all(bool(item["ok"]) for item in checks)
    payload = {"schema_version": 1, "ready": ready, "checks": checks}
    if json_output:
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    else:
        print("Doctor: READY" if ready else "Doctor: FIX REQUIRED")
        for item in checks:
            print(
                f"{'PASS' if item['ok'] else 'FAIL'}  {item['name']}: {item['detail']}"
            )
        if ready:
            print(
                "Next step: run jmo plan with these roots and an explicit state directory."
            )
    return 0 if ready else 2


def run_inspect(
    run_dir: Path, *, json_output: bool = False, redact_paths: bool = False
) -> int:
    """Summarize an audit bundle without exposing paths or requiring its source tree."""

    root = run_dir.expanduser().resolve(strict=True)
    summary_path = root / "summary.txt"
    if not summary_path.is_file():
        print(f"Inspect failed: {root} does not contain summary.txt")
        return 2
    values = read_summary(summary_path)
    result = {
        "schema_version": 1,
        "readiness_state": values.get("readiness_state", "not-evaluated"),
        "preflight_ready": values.get("preflight_ready", "unknown"),
        "records": summary_int(values, "records"),
        "matched": summary_int(values, "matched"),
        "extra": summary_int(values, "extra"),
        "duplicate": summary_int(values, "duplicate"),
        "held": summary_int(values, "held"),
        "suspicious": summary_int(values, "suspicious"),
        "unresolved": summary_int(values, "unresolved"),
        "remaining_total": summary_int(values, "remaining_total"),
        "plan_sha256": values.get("plan_sha256"),
    }
    if not redact_paths:
        result["run_dir"] = str(root)
    if json_output:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        if redact_paths:
            print("Run: [redacted]")
        else:
            print(f"Run: {root}")
        print(f"Status: {result['readiness_state']}")
        for key in (
            "records",
            "matched",
            "extra",
            "duplicate",
            "held",
            "suspicious",
            "unresolved",
            "remaining_total",
        ):
            print(f"{key.replace('_', ' ').title():18} {result[key]}")
        if result["readiness_state"] == "apply-ready":
            print("Next step: run jmo apply ... --check-only before any mutation.")
        elif result["duplicate"] or result["held"]:
            print("Next step: run jmo review against this run's plan.json.")
        else:
            print("Next step: inspect preflight.txt and unresolved.csv for blockers.")
    return 0


def run_review_status(
    session_path: Path,
    *,
    json_output: bool = False,
    run_dir: Path | None = None,
    export_path: Path | None = None,
) -> int:
    """Summarize review progress without exposing reviewed source paths."""

    path = session_path.expanduser().resolve(strict=True)
    session = load_review_session(path.read_bytes())
    states = Counter(item.state.value for item in session.items)
    kinds = Counter(item.kind.value for item in session.items)
    total_items = len(session.items)

    def percent(count: int) -> float:
        return round((count / total_items) * 100, 2) if total_items else 0.0

    answered = states[ReviewItemState.ANSWERED.value]
    deferred = states[ReviewItemState.DEFERRED.value]
    pending = states[ReviewItemState.PENDING.value]
    answered_percent = percent(answered)
    deferred_percent = percent(deferred)
    pending_percent = percent(pending)

    plan_summary: dict[str, object] | None = None
    if export_path is not None and run_dir is None:
        raise ValueError("review export requires --run-dir")

    manifest: Mapping[str, object] | None = None
    if run_dir is not None:
        root = run_dir.expanduser().resolve(strict=True)
        summary_path = root / "summary.txt"
        if not summary_path.is_file():
            raise ValueError(
                "review summary run directory does not contain summary.txt"
            )
        values = read_summary(summary_path)

        records = summary_int(values, "records")
        counts = {
            key: summary_int(values, key)
            for key in (
                "matched",
                "extra",
                "duplicate",
                "held",
                "suspicious",
                "unresolved",
                "companions",
                "associated_companions",
                "ignored_companions",
            )
        }
        untouched = sum(
            counts[key] for key in ("duplicate", "held", "suspicious", "unresolved")
        )
        plan_summary = {
            "records": records,
            "counts": counts,
            "percentages": {
                key: round((counts[key] / records) * 100, 2) if records else 0.0
                for key in (
                    "matched",
                    "extra",
                    "duplicate",
                    "held",
                    "suspicious",
                    "unresolved",
                )
            },
            "movable_videos": counts["matched"] + counts["extra"],
            "untouched_videos": untouched,
            "readiness_state": values.get("readiness_state", "not-evaluated"),
            "preflight_ready": values.get("preflight_ready", "unknown"),
        }
        if export_path is not None:
            manifest_path = root / "plan.json"
            if not manifest_path.is_file():
                raise ValueError(
                    "review export run directory does not contain plan.json"
                )
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("plan.json root must be an object")
            manifest = payload

    result = {
        "schema_version": 2,
        "session_sha256": session.sha256,
        "plan_sha256": session.plan_sha256,
        "items": total_items,
        "duplicates": kinds[ReviewItemKind.DUPLICATE.value],
        "held": kinds[ReviewItemKind.HELD.value],
        "answered": answered,
        "deferred": deferred,
        "pending": pending,
        "percentages": {
            "answered": answered_percent,
            "deferred": deferred_percent,
            "pending": pending_percent,
        },
        "approved_scope_items": len(session.approved_scope_refs),
        "complete": session.complete,
    }
    if plan_summary is not None:
        result["plan_summary"] = plan_summary
    if export_path is not None:
        assert manifest is not None
        _write_review_export(export_path, session, manifest, result)
    if json_output:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    print("Review status")
    print(f"Items:             {result['items']}")
    print(f"Answered:          {answered} ({answered_percent:.2f}%)")
    print(f"Deferred:           {deferred} ({deferred_percent:.2f}%)")
    print(f"Pending:            {pending} ({pending_percent:.2f}%)")
    if plan_summary is not None:
        plan_counts = plan_summary["counts"]
        assert isinstance(plan_counts, dict)
        print("Plan totals")
        for key in (
            "matched",
            "extra",
            "duplicate",
            "held",
            "suspicious",
            "unresolved",
        ):
            plan_percentages = plan_summary["percentages"]
            assert isinstance(plan_percentages, dict)
            print(
                f"  {key.title():16} {plan_counts[key]} ({plan_percentages[key]:.2f}%)"
            )
        print(f"  {'Companions':16} {plan_counts['companions']}")
        print(
            f"  Associated companions: {plan_counts['associated_companions']}\n"
            f"  Ignored companions:    {plan_counts['ignored_companions']}"
        )
        print(f"  Movable videos:   {plan_summary['movable_videos']}")
        print(f"  Untouched videos: {plan_summary['untouched_videos']}")
        print(
            "  Companions move only when associated with a movable video; ignored "
            "companions remain untouched."
        )
    print("Review categories")
    print(f"  Duplicates:      {result['duplicates']}")
    print(f"  Held:            {result['held']}")
    print(
        "Movement:          review decisions never move media; run a fresh plan and "
        "preflight before any apply."
    )
    print(f"Session SHA-256:   {result['session_sha256']}")
    if session.complete:
        print(
            "Next step: compile a fresh reviewed plan with jmo plan --review-session."
        )
    elif session.approved_partial:
        print(
            "Next step: continue the remaining review scope; partial review never authorizes apply."
        )
    else:
        print("Next step: resume the review session with jmo review --resume.")
    return 0


def _safe_review_value(value: object) -> object:
    if isinstance(value, str):
        return path_free_text(value)
    if isinstance(value, list):
        return [_safe_review_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_review_value(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            if name in {"source", "relative_path", "destination", "winner", "losers"}:
                continue
            if name == "candidates" and isinstance(item, list | tuple):
                result[name] = [
                    (
                        f"candidate-{index}-"
                        f"{hashlib.sha256(str(candidate).encode('utf-8')).hexdigest()[:12]}"
                        if isinstance(candidate, str)
                        else _safe_review_value(candidate)
                    )
                    for index, candidate in enumerate(item, start=1)
                ]
                continue
            result[name] = _safe_review_value(item)
        return result
    return value


def _review_record_by_source(
    manifest: Mapping[str, object],
) -> dict[str, Mapping[str, object]]:
    records = manifest.get("records")
    if not isinstance(records, list | tuple):
        raise ValueError("plan.json records must be an array")
    result: dict[str, Mapping[str, object]] = {}
    for value in records:
        if not isinstance(value, Mapping):
            continue
        source = value.get("source")
        if isinstance(source, Mapping):
            relative = source.get("relative_path")
            if isinstance(relative, str):
                result[relative.replace("\\", "/").casefold()] = value
    return result


def _write_review_export(
    output_path: Path,
    session: ReviewSession,
    manifest: Mapping[str, object],
    summary: Mapping[str, object],
) -> None:
    """Write an immutable, path-free evidence snapshot for offline review."""

    records = _review_record_by_source(manifest)
    items: list[dict[str, object]] = []
    for item in session.items:
        record: Mapping[str, object] | None = None
        if item.source is not None:
            record = records.get(item.source.replace("\\", "/").casefold())
        item_data: dict[str, object] = {
            "review_ref": item.review_ref,
            "kind": item.kind.value,
            "state": item.state.value,
            "show_key": path_free_text(item.show_key),
            "collision_class": (
                item.collision_class.value if item.collision_class is not None else None
            ),
            "candidate_count": len(item.candidates),
        }
        evidence: dict[str, object] = {}
        if record is not None:
            for key in (
                "parse",
                "show",
                "provider_episodes",
                "evidence",
                "reason",
                "duplicate",
            ):
                if key in record:
                    evidence[key] = _safe_review_value(record[key])
        item_data["evidence"] = evidence
        items.append(item_data)
    document = {
        "schema_version": 1,
        "session_sha256": session.sha256,
        "plan_sha256": session.plan_sha256,
        "complete": session.complete,
        "approved_scope_items": len(session.approved_scope_refs),
        "summary": _safe_review_value(summary),
        "items": sorted(items, key=lambda value: str(value["review_ref"])),
    }
    output = output_path.expanduser().resolve(strict=False)
    if output.exists():
        raise ValueError("review export output already exists")
    if not output.parent.is_dir():
        raise ValueError("review export output parent directory does not exist")
    output.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def run_report(run_dir: Path, output_dir: Path) -> int:
    """Write a path-free, shareable report bundle from one audit directory."""

    root = run_dir.expanduser().resolve(strict=True)
    summary_path = root / "summary.txt"
    if not summary_path.is_file():
        raise ValueError("audit bundle does not contain summary.txt")
    output = output_dir.expanduser().resolve(strict=False)
    if output.exists():
        raise ValueError("report output directory already exists")
    if not output.parent.is_dir():
        raise ValueError("report output parent directory does not exist")

    values = read_summary(summary_path)

    records = summary_int(values, "records")
    counts = {
        key: summary_int(values, key)
        for key in (
            "matched",
            "extra",
            "duplicate",
            "held",
            "suspicious",
            "unresolved",
            "companions",
            "associated_companions",
            "ignored_companions",
            "duplicate_companions",
        )
    }
    blockers = {key: counts[key] for key in ("suspicious", "unresolved") if counts[key]}
    untouched = (
        counts["duplicate"]
        + counts["held"]
        + counts["suspicious"]
        + counts["unresolved"]
    )
    percentages = {
        key: round((value / records) * 100, 2) if records else 0.0
        for key, value in counts.items()
        if key in {"matched", "extra", "duplicate", "held", "suspicious", "unresolved"}
    }
    companion_percentages = {
        key: round((counts[key] / counts["companions"]) * 100, 2)
        if counts["companions"]
        else 0.0
        for key in (
            "associated_companions",
            "ignored_companions",
            "duplicate_companions",
        )
    }
    payload = {
        "schema_version": 1,
        "tool_version": __version__,
        "platform": platform.platform(aliased=True),
        "python": platform.python_version(),
        "records": records,
        "counts": counts,
        "percentages": percentages,
        "companion_percentages": companion_percentages,
        "movable_videos": counts["matched"] + counts["extra"],
        "untouched_videos": untouched,
        "readiness_state": values.get("readiness_state", "not-evaluated"),
        "preflight_ready": values.get("preflight_ready", "unknown"),
        "failure_categories": blockers,
        "plan_sha256": values.get("plan_sha256"),
    }
    output.mkdir(parents=True)
    (output / "report.json").write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "JMO sanitized audit report",
        f"tool_version={__version__}",
        f"platform={payload['platform']}",
        f"python={payload['python']}",
        f"records={records}",
        f"matched={counts['matched']} ({percentages['matched']}%)",
        f"extra={counts['extra']} ({percentages['extra']}%)",
        f"duplicate={counts['duplicate']} ({percentages['duplicate']}%)",
        f"held={counts['held']} ({percentages['held']}%)",
        f"suspicious={counts['suspicious']} ({percentages['suspicious']}%)",
        f"unresolved={counts['unresolved']} ({percentages['unresolved']}%)",
        f"companions={counts['companions']}",
        f"associated_companions={counts['associated_companions']} ({companion_percentages['associated_companions']}%)",
        f"ignored_companions={counts['ignored_companions']} ({companion_percentages['ignored_companions']}%)",
        f"duplicate_companions={counts['duplicate_companions']} ({companion_percentages['duplicate_companions']}%)",
        f"movable_videos={counts['matched'] + counts['extra']}",
        f"untouched_videos={untouched}",
        f"readiness_state={values.get('readiness_state', 'not-evaluated')}",
        f"preflight_ready={values.get('preflight_ready', 'unknown')}",
        f"failure_categories={json.dumps(blockers, sort_keys=True)}",
        f"plan_sha256={values.get('plan_sha256')}",
        "private_paths=excluded",
        "provider_cache=excluded",
        "credentials=excluded",
        "approval_tokens=excluded",
        "journals=excluded",
    ]
    (output / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Sanitized report written: {output}")
    return 0


def write_example(path: Path | None, content: str) -> int:
    """Print or create a starter file, refusing accidental overwrites."""

    if path is None:
        print(content, end="")
        return 0
    target = path.expanduser().resolve(strict=False)
    if target.exists():
        print(f"Refusing to overwrite existing file: {target}")
        return 2
    if not target.parent.is_dir():
        print(f"Output directory does not exist: {target.parent}")
        return 2
    target.write_text(content, encoding="utf-8", newline="\n")
    print(f"Wrote example: {target}")
    return 0
