# Changelog

All notable public changes will be documented in this file. The project follows Semantic Versioning once releases begin.

## Unreleased

- Plan-only development continues. No media-mutating apply command or public package release exists yet.
- Release-candidate, provider, cache, architecture, and audit-output documentation now matches the active schema and runtime behavior.
- CI verifies the active installed plan schema, covers Python 3.13, enforces branch coverage, and uses immutable Node 24 action revisions.
- Packaging metadata and data-resource configuration use current setuptools contracts.
- Show-resolution orchestration has an enforced complexity ceiling while retaining its existing fail-closed decisions.
