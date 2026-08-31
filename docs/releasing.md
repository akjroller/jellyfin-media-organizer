# Release and versioning policy

Jellyfin Media Organizer (JMO) uses Semantic Versioning for public releases. While the project remains pre-1.0, minor versions may introduce planned interface changes and patch versions are reserved for compatible fixes and packaging/documentation corrections.

## Version source of truth

The package version is defined once as `jellyfin_show_organizer.__version__`. Build metadata reads that value through setuptools, and installed package metadata must match it exactly.

Before creating a release artifact:

1. update `jellyfin_show_organizer.__version__` in a normal reviewed change;
2. run the complete CI suite;
3. confirm `jmo --version`, `organizer --version`, and `python -m jellyfin_show_organizer --version` report the intended version;
4. create a Git tag named exactly `v<version>`, for example `v0.1.0`.

The release-artifact workflow rejects a pushed version tag that does not match the package version.

## Supported runtimes

The package requires Python 3.12 or newer. CI currently exercises:

- Linux with Python 3.12;
- Linux with Python 3.14;
- Windows with Python 3.12.

The project metadata advertises Python 3.12, 3.13, and 3.14 support. A version should not be advertised if it is known to fail the supported public contract.

## Artifact verification

Normal pull-request CI builds both a wheel and source distribution. Each artifact is installed into its own fresh Python 3.12 virtual environment and checked outside the repository source tree. Verification covers:

- installed package version metadata;
- `jmo --version` and the compatibility `organizer` command;
- the `plan` help surface;
- packaged JSON/TOML data required by the planner.

A release tag or deliberate manual invocation of the release-artifact workflow builds the same wheel and source distribution and stores them as a GitHub Actions artifact.

## Publication boundary

There is currently no automatic PyPI or other package-registry publication. Pull requests and ordinary branch pushes never receive release credentials and cannot publish packages. Adding registry publication later requires a separate explicitly reviewed protected release mechanism with least-privilege credentials.

Current releases are **plan-only**. Release notes must not imply that media-moving or `apply` functionality exists until the separately gated apply milestone is implemented and approved.

## Privacy and repository hygiene

Release artifacts must contain only repository source, packaged schemas/defaults, documentation required by packaging, and license/provenance material. Real media, inventories, reports, provider caches, manifests, local override files, machine-specific paths, environment data, and other private operational state must remain untracked and outside release artifacts.
