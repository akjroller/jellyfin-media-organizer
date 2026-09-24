# Getting started with JMO

Jellyfin Media Organizer (JMO) turns a messy **TV-show** library into a
Jellyfin-friendly layout. It is deliberately plan-first:

1. `plan` inventories and proposes changes without touching media.
2. `review` records explicit decisions for duplicates and held files.
3. A second `plan` compiles the reviewed state and runs preflight.
4. `apply --check-only` rechecks the live filesystem and prints a confirmation token.
5. Only an explicit `apply` command with that token moves files.

JMO currently supports shows only. Point it at the exact Shows directory, not a
Movies directory, a mixed-media parent, or a drive root.

## Install

JMO requires Python 3.12 or newer. From a source checkout:

### Windows PowerShell

```powershell
git clone https://github.com/akjroller/jellyfin-media-organizer.git
Set-Location jellyfin-media-organizer
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\jmo.exe --version
```

### Linux or macOS

```bash
git clone https://github.com/akjroller/jellyfin-media-organizer.git
cd jellyfin-media-organizer
python3 -m venv .venv
./.venv/bin/python -m pip install .
./.venv/bin/jmo --version
```

The commands below use `jmo`. If the virtual environment is not activated,
replace it with `.\.venv\Scripts\jmo.exe` on PowerShell or
`./.venv/bin/jmo` on Linux/macOS.

## Five-minute disposable walkthrough

Learn the workflow without pointing JMO at real media:

```text
jmo demo --output JMO-demo
jmo inspect JMO-demo/State/runs/demo-run --json
```

`demo` creates fabricated files, an offline cache, and an audit bundle that
intentionally contains a duplicate review group and an explicit held file. It
never reads or changes an existing directory and refuses to overwrite its
output. Because the demo already includes its own state and audit, do not run
`jmo init` against `JMO-demo`; use `init` for a separate library instead.

## Guided setup

If you do not want to assemble the paths and flags yourself, run either the
bare command or the explicit wizard command:

```text
jmo
jmo wizard
```

The wizard asks for the Shows directory, destination, state directory, and
provider mode, shows the choices back to you, and requires confirmation before
creating state. It runs the read-only doctor and paper plan, offers the guided
review for duplicate and held records, rebuilds the reviewed plan, and can run
the read-only apply check. The wizard never moves, deletes, overwrites, or
quarantines media. An actual apply remains a separate explicit command.

The default `auto` mode uses TVMaze first and consults TMDb only when the
TVMaze result is unresolved or ambiguous. TMDb comparison is enabled only when
`JMO_TMDB_ACCESS_TOKEN` is set; without it, auto mode behaves like the existing
TVMaze path and remains fully offline-safe.

For a new library, initialize a separate state directory and run the read-only
doctor check before planning:

```text
jmo init /path/to/Shows --destination-root /path/to/OrganizedShows --state-dir LocalState
jmo doctor /path/to/Shows --destination-root /path/to/OrganizedShows --output-dir LocalState/runs/initial --cache-dir LocalState/cache
jmo run --state-dir LocalState
```

After `jmo init`, `jmo run --state-dir LocalState` is the normal repeatable
command. It reads the saved roots and provider settings, creates a fresh
timestamped audit bundle, and never overwrites an earlier run.

Every path is an example. Replace it with paths appropriate to your operating
system; keep state, cache, and audit output outside both media roots.

## Configure once, then reuse

For an existing library, `jmo init` creates the state directory, cache,
starter override, and planning config in one step. It never overwrites an
existing state directory:

```text
jmo init /path/to/Shows --destination-root /path/to/OrganizedShows --state-dir LocalState
```

If you want to learn the workflow without using any real media, create the
disposable synthetic workspace first:

```text
jmo demo --output JMO-demo
```

The demo contains fabricated files only and includes its own instructions.

Create a state directory outside both media roots and save this as
`LocalState/planning.toml`:

```toml
schema_version = 1

[plan]
destination_root = "../OrganizedShows"
output_dir = "./audit"
cache_dir = "./cache"
provider_mode = "auto"
max_path_length = 240
max_component_length = 180
```

Paths in the config are relative to the config file. The destination directory
must already exist and be on the same filesystem as the source. The state and
cache paths must be outside both media roots. Command-line options override the
config file.

An empty override file is optional for a first run. If you use one, create and
validate it explicitly:

