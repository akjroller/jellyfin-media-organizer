# Group numbering-mode inference

JMO may infer `aired` versus `absolute` numbering for an already-resolved source-show group when provider catalog evidence makes exactly one complete interpretation compatible.

The decision is group-level and plan-only. It does not choose numbering independently per file and it does not mutate media.

## Safety rules

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
