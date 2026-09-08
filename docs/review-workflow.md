# Non-mutating review workflow

`jmo review` is the human-review boundary between an immutable JMO plan and a new reviewed override contract. It exists to record explicit decisions for duplicate groups and held sources without changing the media library.

**Review never moves, renames, copies, overwrites, deletes, or quarantines media.** A quarantine-candidate answer records review state for a possible future workflow only; no quarantine execution exists. There is still no `jmo apply` command.

## Plan schema requirement

Review requires a freshly generated **plan schema v3** manifest. Plan schema v2 remains a valid historical/read-only manifest format, but it does not contain the structured duplicate `collision_class` required for safe review.

JMO does not infer a missing collision class from terminal status, evidence prose, or record order. If a v2 plan is supplied to `jmo review`, regenerate it with the current `jmo plan` and review the new v3 plan instead.

A normal sequence is:

```bash
jmo plan ExampleMedia/Shows \
  --destination-root ExampleMedia/OrganizedShows \
  --output-dir LocalState/audit-before-review \
  --cache-dir LocalState/cache \
  --overrides LocalState/base-overrides.toml
```

Use the resulting `plan.json` as the review input. Keep the audit directory, cache, review session, answers files, and override files outside every media root.

## First review run

A first interactive run needs four explicit artifacts/locations:

```bash
jmo review LocalState/audit-before-review/plan.json \
  --overrides LocalState/base-overrides.toml \
  --session LocalState/review-session.json \
  --output LocalState/reviewed-overrides.toml \
  --cache-dir LocalState/cache \
  --online
```

Interactive mode requires a TTY. Use `--offline` when every provider lookup needed by the selected review items is already available in the cache. `--online` permits provider requests for review paths that require provider confirmation.

The command will not overwrite an existing session on a first run and will not overwrite an existing active override output on any run. Choose a new `--output` path for each compilation of reviewed state.

## Artifact roles

### Review session

`--session` is the resumable review ledger. It binds:

- the exact input plan hash;
- the exact base override snapshot;
- each review item to a stable review reference;
- duplicate candidate-set identity, including source/companion fingerprints;
- held-source identity, including source/companion fingerprints;
- each answered, deferred, or pending state;
- any explicitly approved partial scope.

The session is persisted atomically after decisions. On interruption, the last successfully written session remains the resume point.

### Answers file

`--answers` supplies a non-interactive schema-2 answer bundle. It is bound to the exact starting plan, base override snapshot, starting review-session hash, review reference, and item identity. It cannot silently apply answers to changed candidates or changed source/companion fingerprints.

A duplicate answer has the following general shape:

```json
{
  "schema_version": 2,
  "plan_sha256": "<exact-plan-sha256>",
  "base_override_snapshot": "<exact-base-override-sha256>",
  "session_sha256": "<exact-starting-session-sha256>",
  "answers": [
    {
      "review_ref": "<stable-review-ref>",
      "action": "select_winner",
      "expected_identity_sha256": "<exact-item-identity-sha256>",
      "winner": "Fabricated Series/release-a.mkv",
      "responses": []
    }
  ]
}
```

Held episode/special/extra answers use item-bound `responses` for the same prompts that interactive provider confirmation would request. Extra unused responses or too few responses fail closed.

### Active reviewed override

`--output` is a newly compiled schema-5 reviewed override contract. It is cryptographically bound to the resulting review-session hash. It contains only active reviewed decisions plus the inherited base override state needed for deterministic replanning.

The active override is not an apply authorization. It is input to another **non-mutating** `jmo plan` run and must be paired with the exact `--review-session` ledger that produced it.

## Resume

If a review is interrupted or intentionally left incomplete, resume the same session against the same plan and base overrides:

```bash
jmo review LocalState/audit-before-review/plan.json \
  --overrides LocalState/base-overrides.toml \
  --session LocalState/review-session.json \
  --output LocalState/reviewed-overrides-resumed.toml \
  --cache-dir LocalState/cache \
  --resume \
  --online
```

`--resume` requires an existing session. The plan hash and base override snapshot must still match the ledger. The new active override output path must not already exist.

