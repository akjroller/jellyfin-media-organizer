# Duplicate winner preferences

Jellyfin Media Organizer keeps duplicate handling fail-closed by default. If two different source operations map to the same logical Jellyfin destination and there is no deterministic winner evidence, both remain suspicious and preflight stays blocked.

Override schema version 2 can provide explicit local preference evidence for a genuine duplicate group. This is planning input only: it does not move, delete, overwrite, or quarantine media.

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
