# Episode numbering policies

JMO records numbering policy explicitly on the canonical show and in assignment evidence. The parser only preserves deterministic hints; provider-backed assignment decides whether those hints identify one catalog episode.

Supported policies are `aired`, `absolute`, `parenthesized-absolute`, `segment-title`, `special`, and `date`. Season `00` remains valid aired numbering and is never rewritten as season one. `special` is reserved for explicit `OVA`/`OAD` numbering evidence; the ordinary word `special` in an episode title does not switch policy. `date` accepts a valid `YYYY-MM-DD` episode date and does not treat a standalone release year as an episode date.

Special and date mapping fail closed. Missing provider entries remain unresolved, duplicate date candidates remain suspicious, and ambiguous special candidates require unique provider evidence. A source-show group cannot mix numbering families silently.

Additional provider-supported orderings should be represented as explicit numbering policies plus normalized catalog evidence rather than show-specific filename-parser branches.
