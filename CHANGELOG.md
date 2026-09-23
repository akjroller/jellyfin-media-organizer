# Changelog

All notable public changes will be documented in this file. The project follows Semantic Versioning.

## 0.2.0 - 2026-09-23

- Added `jmo report` for path-free, shareable diagnostic bundles that include
  readiness, counts, percentages, and failure categories without private paths,
  caches, credentials, approval tokens, or journals.
- Added `jmo review-status` percentages, separated duplicate/held categories,
  and an explicit review-to-apply safety boundary.
- Added `jmo init`, `jmo doctor`, `jmo demo`, `jmo inspect`, first-run guidance,
  and public issue templates and roadmap for portable community use.

## 0.1.0 - 2026-09-21

- First verified standalone JMO version tag and release-artifact build.
- Jellyfin-focused, plan-first TV-show organization workflow with non-mutating
  planning and review plus explicitly gated apply execution.

## Unreleased

- Added read-only first-run usability commands: `jmo doctor` checks media and
  state-root prerequisites, `jmo inspect` summarizes an audit bundle, and
  `jmo config example` / `jmo overrides example` generate starter files.
- Added `jmo init` for non-overwriting state/config creation, `jmo demo` for a
  disposable synthetic workspace, and next-step guidance after plan, review,
  and apply check-only commands.
- Added opt-in `jmo plan --progress` reporting for inventory and show resolution;
  JSON mode remains free of progress noise.
- Added the narrow read-only `jellyfin_show_organizer.api` integration surface
  with `plan_library` and structured `inspect_audit` results.
- Added a platform-neutral getting-started guide with explicit PowerShell and
  Linux/macOS workflows, review/apply boundaries, recovery guidance, and safe
  public bug-reporting practices.
- Added the explicitly gated `jmo apply` executor with exact reviewed-artifact approval, clean-revision and root-bound confirmation, status-gated operation groups, same-filesystem atomic no-overwrite moves, durable journaling, rollback, resume, and final verification.
- Planning, review, and apply check-only remain non-mutating; duplicate, held, ignored, cross-filesystem, overwrite, delete, quarantine, and source-cleanup operations remain unavailable.
- Release-candidate, provider, cache, architecture, and audit-output documentation now matches the active schema and runtime behavior.
- CI verifies the active installed plan schema, covers Python 3.13, enforces branch coverage, and uses immutable Node 24 action revisions.
- Packaging metadata and data-resource configuration use current setuptools contracts.
- Show-resolution orchestration has an enforced complexity ceiling while retaining its existing fail-closed decisions.
- Aired-coordinate assignment now blocks catalog-supported filename-title contradictions, including compound episode packages, instead of silently accepting the parsed coordinate.
- Segment-counted group proof treats alternate releases of the same parsed coordinate and title as one proof unit while retaining collision checks for different coordinates.
- Duplicate preferences for a present source remain dormant while an independent blocking finding makes that source non-movable, so the finding can be audited without discarding the reviewed preference.
