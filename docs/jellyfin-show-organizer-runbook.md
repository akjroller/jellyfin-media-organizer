# Jellyfin Show Organizer runbook

> [!IMPORTANT]
> This project is **Shows-only**. Do not point it at a Movies directory, a mixed media root, or any parent media-library directory.

## Windows setup without PowerShell activation

PowerShell script activation is optional. You can use the virtual environment's Python executable directly without changing execution policy:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m jellyfin_show_organizer plan --help
```

`Activate.ps1` is not required, and there is no reason to use `Set-ExecutionPolicy` for this project.

Run the development checks the same way:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check jellyfin_show_organizer tests
.\.venv\Scripts\python.exe -m ruff format --check jellyfin_show_organizer tests
```

## Authorized root rules

Authorize the exact Shows directory and no higher-level parent. The authorized root must be a real directory rather than a symlink or junction.

Allowed conceptual layout:

```text
ExampleMedia/
  Shows/       <- authorize this directory only
  Movies/      <- out of scope; never authorize or inspect
  quarantine/  <- out of the active Shows tree
```

The scanner contract is read-only and video-only: `.mkv`, `.mp4`, and `.avi`. Subtitles, artwork, metadata, and Movies are not organizer inputs. Symlinks and junctions must never be followed outside the authorized root.

## Operational stages

### 1. Scan

Account for eligible videos beneath one explicitly authorized Shows root. Scanning reads directory metadata and source fingerprints only and writes no media.

Current status: the scanner layer exists but is not yet wired into the CLI plan scaffold.

### 2. Plan

Combine inventory, parsing, show resolution, episode assignment, extras/duplicate decisions, and provider evidence into one immutable, versioned plan.

Current status: the CLI exposes a non-mutating `plan` scaffold while the complete planner is assembled. Invoking the scaffold exits nonzero and reports that planning is not implemented; it creates no plan, report, cache, destination directory, or media output. Use `plan --help` only to inspect the temporary command contract until the end-to-end planner is wired in.

### 3. Audit

Review mapping, unresolved, suspicious, extra, duplicate, and summary outputs derived from the same immutable plan. Audit output must not create media destination directories.

Current status: not implemented yet.

### 4. Approval

A human reviews the complete plan and approves the exact stable plan hash intended for execution. Approval is never implied by generating a plan or by a passing test suite.

Current status: no apply workflow exists.

### 5. Apply

A future apply stage may consume only an explicitly approved plan hash, revalidate source/destination state, journal operations, and refuse unsafe overwrites.

Current status: **not implemented**. There is intentionally no organizer `apply` command.

### 6. Verification

Compare completed operations with the approved plan, validate source fingerprints/destination identity, and surface incomplete or changed operations.

Current status: future work.

### 7. Recovery

Safely resume interrupted operations or recover from an operation journal without deleting source media.

Current status: future work.

## Public repository privacy rules

Never commit real contributor or library data into the public repository. This includes:

- absolute personal media paths or directory listings;
- usernames, real names, machine names, host names, network addresses, share names, or account identifiers;
- inventory/audit exports generated from a real library;
- caches, manifests, plans, or reports containing real filenames;
- copied video, subtitle, artwork, or metadata files;
- production logs or commands containing identifying environment fragments.

Tests should build synthetic paths beneath `tmp_path` and use zero-byte dummy files when a filesystem object is needed. Checked-in provider fixtures should be minimal provider-shaped snapshots required for deterministic tests, not dumps from a private library.

Examples must use fabricated values such as `ExampleMedia` and documentation-only paths. A real-world bug should be reduced to the smallest synthetic reproduction before it is committed.

## Contributor extension points

### Add a filename fixture

1. Use a synthetic filename/path.
2. State the expected parse/match outcome explicitly.
3. Keep the test offline.
4. Never copy a real media file or private directory listing.

### Add an alias or numbering policy

The versioned format is represented by `jellyfin_show_organizer/data/overrides-v1.toml`. The checked-in catalog must remain synthetic and generic. Deployment-specific override data belongs in local, untracked files.

Supported numbering modes are:

- `aired`
- `absolute`
- `parenthesized-absolute`
- `segment-title`

Do not add hidden show-name conditionals to generic parser code when the policy can live in override data.

### Add or change a matcher

A matcher should consume typed parse/catalog evidence and return explicit method, confidence, and reasons. Keep network access outside matching logic; provider responses should come through the persistent cache layer. Weak or ambiguous evidence should remain suspicious or unresolved instead of silently matching.

Add synthetic regression fixtures for both the intended match and nearby false-positive cases.

## Development checklist

Before merging a change:

```text
python -m pytest
python -m ruff check jellyfin_show_organizer tests
python -m ruff format --check jellyfin_show_organizer tests
python -m jellyfin_show_organizer plan --help
```

Provider behavior in the deterministic test gate should use checked-in synthetic/provider-shaped fixtures rather than live calls.
