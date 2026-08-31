# Jellyfin Show Organizer runbook

> [!IMPORTANT]
> This project is **Shows-only** and currently **plan-only**. Do not point it at a Movies directory, a mixed media root, or any parent media-library directory. There is no `apply` command in the current product.

## Installation

JMO currently documents installation from source rather than from a package registry.

### POSIX setup

```bash
git clone https://github.com/akjroller/jellyfin-media-organizer.git
cd jellyfin-media-organizer
python3 -m venv .venv
./.venv/bin/python -m pip install .
./.venv/bin/python -m jellyfin_show_organizer plan --help
```

For development:

```bash
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/python -m pytest
./.venv/bin/python -m ruff check jellyfin_show_organizer tests tools
./.venv/bin/python -m ruff format --check jellyfin_show_organizer tests tools
./.venv/bin/python -m mypy jellyfin_show_organizer tests
```

### Windows setup without PowerShell activation

PowerShell script activation is optional. Use the virtual environment's Python executable directly without changing execution policy:

```powershell
git clone https://github.com/akjroller/jellyfin-media-organizer.git
cd jellyfin-media-organizer
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m jellyfin_show_organizer plan --help
```

`Activate.ps1` is not required, and there is no reason to use `Set-ExecutionPolicy` for this project.

Run development checks the same way:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check jellyfin_show_organizer tests tools
.\.venv\Scripts\python.exe -m ruff format --check jellyfin_show_organizer tests tools
.\.venv\Scripts\python.exe -m mypy jellyfin_show_organizer tests
```

## What the CLI does today

The installed console commands are `jmo` and the compatibility alias `organizer`. The package can also be invoked with `python -m jellyfin_show_organizer`.

At this stage, `plan` is a non-mutating scaffold:

```text
jmo plan --help
organizer plan --help
python -m jellyfin_show_organizer plan --help
```

It does **not** yet accept a Shows root or execute the full inventory/matching/report pipeline. Examples in this runbook therefore do not invent options that are not implemented yet.

## Authorized root rules

When root-taking planning is wired, authorize the exact Shows directory and no higher-level parent. The authorized root must be a real directory rather than a symlink or junction.

Allowed conceptual layout:

```text
ExampleMedia/
  Shows/       <- authorize this directory only
  Movies/      <- out of scope; never authorize or inspect
  Reports/     <- generated state belongs outside the active Shows tree
