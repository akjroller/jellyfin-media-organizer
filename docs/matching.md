# How JMO matches shows and episodes

JMO resolves each source-show group to one provider identity, then maps every file to catalog episodes. Every rule below is conservative: missing, ambiguous, or conflicting evidence leaves a file unresolved or suspicious for review instead of guessing, and none of it authorizes moving media.

## Episode numbering policies

JMO records numbering policy explicitly on the canonical show and in assignment evidence. The parser only preserves deterministic hints; provider-backed assignment decides whether those hints identify one catalog episode.

Supported policies are `aired`, `absolute`, `parenthesized-absolute`, `segment-title`, `special`, and `date`. Season `00` remains valid aired numbering and is never rewritten as season one. `special` is reserved for explicit `OVA`/`OAD` numbering evidence; the ordinary word `special` in an episode title does not switch policy. `date` accepts a valid `YYYY-MM-DD` episode date and does not treat a standalone release year as an episode date.

Special and date mapping fail closed. Missing provider entries remain unresolved, duplicate date candidates remain suspicious, and ambiguous special candidates require unique provider evidence. A source-show group cannot mix numbering families silently.

The requested OVA/OAD number remains the primary special identity. If that exact number has no non-regular provider candidate, JMO may perform a narrow fallback only when the source carries stronger identity evidence. An exact normalized `title_hint` may identify one unique season-zero or explicitly non-regular provider episode, and a valid `YYYY-MM-DD` embedded in the source-relative path may independently identify one unique non-regular episode by airdate. Ambiguous title or date evidence remains suspicious. If unique title and date evidence point to different provider episodes, the source is suspicious. Regular episodes are never eligible for this fallback, and a provider catalog that genuinely lacks the special leaves the source unresolved rather than inventing an `S00` number or provider identity.

Aired and absolute numbering also have a conservative pre-premiere guard. JMO does not treat an old date by itself as special evidence. The guard activates only when the source-relative path contains both a valid calendar date and a token-delimited context such as `short`, `shorts`, `pilot`, `pilots`, `special`, `specials`, or `unaired`, and that date is provably before the provider catalog's first regular episode. If regular catalog dates are unavailable, only an earlier calendar year than the resolved show year is strong enough to activate the fallback.

Once the pre-premiere guard activates, normal season or absolute mapping is suppressed for that source. A unique season-zero or otherwise non-regular provider entry on the same date may be selected. Multiple same-date candidates require an exact unique normalized title match; otherwise they remain suspicious. If the provider has no compatible non-regular entry, the source remains unresolved rather than inventing an `S00` number or collapsing into a regular episode. Other sources in the same show are re-evaluated without the guarded claimant so a false pre-series collision cannot keep a legitimate regular episode suspicious.

Additional provider-supported orderings should be represented as explicit numbering policies plus normalized catalog evidence rather than show-specific filename-parser branches.

## Group numbering-mode inference

JMO may infer `aired` versus `absolute` numbering for an already-resolved source-show group when provider catalog evidence makes exactly one complete interpretation compatible.

The decision is group-level and plan-only. It does not choose numbering independently per file and it does not mutate media.

### Safety rules

- Explicit local show overrides remain higher authority and bypass automatic numbering inference.
- A mode is considered only when every source carrying aired/absolute evidence supplies the evidence required by that mode.
- Dual aired and absolute evidence can therefore be evaluated under both interpretations.
- Separately mixed aired-only and absolute-only files do not produce a guess.
- Aired compatibility requires exact season/episode coordinates in the provider catalog.
- Absolute compatibility maps each requested absolute position to a concrete regular provider episode in deterministic season/episode order; a raw episode count is not recorded as sufficient evidence.
- Duplicate/ambiguous catalog coordinates, malformed required catalog rows, provider failures, multiple compatible modes, and zero compatible modes all fail closed.
- The selected mode, candidate modes, compatibility results, missing coordinates, and concrete mappings are recorded in match evidence and therefore flow into audit output.
- Cached provider search/catalog responses are reused for deterministic offline replay.

This inference layer is compatible with future parser support for preserving simultaneous aired and absolute evidence. It does not require show-specific rules or provider IDs.

## Provider aliases and catalog rescue

JMO treats provider-supplied alternate names as additional show-resolution evidence without lowering the ordinary primary-title match threshold.

### Alias evidence

Primary-title matching runs first. A decisive primary-title result does not require or fetch alias metadata. When primary text is non-decisive and the provider exposes aliases/AKAs, JMO may fetch the aliases lazily and compare them using the same Unicode/case/punctuation normalization used for primary titles.

- An exact normalized provider alias contributes first-class title evidence.
- A very high-similarity alias may strengthen a candidate, but it does not lower the global automatic-match threshold. Without corroborating evidence it cannot become an automatic match merely because the threshold was relaxed.
- Alias collisions stay ambiguous.
- Malformed or unavailable alias metadata is indeterminate. JMO does not treat a failed alias lookup as evidence against that candidate.
- Alias request identities and deterministic content snapshots are recorded in candidate evidence when aliases are consulted.

TVMaze AKA responses are cached separately under the configured cache root. Warm and offline lookups reuse that local record and perform no provider request.

### Catalog rescue

If text and alias evidence remain non-decisive, JMO may use full group episode evidence as a separate rescue rule. This is not a lower title threshold.

Every candidate returned by the provider search is evaluated so a weak text score cannot hide a catalog-compatible competitor. A candidate is compatible only when the source group has one uniquely compatible aired/absolute numbering interpretation against that candidate's complete catalog.

Automatic rescue requires exactly one compatible show candidate. Multiple compatible candidates, no compatible candidates, ambiguous numbering within a candidate, malformed catalog data, or provider/catalog failure remain suspicious or unresolved.

Explicit local overrides and embedded provider identities remain higher-authority inputs and are resolved before alias or catalog rescue logic.

All examples and tests for this workflow use fabricated names and provider identities.

## Structural resolution evidence

JMO can use deterministic structural evidence to discover or disambiguate show candidates without lowering its normal confidence thresholds.

The resolver may use:

- a two-token compacted provider query only when the exact provider search returned no candidates;
- exact token-initialism equivalence, such as a short source token that expands to the initials of contiguous provider-title words;
- multiple aired episode coordinates to rescue a lower-confidence title when only one provider candidate is available and its complete catalog explains the observed group;
- a single aired coordinate to disambiguate multiple provider candidates only when exactly one complete candidate catalog contains that coordinate; and
- multiple exact episode-title observations at known coordinates to break an otherwise unresolved provider-title collision.

These are fail-closed rules. Provider/catalog failures, incomplete evidence, ambiguous title matches, and multiple compatible candidates remain unresolved or suspicious. A single low-confidence aired coordinate cannot validate a lone provider candidate by itself. The normal show-match threshold is not lowered.

These rules do not authorize media mutation, overwrite, or deletion.
