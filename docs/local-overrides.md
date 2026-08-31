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

The end-to-end `plan` command does not yet accept a local override path; that wiring belongs to the plan CLI work. The current public command validates an explicitly selected file without reading media:

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

Supported `numbering_mode` values are:

- `aired`
- `absolute`
- `parenthesized-absolute`
- `segment-title`

Supported `title_preference` values are:

- `provider`
- `source`
- `override`

`title_preference = "override"` requires `preferred_title`.

## Fail-closed validation

Validation rejects the entire catalog when it encounters:

- an unsupported schema version;
- unknown top-level or show-level fields;
- malformed field types or invalid enum values;
- empty or untrimmed keys, aliases, or preferred titles;
- duplicate aliases after Unicode/case normalization;
- identities that make two show entries ambiguous after normalization;
- one TVMaze ID assigned to multiple override entries;
- an override title preference without the required preferred title.

The loader uses Unicode NFKC normalization plus case-insensitive identity matching so a catalog cannot become valid on one platform and ambiguous on another merely because of casing or equivalent Unicode forms.

## Determinism and precedence

A validated override catalog has a canonical byte representation and stable SHA-256 snapshot identity. Show-table order and alias order do not affect that identity. Local filesystem paths are not included in the snapshot.

When a matching show override is consumed by the resolver:

1. explicit provider IDs from source evidence and the override must agree;
2. an override year must agree with any source year evidence;
3. the override numbering mode becomes the canonical numbering policy for that show;
4. title preference controls whether provider, source, or preferred override text wins;
5. any conflict remains unresolved rather than allowing the override to bypass safety checks.

Narrow episode-level override decisions and generation of override stubs from stable unresolved report references remain follow-up work once the immutable plan/report and end-to-end plan CLI layers are available.

## Local-only handling

Real library-specific override catalogs can reveal media names, provider IDs, and organizational details. Keep them local and untracked. The repository ignores the recommended root-level filename `.jmo-overrides.toml`; arbitrary local paths can also be validated explicitly.

Do not add real library override catalogs to bug reports. Reduce a problem to a fabricated synthetic case before committing it to the public repository.
