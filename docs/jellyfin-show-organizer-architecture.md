# Jellyfin Show Organizer architecture and provenance

## Provenance

This fork is based on upstream `jkwill87/mnamer` `main` at commit
`4703dfea2d851ef55c4a62af0a1e1b80581fdc0c` ("Adds v3 dev note").

Production evidence that motivated the organizer work came from mnamer 2.7.2
plus local patches. Those local patches are evidence only: useful behavior must
be reimplemented in repository-owned code with regression tests rather than
copied blindly or treated as authoritative mappings.

The upstream MIT license and Git history remain intact.

## Subsystem boundary

`jellyfin_show_organizer` is a standalone Python subsystem that lives beside
the existing `mnamer` package. The existing `mnamer` CLI and its relocation
behavior are intentionally unchanged.

The subsystem has its own `organizer` console entry point. During the bootstrap
milestone, `plan` is the only subcommand. It is deliberately a scaffold: it
does not scan, inventory, query providers, create destination directories,
move, rename, copy, or delete media.

Future organizer code must keep planning separate from mutation:

1. scan an explicitly authorized Shows root read-only,
2. parse and resolve against versioned models and cached provider evidence,
3. build one immutable plan,
4. audit and preflight that whole plan,
5. require separate human approval before any future apply implementation.

No apply command exists at this stage.

## Safety boundary

Repository code and tests must never access `D:\\Jellyfin` or any other real
media library. Tests use temporary directories and synthetic zero-byte files
only. Movies are out of scope for this subsystem.

Real inventory exports, audit CSVs/logs, provider caches, manifests, generated
plan files, copied library roots, and common video files are ignored by Git so
local evidence cannot be committed accidentally.

## Publishing safety

The inherited PyPI and Docker publishing workflows remain in the repository
for upstream provenance, but their publishing jobs are owner-guarded to
`jkwill87/mnamer`. The caller jobs in `push.yml` carry the same guard.

Therefore the `akjroller/mnamer` fork can run lint and tests while inherited
publishing jobs are skipped, including manual dispatch of the reusable
publishing workflows.

## Development commands

A clean clone should use the repository lockfile:

```bash
uv sync --frozen --dev
uv run pytest -m local
uv run ruff check mnamer jellyfin_show_organizer tests
uv run ruff format --check mnamer jellyfin_show_organizer tests
uv run mypy mnamer jellyfin_show_organizer tests
uv run organizer plan --help
```

The organizer command can also be run directly from repository code:

```bash
uv run python -m jellyfin_show_organizer plan --help
```
