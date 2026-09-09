# First run on your own machine

JMO currently organizes shows, not movies. Start with disposable copies of a small representative library. No example below refers to a real library. Replace the paths deliberately; never paste someone else's approval values.

## Install and choose your shell

Follow the README installation steps using Python 3.12 or later. In the checkout directory, define a session-local convenience function so the remaining commands work without changing PATH or execution policy.

Bash:

```bash
jmo() { ./.venv/bin/jmo "$@"; }
mkdir -p LocalState ExampleMedia/OrganizedShows
printf 'schema_version = 4\n' > LocalState/base-overrides.toml
```

PowerShell:

```powershell
function jmo { & .\.venv\Scripts\jmo.exe @args }
New-Item -ItemType Directory -Force LocalState, ExampleMedia/OrganizedShows
'schema_version = 4' | Set-Content -Encoding utf8 LocalState/base-overrides.toml
```

Use a new state directory if these files already exist. Place your disposable test shows in `ExampleMedia/Shows`. The destination must already exist and be on the same filesystem as the source. State, cache, session, and journal directories must be outside both media roots. An empty schema-4 override is a valid starting point; no personal override catalog is required.

## Plan and review

These one-line commands work in either shell:

```text
jmo overrides validate LocalState/base-overrides.toml
jmo plan ExampleMedia/Shows --destination-root ExampleMedia/OrganizedShows --output-dir LocalState/base-plan --cache-dir LocalState/cache --overrides LocalState/base-overrides.toml --online
jmo review LocalState/base-plan/plan.json --overrides LocalState/base-overrides.toml --session LocalState/review-session.json --output LocalState/reviewed-overrides.toml --cache-dir LocalState/cache --online
```

Inspect the reports even when planning succeeds. A blocked base plan may still be reviewed if valid artifacts were produced; a configuration/provider failure is not permission to continue blindly. Select duplicate winners deliberately. Keep ambiguous sources held rather than guessing. Review does not move files.

If review stops early, repeat its command with `--resume` and a new output override filename. Exit code 12 means review is incomplete, not ready for apply. See [review workflow](review-workflow.md) for filtering, deferred items, and resume rules.

Compile a fresh plan from the completed review:

```text
jmo plan ExampleMedia/Shows --destination-root ExampleMedia/OrganizedShows --output-dir LocalState/reviewed-plan --cache-dir LocalState/cache --overrides LocalState/reviewed-overrides.toml --review-session LocalState/review-session.json --online
```

For reproducibility, repeat into another new output directory using `--offline`. It must use the same overrides/session and warmed cache. Investigate any plan or decision hash differences before approval.

## Read readiness honestly

Every audit bundle includes `remaining.csv`. It is the authoritative list of entries that `jmo apply` deliberately leaves at the source or that still block apply. Matched videos, extras, and associated companions are excluded because they are apply-movable.

`remaining.csv` marks each row as `intentional` or `blocking`:

- duplicate loser videos and duplicate companions are intentional leftovers from apply; reversible quarantine is a separate approval;
- held videos and ignored companions are intentional leftovers;
- suspicious or unresolved videos and unresolved companions are blocking leftovers.

`summary.txt` reports `readiness_state=not-evaluated|apply-ready|blocked`, `apply_safe`, `library_fully_organized`, and remaining totals. `apply_safe=true` means the approved movement subset may cross the apply boundary. It does **not** mean the source tree becomes empty. `library_fully_organized=true` is only reported when no planned leftovers remain at all.

## Check, approve, apply

Read `summary.txt`, `remaining.csv`, and preflight together. Zero preflight findings means the approved subset is safe to apply, not that every library file will be organized. Held videos, duplicate losers, and ignored companions remain untouched by apply.

Take the exact plan hash from `plan.sha256`, review-session hash from `run-provenance.json` under `review.session_sha256`, and revision from `source_revision.commit`. Replace the uppercase placeholders below with those values:

```text
jmo apply LocalState/reviewed-plan/plan.json --preflight LocalState/reviewed-plan/preflight.json --run-provenance LocalState/reviewed-plan/run-provenance.json --source-root ExampleMedia/Shows --destination-root ExampleMedia/OrganizedShows --approve-plan-sha256 PLAN_HASH --approve-review-session-sha256 SESSION_HASH --approve-source-revision REVISION --check-only
```

Check-only moves nothing. Inspect its file/group counts. To perform the approved test move, use the same command without `--check-only` and add `--journal LocalState/apply-001.jsonl`. The interactive prompt requires the complete confirmation token. Automated use requires `--confirm-apply` with that exact token. This is the first mutating step.

Keep the complete bundle and journal. Verify the organized files and Jellyfin's interpretation before using the workflow on a larger library. Have an independent backup before real-library mutation.

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
| NAS, SMB, unusual mounts | Not blanket-certified; validate actual atomic-rename and locking behavior with disposable files first; unsupported primitives fail closed |
| Links and junctions | Rejected as media roots and within operation parent chains |
| Completed-apply rollback | Supported with exact original artifacts, completed apply journal, revalidation, and separate approval/journal |
| Duplicate quarantine and restore | Supported as separately approved same-filesystem operations; no deletion |
| Duplicate deletion | Not implemented |

Updates require reinstalling the package and generating fresh reviewed artifacts when the executable revision changes. Never substitute an old approval token for a new build.
