# Contributing to JMO

Jellyfin Media Organizer is a public, plan-first project. Contributions should remain deterministic, machine-neutral, synthetic, and non-mutating unless a future issue explicitly owns the gated apply path.

## Development setup

Python 3.12 or newer is required. From a source checkout:

```bash
python -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/python -m pytest
./.venv/bin/python -m ruff check jellyfin_show_organizer tests tools
./.venv/bin/python -m ruff format --check jellyfin_show_organizer tests tools
./.venv/bin/python -m mypy jellyfin_show_organizer tests
./.venv/bin/python tools/check_repository_safety.py
```

On Windows PowerShell, activation is optional:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check jellyfin_show_organizer tests tools
.\.venv\Scripts\python.exe -m ruff format --check jellyfin_show_organizer tests tools
.\.venv\Scripts\python.exe -m mypy jellyfin_show_organizer tests
.\.venv\Scripts\python.exe tools\check_repository_safety.py
```

CI also checks Linux on Python 3.12 and 3.14, Windows on Python 3.12, repository safety, and clean wheel/source-distribution installs.

## Synthetic fixtures

Use fabricated titles, filenames, provider payloads, and temporary directory trees in public tests. When a real library reveals a defect, reduce it to the smallest synthetic reproduction before changing code.

Generated inventories, caches, plans, reports, override catalogs, and media files remain local and untracked.

## Filename parsing and matchers

Parser rules should recognize structural evidence rather than specific shows. Ambiguous numeric forms remain unresolved unless an explicit policy makes the interpretation unique.

Matchers consume typed parse/catalog evidence and return explicit method, confidence, and reasons. Provider access stays outside parser and matcher logic. Add fabricated regression coverage for the supported form and its ambiguity boundary.

## Aliases and overrides

Packaged defaults live under `jellyfin_show_organizer/data/` and must remain generic. Deployment-specific aliases, years, provider identities, numbering choices, and title preferences belong in explicit local override files.

Do not add a one-off title check in Python when the behavior belongs in typed configuration or an override contract.

## Numbering policies

Current modes include aired, absolute, parenthesized absolute, segment-title, special/OVA-OAD, and date-based matching.

New ordering behavior must be represented through typed parse/model/override/provider evidence. One source-show group cannot silently mix numbering policies, and provider-backed mappings fail closed when no unique destination exists.

See `docs/numbering-policies.md`.

## Destination policies

Destination construction consumes already-resolved logical identity. It must not reparse filenames or perform provider lookup.

Changes must remain deterministic across Windows and POSIX semantics and retain coverage for reserved names, forbidden characters, trailing-dot/space hazards, Unicode/case convergence, path-length limits, multi-episode names, specials, and collision keys.

A sanitization collision is evidence for duplicate/preflight handling, not permission to overwrite a destination.

## Sidecars

Sidecars are companions to a resolved video, not standalone episodes. A new rule must define deterministic association, destination derivation, collision behavior, and ambiguous/unsupported handling.

Unsupported adjacent files remain untouched unless a later explicit feature handles them.

## Metadata providers

TVMaze remains the initial configured provider. Provider-specific response shapes belong behind `jellyfin_show_organizer/providers.py`.

Use `ProviderIdentity`, normalized provider show/episode models, provider-scoped snapshots, and the `MetadataProvider` protocol rather than spreading raw provider fields through parser, assignment, destination, duplicate, report, or preflight code.

Adding a second provider should require an adapter plus fabricated offline fixtures, not show-specific parser branches or planner-wide rewrites.

## Plan, reports, and preflight

The immutable plan is the source of truth. Reports derive from the same canonical records used for hashing and preflight.

Planning and preflight do not move, copy, rename, overwrite, or delete media and do not create planned destination directories. Any blocking invariant rejects the whole plan.

## Apply boundary

There is currently no `apply` command. Media mutation must not appear incidentally in parser, provider, report, documentation, or cleanup work.

Future apply work belongs only to its explicitly gated issue and must consume an exact approved immutable plan hash, revalidate reality, refuse overwrites, journal operations, and use conservative recovery semantics.

## Documentation and releases

Keep examples copy/pasteable and platform-neutral. Windows examples should invoke the virtual environment executables directly rather than requiring PowerShell activation or execution-policy changes.

Normal pull-request CI verifies installable artifacts but does not publish packages. The repository currently has no JMO release/tag by design; the first public release is a separate deliberate decision after the plan-only milestone is considered ready.

See `docs/releasing.md` for version, tag, and artifact rules.
