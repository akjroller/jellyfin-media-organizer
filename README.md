# Jellyfin Media Organizer (JMO)

A plan-first Python CLI for organizing media into Jellyfin-friendly layouts. The current implementation is deliberately focused on **TV shows** while the planning and safety model is built out.

JMO is intentionally conservative: planning, parsing, inventory, reconciliation, provider-cache handling, and manifest contracts are developed separately from filesystem mutation. There is currently **no apply command**, so the tool cannot move, rename, copy, overwrite, or delete media.

## Current capabilities

- deterministic parsing of common season/episode, absolute, special/OVA-OAD, and date-based filename patterns;
- read-only inventory scanning for `.mkv`, `.mp4`, and `.avi` files;
- explicit handling of samples, unreadable entries, blocked links, extras, and ambiguous evidence;
- deterministic inventory reconciliation;
- versioned organizer plan models and JSON schema validation;
- data-driven aliases and numbering policies;
- persistent TVMaze cache primitives for deterministic/offline replay;
- canonical TVMaze show resolution with fail-closed ambiguity handling;
- a namespaced metadata-provider boundary while TVMaze remains the configured provider;
- end-to-end, show-grouped plan generation with cached provider metadata;
- companion subtitle planning and duplicate-safe operation groups;
- immutable JSON/CSV/text audit bundles with provenance and stable hashes;
- whole-plan preflight that blocks unresolved, colliding, or unsafe plans;
- synthetic regression fixtures for ambiguous and adversarial cases.

`jmo plan` is operational and remains strictly non-mutating. It inventories one explicit Shows root, resolves each show through the persistent provider cache, constructs destinations, classifies duplicates and companions, runs preflight, and writes an immutable audit bundle. It never moves, copies, renames, overwrites, or deletes media.

## Requirements

- Python 3.12+
- CI tests Linux on Python 3.12 and 3.14, plus Windows on Python 3.12.
- Project metadata advertises Python 3.12, 3.13, and 3.14 support.

The runtime package currently has no third-party dependencies.

## Repository identity

The standalone project is maintained in the `jellyfin-media-organizer` repository, matching the installable project name. This repository name is the intentional long-term identity for JMO rather than a temporary fork-era name.

Maintained project metadata points to this repository and its issue tracker. Packaging or documentation changes should not reintroduce obsolete upstream package/repository URLs as JMO's own project metadata; historical upstream credit remains in the acknowledgments and license sections below.

## Install

From a source checkout:

```bash
git clone https://github.com/akjroller/jellyfin-media-organizer.git
cd jellyfin-media-organizer
python -m venv .venv
./.venv/bin/python -m pip install .
./.venv/bin/jmo --version
./.venv/bin/jmo plan --help
```

Windows PowerShell does not require virtual-environment activation or an execution-policy change:

```powershell
git clone https://github.com/akjroller/jellyfin-media-organizer.git
cd jellyfin-media-organizer
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\jmo.exe --version
.\.venv\Scripts\jmo.exe plan --help
```

A minimal planning run uses separate existing source and destination roots, plus generated-state locations outside both media roots:

```bash
jmo plan ExampleMedia/Shows \
  --destination-root ExampleMedia/OrganizedShows \
  --output-dir LocalState/audit-001 \
  --cache-dir LocalState/cache
```

Review `plan.json`, `plan.sha256`, `preflight.txt`, and the CSV reports in the output directory. A ready plan exits `0`; configuration, provider, unresolved, and preflight failures use distinct nonzero exit codes.

Use `--offline` for a hard zero-provider-call replay from a warmed cache and `--refresh` for a deliberate refresh. Local override files are passed explicitly with `--overrides`. `--json` emits the versioned machine-readable summary; `--verbose` opts into additional local diagnostic detail.

The historical `organizer` command remains available as a compatibility alias:

```bash
organizer --version
organizer plan --help
```

You can also run the package directly:

```bash
python -m jellyfin_show_organizer --version
python -m jellyfin_show_organizer plan --help
```

## Safety boundary

The current implementation is **Shows-only**. Do not point it at a Movies directory, a mixed media root, or a parent directory containing unrelated media.

Planning and preflight are read-only with respect to media. A successful plan is not authorization to mutate files, and there is intentionally no `apply` command today.

Repository examples and tests use synthetic paths and fixtures. Real library inventories, provider caches, manifests, reports, media files, deployment-specific overrides, machine-specific paths, and other environment-specific data should remain local and untracked.

## Documentation

- [Operational runbook](docs/jellyfin-show-organizer-runbook.md)
- [Troubleshooting safely](docs/troubleshooting.md)
- [Contributor workflow](docs/contributing.md)
- [Architecture](docs/jellyfin-show-organizer-architecture.md)
- [Local overrides](docs/local-overrides.md)
- [Provider cache and offline policy](docs/provider-cache-policy.md)
- [Numbering policies](docs/numbering-policies.md)
- [Metadata-provider boundary](docs/metadata-provider-boundary.md)
- [Release and versioning policy](docs/releasing.md)
- [Upstream acknowledgments](ACKNOWLEDGMENTS.md)

## Development

```bash
python -m pip install -e ".[dev]"
python tools/check_ci_constraints.py
python -m ruff check jellyfin_show_organizer tests tools
python -m ruff format --check jellyfin_show_organizer tests tools
python -m mypy jellyfin_show_organizer tests
python -m pytest
python tools/check_repository_safety.py
```

Public regression tests use fabricated data and offline provider fixtures. See the [contributor workflow](docs/contributing.md) before adding parser, matcher, numbering, destination, sidecar, override, or provider behavior.

## Project layout

```text
jellyfin_show_organizer/   core application package
  data/                    versioned schemas and generic packaged defaults
tests/
  fixtures/                synthetic deterministic fixtures
  local/                   offline test suite
docs/                      architecture and operating guidance
```

## Releases

JMO uses Semantic Versioning. Pull-request CI builds and verifies both wheel and source-distribution installs in isolated environments. Verified artifacts can be built by the deliberate release-artifact workflow or a matching version tag; the repository does not automatically publish packages to a package registry.

**No JMO release or tag has been created yet by design.** The first public release will be a deliberate decision once the plan-only milestone is considered ready. Until a separately gated apply milestone is implemented and approved, any future release notes must describe JMO as plan-only and must not imply media-moving capability.

See the [release policy](docs/releasing.md) for the version source of truth, supported runtime matrix, tag rules, artifact verification process, and privacy boundary.

## Project history and credit

JMO began as a fork of [`jkwill87/mnamer`](https://github.com/jkwill87/mnamer), created and maintained by Jessy Williams. That MIT-licensed project provided the original media-organizing groundwork from which this project started.

JMO has since diverged into its own Jellyfin-focused, plan-first design. The upstream project and its maintainers are not responsible for JMO's current behavior or support. See [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) for the retained attribution.

## License

MIT. The original upstream copyright and permission notice is retained in `LICENSE.txt`.
