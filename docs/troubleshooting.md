# Troubleshooting JMO safely

Jellyfin Media Organizer is currently **Shows-only** and **plan-only**. Troubleshooting should never require moving media by hand, weakening preflight, or publishing a real library listing.

All examples below are fabricated. Replace them locally with your own paths, but do not paste private inventories, generated plans, provider caches, machine names, usernames, network addresses, or full production logs into public issues.

## PowerShell says script execution is disabled

JMO does not require virtual-environment activation. Use the environment's Python or console executable directly:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\jmo.exe plan --help
```

Do not change PowerShell execution policy just to run JMO.

## Planning fails before an audit bundle is produced

First confirm that the selected source is the exact Shows root and that destination/cache/output locations are separate from it:

```text
ExampleMedia/
  Shows/
  OrganizedShows/
LocalState/
  cache/
  audit-001/
```

A Movies directory, mixed-media parent, symlinked/junction source root, missing required path, invalid configuration value, or unsafe generated-state location is expected to fail closed.

Run `jmo plan --help` and compare the command with the current public options. Add `--verbose` only when you intentionally want more local diagnostic detail on your own machine.

## Provider data is unavailable

A warmed cache can be replayed with a hard zero-provider-call contract:

```bash
jmo plan ExampleMedia/Shows \
  --destination-root ExampleMedia/OrganizedShows \
  --output-dir LocalState/audit-offline \
  --cache-dir LocalState/cache \
  --offline
```

Use `--refresh` only when you deliberately want to refresh provider data. `--offline` and `--refresh` are mutually exclusive.

Do not publish the contents of a real provider cache to diagnose a bug. Reduce the behavior to a fabricated provider-shaped test fixture instead.

## The plan is unresolved or suspicious

This is a normal fail-closed outcome, not permission to force a mapping. Review the immutable audit bundle, especially the mapping/unresolved reports and `preflight.txt`.

If a deterministic local correction is appropriate, put it in an untracked override file and validate it first:

```bash
jmo overrides validate LocalState/example-overrides.toml
```

Then pass it explicitly to planning:

```bash
jmo plan ExampleMedia/Shows \
  --destination-root ExampleMedia/OrganizedShows \
  --output-dir LocalState/audit-with-overrides \
  --cache-dir LocalState/cache \
  --overrides LocalState/example-overrides.toml
```

An override must not be used to bypass a whole-plan preflight block.

## A destination collision or unsafe path is reported

Do not manually move one file to make the warning disappear. JMO intentionally treats case convergence, Unicode convergence, reserved names, path-length hazards, existing destinations, and unrelated sources that sanitize to the same destination as planning problems.

Keep the media unchanged, reduce the collision to a synthetic reproduction, and fix the relevant parser, numbering, destination, override, duplicate, or preflight policy.

## A subtitle or adjacent file is not associated

Subtitle association is conservative. Supported subtitle extensions are `.srt`, `.ass`, `.ssa`, `.vtt`, `.sub`, and `.idx`; `.idx` + `.sub` pairs stay together. Language/default/forced/SDH/CC suffixes are preserved when the association is deterministic.

Ambiguous sidecars remain unresolved. Unsupported adjacent files remain untouched rather than being silently deleted. Do not rename or delete the source files merely to force the current planner to accept them.

## Reporting a bug publicly

Create the smallest fabricated reproduction that demonstrates the behavior. Good reports contain:

- a synthetic filename or temporary directory tree;
- the JMO version from `jmo --version`;
- the command shape with fabricated paths;
- the expected terminal status or safety outcome;
- the minimal error/reason text needed to identify the defect.

Do not include real media names, directory listings, plans, reports, caches, override catalogs, host/network details, or copied terminal sessions containing identifying paths. If a defect was discovered on a private library, translate it into a synthetic regression before posting it.
