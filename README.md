# Jellyfin Media Organizer (JMO)

A plan-first Python CLI for organizing media into Jellyfin-friendly layouts. The current implementation is deliberately focused on **TV shows** while the planning and safety model is built out.

JMO is intentionally conservative: planning, parsing, inventory, reconciliation, provider-cache handling, and manifest contracts are developed separately from filesystem mutation. There is currently **no apply command**, so the tool cannot move, rename, copy, overwrite, or delete media.

## Current capabilities

- deterministic parsing of common season/episode and absolute-number filename patterns;
- read-only inventory scanning for `.mkv`, `.mp4`, and `.avi` files;
- explicit handling of samples, unreadable entries, and blocked links;
- deterministic inventory reconciliation;
- versioned organizer plan models and JSON schema validation;
- data-driven aliases and numbering policies;
- persistent TVMaze cache primitives designed for deterministic/offline testing;
- canonical TVMaze show resolution with fail-closed ambiguity handling;
- synthetic regression fixtures for ambiguous and adversarial filename cases.

The `jmo plan` command is still a scaffold while the complete planning pipeline is assembled. Running the scaffold exits nonzero and reports that planning is not implemented; it does not create a plan, report, cache, destination directory, or media output. `jmo plan --help` remains available for inspecting the current command contract.

## Requirements

- Python 3.12+
- CI tests Linux on Python 3.12 and 3.14, plus Windows on Python 3.12.
- Project metadata advertises Python 3.12, 3.13, and 3.14 support.

The runtime package currently has no third-party dependencies.

## Repository identity

The standalone project is maintained in the `jellyfin-media-organizer` repository, matching the installable project name. This repository name is the intentional long-term identity for JMO rather than a temporary fork-era name.

Maintained project metadata points to this repository and its issue tracker. Packaging or documentation changes should not reintroduce obsolete upstream package/repository URLs as JMO's own project metadata; historical upstream credit remains in the acknowledgments and license sections below.

## Install

```bash
python -m pip install .
jmo --version
jmo plan --help
```

The historical `organizer` command remains available as a compatibility alias:

```bash
organizer --version
organizer plan --help
```

For development:

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check jellyfin_show_organizer tests
python -m ruff format --check jellyfin_show_organizer tests
```

You can also run the package directly:

```bash
python -m jellyfin_show_organizer --version
python -m jellyfin_show_organizer plan --help
```

## Safety boundary

The current implementation is **Shows-only**. Do not point it at a Movies directory, a mixed media root, or a parent directory containing unrelated media.

Repository examples and tests use synthetic paths and zero-byte fixtures. Real library inventories, provider caches, manifests, reports, media files, machine-specific paths, hostnames, usernames, and other environment-specific data should remain local and untracked.

## Documentation

- [Architecture](docs/jellyfin-show-organizer-architecture.md)
- [Windows and operational runbook](docs/jellyfin-show-organizer-runbook.md)
- [Release and versioning policy](docs/releasing.md)
- [Upstream acknowledgments](ACKNOWLEDGMENTS.md)

## Project layout

```text
jellyfin_show_organizer/   core application package
  data/                    versioned schemas and synthetic override data
tests/
  fixtures/                synthetic deterministic fixtures
  local/                   offline test suite
docs/                      architecture and operating guidance
```

## Releases

JMO uses Semantic Versioning. Pull-request CI builds and verifies both wheel and source-distribution installs in isolated environments. Release artifacts are produced only by an explicit release workflow or a matching version tag; the repository does not automatically publish packages to a package registry.

Current releases are **plan-only**. See the [release policy](docs/releasing.md) for the version source of truth, supported runtime matrix, tag rules, artifact verification process, and privacy boundary.

## Project history and credit

JMO began as a fork of [`jkwill87/mnamer`](https://github.com/jkwill87/mnamer), created and maintained by Jessy Williams. That MIT-licensed project provided the original media-organizing groundwork from which this project started.

JMO has since diverged into its own Jellyfin-focused, plan-first design. The upstream project and its maintainers are not responsible for JMO's current behavior or support. See [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) for the retained attribution.

## License

MIT. The original upstream copyright and permission notice is retained in `LICENSE.txt`.