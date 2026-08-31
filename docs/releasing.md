# Release and versioning policy

Jellyfin Media Organizer (JMO) uses Semantic Versioning for public releases. While the project remains pre-1.0, minor versions may introduce planned interface changes and patch versions are reserved for compatible fixes and packaging/documentation corrections.

**No JMO release or version tag has been created yet by design.** The first public release is a deliberate milestone decision, not something created automatically by ordinary development or CI.

## Version source of truth

The package version is defined once as `jellyfin_show_organizer.__version__`. Build metadata reads that value through setuptools, and installed package metadata must match it exactly.

Before creating the first or any later public release:

1. decide explicitly that the current plan-only product is ready to release;
2. update `jellyfin_show_organizer.__version__` in a normal reviewed change when needed;
3. run the complete CI suite;
4. confirm `jmo --version`, `organizer --version`, and `python -m jellyfin_show_organizer --version` report the intended version;
5. create a Git tag named exactly `v<version>`, for example `v0.1.0`;
6. verify the resulting artifacts before creating or announcing a GitHub release.

The release-artifact workflow rejects a pushed version tag that does not match the package version.

## Supported runtimes

The package requires Python 3.12 or newer. CI currently exercises:

- Linux with Python 3.12;
- Linux with Python 3.14;
- Windows with Python 3.12.

The project metadata advertises Python 3.12, 3.13, and 3.14 support. A version should not be advertised if it is known to fail the supported public contract.

## Artifact verification

Normal pull-request CI builds both a wheel and source distribution. Each artifact is installed into its own fresh Python 3.12 environment and checked outside the repository source tree. Verification covers:

- installed package version metadata;
- `jmo --version` and the compatibility `organizer` command;
- the `plan` help surface;
- packaged JSON/TOML data required by the planner.

A deliberate manual invocation of the release-artifact workflow can build verified artifacts without creating a public version tag or release. A matching version tag triggers the same artifact workflow and additionally verifies that the tag matches the package version.

A workflow artifact is not itself a decision to publish or announce a JMO release.

## Publication boundary

There is currently no automatic PyPI or other package-registry publication. Pull requests and ordinary branch pushes never receive release credentials and cannot publish packages. Adding registry publication later requires a separate explicitly reviewed protected release mechanism with least-privilege credentials.

Until the separately gated apply milestone is implemented and approved, the first and any subsequent public release must be described as **plan-only**. Release notes must not imply that media-moving or `apply` functionality exists when it does not.

## Privacy and repository hygiene

Release artifacts must contain only repository source, packaged schemas/defaults, documentation required by packaging, and license/provenance material. Real media, inventories, reports, provider caches, manifests, local override files, machine-specific paths, environment data, and other private operational state must remain untracked and outside release artifacts.
