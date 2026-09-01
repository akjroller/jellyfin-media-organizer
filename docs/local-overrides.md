# Local override files

Jellyfin Media Organizer supports a versioned TOML override catalog for deterministic planning decisions that cannot be resolved safely from filenames and provider data alone.

Local override files are **plan-only configuration**. They do not authorize media mutation, bypass preflight, or create an apply path.

## Current scope

Override schema versions 1, 2, and 3 are accepted. The packaged synthetic defaults remain schema version 1 for compatibility. Supported show-level decisions include:

- an explicit provider show identity;
- aliases used to identify the source show group;
- a release year constraint;
- numbering mode;
- title preference and an optional preferred title.

Schema version 2 additionally supports explicit local duplicate preferences. Schema version 3 adds exact-source episode decisions for cases where review establishes the intended numbering evidence but the filename alone is not safe to interpret.

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

The starter intentionally does not invent episode decisions. A reviewer must add any schema-version-3 `episode_decisions` entry deliberately.

## Narrow episode decisions

Episode decisions are scoped to one normalized relative source path and bound to the provider identity of the resolved show. They replace only episode-numbering evidence; show identity, year evidence, and other source metadata are preserved.

A decision never directly authorizes a provider episode ID. Planning still resolves the selected numbering evidence through that show's provider catalog. This keeps ordinary catalog validation, duplicate-provider-episode protection, preflight, and plan hashing in the normal path.

Example:

```toml
schema_version = 3

[[shows]]
key = "example-series-key"
provider = "tvmaze"
provider_id = 45001
aliases = ["Example Series"]
numbering_mode = "aired"
title_preference = "provider"

[[episode_decisions]]
source = "Example Series/Example Series - ambiguous.mkv"
show_provider = "tvmaze"
show_provider_id = 45001
numbering_mode = "aired"
season = 1
episodes = [2]
reasons = ["reviewed against the provider catalog"]
```

Each episode decision declares exactly one numbering family:

- `aired`: `season` plus one or more `episodes`;
- `absolute` or `parenthesized-absolute`: a positive `absolute_episode`;
- `special`: `special_kind` plus positive `special_episode`;
- `date`: canonical `episode_date` in `YYYY-MM-DD` form;
- `segment-title`: trimmed `segment_hint` and `title_hint`.

Fields from multiple numbering families are rejected. Duplicate source paths are compared after Unicode normalization and case folding and are rejected as conflicts. Source paths must be relative and cannot contain drive prefixes or dot segments.

The `show_provider` and `show_provider_id` fields are a safety binding, not a second show resolver. Planning consumes a decision only after the source show has already resolved, only when the source is still an episode candidate, and only when both the provider identity and numbering mode agree with that canonical show. A mismatch is a planning configuration error before catalog access. After the decision is applied, the normal catalog assignment must still find the requested episode evidence; a missing catalog row remains unresolved rather than being forced through.

The resulting plan records the episode-decision evidence together with the ordinary show-resolution and catalog evidence, so the local intervention is visible in audit output and participates in deterministic plan hashing through the override snapshot.

## Show-level example

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

Validation rejects unsupported schema versions, unknown fields, malformed types, invalid enum values, untrimmed identities, duplicate aliases after Unicode/case normalization, identities that make different show entries ambiguous, duplicate provider identities, incomplete override-title preferences, malformed episode decisions, mixed numbering evidence, duplicate episode-decision sources, and unsafe episode-decision paths.

Planning also fails closed when an episode decision targets a source that is not an episode candidate, names a provider identity different from the resolved show, or selects a numbering mode different from the canonical show policy. Provider catalog misses remain unresolved.

An alias may normalize to the same identity as its own entry key because both names select the same show. Equivalent identities that point at different show entries remain invalid.

The loader uses Unicode NFKC normalization plus case-insensitive identity matching so equivalent Unicode/casing cannot make a catalog platform-dependent.

## Determinism

A validated override catalog has a canonical byte representation and stable SHA-256 snapshot identity. Show-table order, alias order, episode-decision table order, and decision-reason order do not affect that identity. Local filesystem paths are not included in the snapshot; only normalized relative source references are recorded.

Using the same inventory, cache snapshots, planning config, and override snapshot produces the same immutable plan hash. Changing an override changes the recorded override snapshot and therefore the plan identity.

## Local-only handling

Real library-specific override catalogs can reveal media names, provider IDs, and organizational details. Keep them local and untracked. The repository ignores the recommended root-level filename `.jmo-overrides.toml`.

Do not add real library override catalogs, plan manifests, or unresolved reports to bug reports. Reduce a problem to a fabricated synthetic case before committing it to the public repository.
