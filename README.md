# Jellyfin Media Organizer (JMO)

A plan-first Python CLI for organizing media into Jellyfin-friendly layouts. The current implementation is deliberately focused on **TV shows** while the planning and safety model is built out.

> [!IMPORTANT]
> JMO is currently **plan-only**. There is no `apply` command, so the released code cannot move, rename, copy, overwrite, or delete media.

## Current status

The repository contains read-only inventory, parsing, reconciliation, provider-cache, show-resolution, schema, and safety primitives that are being assembled into one end-to-end planner. The public `jmo plan` / `organizer plan` command is still a scaffold and does not yet accept or scan a media root.

Current capabilities include deterministic parsing of common TV episode naming patterns, read-only inventory of supported video files, explicit sample/link/error handling, data-driven aliases and numbering policies, persistent TVMaze cache primitives, fail-closed show resolution, versioned plan/schema models, and synthetic regression coverage.

The active scope is **Shows-only**. Do not point future planning commands at a Movies directory, mixed media root, or a parent directory containing unrelated media.

## Requirements

- Python 3.12 or newer.
- Linux and Windows are exercised in CI. The current CI matrix includes Python 3.12 on Linux/Windows and a newer Python on Linux.
- The runtime package currently has no third-party dependencies.

## Install from source

JMO is not documented as a package-registry install yet. Install from a checked-out source tree until the release-packaging work is complete.

### POSIX (Linux/macOS shell)

```bash
git clone https://github.com/akjroller/jellyfin-media-organizer.git
cd jellyfin-media-organizer
python3 -m venv .venv
./.venv/bin/python -m pip install .
./.venv/bin/python -m jellyfin_show_organizer plan --help
```

### Windows PowerShell

PowerShell activation is optional. Invoke the virtual environment's Python executable directly:

```powershell
git clone https://github.com/akjroller/jellyfin-media-organizer.git
cd jellyfin-media-organizer
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m jellyfin_show_organizer plan --help
```

`Activate.ps1` is not required, and the project does not require changing PowerShell execution policy.

After installation, `jmo` is the preferred console command. `organizer` remains a compatibility alias:

```text
jmo plan --help
organizer plan --help
```

## Development setup

Install the development extras, then run the same gates used by CI:

```bash
python -m pip install -e ".[dev]"
python -m ruff check jellyfin_show_organizer tests tools
python -m ruff format --check jellyfin_show_organizer tests tools
python -m mypy jellyfin_show_organizer tests
python -m pytest
python tools/check_ci_constraints.py
python tools/check_repository_safety.py
```

Provider-facing regression tests are designed to run from synthetic checked-in fixtures rather than live calls.

## Safety and privacy boundary

Planning code must fail closed when identity or filesystem evidence is ambiguous. Generating a plan never implies approval to mutate media, and future mutation work is gated behind separate plan validation and explicit approval milestones.

This is a public repository. Keep real library data and contributor environment details out of tracked files, examples, issue reproductions, and fixtures. That includes real media, inventories, reports, caches, manifests, local override catalogs, absolute contributor paths, usernames, hostnames, network/share details, and production logs. Reduce real-world bugs to fabricated synthetic reproductions before committing them.

Repository tests and documentation use fabricated names such as `ExampleMedia` and temporary test paths rather than one contributor's filesystem layout.

## Planned lifecycle

The intended product lifecycle is:

```text
install -> configure -> scan/plan -> review unresolved -> local overrides
       -> audit/preflight -> explicit approval -> apply (future)
       -> verification -> recovery
```

Only the plan-side building blocks exist today. Audit/preflight, final CLI wiring, approval, and apply/recovery are separate milestones and must not be inferred from the presence of internal models.

## Documentation

- [Architecture](docs/jellyfin-show-organizer-architecture.md)
- [Installation, operation, safety, and contributor runbook](docs/jellyfin-show-organizer-runbook.md)
- [Upstream acknowledgments](ACKNOWLEDGMENTS.md)

## Project layout

```text
jellyfin_show_organizer/   core application package
  data/                    versioned schemas and synthetic override data
tests/
  fixtures/                synthetic deterministic fixtures
  local/                   offline test suite
docs/                      architecture and operating guidance
tools/                     CI/repository-safety helpers
```

## Project history and credit

JMO began as a fork of [`jkwill87/mnamer`](https://github.com/jkwill87/mnamer), created and maintained by Jessy Williams. That MIT-licensed project provided the original media-organizing groundwork from which this project started.

JMO has since diverged into its own Jellyfin-focused, plan-first design. The upstream project and its maintainers are not responsible for JMO's current behavior or support. See [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) for the retained attribution.

## License

MIT. The original upstream copyright and permission notice is retained in `LICENSE.txt`.
