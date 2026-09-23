"""Small, read-only commands that make the core workflow approachable."""

from __future__ import annotations

import json
import os
import platform
import textwrap
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from . import __version__
from .planner import PlanningConfig
from .review_session import ReviewItemKind, ReviewItemState, load_review_session

CONFIG_EXAMPLE = """schema_version = 1

[plan]
destination_root = "../OrganizedShows"
output_dir = "./audit"
cache_dir = "./cache"
provider_mode = "online"
max_path_length = 240
max_component_length = 180
"""

OVERRIDES_EXAMPLE = "schema_version = 4\n"


def run_init(
    shows_root: Path,
    destination_root: Path,
    state_dir: Path,
    *,
    provider_mode: str = "online",
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
    if provider_mode not in {"online", "offline", "refresh"}:
        print(f"Init failed: unsupported provider mode: {provider_mode}")
        return 2
    if state.exists():
        print(f"Init failed: refusing to use existing state directory: {state}")
        return 2
    state.mkdir(parents=True)
    (state / "cache").mkdir()
    (state / "runs").mkdir()
    destination_value = os.path.relpath(destination, state).replace(os.sep, "/")
    config = textwrap.dedent(
        f'''\
        schema_version = 1

        [plan]
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
    (state / "base-overrides.toml").write_text(
        OVERRIDES_EXAMPLE, encoding="utf-8", newline="\n"
    )
    (episode_dir / "Example Show - S01E01.mkv").write_bytes(b"synthetic demo video")
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
    if not outcome.preflight.ready:
        print("Demo failed: seeded offline plan was not preflight-ready")
        return 2
    (output / "README.txt").write_text(
        f"""JMO synthetic demo workspace

This contains fabricated media only. It is safe to delete.

Run from the repository or installed environment:

jmo doctor "{shows}" --destination-root "{organized}" --output-dir "{state / "runs" / "initial"}" --cache-dir "{state / "cache"}"
jmo inspect "{demo_run}"

The demo plan is created from a local synthetic provider cache and never makes
a network request. It is safe to delete this entire directory.
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
    values: dict[str, str] = {}
    for line in summary_path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    result = {
        "schema_version": 1,
        "readiness_state": values.get("readiness_state", "not-evaluated"),
        "preflight_ready": values.get("preflight_ready", "unknown"),
        "records": int(values.get("records", "0")),
        "matched": int(values.get("matched", "0")),
        "extra": int(values.get("extra", "0")),
        "duplicate": int(values.get("duplicate", "0")),
        "held": int(values.get("held", "0")),
        "suspicious": int(values.get("suspicious", "0")),
        "unresolved": int(values.get("unresolved", "0")),
        "remaining_total": int(values.get("remaining_total", "0")),
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


def run_review_status(session_path: Path, *, json_output: bool = False) -> int:
    """Summarize review progress without exposing reviewed source paths."""

    path = session_path.expanduser().resolve(strict=True)
    session = load_review_session(path.read_bytes())
    states = Counter(item.state.value for item in session.items)
    kinds = Counter(item.kind.value for item in session.items)
    result = {
        "schema_version": 1,
        "session_sha256": session.sha256,
        "plan_sha256": session.plan_sha256,
        "items": len(session.items),
        "duplicates": kinds[ReviewItemKind.DUPLICATE.value],
        "held": kinds[ReviewItemKind.HELD.value],
        "answered": states[ReviewItemState.ANSWERED.value],
        "deferred": states[ReviewItemState.DEFERRED.value],
        "pending": states[ReviewItemState.PENDING.value],
        "approved_scope_items": len(session.approved_scope_refs),
        "complete": session.complete,
    }
    if json_output:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    print("Review status")
    print(f"Items:             {result['items']}")
    print(f"Duplicates:        {result['duplicates']}")
    print(f"Held:              {result['held']}")
    print(f"Answered:          {result['answered']}")
    print(f"Deferred:          {result['deferred']}")
    print(f"Pending:           {result['pending']}")
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

    values: dict[str, str] = {}
    for line in summary_path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value

    def number(key: str) -> int:
        try:
            return int(values.get(key, "0"))
        except ValueError as exc:
            raise ValueError(f"audit summary has invalid {key}") from exc

    records = number("records")
    counts = {
        key: number(key)
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
    payload = {
        "schema_version": 1,
        "tool_version": __version__,
        "platform": platform.platform(aliased=True),
        "python": platform.python_version(),
        "records": records,
        "counts": counts,
        "percentages": percentages,
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
