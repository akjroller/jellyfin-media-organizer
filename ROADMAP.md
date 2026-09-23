# Roadmap

Jellyfin Media Organizer is a plan-first, fail-closed tool for organizing media libraries across operating systems and provider configurations.

## Current release: v0.1.1

The current release includes deterministic planning, hash-bound review sessions, check-only apply validation, recovery journals, onboarding commands, redacted inspection, review progress, and sanitized diagnostic reports. The next minor release will follow another user-facing improvement validated in CI.

## Priorities

- **First-run and onboarding:** keep `init`, `doctor`, `demo`, and `inspect` useful on clean installs with synthetic, portable examples.
- **Review UX:** show totals, percentages, movable and untouched records, failure categories, and evidence together; preserve resumable hash-bound decisions.
- **Provider and cache improvements:** make identity, freshness, offline replay, and provider failures clear without leaking credentials or caches.
- **Cross-platform packaging:** test supported Python versions and Windows, macOS, and Linux without machine-specific assumptions.
- **Recovery and rollback:** strengthen journal verification, resume, rollback, and uncertain-state diagnostics while keeping mutation explicit.
- **Future movie-library support:** design a provider-neutral movie domain with collision, review, sidecar, and recovery rules before mutation support.

## Proposing work

Use the issue templates with synthetic examples. Never attach real inventories, private paths, provider caches, credentials, approval tokens, or raw private filenames.
