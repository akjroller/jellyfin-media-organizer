# Local override files

Jellyfin Media Organizer supports a versioned TOML override catalog for deterministic planning decisions that cannot be resolved safely from filenames and provider data alone.

Local override files are **plan-only configuration**. They do not authorize media mutation, bypass preflight, or create an apply path.

## Current scope

Override schema versions 1, 2, 3, and 4 are accepted. The packaged synthetic defaults remain schema version 1 for compatibility. Supported show-level decisions include:

- an explicit provider show identity;
- aliases used to identify the source show group;
- a release year constraint;
- numbering mode;
- title preference and an optional preferred title.

Schema version 2 additionally supports explicit local duplicate preferences. Schema version 3 adds exact-source episode decisions for cases where review establishes the intended numbering evidence but the filename alone is not safe to interpret. Schema version 4 adds exact-source `source_holds` for reviewed media that must remain untouched because no safe provider-backed destination can be established.

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

`jmo overrides suggest plan.json` emits a separate schema-4 suggestion file for
repeated high-confidence provider candidates. It promotes a candidate only when
the same provider ID is the top candidate for at least two records in one
family. The provider title becomes the key and the observed source family is
retained as an alias. Review, edit, and validate the output before using it with
`jmo plan --overrides`; planning never loads suggestions implicitly.

The command validates the current plan manifest, groups unresolved records by source-show identity, and emits schema-version-2 TOML to stdout. It does not scan media, contact the provider, write a destination, or modify the source plan. Review references and concise reasons are emitted as comments.

Observed provider IDs remain comments such as `# observed_tvmaze_id = 45001`; they are **not** promoted into active override decisions automatically. Review the generated file and deliberately add or change identity, aliases, numbering policy, year, or title preference before using it with `plan --overrides`.

The starter intentionally does not invent episode decisions or source holds. A reviewer must add schema-version-3 `episode_decisions` or schema-version-4 `source_holds` deliberately.

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

## Exact-source leave-in-place decisions

A schema-version-4 source hold is an explicit review decision that one exact video source must remain where it is. It is intended for cases where the file is known to be real media but the active provider cannot supply a safe coordinate or other destination identity. Holds are never inferred automatically.

```toml
schema_version = 4

[[source_holds]]
source = "Example Series/Unsupported Special.mkv"
reasons = ["provider has no safe episode coordinate"]
```

A held source is emitted in the plan with terminal status `held`. It has no destination, no provider episodes, and no extra or duplicate decision. Preflight treats the video as explicitly non-moving instead of unresolved. Any companion associated with that held video is ignored/non-moving with it. The apply contract excludes held videos from mutation operation groups.

Source holds use the same normalized relative-path safety rules as other exact-source overrides. Each hold must reference a video present in the current inventory, include at least one non-empty reason, and cannot overlap an episode decision or duplicate preference for the same normalized source. A stale or unknown held path is a planning configuration error rather than a silent skip.

A hold is not a successful media match and does not create a destination. It is an auditable instruction to preserve that exact source unchanged while allowing independently safe records to proceed.

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

When a release uses source season numbers that differ from the provider catalog,
an explicit show override may include `season_map`, for example
`season_map = { "1" = 24 }`. Keys are source seasons and values are provider
seasons. This is applied only during catalog lookup, is included in the override
snapshot hash, and is never inferred automatically.

Supported `title_preference` values are `provider`, `source`, and `override`. `title_preference = "override"` requires `preferred_title`.

## Fail-closed validation

Validation rejects unsupported schema versions, unknown fields, malformed types, invalid enum values, untrimmed identities, duplicate aliases after Unicode/case normalization, identities that make different show entries ambiguous, duplicate provider identities, incomplete override-title preferences, malformed episode decisions, mixed numbering evidence, duplicate episode-decision sources, unsafe exact-source paths, duplicate source holds, and overlaps between a hold and another exact-source decision.

Planning also fails closed when an episode decision targets a source that is not an episode candidate, names a provider identity different from the resolved show, selects a numbering mode different from the canonical show policy, or when a source hold references a video absent from the current inventory. Provider catalog misses remain unresolved unless a reviewer deliberately chooses to hold that exact source in a later schema-version-4 catalog.

An alias may normalize to the same identity as its own entry key because both names select the same show. Equivalent identities that point at different show entries remain invalid.

The loader uses Unicode NFKC normalization plus case-insensitive identity matching so equivalent Unicode/casing cannot make a catalog platform-dependent.

## Duplicate winner preferences

Jellyfin Media Organizer keeps duplicate handling fail-closed by default. If two different source operations map to the same logical Jellyfin destination and there is no deterministic winner evidence, both remain suspicious and preflight stays blocked.

Override schema version 2 can provide explicit local preference evidence for a genuine duplicate group. This is planning input only: it does not move, delete, overwrite, or quarantine media.

