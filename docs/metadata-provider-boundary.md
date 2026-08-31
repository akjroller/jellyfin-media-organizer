# Metadata provider boundary

Jellyfin Media Organizer currently uses TVMaze as its configured metadata provider, but the planning pipeline does not treat TVMaze response objects or integer IDs as core domain types.

## Provider contract

`MetadataProvider` exposes normalized show search and episode catalog retrieval through `ProviderSearchSnapshot`, `ProviderShow`, `ProviderEpisodeCatalog`, and `ProviderEpisode`.

Provider-owned objects use `ProviderIdentity(provider, value)`. The provider namespace is normalized, while the identifier value remains an opaque provider-owned string. This prevents IDs from different providers from colliding and avoids requiring future providers to use integer identifiers.

TVMaze is implemented by `TvmazeProviderAdapter`, which is the only layer responsible for converting cached TVMaze response shapes into normalized domain records.

## Planning boundary

Show resolution and episode assignment accept a `MetadataProvider` and consume only normalized provider records. The planner creates the currently configured TVMaze adapter once and passes that adapter through resolution and assignment instead of passing raw JSON responses or TVMaze cache records into core matching logic.

Canonical shows, candidate evidence, planned provider episodes, duplicate logical identities, destination audit evidence, and preflight provider identities use namespaced provider identity. Legacy TVMaze accessors remain compatibility views for existing callers and the current v1 serialized plan format; they are not the canonical in-memory identity.

Destination construction depends on normalized show and episode models. Audit mapping reports expose the provider namespace and provider-owned identifier while retaining the existing TVMaze column for current consumers.

## Cache and provenance

The current TVMaze cache remains provider-specific at the adapter/orchestration edge. Cache snapshots recorded in plan provenance carry a provider namespace, kind, request key, and immutable snapshot hash. A future provider therefore needs its own adapter/cache implementation and fixtures without sharing or colliding with TVMaze cache keys.

## Compatibility and future providers

TVMaze remains the only configured provider in this change. Existing TVMaze matching outcomes, destinations, and offline replay behavior stay covered by the synthetic test suite. Compatibility wrappers still accept the pre-boundary TVMaze constructor forms while immediately normalizing them to `ProviderIdentity`.

A future provider should primarily require:

1. an implementation of `MetadataProvider`,
2. provider-scoped cache/snapshot fixtures,
3. orchestration that selects that provider.

It should not require provider-specific branches in filename parsing, show resolution, episode assignment, destination construction, duplicate classification, reporting, or preflight logic.

The v1 plan JSON serializer intentionally remains a TVMaze compatibility artifact while TVMaze is the only configured provider. Introducing a second configured provider requires a versioned provider-neutral wire schema, not a rewrite of the planning pipeline.
