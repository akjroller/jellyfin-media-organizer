# Jellyfin Show Organizer Windows runbook

> [!IMPORTANT]
> This subsystem is **Shows-only**. Do not point it at a Movies directory, a
> mixed media root, or any parent media-library directory. Repository tests and
> examples must never access a real media library.

The organizer is being built as a plan-first subsystem beside the existing
`mnamer` CLI. The existing `mnamer` relocation behavior is not the organizer's
apply mechanism and must not be substituted for one.

## Windows setup without PowerShell activation

PowerShell execution policy does not need to be changed and `Activate.ps1` is
not required. From a repository checkout in Command Prompt or PowerShell:

```powershell
uv sync --frozen --dev
.\.venv\Scripts\python.exe -m jellyfin_show_organizer plan --help
```

Run tests and checks through the venv executable directly as well:

```powershell
.\.venv\Scripts\python.exe -m pytest -m local
.\.venv\Scripts\python.exe -m ruff check mnamer jellyfin_show_organizer tests
.\.venv\Scripts\python.exe -m ruff format --check mnamer jellyfin_show_organizer tests
.\.venv\Scripts\python.exe -m mypy mnamer jellyfin_show_organizer tests
```

Do not work around script policy by changing the machine-wide execution policy.
The direct interpreter form is predictable in shells, CI, and support notes.

## Authorized root rules

When scanning is available, authorize the exact **Shows** directory and no
higher-level parent. The authorized root must be a real directory rather than a
symlink or junction.

Allowed conceptual layout:

```text
ExampleMedia/
  Shows/       <- authorize this directory only
  Movies/      <- out of scope; never authorize or inspect
  quarantine/  <- out of the active Shows tree
```

The scanner contract is read-only and video-only: `.mkv`, `.mp4`, and `.avi`.
Subtitles, artwork, metadata, and Movies are not organizer inputs. Symlinks and
junctions must never be followed outside the authorized root.

## Operational stages

The stages below are deliberately separate. A later stage must not be inferred
from completion of an earlier one.

### 1. Scan

Purpose: account for eligible videos beneath one explicitly authorized Shows
root. Scanning reads directory metadata and source fingerprints only and writes
no media.

Current status: implemented in the scanner layer, but not yet wired into the
`organizer plan` CLI scaffold.

### 2. Plan

Purpose: combine inventory, parsing, show resolution, episode assignment,
extras/duplicate decisions, and cached provider evidence into one immutable,
versioned plan.

Current status: the CLI exposes only a non-mutating `plan` scaffold. The full
planner is still being assembled from later issues.

### 3. Audit

Purpose: review mapping, unresolved, suspicious, extra, duplicate, and summary
outputs derived from the same immutable plan. Audit output must not create
media destination directories.

Current status: not implemented yet.

### 4. Approval

Purpose: a human reviews the complete plan and records the exact stable plan
hash approved for execution. Approval is never implied by generating a plan or
by a passing test suite.

Current status: no library plan has been approved.

### 5. Apply

Purpose: consume only an explicitly approved plan hash, revalidate source and
destination state, journal each operation, and use no-overwrite same-volume
renames initially.

Current status: **not implemented**. There is intentionally no organizer
`apply` command. Do not use the existing mnamer relocation path as a substitute.

### 6. Verification

Purpose: compare completed operations with the approved plan, validate source
fingerprints/destination identity, and surface anything incomplete or changed.

Current status: future work associated with the apply milestone.

### 7. Recovery

Purpose: safely resume interrupted operations or recover from the operation
journal without deleting source media.

Current status: future work associated with the apply milestone.

## Public repository privacy rules

Never commit or paste real contributor or library data into the public
repository, including source code, tests, docs, issues, PR descriptions,
comments, CI logs intentionally checked in, or examples. This includes:

- absolute personal media paths or directory listings;
- usernames, real names, machine names, host names, network addresses, share
  names, or account identifiers;
- inventory or audit CSVs generated from a real library;
- cache/manifests/plans containing real filenames;
- copied video, subtitle, artwork, or metadata files;
- production logs or commands containing identifying environment fragments.

Tests must build synthetic paths beneath `tmp_path` and may use zero-byte dummy
files. Checked-in provider fixtures must be minimal synthetic/provider-shaped
snapshots needed for deterministic tests, not dumps or excerpts from a private
library.

Examples must use clearly fabricated values such as `ExampleMedia`,
`ExampleHost`, and documentation-only drive letters/paths. Never copy an example
from a contributor's actual environment.

If private validation reveals a parser or resolver problem, reduce it to the
smallest synthetic fixture that reproduces the behavior before committing it.

## Contributor extension points

Show-specific behavior belongs in obvious data or focused strategy code rather
than hidden conditionals in a generic parser.

### Add a filename fixture

1. Create only a synthetic filename/path under the relevant test fixture area
   or build it beneath `tmp_path` in the test.
2. State the expected parse/match outcome explicitly.
3. Keep the test offline.
4. Never copy the real media file or full private directory listing.

### Add an alias or numbering policy

The versioned format is documented by
`jellyfin_show_organizer/data/overrides-v1.toml`. The checked-in catalog must
remain synthetic and generic. Real deployment aliases, provider IDs, years, and
numbering policies belong in local, untracked override data using the same
format.

An override may supply aliases, a TVMaze ID, year, numbering mode, title
preference, and an explicit preferred title where needed.

Supported numbering modes are:

- `aired`
- `absolute`
- `parenthesized-absolute`
- `segment-title`

Do not add `if show == "..."` branches to parser code for policy that can live
in override data.

### Add or change a matcher

A matcher should consume typed parse/catalog evidence and return explicit
method, confidence, and reasons. Keep network access outside matching logic;
provider responses should come through the persistent cache layer. Weak or
ambiguous evidence must remain suspicious/unresolved rather than becoming a
silent match.

Add adversarial regression fixtures for both the intended match and at least
one nearby false-positive case when changing matching behavior. Keep those
fixtures synthetic.

## Development checklist

Before opening or updating a PR:

```powershell
uv sync --frozen --dev
.\.venv\Scripts\python.exe -m pytest -m local
.\.venv\Scripts\python.exe -m ruff check mnamer jellyfin_show_organizer tests
.\.venv\Scripts\python.exe -m ruff format --check mnamer jellyfin_show_organizer tests
.\.venv\Scripts\python.exe -m mypy mnamer jellyfin_show_organizer tests
```

Provider behavior in PR tests must be replayed from checked-in synthetic
fixtures/cache snapshots. Live provider calls belong outside the deterministic
PR gate.

This runbook must be updated again when the full plan-only milestone is
complete, and again before any apply implementation is considered operational.