```

The primary inventory is video-led. Current inventory code recognizes `.mkv`, `.mp4`, and `.avi` videos and must not follow symlinks or junctions outside the authorized root. Subtitle/sidecar association is developed as a companion planning layer rather than as independent episode discovery.

Do not encode a contributor drive letter, home directory, NAS mount, UNC path, host name, share name, or network address in defaults, tests, or documentation examples.

## Product lifecycle

### 1. Install and configure

Install the package and, once configuration wiring lands, provide only explicit user-local configuration and override files. Repository defaults must remain machine-neutral.

Current status: source installation works; end-to-end planner configuration is still being assembled.

### 2. Scan and plan

Authorize one Shows root, inventory eligible videos, parse/group source identity, resolve provider identity, assign episodes/extras, associate sidecars, construct destinations, classify duplicates, and produce one immutable plan.

Current status: several component layers exist, but the public `plan` command is still a scaffold and does not scan media.

### 3. Review unresolved and apply local overrides

Ambiguous or suspicious results must remain unresolved until deterministic evidence or a user-local override resolves them. Overrides must be validated, deterministic, and local by default.

Current status: override schema/catalog primitives exist; the user-facing unresolved review loop is future integration work.

### 4. Audit and preflight

Human and machine-readable reports must come from the immutable plan. Whole-plan preflight must reject collisions, unsafe destinations, changed sources, root escape, and other blocking conditions without creating destination directories.

Current status: not wired end to end yet.

### 5. Approval

A human reviews the complete release-candidate plan and approves the exact stable plan hash. Generating a plan or passing CI never implies approval.

Current status: no apply workflow exists.

### 6. Apply

A future apply stage may consume only an explicitly approved plan hash, revalidate reality immediately before operations, journal every operation, and refuse unsafe overwrites.

Current status: **not implemented**. There is intentionally no organizer `apply` command.

### 7. Verification and recovery

Future verification compares results with the approved plan. Recovery must be conservative and journal-driven; if an automatic rollback cannot be proven safe, the product should surface exact recovery information instead of guessing.

Current status: future work.

## Provider cache and offline policy

Provider cache data is reproducibility input, not an invisible optimization. The cache-policy work is being integrated around these rules while the public CLI flags remain part of the end-to-end `plan` work:

- title-search and episode-catalog entries have separate freshness semantics;
- ordinary warmed planning does not silently refresh stale provider data;
- explicit offline mode is a hard zero-provider-call contract;
- a cold or corrupt offline cache produces an explicit unresolved/miss result rather than a hidden live fetch;
- deliberate refresh is distinct from ordinary planning;
- cache records retain request/retrieval/provenance information and expose a stable snapshot identity for later plan audit;
- provider timeouts, rate limits, transient HTTP failures, malformed responses, and not-found results fail closed.

Do not document `--offline` or refresh examples as usable CLI syntax until those options actually appear in `organizer plan --help`.

## Destination and sidecar policy status

Destination construction and sidecar preservation are separate planning layers. Their public contracts are being implemented independently so matching decides **what** a source is while destination code decides **where** it would go.

Until those changes are merged and wired into the immutable plan, documentation must not promise a final Jellyfin filename template or imply that subtitles/artwork are already moved. No current command moves any file.

## Public repository privacy rules

Never commit real contributor or library data into the public repository. This includes:

- absolute personal media paths or directory listings;
- usernames, real names, machine names, host names, network addresses, share names, or account identifiers;
- inventory/audit exports generated from a real library;
- caches, manifests, plans, reports, or local override catalogs containing real filenames;
- copied video, subtitle, artwork, or metadata files;
- production logs or copied terminal sessions containing identifying environment fragments.

Tests should build synthetic paths beneath `tmp_path` and use zero-byte dummy files when a filesystem object is needed. Checked-in provider fixtures should be minimal fabricated provider-shaped snapshots required for deterministic tests, not dumps from a private library.

Examples must use fabricated values such as `ExampleMedia`, `Example Series`, and documentation-only paths. A real-world bug should be reduced to the smallest non-identifying synthetic reproduction before it is committed or posted publicly.

Generated local state should remain untracked. Do not ask users to publish real library listings, reports, cache entries, or full production logs to troubleshoot a bug when a synthetic reproduction can demonstrate it.

## Contributor extension points

### Add a filename fixture

1. Use a fabricated filename/path.
2. State the expected parse/match outcome explicitly.
3. Keep parser-only tests independent of filesystem and network state.
4. Never copy a real media filename set or private directory listing into the repository.

### Add an alias or numbering policy

The versioned format is represented by `jellyfin_show_organizer/data/overrides-v1.toml`. The checked-in catalog must remain synthetic and generic. Deployment-specific override data belongs in local, untracked files.

Supported numbering modes in the current model are `aired`, `absolute`, `parenthesized-absolute`, and `segment-title`. Additional numbering behavior should be represented as explicit data/policy, not hidden show-name conditionals in generic parser code.

### Add or change a matcher

A matcher should consume typed parse/catalog evidence and return explicit method, confidence, and reasons. Keep network access outside matching logic; provider responses should come through the cache/provider boundary. Weak or ambiguous evidence should remain suspicious or unresolved instead of silently matching.

Add synthetic regression fixtures for both the intended match and nearby false-positive cases.

### Add a destination policy

Destination construction must consume already-resolved logical identity instead of reparsing source filenames. Keep templates/policies data-driven and deterministic across Windows and POSIX semantics. Sanitization collisions must remain visible to later duplicate/preflight layers.

Use fabricated canonical show names and paths in tests, including Windows-reserved names, Unicode/case convergence, forbidden characters, trailing dots/spaces, and path-length boundaries.

### Add a provider adapter

TVMaze is the initial provider. New provider work must normalize provider-specific response shapes at an adapter boundary rather than spreading raw response dictionaries or provider-specific IDs through parser, assignment, destination, duplicate, reporting, or preflight code.

Provider identity should be namespaced by provider plus provider-specific ID. Cache keys/snapshots and override identities must remain provider-scoped. A new provider should bring synthetic adapter/cache fixtures and should not require show-specific parser branches.

Do not add a second provider until the provider-adapter boundary is explicitly implemented and reviewed.

## Development and CI checklist

Before merging a change, run:

```text
python tools/check_ci_constraints.py
python -m ruff check jellyfin_show_organizer tests tools
python -m ruff format --check jellyfin_show_organizer tests tools
python -m mypy jellyfin_show_organizer tests
python -m pytest
python tools/check_repository_safety.py
python -m jellyfin_show_organizer plan --help
```

CI also builds distribution artifacts and performs installed-package smoke tests outside the source tree. Provider-facing deterministic tests should use synthetic checked-in responses rather than live network calls.

When adding documentation examples, keep them copy/pasteable, capability-accurate, and independent of one contributor's machine layout.
