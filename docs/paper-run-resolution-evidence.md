# Conservative structural resolution evidence

JMO can use deterministic structural evidence to discover or disambiguate show candidates without lowering its normal confidence thresholds.

The resolver may use:

- a two-token compacted provider query only when the exact provider search returned no candidates;
- exact token-initialism equivalence, such as a short source token that expands to the initials of contiguous provider-title words;
- multiple aired episode coordinates to rescue a lower-confidence title only when exactly one complete provider catalog explains the whole observed group; and
- multiple exact episode-title observations at known coordinates to break an otherwise unresolved provider-title collision.

These are fail-closed rules. Provider/catalog failures, incomplete evidence, single-episode low-confidence groups, ambiguous title matches, and multiple compatible candidates remain unresolved or suspicious. The normal show-match threshold is not lowered.

These rules do not authorize media mutation, overwrite, or deletion.