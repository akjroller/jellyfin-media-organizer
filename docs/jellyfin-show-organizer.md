# Jellyfin show organizer

This fork will contain a standalone, plan-first organizer for television video
files. It preserves mnamer's MIT-licensed history and useful parsing/provider
concepts, but it will not extend mnamer's file-by-file batch relocation flow.

## Provenance

- Repository base: upstream `jkwill87/mnamer` `main` at `4703dfe`.
- Production evidence: mnamer 2.7.2 with locally modified `target.py` and
  `frontends.py`.
- Local patches are reference material only. They must be ported into repository
  source with focused regression tests; `.venv` content must never be copied.

## Non-negotiable safety boundary

The organizer is read-only until a complete library plan has passed review.
Development and CI must use synthetic files under pytest temporary directories
and checked-in, anonymized metadata snapshots. Tests must never access
`D:\Jellyfin`.

The initial workflow is:

1. Inventory video files without mutation.
2. Parse locally and group by probable show.
3. Resolve each show once to one canonical provider ID.
4. Fetch and cache each show's episode catalog once.
5. Assign episodes collectively within that canonical show.
6. Classify extras, unresolved files, suspicious matches, and duplicates.
7. Build one immutable plan and validate the whole plan case-insensitively.
8. Produce audit reports without creating media destination directories.

An eventual apply command is a separate, approval-gated milestone. It must
consume the exact approved plan hash, revalidate every source and destination,
journal every operation, avoid overwrite and deletion, support resume and
recovery, and keep duplicate quarantine outside Jellyfin's active Shows tree.

## Public-repository data policy

Do not commit real inventories, audit CSVs, verbose logs, provider caches,
manifests, journals, hashes tied to private media, or absolute user/media paths.
Sanitized fixtures should retain only the filename grammar required for a test.

## Compatibility

The existing `mnamer` CLI and its tests remain intact. The organizer will use a
separate package boundary and command entry point so upstream updates can still
be compared and integrated deliberately.
