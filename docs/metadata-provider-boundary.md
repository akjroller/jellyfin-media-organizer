# Metadata provider boundary

Jellyfin Media Organizer keeps metadata-provider transport and response shapes outside the core planning logic. TVMaze remains the initial provider, but provider-specific JSON and HTTP/cache details are normalized before show resolution or episode assignment consumes them.

## Domain identity

Provider-backed objects use `ProviderIdentity(provider, value)` rather than a bare numeric ID. The stable textual form is `provider:value`, for example `tvmaze:12345`.

A canonical show therefore carries:

- the source-group key;
- one namespaced provider identity;
- the canonical display title and optional year;
- the explicit numbering policy.

Existing `tvmaze_id` constructors and properties remain compatibility aliases for the initial provider. New provider-aware code should use `provider_identity`, `provider`, and `provider_id` instead.

Plan manifests serialize the namespaced identity. They do not serialize a provider-neutral object as though every provider used a TVMaze integer.

## Adapter interface

`MetadataProvider` is intentionally small. An adapter supplies:

1. `search_shows(title)` returning a normalized `ProviderSearchSnapshot`;
2. `episode_catalog(show_identity)` returning a normalized `ProviderEpisodeCatalog`.

Snapshots contain the provider namespace, provider-local request key, normalized domain objects, and explicit unresolved/error state. Their `snapshot_identity` combines the provider namespace and request key so snapshot provenance cannot collide across providers.

Core show scoring consumes `ProviderShow` objects. Core episode assignment consumes `ProviderEpisode` objects. Neither layer parses raw TVMaze JSON.

## TVMaze adapter

`TvmazeProviderAdapter` wraps the existing `TvmazeCatalogCache` and injected JSON getter. It is the only layer responsible for translating TVMaze search and episode response shapes into normalized provider models.

The adapter preserves the existing cache behavior and request keys for compatibility while adding the `tvmaze` namespace at the snapshot boundary. Warm-cache behavior and matching outcomes therefore remain unchanged.

Malformed provider rows are normalized into explicit catalog errors before assignment. Duplicate provider episode IDs and duplicate aired season/episode coordinates remain fail-closed conditions.

## Overrides

Show overrides may continue to use the compatibility field:

```toml
[[shows]]
key = "example"
tvmaze_id = 12345
```

Provider-neutral overrides use a namespace and provider-local value:

```toml
[[shows]]
key = "example"
provider = "example-provider"
provider_id = "series-12345"
```

Supplying both forms is accepted only when they describe the same TVMaze identity. Conflicting identities fail closed.

## Adding another provider later

A future provider should require an adapter and synthetic fixture set, not changes to filename parsing, show scoring, episode assignment, destination construction, duplicate handling, or preflight logic.

The adapter must:

- choose a stable lowercase provider namespace;
- return namespaced show and episode identities;
- normalize search results and episode catalogs into the existing domain models;
- make cache/snapshot provenance provider-scoped;
- return explicit unresolved/error states instead of leaking transport exceptions into matching;
- use fabricated public regression fixtures only.

This boundary does not add another provider and does not change JMO's plan-only safety model.
