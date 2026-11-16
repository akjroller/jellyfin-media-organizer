"""Small, read-only commands that make the core workflow approachable."""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from .planner import PlanningConfig
from .review_execution import execute_plan
from .tvmaze_cache import TvmazeCatalogCache

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
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        print(f"Init failed: destination must be a real directory: {destination}")
        return 2
    if not destination.exists() and not destination.parent.is_dir():
        print(
            "Init failed: destination does not exist and its parent directory is "
            f"missing: {destination.parent}"
        )
        return 2
    try:
        destination_device = os.stat(
            destination if destination.exists() else destination.parent
        ).st_dev
        if os.stat(source).st_dev != destination_device:
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
    if not destination.exists():
        destination.mkdir()
    try:
        source_value = os.path.relpath(source, state).replace(os.sep, "/")
    except ValueError:
        source_value = str(source).replace("\\", "/")
    try:
        destination_value = os.path.relpath(destination, state).replace(os.sep, "/")
    except ValueError:
        destination_value = str(destination).replace("\\", "/")
    config = textwrap.dedent(
        f"""\
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
        """
    )
    state.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{state.name}.", dir=state.parent))
    (staging / "cache").mkdir()
    (staging / "runs").mkdir()
    (staging / "planning.toml").write_text(config, encoding="utf-8", newline="\n")
    (staging / "base-overrides.toml").write_text(
        OVERRIDES_EXAMPLE, encoding="utf-8", newline="\n"
    )
    staging.replace(state)
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
    from .tvmaze_cache import TVMAZE_EPISODES_URL, TVMAZE_SEARCH_URL

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
