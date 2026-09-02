# Duplicate winner preferences

Jellyfin Media Organizer keeps duplicate handling fail-closed by default. If two different source operations map to the same logical Jellyfin destination and there is no deterministic winner evidence, both remain suspicious and preflight stays blocked.

Override schema version 2 can provide explicit local preference evidence for a genuine duplicate group. This is planning input only: it does not move, delete, overwrite, or quarantine media.

## Reviewing duplicate collisions

Every plan audit bundle includes `duplicates.csv`. Unlike `mapping.csv`, which remains one row per video record, `duplicates.csv` is one row per destination collision so the complete duplicate decision can be reviewed together.

Each duplicate row includes:

- `duplicate_ref`: a stable local reference derived only from the normalized destination key and complete candidate set;
- `destination_key` and `destination`: the normalized collision identity and observed planned destination;
- `decision_state`: `winner-selected` or `review-required`;
- `candidates` and `candidate_review_refs`: every source operation competing for the destination and the same stable source refs used by other review output;
- `winner`, `winner_review_ref`, `losers`, and `loser_review_refs`: the current duplicate decision without implying deletion authority;
- `confidence` and `evidence`: the duplicate classifier's decision evidence;
- `record_statuses`, `record_sources`, and `operation_group_ids`: cross-references back to `mapping.csv` and `plan.json`.

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

The planner also rejects stale or unsafe preference entries. A configured source must exist as a movable plan candidate and must actually participate in a destination collision. This prevents a preference file from silently drifting away from the plan it was reviewed against.

## Determinism and audit evidence

Duplicate preference tables participate in the canonical override snapshot. Table ordering and reason ordering do not change the snapshot identity, while changing a source, rank, or reason does.

When a preference selects a winner, the immutable duplicate decision records the complete candidate set, the selected winner, loser decisions, explicit rank, and configured reasons. Video and associated subtitle/sidecar files remain one indivisible source operation group.

Exact byte-equivalent duplicates may still be resolved by the existing SHA-256 rule. Modification time, filesystem enumeration order, source path length, and file size are never treated as winner evidence.

## Local-only handling

Real duplicate preference files can reveal library filenames. Keep them in the same local, untracked override file used by `jmo plan --overrides`; do not commit real library preferences to the public repository.
