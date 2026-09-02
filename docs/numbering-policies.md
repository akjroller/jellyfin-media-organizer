# Episode numbering policies

JMO records numbering policy explicitly on the canonical show and in assignment evidence. The parser only preserves deterministic hints; provider-backed assignment decides whether those hints identify one catalog episode.

Supported policies are `aired`, `absolute`, `parenthesized-absolute`, `segment-title`, `special`, and `date`. Season `00` remains valid aired numbering and is never rewritten as season one. `special` is reserved for explicit `OVA`/`OAD` numbering evidence; the ordinary word `special` in an episode title does not switch policy. `date` accepts a valid `YYYY-MM-DD` episode date and does not treat a standalone release year as an episode date.

Special and date mapping fail closed. Missing provider entries remain unresolved, duplicate date candidates remain suspicious, and ambiguous special candidates require unique provider evidence. A source-show group cannot mix numbering families silently.

Aired and absolute numbering also have a conservative pre-premiere guard. JMO does not treat an old date by itself as special evidence. The guard activates only when the source-relative path contains both a valid calendar date and a token-delimited context such as `short`, `shorts`, `pilot`, `pilots`, `special`, `specials`, or `unaired`, and that date is provably before the provider catalog's first regular episode. If regular catalog dates are unavailable, only an earlier calendar year than the resolved show year is strong enough to activate the fallback.

Once the pre-premiere guard activates, normal season or absolute mapping is suppressed for that source. A unique season-zero or otherwise non-regular provider entry on the same date may be selected. Multiple same-date candidates require an exact unique normalized title match; otherwise they remain suspicious. If the provider has no compatible non-regular entry, the source remains unresolved rather than inventing an `S00` number or collapsing into a regular episode. Other sources in the same show are re-evaluated without the guarded claimant so a false pre-series collision cannot keep a legitimate regular episode suspicious.

Additional provider-supported orderings should be represented as explicit numbering policies plus normalized catalog evidence rather than show-specific filename-parser branches.
