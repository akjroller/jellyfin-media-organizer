# Apply safety contract foundation

The repository still has **no enabled media-mutating apply command**. Issue #15 remains blocked by release-candidate approval in #14. This document describes the apply-safety primitives that can be implemented and tested before that gate is cleared.

## Exact approval context

A future apply executor must consume one already-generated immutable plan. It must not rerun parsing, provider resolution, episode matching, duplicate selection, or destination construction.

Before an executor may perform any filesystem operation, the apply contract requires all of the following to agree exactly:

- the current supported plan schema version;
- the canonical SHA-256 of `plan.json`;
- the approved tool version;
- the approved configuration snapshot;
- the approved override snapshot;
- the complete provider/cache snapshot context;
- a `preflight.json` for the same plan hash with `ready = true`, no blocked groups, and no findings.

Any mismatch fails closed before filesystem access.

## Operation groups

The contract derives immutable operation groups from the approved manifest. One group contains exactly one video and any associated companions that share its `operation_group_id`. Duplicate losers and explicitly ignored companions are non-moving. Unresolved or suspicious videos/companions are rejected at the apply boundary.

A group is retained when at least one member would change path. This allows a video no-op and a companion rename to remain one indivisible operation group instead of splitting sidecar handling from the video decision.

The contract stores only approved source-relative paths, destination-relative paths, and source fingerprints. It does not invent or recalculate destinations.

## Live read-only revalidation

`apply_validation` now provides the live-state checks that a future executor must run immediately before each move. These checks are deliberately read-only and perform no directory creation or media mutation.

For every moving member they require:

- source and destination roots to already exist as real directories rather than symlinks or junctions;
- source and destination identities to remain safe relative paths with no drive qualification, absolute paths, empty segments, or traversal segments;
- every existing source/destination parent component to remain a real directory rather than a symlink or junction;
- the source to still exist as a regular file;
- source size and nanosecond modification time to match the approved fingerprint;
- SHA-256 to match when the approved plan contains one, including a second stat check to detect changes during hashing;
- the exact destination path to remain absent, including broken symlinks;
- the source and nearest existing destination parent to be on the same filesystem/device.

A missing destination parent is allowed during validation but is **not created**. This keeps directory creation behind the later journaled apply executor while still proving the target filesystem before mutation.

## Journal replay foundation

The append-only journal model records ordered events tied to one exact plan hash:

- group started;
- member completed;
- group failed;
- group completed.

Replay rejects non-contiguous sequence numbers, events for another plan, unknown groups, member paths outside the approved group, duplicate member completion, and group completion before every moving member is recorded.

A failed group may be started again during recovery. Previously completed members remain recorded, so a future executor can revalidate reality and avoid blindly repeating an already-completed move.

## Still blocked

This foundation does **not** implement or expose:

- `jmo apply`;
- directory creation;
- rename/move operations;
- append/fsync journal persistence;
- verification after moves;
- rollback or recovery writes.

Those operations remain gated until #14 approves an exact immutable plan hash and the later #15 implementation adds platform-safe no-overwrite filesystem operations plus fault-injection coverage. Cross-filesystem copy+delete remains explicitly out of scope for the first apply release.
