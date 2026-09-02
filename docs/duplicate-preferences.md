# Duplicate winner preferences

Jellyfin Media Organizer keeps duplicate handling fail-closed by default. If two different source operations map to the same logical Jellyfin destination and there is no deterministic winner evidence, both remain suspicious and preflight stays blocked.

Override schema version 2 can provide explicit local preference evidence for a genuine duplicate group. This is planning input only: it does not move, delete, overwrite, or quarantine media.

## Reviewing duplicate collisions

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

## Source-specific preferences

Preferences use the source video's normalized path relative to the authorized Shows root. Absolute paths, drive-qualified paths, and `..` traversal are rejected.

```toml
schema_version = 2

[[duplicate_preferences]]
source = "Example Series/release-a.mkv"
rank = 100
reasons = ["reviewed preferred source"]
```

A preference may be supplied for only the intended winner. Missing preference entries on the other candidates are not treated as implicit quality evidence. If more than one candidate has the same highest explicit rank, the group remains suspicious. If no unique highest explicit rank exists, planning does not invent a winner.

The planner also rejects stale or unsafe preference entries. A configured source must exist as a movable plan candidate and must actually participate in a duplicate collision. This prevents a preference file from silently drifting away from the plan it was reviewed against.

## Determinism and audit evidence

Duplicate preference tables participate in the canonical override snapshot. Table ordering and reason ordering do not change the snapshot identity, while changing a source, rank, or reason does.

When a preference selects a winner, the immutable duplicate decision records the complete candidate set, the selected winner, loser decisions, explicit rank, and configured reasons. Video and associated subtitle/sidecar files remain one indivisible source operation group.

Exact byte-equivalent duplicates may still be resolved by the existing SHA-256 rule. Otherwise a unique explicit local preference has the next authority. Automatic release-quality evidence is considered only after those cases and remains conservative: remux and encode candidates are incomparable, different known source families are incomparable, and incomplete evidence does not invent a winner. Token-delimited forms such as `BD Remux` and `BD-Remux` are recognized as Blu-ray remux metadata without changing those comparison rules.

Modification time, filesystem enumeration order, source path length, and file size are never treated as winner evidence.

## Local-only handling

Real duplicate preference files can reveal library filenames. Keep them in the same local, untracked override file used by `jmo plan --overrides`; do not commit real library preferences to the public repository.
