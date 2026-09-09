# Apply safety contract

`jmo apply` is the only media-mutating command. It consumes one immutable reviewed plan and never reruns parsing, provider resolution, episode matching, duplicate selection, destination construction, or held-source review.

Planning, review, and `jmo apply --check-only` remain non-mutating. A ready plan is necessary but is not permission to move media.

## Exact approval boundary

Apply requires all of the following to agree exactly:

- supported plan schema and canonical `plan.json` SHA-256;
- complete review-session SHA-256;
- clean 40-character source revision recorded by the reviewed plan;
- tool, configuration, override, and provider-cache snapshot context;
- matching `run-provenance.json` and ready `preflight.json` with zero findings;
- the complete derived operation-group scope;
- explicit source and destination roots;
- an exact confirmation token bound to both hashes, the revision, and both resolved roots.

Any mismatch fails before mutation. A partial review, dirty planning revision, provider failure, stale source fingerprint, existing destination, changed candidate set, or unsupported artifact fails closed.

The executor must identify the exact clean Git commit recorded by the reviewed plan. Source checkouts use their own Git state. Installed wheels and source distributions use embedded build metadata and verify every packaged Python/data file against that record; they never inherit an ancestor checkout's revision. Dirty builds, modified packages, unstamped installs, and different or unavailable revisions are refused. Build metadata is an integrity check, not a publisher signature.

After executor code changes, generate and review a fresh plan on the final clean commit. An older green plan is development evidence, not authority for a newer executable.

## Movement eligibility

Only video records with status `matched` or `extra` enter apply operation groups. Only `associated` companions belonging to those exact video groups join them.

The following never move:

- `duplicate` video records, including losers whose audit row retains a collided destination;
- `held` video records;
- `ignored` or `duplicate` companions;
- any unresolved or suspicious item.

Eligibility is determined by terminal status and the validated operation-group contract, never by `destination != null`.

## Required check-only pass

Use fabricated paths in documentation and public reports. On a private machine, supply the exact local artifacts and approval values:

```text
jmo apply LocalState/reviewed/plan.json \
  --preflight LocalState/reviewed/preflight.json \
  --run-provenance LocalState/reviewed/run-provenance.json \
  --source-root ExampleMedia/Shows \
  --destination-root ExampleMedia/OrganizedShows \
  --approve-plan-sha256 <64-hex-plan-hash> \
  --approve-review-session-sha256 <64-hex-session-hash> \
  --approve-source-revision <40-hex-clean-revision> \
  --check-only
```

Check-only performs full live revalidation, creates no destination directories, writes no journal, and moves nothing. It prints the exact root-bound confirmation token required by an actual apply.

## Actual apply

An actual run uses the same artifacts, hashes, revision, and roots, plus a new journal outside all media roots and the exact token printed by check-only:

```text
jmo apply LocalState/reviewed/plan.json \
  --preflight LocalState/reviewed/preflight.json \
  --run-provenance LocalState/reviewed/run-provenance.json \
  --source-root ExampleMedia/Shows \
  --destination-root ExampleMedia/OrganizedShows \
  --journal LocalState/apply-001.jsonl \
  --approve-plan-sha256 <64-hex-plan-hash> \
  --approve-review-session-sha256 <64-hex-session-hash> \
  --approve-source-revision <40-hex-clean-revision> \
  --confirm-apply '<exact-token-from-check-only>'
```

If `--confirm-apply` is omitted, a real interactive terminal must type the complete displayed token. Non-interactive execution cannot bypass this confirmation.

## Filesystem rules

- Source and destination roots must already exist as real directories, not links or junctions.
- Every source is rechecked against its approved size and nanosecond mtime immediately before moving. SHA-256 is also checked when present in the plan.
- Every destination is rechecked as absent immediately before moving.
- Existing parent chains must remain real directories. Apply creates only missing approved destination parents.
- Source and target must be on the same filesystem/device.
- No-op detection uses exact relative names only when the validated roots are the same. Separate-root imports retain unchanged relative names as moving operations. Case-only changes are not silently skipped; an existing destination on an insensitive filesystem remains a blocker.
- The move primitive is an atomic no-overwrite rename: native non-replacing rename on Windows, `renameat2(RENAME_NOREPLACE)` on Linux, or `renamex_np(RENAME_EXCL)` on macOS. Unsupported hosts fail closed.
- Apply does not perform cross-filesystem copy-and-delete, overwrite, source-directory cleanup, duplicate deletion, or quarantine. Separate `quarantine-plan`, `quarantine`, and `quarantine-restore` commands implement explicitly approved reversible duplicate handling; see [First run](first-run.md#recovery-and-support-boundaries).

## Journal and group recovery

The JSON Lines journal is append-only and fsynced after every event. Every event carries the exact plan hash, review-session hash, source revision, sequence, result, and relevant group/member paths. Member start entries include the planned fingerprint pre-state and recovery identity.

One moving video and all deterministic companions form one operation group. If a later member fails, already-moved members in that group are verified and atomically restored in reverse order. Empty destination directories are intentionally left in place.

The persistent adjacent lock file uses an operating-system advisory lock. A crash releases the lock automatically; its presence alone does not block recovery.

Resume uses the exact same command and approval context plus `--resume`. It validates the existing journal. Completed groups are verified and skipped. If a crash occurred after an atomic rename but before its completion event, resume accepts it only when the source is absent and the exact destination fingerprint matches; otherwise it stops with recovery guidance.

If automatic rollback cannot prove the source/destination state, JMO refuses to guess and reports the exact member requiring manual inspection.

## Final verification

Before reporting success, apply verifies every approved moving member at its destination with its source absent. Keep the journal with the approved plan, preflight, provenance, review session, and override artifacts. Do not publish those private artifacts.
