# Apply safety contract

`jmo apply` is the only media-mutating command. It consumes one immutable reviewed plan and never reruns parsing, provider resolution, episode matching, duplicate selection, destination construction, or held-source review.

For a first live rehearsal, use an immutable canary scope. A scope names an explicit, sorted proper subset of operation-group IDs from one exact plan; it cannot select the first N groups, infer a selection, or be reused with another plan, review session, source revision, or journal.

Planning, review, and `jmo apply --check-only` remain non-mutating. A ready plan is necessary but is not permission to move media.

## Permanent command boundary

This separation is a compatibility and safety invariant, not merely a
workflow recommendation:

| Command | May inspect or write audit state | May mutate media |
| --- | --- | --- |
| `jmo run` / `jmo plan` | Yes, the new plan bundle only | No |
| `jmo review` | Yes, the hash-bound review ledger and answers | No |
| `jmo apply --check-only` | No journal; only a temporary rename-probe directory, removed before it returns | No |
| `jmo apply` with a valid confirmation token and journal | Yes | Yes, and only for approved groups |

Future commands must preserve this boundary. Planning and review code must not
call the apply executor, and no convenience or automatic mode may infer an
approval token, create an apply journal, or turn a check-only run into a
mutation. Changes to this contract require regression tests for every row in
the table and a documentation review before release.

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

### Optional one-group canary

Create the scope outside both media roots, using the exact hashes and clean revision from the reviewed bundle:

```text
jmo apply-scope create LocalState/reviewed/plan.json \
  --preflight LocalState/reviewed/preflight.json \
  --run-provenance LocalState/reviewed/run-provenance.json \
  --source-root ExampleMedia/Shows \
  --destination-root ExampleMedia/OrganizedShows \
  --approve-plan-sha256 PLAN_HASH \
  --approve-review-session-sha256 SESSION_HASH \
  --approve-source-revision REVISION \
  --group-id OPERATION_GROUP_ID \
  --output LocalState/canary-scope.json
```

