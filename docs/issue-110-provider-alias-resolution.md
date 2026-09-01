# Provider aliases and low-confidence show resolution

JMO treats provider-supplied alternate names as additional show-resolution evidence without lowering the ordinary primary-title match threshold.

## Alias evidence

Primary-title matching runs first. A decisive primary-title result does not require or fetch alias metadata. When primary text is non-decisive and the provider exposes aliases/AKAs, JMO may fetch the aliases lazily and compare them using the same Unicode/case/punctuation normalization used for primary titles.

- An exact normalized provider alias contributes first-class title evidence.
- A very high-similarity alias may strengthen a candidate, but it does not lower the global automatic-match threshold. Without corroborating evidence it cannot become an automatic match merely because the threshold was relaxed.
- Alias collisions stay ambiguous.
- Malformed or unavailable alias metadata is indeterminate. JMO does not treat a failed alias lookup as evidence against that candidate.
- Alias request identities and deterministic content snapshots are recorded in candidate evidence when aliases are consulted.

TVMaze AKA responses are cached separately under the configured cache root. Warm and offline lookups reuse that local record and perform no provider request.

## Catalog rescue

If text and alias evidence remain non-decisive, JMO may use full group episode evidence as a separate rescue rule. This is not a lower title threshold.

Every candidate returned by the provider search is evaluated so a weak text score cannot hide a catalog-compatible competitor. A candidate is compatible only when the source group has one uniquely compatible aired/absolute numbering interpretation against that candidate's complete catalog.

Automatic rescue requires exactly one compatible show candidate. Multiple compatible candidates, no compatible candidates, ambiguous numbering within a candidate, malformed catalog data, or provider/catalog failure remain suspicious or unresolved.

Explicit local overrides and embedded provider identities remain higher-authority inputs and are resolved before alias or catalog rescue logic.

All examples and tests for this workflow use fabricated names and provider identities.