### Reviewing duplicate collisions

Every plan audit bundle includes `duplicates.csv`. Unlike `mapping.csv`, which remains one row per video record, `duplicates.csv` is one row per duplicate collision so the complete decision can be reviewed together.

Each duplicate row includes:

- `duplicate_ref`: a stable local reference derived only from the normalized collision key and complete candidate set;
- `destination_key` and `destination`: the normalized collision identity and the observed planned destination or destinations;
- `decision_state`: `winner-selected` or `review-required`;
- `candidates` and `candidate_review_refs`: every source operation competing for the destination and the same stable source refs used by other review output;
- `winner`, `winner_review_ref`, `losers`, and `loser_review_refs`: the current duplicate decision without implying deletion authority;
- `confidence` and `evidence`: the duplicate classifier's decision evidence;
- `record_statuses`, `record_sources`, and `operation_group_ids`: cross-references back to `mapping.csv` and `plan.json`.

Most duplicate collisions use the normalized Jellyfin destination as `destination_key`. Equivalent source releases that independently resolve to the same complete provider-episode set may have different filename extensions, so they can have different final destination filenames. Those groups use a deterministic `provider-episode-collision:` identity instead. The `destination` column continues to show the actual planned destinations for review; the synthetic collision key is never used as a filesystem path.

Provider-episode collision recovery is deliberately narrow. The strict episode assignment guard fires first. JMO then rechecks affected sources individually and admits a group to duplicate selection only when every connected claimant independently resolves and every member has the exact same complete provider-episode identity set. Partial overlaps, such as a multi-episode file sharing only one episode with a single-episode file, remain suspicious and blocking.

Repeated copies of the same immutable duplicate decision are collapsed into one row. If plan records ever carry incompatible duplicate decisions for the same destination key, report generation fails closed instead of publishing contradictory review output.

`duplicates.csv` is an audit view only. Editing it has no effect on planning. Use the local override mechanism below when a genuine duplicate group needs an explicit source preference.

### Source-specific preferences

Preferences use the source video's normalized path relative to the authorized Shows root. Absolute paths, drive-qualified paths, and `..` traversal are rejected.

```toml
schema_version = 2

[[duplicate_preferences]]
source = "Example Series/release-a.mkv"
rank = 100
reasons = ["reviewed preferred source"]
```

A preference may be supplied for only the intended winner. Missing preference entries on the other candidates are not treated as implicit quality evidence. If more than one candidate has the same highest explicit rank, the group remains suspicious. If no unique highest explicit rank exists, planning does not invent a winner.

The planner also rejects stale or unsafe preference entries. A configured source must exist in the current inventory. When that source is movable, it must actually participate in a duplicate collision. If an independent blocking finding temporarily makes the source non-movable, the preference remains dormant and the original finding stays visible; it cannot turn the blocked source into a match or hide the blocker. This preserves reviewed duplicate evidence across a fail-closed reclassification without allowing an unknown or unrelated source preference to drift into the plan.

### Determinism and audit evidence

Duplicate preference tables participate in the canonical override snapshot. Table ordering and reason ordering do not change the snapshot identity, while changing a source, rank, or reason does.

When a preference selects a winner, the immutable duplicate decision records the complete candidate set, the selected winner, loser decisions, explicit rank, and configured reasons. Video and associated subtitle/sidecar files remain one indivisible source operation group.

Exact byte-equivalent duplicates may still be resolved by the existing SHA-256 rule. Otherwise a unique explicit local preference has the next authority. Automatic release-quality evidence is considered only after those cases and remains conservative: remux and encode candidates are incomparable, different known source families are incomparable, and incomplete evidence does not invent a winner. Token-delimited forms such as `BD Remux` and `BD-Remux` are recognized as Blu-ray remux metadata without changing those comparison rules.

Modification time, filesystem enumeration order, source path length, and file size are never treated as winner evidence.

### Local-only handling

Real duplicate preference files can reveal library filenames. Keep them in the same local, untracked override file used by `jmo plan --overrides`; do not commit real library preferences to the public repository.

## Determinism

A validated override catalog has a canonical byte representation and stable SHA-256 snapshot identity. Show-table order, alias order, episode-decision table order, source-hold table order, and decision-reason order do not affect that identity. Local filesystem roots are not included in the snapshot; only normalized relative source references are recorded.

Using the same inventory, cache snapshots, planning config, and override snapshot produces the same immutable plan hash. Changing an override changes the recorded override snapshot and therefore the plan identity.

## Local-only handling

Real library-specific override catalogs can reveal media names, provider IDs, and organizational details. Keep them local and untracked. The repository ignores the recommended root-level filename `.jmo-overrides.toml`.

Do not add real library override catalogs, plan manifests, or unresolved reports to bug reports. Reduce a problem to a fabricated synthetic case before committing it to the public repository.