Useful narrowing options are `--show`, `--kind duplicate|held`, `--ref`, and `--pending-only`.

## Review outcomes

Duplicate groups support:

- **accept recommended winner** when an automatic winner exists;
- **select winner** only for a same-logical-identity collision;
- **keep all** to preserve every candidate in place;
- **quarantine candidate marker** when a displayed winner/loser relationship is reviewable; this records future-review state only and does not move or quarantine anything;
- **defer** to leave the item unresolved in the review ledger.

A multiple-logical-identity destination conflict never permits manual winner selection.

Held sources support:

- **keep held** to continue leaving the exact source untouched;
- **episode** to bind the source to a provider-confirmed regular episode;
- **special** to bind it to a provider-confirmed special;
- **explicit extra** to classify it as a deliberate Jellyfin extra and preview a safe destination;
- **defer** to leave the item unresolved.

Provider-confirmed episode/special decisions bind the provider identity and exact provider metadata used during review. Replanning fails closed if that provider identity/metadata drifts.

## Batch acceptance

`--batch-accept-recommended` is limited to the selected duplicate scope. Every selected unresolved duplicate must already have a recommended winner, and JMO displays the complete batch before requesting one explicit confirmation. The result is still stored as one bound decision per duplicate group.

Batch acceptance cannot be combined with `--answers`.

## Complete and partial review

A **complete** review means every review item is answered. It exits successfully and produces a schema-5 active override contract.

An ordinary incomplete review saves the ledger and exits with code `12`.

`--approve-partial` is available only with an explicit `--show`, `--kind`, or `--ref` scope. Every item in that selected scope must be answered. Approved partial state permits further **non-mutating planning only**; it is recorded separately from complete review and does not authorize file movement.

The apply-safety contract explicitly rejects approved-partial review provenance. Any future apply workflow still requires separate exact full-plan approval after complete review and fresh preflight.

## Exit codes

For `jmo review`:

- `0` — review is complete, or an explicitly narrowed partial scope was fully answered and approved for further non-mutating planning;
- `2` — configuration, artifact binding, schema, provider, filesystem-state, or other review contract failure; JMO fails closed;
- `12` — review state was saved but remains incomplete and is not approved as a narrowed partial scope;
- `130` — interrupted with `Ctrl+C`; the last atomically saved session remains resumable.

These are review-command exit codes. `jmo plan` has its own plan/provider/unresolved/preflight exit-code contract.

## Reviewed replanning

After review, generate a new plan using both the active schema-5 override and its exact session ledger:

```bash
jmo plan ExampleMedia/Shows \
  --destination-root ExampleMedia/OrganizedShows \
  --output-dir LocalState/audit-reviewed-online \
  --cache-dir LocalState/cache \
  --overrides LocalState/reviewed-overrides.toml \
  --review-session LocalState/review-session.json \
  --online
```

Reviewed replanning verifies the contract/session binding before consuming reviewed decisions. It also rechecks current duplicate candidate sets, source/companion fingerprints, provider-confirmed metadata, and safe destination construction. Drift fails closed instead of silently reusing stale review state.

## Warmed-cache offline replay

For deterministic replay, keep the media inputs, configuration, reviewed override, review session, and warmed cache unchanged. Then run a second plan to a different output directory:

```bash
jmo plan ExampleMedia/Shows \
  --destination-root ExampleMedia/OrganizedShows \
  --output-dir LocalState/audit-reviewed-offline \
  --cache-dir LocalState/cache \
  --overrides LocalState/reviewed-overrides.toml \
  --review-session LocalState/review-session.json \
  --offline
```

Offline mode is a hard zero-provider-call path. Compare the online and offline plan/audit artifacts and hashes; only run provenance fields that deliberately describe provider mode should differ when the warmed cache fully reproduces the same decisions.

## Safety summary

Review is a decision-recording and preview workflow only. It does not create an apply capability and does not weaken preflight. A reviewed plan may still be blocked by unresolved items, duplicate conflicts, destination safety findings, provider drift, fingerprint drift, or any other preflight condition.

No exact plan hash is approved merely because review completed successfully.
