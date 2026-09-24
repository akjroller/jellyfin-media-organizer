# Changelog

All notable public changes will be documented in this file. The project follows Semantic Versioning.

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

## Unreleased

No unreleased changes.