```text
schema_version = 4
```

```text
jmo overrides validate LocalState/base-overrides.toml
```

## The safe first run

Use a disposable copy or a small representative subset first. Replace the
example paths with your own exact directories:

Before the first plan, run the read-only environment check:

```text
jmo doctor /path/to/Shows \
  --destination-root /path/to/OrganizedShows \
  --output-dir LocalState/audit \
  --cache-dir LocalState/cache
```

Every check must pass before planning. This catches missing roots, cross-volume
destinations, and state directories accidentally placed inside the media tree.

```text
jmo plan /path/to/Shows --config LocalState/planning.toml --overrides LocalState/base-overrides.toml
```

For a large library, add `--progress` to show inventory and show-resolution
progress on stderr. Progress is suppressed for `--json` so automation receives
machine-readable output only.

PowerShell example:

```powershell
.\.venv\Scripts\jmo.exe plan "D:\Media\Shows" --config "C:\JMO\LocalState\planning.toml" --overrides "C:\JMO\LocalState\base-overrides.toml" --json
```

Inspect the generated `summary.txt`, `preflight.txt`, `mapping.csv`,
`duplicates.csv`, `extras.csv`, `unresolved.csv`, and `sidecars.csv`. A plan can
finish successfully while still leaving intentional held files or duplicate
losers at the source. `preflight_ready=true` means the approved movable subset
is safe; it does not mean every file will move.

For a quick overview without opening the report files individually:

```text
jmo inspect LocalState/audit
```

For a shareable machine-readable summary, omit the local audit path:

```text
jmo inspect LocalState/audit --json --redact-paths
```

This reports counts, readiness, and the plan identity without exposing local
directory names.

To generate the starter files from the installed version instead of copying
them from this guide:

```text
jmo config example --output LocalState/planning.toml
jmo overrides example --output LocalState/base-overrides.toml
```

If the plan contains duplicate or held review items, run:

```text
jmo review LocalState/audit/plan.json \
  --overrides LocalState/base-overrides.toml \
  --output LocalState/reviewed-overrides.toml \
  --session LocalState/review-session.json \
  --cache-dir LocalState/cache \
  --online
```

On PowerShell, put the command on one line or use PowerShell's backtick for
continuation; Bash `\` line continuations do not work in PowerShell. Review
decisions are persisted and resumable. Review never moves or deletes media.

Compile the reviewed plan into a new output directory:

```text
jmo plan /path/to/Shows --config LocalState/planning.toml --output-dir LocalState/reviewed-plan \
  --overrides LocalState/reviewed-overrides.toml \
  --review-session LocalState/review-session.json
```

For a deterministic replay, repeat with `--offline` and a different output
directory. Plan and decision hashes should match the online run.

## Before the first move

Read the reviewed `summary.txt`, `remaining.csv`, and `preflight.txt`. Keep an
independent backup. Then run the exact reviewed artifacts with `--check-only`:

```text
jmo apply LocalState/reviewed-plan/plan.json \
  --preflight LocalState/reviewed-plan/preflight.json \
  --run-provenance LocalState/reviewed-plan/run-provenance.json \
  --source-root /path/to/Shows \
  --destination-root /path/to/OrganizedShows \
  --approve-plan-sha256 PLAN_HASH \
  --approve-review-session-sha256 SESSION_HASH \
  --approve-source-revision REVISION \
  --check-only
```

Check-only moves nothing. It reports the operation counts and prints the exact
confirmation token. The real move additionally requires that token and a new
journal path outside both media roots. Duplicate losers, held sources, ignored
companions, unresolved records, and suspicious records are never moved by
`apply`.

For interruption or recovery, keep the plan, review session, provenance,
preflight, approval values, and journal together. Use `--resume` with the same
artifacts; do not generate a replacement plan over a partially moved library.
See [the apply safety contract](apply-safety-contract.md) for rollback and
duplicate-quarantine procedures.

## What to send when asking for help

Do not publish a real library listing or private filenames. Share:

- JMO version (`jmo --version`);
- operating system and Python version;
- the exit code and the relevant `summary.txt` / `preflight.txt` lines;
- whether the run was online or offline;
- sanitized counts of matched, extra, duplicate, held, suspicious, and unresolved records.

Never share provider credentials, cache contents, approval tokens, journals, or
unsanitized real-library paths in a public issue.
