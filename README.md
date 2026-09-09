# Jellyfin Media Organizer (JMO)

A plan-first Python CLI for organizing media into Jellyfin-friendly layouts. The current implementation is deliberately focused on **TV shows** while the planning and safety model is built out.

JMO is intentionally conservative: planning and review remain non-mutating, while `jmo apply` is an explicitly gated executor for one exact reviewed plan. Apply permits only same-filesystem, atomic, no-overwrite renames for `matched` and `extra` operation groups. It never copies across filesystems, overwrites, deletes, quarantines, or moves duplicate/held/ignored records.

## Current capabilities

- deterministic parsing of common season/episode, absolute, special/OVA-OAD, and date-based filename patterns;
- read-only inventory scanning for `.mkv`, `.mp4`, and `.avi` files;
- explicit handling of samples, unreadable entries, blocked links, extras, and ambiguous evidence;
- deterministic inventory reconciliation;
- versioned organizer plan models and immutable JSON schema contracts;
- data-driven aliases and numbering policies;
- persistent TVMaze cache primitives for deterministic/offline replay;
- canonical TVMaze show resolution with fail-closed ambiguity handling;
- a namespaced metadata-provider boundary while TVMaze remains the configured provider;
- end-to-end, show-grouped plan generation with cached provider metadata;
- companion subtitle planning and duplicate-safe operation groups;
- immutable JSON/CSV/text audit bundles with provenance and stable hashes;
- whole-plan preflight that blocks unresolved, colliding, or unsafe plans;
- session-bound, resumable **non-mutating review** for duplicate groups and held sources;
- exact-hash, revision, and root-bound apply confirmation;
- append-only durable apply journals with group rollback, resume, and verification;
- synthetic regression fixtures for ambiguous and adversarial cases.

`jmo plan` is operational and remains strictly non-mutating. It inventories one explicit Shows root, resolves each show through the persistent provider cache, constructs destinations, classifies duplicates and companions, runs preflight, and writes an immutable audit bundle. It never moves, copies, renames, overwrites, or deletes media.

`jmo review` consumes a fresh plan-schema-v3 manifest and records explicit review decisions into a resumable session plus a new reviewed override contract. Review can resolve duplicate decisions and held sources as provider-confirmed episodes/specials or explicit extras, but it is also strictly non-mutating: quarantine choices are markers only, and review never moves, deletes, or quarantines media. See the [non-mutating review workflow](docs/review-workflow.md) before using it.

`jmo apply` consumes the already-reviewed `plan.json`, `preflight.json`, and `run-provenance.json`. It does not rerun parsing or make review decisions. Start with `--check-only`; an actual run additionally requires an external journal and the exact confirmation token bound to the plan hash, review-session hash, clean source revision, source root, and destination root. See the [apply safety contract](docs/apply-safety-contract.md).

## Requirements

- Python 3.12+
- CI is configured for Linux on Python 3.12, 3.13, and 3.14, plus Windows and macOS on Python 3.12. Installed wheel/sdist workflows run on all three operating systems; consult the CI results for the revision you install.
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
./.venv/bin/jmo plan ExampleMedia/Shows \
  --destination-root ExampleMedia/OrganizedShows \
  --output-dir LocalState/audit-001 \
  --cache-dir LocalState/cache
```

In PowerShell, use `.\.venv\Scripts\jmo.exe` instead of `./.venv/bin/jmo` and put the arguments on one line (Bash backslashes are not PowerShell continuations).

For a complete first-time sequence, see [First run](docs/first-run.md). Other documentation uses `jmo` as shorthand for the executable in your environment; activation is optional.

Normal wheels and source distributions carry a file-verified build revision. They do not need a Git checkout at runtime. Builds made from dirty checkouts remain ineligible for apply, and altered or unstamped installations fail closed. Reinstall after pulling changes: `pip install .` installs a snapshot, not a live view of the checkout. Build metadata verifies integrity, not publisher authenticity; install artifacts only from a trusted source.

Review `plan.json`, `plan.sha256`, `decision.sha256`, `run-provenance.json`, `preflight.json`, `preflight.txt`, and the CSV reports in the output directory. A ready plan exits `0`; configuration, provider, unresolved, and preflight failures use distinct nonzero exit codes.

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

Planning, review, preflight, and `jmo apply --check-only` are read-only with respect to media. A successful plan or completed review is not authorization to mutate files. Mutation requires the explicit `apply` subcommand, three exact approval values, a root-bound confirmation token, a journal outside the media roots, and successful live revalidation.

Apply eligibility is status-based. Only `matched` and `extra` video records plus their `associated` companions may move. A duplicate loser can retain the collided destination as audit evidence, so implementations and operators must never interpret a non-null destination as movement eligibility.

Repository examples and tests use synthetic paths and fixtures. Real library inventories, provider caches, manifests, reports, media files, deployment-specific overrides, machine-specific paths, and other environment-specific data should remain local and untracked.

## Documentation

- [Operational runbook](docs/jellyfin-show-organizer-runbook.md)
- [Non-mutating review workflow](docs/review-workflow.md)
- [Apply safety contract and runbook](docs/apply-safety-contract.md)
- [Troubleshooting safely](docs/troubleshooting.md)
- [Contributor workflow](docs/contributing.md)
- [Architecture](docs/jellyfin-show-organizer-architecture.md)
- [Plan-only release-candidate validation](docs/release-candidate-validation.md)
- [Local overrides](docs/local-overrides.md)
- [Provider cache and offline policy](docs/provider-cache-policy.md)
- [Numbering policies](docs/numbering-policies.md)
- [Metadata-provider boundary](docs/metadata-provider-boundary.md)
- [Release and versioning policy](docs/releasing.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)
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

**No JMO release or tag has been created yet by design.** The first public release remains a separate deliberate decision after the gated apply milestone is validated on exact synthetic and local rehearsal evidence. The presence of `jmo apply` in source does not itself approve a release or authorize any media run.

See the [release policy](docs/releasing.md) for the version source of truth, supported runtime matrix, tag rules, artifact verification process, and privacy boundary.

## Project history and credit

JMO began as a fork of [`jkwill87/mnamer`](https://github.com/jkwill87/mnamer), created and maintained by Jessy Williams. That MIT-licensed project provided the original media-organizing groundwork from which this project started.

JMO has since diverged into its own Jellyfin-focused, plan-first design. The upstream project and its maintainers are not responsible for JMO's current behavior or support. See [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md) for the retained attribution.

## License

MIT. The original upstream copyright and permission notice is retained in `LICENSE.txt`.
