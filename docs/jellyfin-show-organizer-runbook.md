# Jellyfin Show Organizer runbook

> [!IMPORTANT]
> JMO is **Shows-only** and currently **plan-only**. Do not point it at a Movies directory, a mixed media root, or any parent media-library directory. There is no `apply` command in the current product.

## Installation

JMO currently supports source installation. Release artifacts may be built and verified by the repository release workflow, but the project is not automatically published to a package registry.

### POSIX setup

```bash
git clone https://github.com/akjroller/jellyfin-media-organizer.git
cd jellyfin-media-organizer
python3 -m venv .venv
./.venv/bin/python -m pip install .
./.venv/bin/jmo --version
./.venv/bin/jmo plan --help
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
.\.venv\Scripts\jmo.exe --version
.\.venv\Scripts\jmo.exe plan --help
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

Version reporting is available on all supported entry surfaces:

```text
jmo --version
organizer --version
python -m jellyfin_show_organizer --version
```

The public `plan` command is still a non-mutating scaffold:

```text
jmo plan --help
organizer plan --help
python -m jellyfin_show_organizer plan --help
```

Running `jmo plan` itself exits nonzero because planning is not implemented end to end yet. It does not create a plan, report, cache, destination directory, or media output.

Local override files can be validated explicitly without scanning media or contacting a metadata provider:

```text
jmo overrides validate local-overrides.toml
```

Validation does not load local overrides implicitly and does not print the selected local path by default. See `docs/local-overrides.md` for the supported schema contract.

## Authorized root rules

When root-taking planning is wired, authorize the exact Shows directory and no higher-level parent. The authorized root must be a real directory rather than a symlink or junction.

Allowed conceptual layout:

```text
ExampleMedia/
  Shows/       <- authorize this directory only
  Movies/      <- out of scope; never authorize or inspect
  Reports/     <- generated state belongs outside the active Shows tree