Then add `--scope LocalState/canary-scope.json --approve-scope-sha256 SCOPE_HASH` to the check-only `jmo apply` command. The resulting token is bound to the scope as well as the plan, review session, revision, and roots. Check-only still moves nothing. A real canary requires the same exact scope, token, and an external journal; all groups outside the scope remain untouched.

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
- Before any move, `jmo apply` (including `--check-only` and `--resume`), `jmo rollback`, `jmo quarantine`, and `jmo quarantine-restore` probe that primitive inside a uniquely named `.jmo-rename-probe-*` directory under the destination (or quarantine) root: one rename to a free name must succeed, and a rename onto an existing name must be refused with both files intact. The probe touches only its own scratch entries and removes them whether it passes or fails. Many NFS, SMB/CIFS, and FUSE network mounts reject `RENAME_NOREPLACE`; those now fail before any journal entry or move with a message naming the filesystem limitation, and the same message is reported if a rename is rejected mid-run (after the group's automatic rollback).
- Apply does not perform cross-filesystem copy-and-delete, overwrite, source-directory cleanup, duplicate deletion, or quarantine. Empty source folders can be removed afterwards with the separate `prune-empty-dirs` command (see below). Separate `quarantine-plan`, `quarantine`, and `quarantine-restore` commands implement explicitly approved reversible duplicate handling; see [Recovery and support boundaries](#recovery-and-support-boundaries).

## Journal and group recovery

The JSON Lines journal is append-only and fsynced after every event. Every event carries the exact plan hash, review-session hash, source revision, sequence, result, and relevant group/member paths. Member start entries include the planned fingerprint pre-state and recovery identity.

One moving video and all deterministic companions form one operation group. If a later member fails, already-moved members in that group are verified and atomically restored in reverse order. Empty destination directories are intentionally left in place.

The persistent adjacent lock file uses an operating-system advisory lock. A crash releases the lock automatically; its presence alone does not block recovery.

Resume uses the exact same command and approval context plus `--resume`. It validates the existing journal. Completed groups are verified and skipped. If a crash occurred after an atomic rename but before its completion event, resume accepts it only when the source is absent and the exact destination fingerprint matches; otherwise it stops with recovery guidance.

If automatic rollback cannot prove the source/destination state, JMO refuses to guess and reports the exact member requiring manual inspection.

## Final verification

Before reporting success, apply verifies every approved moving member at its destination with its source absent. Keep the journal with the approved plan, preflight, provenance, review session, and override artifacts. Do not publish those private artifacts.

## Empty source folder cleanup

After a completed apply, the source folders that held the moved files are often left empty. `jmo prune-empty-dirs` removes them, as a separate, explicitly confirmed step:

```bash
jmo prune-empty-dirs --source-root ExampleMedia/Shows --apply-journal LocalState/apply.jsonl --check-only
jmo prune-empty-dirs --source-root ExampleMedia/Shows --apply-journal LocalState/apply.jsonl --journal LocalState/prune.jsonl --confirm-prune '<exact-token-from-check-only>'
```

- Only folders that held files moved by that one completed apply journal, and their parents, are candidates. The Shows root itself is never removed.
- A folder is removed only when it is completely empty, deepest first, with `rmdir`, which the operating system refuses for any folder that is not empty. A leftover file of any kind, including hidden ones such as `Thumbs.db` or `.DS_Store`, keeps the folder and its parents.
- `--check-only` lists the exact folders and prints a token bound to that list, the source root, and the apply journal. A real run requires that token and a new journal outside the source root, and refuses if anything changed since the check.
- `jmo rollback` needs the original source folders. If you pruned and then want to roll back, run `jmo restore-pruned-dirs --source-root ExampleMedia/Shows --prune-journal LocalState/prune.jsonl` first; it recreates exactly the folders the prune removed and is safe to repeat.

## Recovery and support boundaries

After interruption, preserve all files and artifacts. Resume with the exact same apply command, journal, and approval values plus `--resume`. Do not generate a replacement plan over a partially moved library and use it as a recovery shortcut.

Apply resume handles incomplete groups and verifies completed ones. To reverse a completed apply, use the separate `jmo rollback` command with the original plan, preflight, provenance, approval hashes/revision, original roots, and `--apply-journal`. Start with `--check-only`; mutation additionally requires a new `--rollback-journal` and the exact `--confirm-rollback` token. It verifies fingerprints and refuses occupied original paths. Resume an interrupted rollback using that same rollback journal and `--resume`, not apply resume. If recovery reports an uncertain state, stop and follow its member-specific guidance.

Duplicate quarantine is a separate opt-in workflow: `jmo quarantine-plan` creates an immutable artifact from reviewed duplicate decisions. `jmo quarantine` checks that artifact with `--approve-quarantine-plan-sha256`, the original approvals, and explicit source, destination, and quarantine roots. The quarantine root must be outside the library on the same filesystem. Check-only may inspect winners before apply, but mutation requires verified organized winners, a new journal, and the exact quarantine confirmation token. It moves reviewed losers only and never deletes them. `jmo quarantine-restore` reverses a completed quarantine using its original artifact/journal, a separate restore journal, and its own confirmation token. Run each command's `--help` for the required arguments; each mutating operation has its own `--check-only` gate. Restore quarantined losers before rolling back their organized winners.

| Environment or operation | Support boundary |
|---|---|
| Windows and Linux | Automated offline test coverage; same-filesystem no-overwrite moves |
| macOS | Dedicated CI runner configured; release support requires that runner to pass |
| Installed wheel/sdist | Build identity verified independently of nearby Git repositories |
| Case-only rename | Executed on case-sensitive filesystems; blocked when the destination already exists on an insensitive filesystem |
| Separate roots | Supported on the same filesystem, including already-canonical relative names |
| Cross-volume moves | Unsupported; no copy-and-delete fallback |
| NAS, SMB, unusual mounts | Apply, rollback, and quarantine probe the atomic no-overwrite rename first and refuse mounts that do not support it, with an explanation; validate locking with disposable files before a real library |
| Links and junctions | Rejected as media roots and within operation parent chains |
| Completed-apply rollback | Supported with exact original artifacts, completed apply journal, revalidation, and separate approval/journal |
| Duplicate quarantine and restore | Supported as separately approved same-filesystem operations; no deletion |
| Duplicate deletion | Not implemented |

Updates require reinstalling the package and generating fresh reviewed artifacts when the executable revision changes. Never substitute an old approval token for a new build.
