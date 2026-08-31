# Local override files

Jellyfin Media Organizer supports a versioned TOML override catalog for deterministic planning decisions that cannot be resolved safely from filenames and provider data alone.

Local override files are **plan-only configuration**. They do not authorize media mutation, bypass preflight, or create an apply path.

## Current scope

Schema version 1 supports show-level decisions for:

- an explicit TVMaze show ID;
- aliases used to identify the source show group;
- a release year constraint;
- numbering mode;
- title preference and an optional preferred title.

The end-to-end `plan` command does not yet accept a local override path. The current public command only validates an explicitly selected file without reading media:

```text
jmo overrides validate local-overrides.toml
```

A valid file prints its schema version, show count, and a path-independent SHA-256 snapshot identity. The command does not print the local override path by default.

## Example

All examples in the public repository are fabricated.

```toml
schema_version = 1

[[shows]]
key = "example-series-key"
tvmaze_id = 45001
aliases = ["Example Series", "Example Series Alternate Title"]
year = 2024
numbering_mode = "aired"
title_preference = "override"
preferred_title = "Example Series"
```

Supported `numbering_mode` values are `aired`, `absolute`, `parenthesized-absolute`, and `segment-title`.

Supported `title_preference` values are `provider`, `source`, and `override`. `title_preference = "override"` requires `preferred_title`.

## Fail-closed validation

Validation rejects the entire catalog for unsupported schema versions, unknown fields, malformed types, invalid enum values, untrimmed identities, duplicate aliases after Unicode/case normalization, identities that make different show entries ambiguous, duplicate TVMaze identities, or incomplete override-title preferences.

The loader uses Unicode NFKC normalization plus case-insensitive identity matching so equivalent Unicode/casing cannot make a catalog platform-dependent.

## Determinism

A validated override catalog has a canonical byte representation and stable SHA-256 snapshot identity. Show-table order and alias order do not affect that identity. Local filesystem paths are not included in the snapshot.

## Local-only handling

Real library-specific override catalogs can reveal media names, provider IDs, and organizational details. Keep them local and untracked. The repository ignores the recommended root-level filename `.jmo-overrides.toml`.

Do not add real library override catalogs to bug reports. Reduce a problem to a fabricated synthetic case before committing it to the public repository.