```

The primary inventory is video-led. Current inventory code recognizes `.mkv`, `.mp4`, and `.avi` videos and must not follow symlinks or junctions outside the authorized root.

Subtitle sidecars are a companion planning layer rather than independent episode discovery. The current sidecar primitive recognizes `.srt`, `.ass`, `.ssa`, `.vtt`, `.sub`, and `.idx`, preserves supported language/default/forced/SDH/CC suffixes, keeps `.idx` + `.sub` pairs together, and leaves ambiguous associations unresolved. It does not move or rename sidecars.

Do not encode a contributor drive letter, home directory, NAS mount, UNC path, host name, share name, or network address in defaults, tests, or documentation examples.

## Product lifecycle

### 1. Install and configure

Install the package and keep deployment-specific overrides, caches, and generated state local. Repository defaults remain machine-neutral.

Current status: source installation, version reporting, package artifact verification, and explicit local-override validation exist; end-to-end planner configuration is still being assembled.

### 2. Scan and plan

The intended planner sequence is: authorize one Shows root, inventory eligible videos, parse/group source identity, resolve provider identity, assign episodes/extras, associate sidecars, construct destinations, classify duplicates, run preflight, and produce one immutable plan.

Current status: the component layers are being implemented independently, but the public `plan` command is still a scaffold and does not scan media.

### 3. Review unresolved and apply local overrides

Ambiguous or suspicious results must remain unresolved until deterministic evidence or a user-local override resolves them. Overrides are validated fail-closed and have path-independent snapshot identities for later plan provenance.

Current status: show-level override validation exists. Stable unresolved references, override-stub generation, narrow episode-level decisions, and final planner precedence remain follow-up integration work.

### 4. Audit and preflight

Human- and machine-readable reports must derive from the same immutable plan. Whole-plan preflight must reject collisions, unsafe destinations, changed sources, root escape, and other blocking conditions without creating media destination directories.

Current status: these layers are still being integrated; do not treat the current CLI scaffold as an audit or preflight result.

### 5. Approval

A human reviews the complete release-candidate plan and approves the exact stable plan hash intended for execution. Generating a plan or passing CI never implies approval.

Current status: no apply workflow exists.

### 6. Apply

A future apply stage may consume only an explicitly approved plan hash, revalidate reality immediately before operations, journal every operation, and refuse unsafe overwrites.

Current status: **not implemented**. There is intentionally no organizer `apply` command.

### 7. Verification and recovery

Future verification compares completed operations with the approved plan. Recovery must be conservative and journal-driven; if an automatic rollback cannot be proven safe, the product should surface recovery information instead of guessing.

Current status: future work.

## Provider cache and offline policy

Provider cache data is reproducibility input, not an invisible optimization. The current cache primitives follow these rules while public `plan` flags remain future integration work:

- title-search and episode-catalog entries have separate freshness semantics;
- ordinary warmed planning does not silently refresh stale provider data;
- explicit offline behavior is a hard zero-provider-call contract at the cache/provider layer;
- a cold or corrupt offline cache produces an explicit miss/error instead of a hidden live fetch;
- deliberate refresh is distinct from ordinary planning;
- cache records retain request/retrieval/provenance information and expose stable snapshot identity;
- provider timeouts, rate limits, transient HTTP failures, malformed responses, and not-found results fail closed.

Do not document `--offline` or refresh syntax as usable `plan` CLI options until those options actually appear in `organizer plan --help`.

## Numbering policies

Numbering behavior is explicit policy rather than show-name-specific parser code. Current model/parser support includes:

- `aired`;
- `absolute`;
- `parenthesized-absolute`;
- `segment-title`;
- `special` for explicitly identified OVA/OAD-style provider-special evidence;
- `date` for unique provider episode air dates.

Mixed numbering families within one source-show group fail closed. Date/special modes require deterministic provider evidence and do not guess when multiple provider episodes could match.

## Destination and sidecar policy

Destination construction consumes already-resolved logical identity instead of reparsing filenames. It handles platform-sensitive sanitization and exposes convergence/collision evidence for later duplicate and preflight layers.

Sidecar destinations are derived only after a video destination has already been selected. Sidecars remain companion operation groups; discovery itself performs no media writes and does not create destination directories.

## Release and packaging boundary

JMO uses one package version source of truth and verifies wheel and source-distribution installs in clean environments. Release artifacts are built only by a deliberate workflow invocation or matching version tag.

The release workflow has read-only repository permissions and does not contain package-registry publishing credentials. Pull requests do not publish packages. See `docs/releasing.md` for the full policy.

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

Generated local state should remain untracked. Do not ask users to publish real library listings, reports, cache entries, or full production logs when a synthetic reproduction can demonstrate the defect.

## Contributor extension points

### Add a filename fixture

1. Use a fabricated filename/path.
2. State the expected parse/match outcome explicitly.
3. Keep parser-only tests independent of filesystem and network state.
4. Never copy a real media filename set or private directory listing into the repository.

### Add an alias or numbering policy

The packaged override defaults remain versioned under `jellyfin_show_organizer/data/overrides-v1.toml`. Checked-in catalogs must remain synthetic and generic. Deployment-specific override data belongs in local, untracked files.

Additional numbering behavior should be represented as explicit typed data/policy with synthetic ambiguity tests, not hidden show-name conditionals.

### Add or change a matcher

A matcher should consume typed parse/catalog evidence and return explicit method, confidence, and reasons. Keep network access outside matching logic; provider responses should come through the cache/provider boundary. Weak or ambiguous evidence should remain suspicious or unresolved instead of silently matching.

### Add a destination policy

Destination construction must consume already-resolved logical identity. Keep templates/policies deterministic across Windows and POSIX semantics. Sanitization collisions must remain visible to duplicate/preflight layers.

Use fabricated canonical show names and paths in tests, including Windows-reserved names, Unicode/case convergence, forbidden characters, trailing dots/spaces, and path-length boundaries.

### Add a metadata provider

TVMaze remains the initial provider. A second provider should not be added by spreading raw provider response dictionaries or provider-specific IDs through parser, assignment, destination, duplicate, reporting, or preflight code. Provider abstraction work should be introduced as a separately versioned/reviewed boundary when it is actually needed.

## Development and CI checklist

Before merging a change, run:

```text
python tools/check_ci_constraints.py
python -m ruff check jellyfin_show_organizer tests tools
python -m ruff format --check jellyfin_show_organizer tests tools
python -m mypy jellyfin_show_organizer tests
python -m pytest
python tools/check_repository_safety.py
python -m jellyfin_show_organizer --version
python -m jellyfin_show_organizer plan --help
```

CI also verifies both wheel and source-distribution installs outside the source tree. Provider-facing deterministic tests use fabricated checked-in responses instead of live network calls.

Keep documentation examples copy/pasteable, capability-accurate, and independent of one contributor's machine layout.
