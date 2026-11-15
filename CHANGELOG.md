# Changelog

All notable public changes will be documented in this file. The project follows Semantic Versioning.

## Unreleased

- Jellyfin episode files now move with their episode: `<video filename>.nfo`
  and `<video filename>-thumb.<image>` are renamed to the new episode filename
  in the same operation group, so apply and rollback treat them like
  subtitles. Series and season files (`tvshow.nfo`, `poster.jpg`, ...) and
  episode files shared by two same-named videos stay in place.
- JMO now organizes `.m4v`, `.mov`, `.webm`, `.wmv`, `.ts`, `.m2ts`, `.mts`,
  `.mpg`, and `.mpeg` files in addition to `.mkv`, `.mp4`, and `.avi`, keeping
  each file's extension. The same episode in two containers goes to duplicate
  review as before. Disc images (`.iso`), DVD streams (`.vob`), anything inside
  a `BDMV`, `VIDEO_TS`, or `HVDVD_TS` folder, and rarer containers stay in
  `skipped.csv` and are never moved.
- Plan validation now enforces the published plan schema inside each
  record's `show`, `parse`, and `evidence` objects, companion extensions,
  and provenance digests. Plans written by JMO already conform; only
  hand-edited or corrupted plan files are newly rejected, before review or
  apply can use them.
- `jmo apply --resume` no longer gets stuck after an automatic group rollback
  whose completion could not be journaled (for example because the journal
  device failed after the media was already restored). Resume now recognizes
  a member that is verifiably back at its source, records the recovered
  rollback, and moves it again; any other state still fails closed.
- `jmo apply` (including `--check-only` and `--resume`), `jmo rollback`, and
  the quarantine commands now prove the destination filesystem supports
  atomic no-overwrite rename before moving anything, using a temporary probe
  directory they remove again. NFS, SMB, and FUSE mounts that reject
  `RENAME_NOREPLACE` now fail early with a clear explanation instead of a raw
  "Invalid argument" partway through an apply.
- Video containers JMO does not organize yet (`.m4v`, `.ts`, `.m2ts`,
  `.webm`, `.mov`, `.wmv`, and others) are now reported instead of being
  dropped silently. They and excluded samples are listed in a new
  `skipped.csv` audit file, counted in `summary.txt`, `jmo inspect`,
  `jmo report`, and the `jmo plan` output, and never moved. One such file
  next to a planned video no longer blocks the whole plan as an
  "unsupported adjacent file"; it is an ignored companion left in place.
- Provider caches no longer persist transient failures (timeouts, rate
  limits, 5xx, network errors). One network blip no longer makes later runs
  fail until `--refresh`; online runs retry such entries, including ones
  written by older versions.
- A failed `--refresh` now keeps the previous good provider response instead
  of overwriting it with the error.
- TVMaze and TMDb now share one cache implementation, so TMDb follows the
  documented refresh, freshness, and failure-classification policy.
- Provider HTTP requests are rate limited and retry 429/5xx/network failures
  with bounded backoff, honouring `Retry-After`. DNS, connection, and timeout
  errors are now classified instead of reported as unknown.
- Fixed episode titles that begin with a number (for example
  `S01E04 5 Years Gone`) and space-separated resolutions (`S01E01 720p`)
  being parsed as an extra episode, which could plan a single episode as a
  two-part `S01E04-E05` match. Zero-padded and tightly attached
  continuations (`S01E01 & 02`, `S01E09-10`) remain multi-episode.
- Improved conservative show-title extraction for release trees whose folder
  names contain subtitle text, while preserving conflicting title evidence as
  unresolved instead of guessing.
- Hardened guided setup so it only performs read-only checks and leaves any
  mutating apply operation behind an explicit `jmo apply` command.
- Made the guided wizard stop at the read-only apply-check boundary; only the
  explicit `jmo apply` command can mutate media.
- Made wizard setup safe for Windows state directories on another volume and
  staged initialization so failed writes do not publish partial state.
- Updated first-run, security, provider, and PyPI smoke-test documentation for
  the current release and provider boundary.

## 0.4.1 - 2026-09-25

- Corrected automatic provider reporting in the live CLI planning path so
  `jmo plan --auto` and run provenance identify the adapters actually used.
- Added an end-to-end regression test for the CLI-to-planner provider handoff.

## 0.4.0 - 2026-09-24

- Added saved-configuration `jmo run` for repeatable, read-only planning after
  guided setup.
- Added the optional TMDb adapter and deterministic `auto` provider mode.
- Improved provider-aware matching, override feedback, review summaries, and
  hash-bound review decisions.
- Made the fail-closed review/apply boundary an explicit permanent invariant.
- Added cross-platform package, wheel, source-distribution, and installed
  workflow validation for the normal first-run path.

The demo intentionally remains blocked until its synthetic held and ambiguous
records are reviewed; it never silently authorizes an apply.

## 0.3.1 - 2026-09-24

- Added protected, release-only PyPI publishing through GitHub Actions Trusted
  Publishing with wheel, source-distribution, metadata, package-data, and CLI
  verification before upload.
- Added install and publication guidance for the PyPI-first community workflow.

## 0.3.0 - 2026-09-23

- Added guided first-run review workflows, evidence-aware review batches,
  structured review evidence export, and clearer held/duplicate summaries.
- Hardened quarantine planning for duplicate destinations, extension-specific
  names, reviewed extras, and already-absent members.
- Added modularity and complexity checks, one-command contributor validation,
  and a typed-package marker for downstream type checkers.
- CI and release documentation now describe the actual Linux, Windows, and
  macOS validation matrix.

## 0.2.0 - 2026-09-23

- Added `jmo report` for path-free, shareable diagnostic bundles that include
  readiness, counts, percentages, and failure categories without private paths,
  caches, credentials, approval tokens, or journals.
- Added `jmo review-status` percentages, separated duplicate/held categories,
  and an explicit review-to-apply safety boundary.
- Added `jmo init`, `jmo doctor`, `jmo demo`, `jmo inspect`, first-run guidance,
  and public issue templates and roadmap for portable community use.
- Added opt-in `jmo plan --progress`, the read-only `jellyfin_show_organizer.api`
  surface, platform-neutral getting-started guidance, and next-step summaries.
- Added the explicitly gated `jmo apply` executor with exact reviewed-artifact
  approval, durable journaling, rollback, resume, and final verification.
- Planning, review, and apply check-only remain non-mutating; duplicate, held,
  ignored, cross-filesystem, overwrite, delete, quarantine, and source-cleanup
  operations remain unavailable.
- Updated release-candidate, provider, cache, architecture, and audit-output
  documentation to match the active schema and runtime behavior.
- First verified standalone JMO version tag and release-artifact build.
- Jellyfin-focused, plan-first TV-show organization workflow with non-mutating
  planning and review plus explicitly gated apply execution.
