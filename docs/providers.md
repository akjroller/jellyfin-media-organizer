# Metadata providers

JMO's planner defaults to `auto`: it uses TVMaze first and consults the
optional TMDb adapter only when the TVMaze result is unresolved or ambiguous.
This preserves the stable TVMaze path for most libraries while allowing a
second provider to supply independent identity evidence when it can help.

TMDb comparison is enabled by setting `JMO_TMDB_ACCESS_TOKEN` to a TMDb v4
read-access token. The token is kept in memory only:

```python
from pathlib import Path

from jellyfin_show_organizer.providers import TmdbProviderAdapter
from jellyfin_show_organizer.tmdb_cache import TmdbCatalogCache, tmdb_http_getter

provider = TmdbProviderAdapter(
    TmdbCatalogCache(Path("state/cache/tmdb"), offline=False),
    tmdb_http_getter("your-token"),
)
```

The cache stores only provider responses and request metadata; it never writes
the access token. Set `offline=True` to replay an existing cache without any
network calls. Missing or incomplete TMDb data remains unresolved rather than
being converted into an unsafe match.

With no token, `auto` behaves like the TVMaze-only path and remains fully
offline-safe. With a token, TMDb identities are recorded as additional
provider evidence; JMO requires provider consensus before using that evidence
to resolve an otherwise ambiguous match. A disagreement remains blocked for
review rather than being silently promoted.

Advanced users can still select the TVMaze-only behavior with the explicit
`tvmaze` provider strategy in planning configuration. The normal CLI and
wizard default remains `auto`.

## Provider boundary

Jellyfin Media Organizer currently uses TVMaze as its configured metadata provider, but the planning pipeline does not treat TVMaze response objects or integer IDs as core domain types.

### Provider contract

`MetadataProvider` exposes normalized show search and episode catalog retrieval through `ProviderSearchSnapshot`, `ProviderShow`, `ProviderEpisodeCatalog`, and `ProviderEpisode`.

Provider-owned objects use `ProviderIdentity(provider, value)`. The provider namespace is normalized, while the identifier value remains an opaque provider-owned string. This prevents IDs from different providers from colliding and avoids requiring future providers to use integer identifiers.

TVMaze is implemented by `TvmazeProviderAdapter`, which is the only layer responsible for converting cached TVMaze response shapes into normalized domain records.

### Planning boundary

Show resolution and episode assignment accept a `MetadataProvider` and consume only normalized provider records. The planner creates the currently configured TVMaze adapter once and passes that adapter through resolution and assignment instead of passing raw JSON responses or TVMaze cache records into core matching logic.

Canonical shows, candidate evidence, planned provider episodes, duplicate logical identities, destination audit evidence, and preflight provider identities use namespaced provider identity. Legacy TVMaze accessors remain compatibility views for existing callers and the current v3 serialized plan format; they are not the canonical in-memory identity.

Destination construction depends on normalized show and episode models. Audit mapping reports expose the provider namespace and provider-owned identifier while retaining the existing TVMaze column for current consumers.

### Cache and provenance

The current TVMaze cache remains provider-specific at the adapter/orchestration edge. Cache snapshots recorded in plan provenance carry a provider namespace, kind, request key, and immutable snapshot hash. A future provider therefore needs its own adapter/cache implementation and fixtures without sharing or colliding with TVMaze cache keys.

### Compatibility and future providers

TVMaze remains the only configured provider in this change. Existing TVMaze matching outcomes, destinations, and offline replay behavior stay covered by the synthetic test suite. Compatibility wrappers still accept the pre-boundary TVMaze constructor forms while immediately normalizing them to `ProviderIdentity`.

A future provider should primarily require:

1. an implementation of `MetadataProvider`,
2. provider-scoped cache/snapshot fixtures,
3. orchestration that selects that provider.

It should not require provider-specific branches in filename parsing, show resolution, episode assignment, destination construction, duplicate classification, reporting, or preflight logic.

The v3 plan JSON serializer intentionally retains TVMaze compatibility views while TVMaze is the only configured provider. Introducing a second configured provider requires a later versioned provider-neutral wire schema, not a rewrite of the planning pipeline.

## Cache and offline policy

The organizer treats provider cache data as reproducibility input, not as an invisible best-effort optimization. Planning code should be able to explain which cached provider snapshot it used and must never refresh data behind the user's back.

### Freshness windows

Title-search entries and episode-catalog entries have separate default freshness windows. TVMaze and the optional TMDb adapter share one cache implementation, so the same windows and rules apply to both:

- title searches: 7 days
- episode catalogs: 30 days

Freshness is an audit signal. In ordinary mode, an existing cache entry is replayed even when it is stale. Ordinary planning therefore remains deterministic against a warmed cache instead of silently changing because wall-clock time passed.

A deliberate refresh mode may replace a stale, corrupt, or previously failed entry. A fresh successful entry is not fetched again merely because refresh mode is enabled. A refresh that fails never replaces a previously successful response: the last good response is kept on disk and replayed, marked stale, so a provider outage during `--refresh` cannot destroy warmed catalog data.

### Offline behavior

Offline mode is a hard no-network contract. Provider getters are never called while it is enabled.

- a warm entry is replayed as-is, including a stale entry;
- a missing entry returns an explicit `miss` result with `offline cache miss` and is not written to disk;
- a corrupt entry remains an explicit corrupt result and is not repaired through the network;
- offline and refresh modes are mutually exclusive.

The public `jmo plan` command exposes this contract through `--offline`, `--refresh`, and ordinary online mode. The compatibility `organizer plan` entry point exposes the same behavior.

### Provenance and snapshot identity

Cache schema version 2 stores the provider name, normalized request key, request URL and parameters, retrieval timestamp, response/error state, provider response, and classified provider failure kind.

Every cache record exposes a deterministic SHA-256 `snapshot_id` derived from the persisted canonical record. Runtime-only values such as whether the record came from disk or the network and whether it is currently fresh or stale are excluded from that identity. Downstream immutable plans can therefore record the provider snapshot they consumed without embedding local cache paths.

### Provider failures

Provider access fails closed. The cache records explicit categories for timeouts, rate limiting (HTTP 429), provider-not-found responses (HTTP 404), transient server failures (HTTP 5xx), network failures, malformed provider responses, and unknown failures. Failed responses are not treated as resolved catalog data.

Only deterministic provider answers are persisted as failures: provider-not-found (HTTP 404) and malformed responses. Timeouts, rate limits, 5xx responses, network failures, and unknown failures describe the moment of the request rather than the provider's data, so they are reported for the current run but not written to the cache. The next online run retries them instead of replaying the failure. Older caches may still contain such transient failure records; online runs retry those too, while offline mode replays them unchanged.

### Polite provider access

The built-in HTTP getters space requests per process (TVMaze: at most one request every 0.5 seconds, matching its published limit of about 20 calls per 10 seconds; TMDb: one every 0.1 seconds). Rate limits (HTTP 429), 5xx responses, timeouts, and network failures are retried up to four attempts with exponential backoff; a `Retry-After` header in seconds is honoured, capped at 30 seconds. Provider-not-found responses and malformed JSON are not retried. Offline mode never constructs a request, so none of this applies there.

### Filesystem safety

Cache writes remain atomic through a temporary file followed by replacement inside the configured cache root. Offline misses do not create cache directories. This policy does not add cache-deletion or cleanup commands; any future maintenance command must remain confined to the authorized cache root.
