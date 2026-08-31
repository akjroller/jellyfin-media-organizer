# Local override files

Jellyfin Media Organizer supports a versioned TOML override catalog for deterministic planning decisions that cannot be resolved safely from filenames and provider data alone.

Local override files are **plan-only configuration**. They do not authorize media mutation, bypass preflight, or create an apply path.

## Current scope

Override schema versions 1 and 2 are accepted for the current show-level contract. The packaged synthetic defaults remain schema version 1 for compatibility. Supported decisions include:

- an explicit TVMaze show ID;
- aliases used to identify the source show group;
- a release year constraint;
- numbering mode;
- title preference and an optional preferred title.

The end-to-end planner accepts an explicitly selected local override file:

```text
jmo plan ExampleMedia/Shows --destination-root ExampleMedia/Organized --output-dir ExampleOutput --cache-dir ExampleCache --overrides local-overrides.toml
```

Configuration precedence remains deterministic: command line options override an explicit planning config, which overrides project defaults. Override files are never discovered implicitly.

You can validate a local catalog without reading media:

```text
jmo overrides validate local-overrides.toml
```

A valid file prints its schema version, show count, and a path-independent SHA-256 snapshot identity. The command does not print the local override path by default.

## Unresolved review workflow

Every video row in `mapping.csv` and `unresolved.csv` includes a stable `review_ref` derived only from the normalized relative source identity. Equivalent casing and Unicode normalization produce the same reference. The reference is local review metadata and is not an apply authorization.

To turn one completed plan's unresolved/suspicious records into an editable starter, run:

```text
jmo overrides stub ExampleOutput/plan.json > local-overrides.toml
```

The command validates the current plan manifest, groups unresolved records by source-show identity, and emits schema-version-2 TOML to stdout. It does not scan media, contact the provider, write a destination, or modify the source plan. Review references and concise reasons are emitted as comments.

Observed provider IDs remain comments such as `# observed_tvmaze_id = 45001`; they are **not** promoted into active override decisions automatically. Review the generated file and deliberately add or change identity, aliases, numbering policy, year, or title preference before using it with `plan --overrides`.

The starter intentionally does not invent narrow episode remaps. Episode-level decisions require an explicit supported contract; unsupported mappings remain unresolved rather than being encoded as hidden show-specific exceptions.

## Example

All examples in the public repository are fabricated.

```toml
schema_version = 2

[[shows]]
key = "example-series-key"
tvmaze_id = 45001
aliases = ["Example Series", "Example Series Alternate Title"]
year = 2024
numbering_mode = "aired"
title_preference = "override"
preferred_title = "Example Series"
```

Supported `numbering_mode` values are `aired`, `absolute`, `parenthesized-absolute`, `segment-title`, `special`, and `date`.

Supported `title_preference` values are `provider`, `source`, and `override`. `title_preference = "override"` requires `preferred_title`.

## Fail-closed validation

Validation rejects the entire catalog for schema versions other than 1 or 2, unknown fields, malformed types, invalid enum values, untrimmed identities, duplicate aliases after Unicode/case normalization, identities that make different show entries ambiguous, duplicate TVMaze identities, or incomplete override-title preferences.

An alias may normalize to the same identity as its own entry key because both names select the same show. Equivalent identities that point at different show entries remain invalid.

The loader uses Unicode NFKC normalization plus case-insensitive identity matching so equivalent Unicode/casing cannot make a catalog platform-dependent.

## Determinism

A validated override catalog has a canonical byte representation and stable SHA-256 snapshot identity. Show-table order and alias order do not affect that identity. Local filesystem paths are not included in the snapshot.

Using the same inventory, cache snapshots, planning config, and override snapshot produces the same immutable plan hash. Changing an override changes the recorded override snapshot and therefore the plan identity.

## Local-only handling

Real library-specific override catalogs can reveal media names, provider IDs, and organizational details. Keep them local and untracked. The repository ignores the recommended root-level filename `.jmo-overrides.toml`.

Do not add real library override catalogs, plan manifests, or unresolved reports to bug reports. Reduce a problem to a fabricated synthetic case before committing it to the public repository.
