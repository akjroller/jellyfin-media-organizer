# Jellyfin Show Organizer

A plan-first Python CLI for organizing TV-show media into Jellyfin-friendly layouts.

The project is intentionally conservative: planning, parsing, inventory, reconciliation, provider-cache handling, and manifest contracts are developed separately from filesystem mutation. There is currently **no apply command**, so the tool cannot move, rename, copy, overwrite, or delete media.

## Current capabilities

- deterministic parsing of common season/episode and absolute-number filename patterns;
- read-only inventory scanning for `.mkv`, `.mp4`, and `.avi` files;
- explicit handling of samples, unreadable entries, and blocked links;
- deterministic inventory reconciliation;
- versioned organizer plan models and JSON schema validation;
- data-driven aliases and numbering policies;
- persistent TVMaze cache primitives designed for deterministic/offline testing;
- synthetic regression fixtures for ambiguous and adversarial filename cases.

The `organizer plan` command is still a scaffold while the planning pipeline is assembled.

## Requirements

- Python 3.12+

The runtime package currently has no third-party dependencies.

## Install

```bash
python -m pip install .
organizer plan --help
```

For development:

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check jellyfin_show_organizer tests
python -m ruff format --check jellyfin_show_organizer tests
```

You can also run the CLI without the console-script wrapper:

```bash
python -m jellyfin_show_organizer plan --help
```

## Safety boundary

This project is **Shows-only**. Do not point it at a Movies directory, a mixed media root, or a parent directory containing unrelated media.

Repository examples and tests use synthetic paths and zero-byte fixtures. Real library inventories, provider caches, manifests, reports, media files, machine-specific paths, hostnames, usernames, and other environment-specific data should remain local and untracked.

## Documentation

- [Architecture](docs/jellyfin-show-organizer-architecture.md)
- [Windows and operational runbook](docs/jellyfin-show-organizer-runbook.md)

## Project layout

```text
jellyfin_show_organizer/   application package
  data/                    versioned schemas and synthetic override data
tests/
  fixtures/                synthetic deterministic fixtures
  local/                   offline test suite
docs/                      architecture and operating guidance
```

## License

MIT. See `LICENSE.txt` for the retained license notice covering inherited portions of the codebase.
