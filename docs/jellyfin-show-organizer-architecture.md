# Jellyfin Show Organizer architecture and provenance

## Provenance

This fork is based on upstream `jkwill87/mnamer` `main` at commit
`4703dfea2d851ef55c4a62af0a1e1b80581fdc0c` ("Adds v3 dev note").

Private local validation and historical patches helped motivate the organizer
work. Those materials are evidence only: useful behavior must be reimplemented
in repository-owned code with regression tests rather than copied blindly or
treated as authoritative mappings.

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

## Privacy and safety boundary

Repository code, tests, documentation, issues, pull requests, fixtures, and
examples must not contain or access real user environment details. This includes
real absolute media paths, usernames, machine or host names, network addresses,
share names, directory inventories, account identifiers, private filenames,
production logs, or other data copied from a contributor's system.

Tests use temporary directories and synthetic zero-byte files only. Examples
must use obviously fabricated paths such as `X:/ExampleMedia/Shows` or
`/example/media/shows`; those examples must never be copied from a real system.
Movies are out of scope for this subsystem.

Real inventory exports, audit CSVs/logs, provider caches, manifests, generated
plan files, copied library roots, and common video files are ignored by Git so
local evidence cannot be committed accidentally. Any bug discovered from a
private library must be reduced to the smallest synthetic reproduction before it
is added to the repository.

## Publishing safety

The inherited PyPI and Docker publishing workflows remain in the repository
for upstream provenance, but their publishing jobs are owner-guarded to
`jkwill87/mnamer`. The caller jobs in `push.yml` carry the same guard.

The fork can therefore run lint and tests while inherited publishing jobs are
skipped, including manual dispatch of the reusable publishing workflows.

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

## Versioned organizer contracts

The organizer's cross-stage data contract lives in `jellyfin_show_organizer/models.py`
and `jellyfin_show_organizer/data/plan-schema-v1.json`. Plans are serialized with
stable key ordering and compact JSON before SHA-256 hashing; timestamps or other
run-local metadata are deliberately excluded from the plan model so equivalent
inputs can produce the same plan hash.

Show-specific aliases and numbering decisions are represented by the versioned
override format in `jellyfin_show_organizer/data/overrides-v1.toml`. The checked-in
catalog contains synthetic examples only so the public repository does not encode
any contributor's private library. Real deployments should use local, untracked
override data.

Supported numbering modes are `aired`, `absolute`, `parenthesized-absolute`, and
`segment-title`. Parser code must consume these policies rather than embedding
show names or one-off decisions.
