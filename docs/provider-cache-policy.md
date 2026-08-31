# Provider cache policy

The organizer treats provider cache data as reproducibility input, not as an invisible best-effort optimization. Planning code should be able to explain which cached provider snapshot it used and must never refresh data behind the user's back.

## Freshness windows

TVMaze title-search entries and episode-catalog entries have separate default freshness windows:

- title searches: 7 days
- episode catalogs: 30 days

Freshness is an audit signal. In ordinary mode, an existing cache entry is replayed even when it is stale. Ordinary planning therefore remains deterministic against a warmed cache instead of silently changing because wall-clock time passed.

A deliberate refresh mode may replace a stale, corrupt, or previously failed entry. A fresh successful entry is not fetched again merely because refresh mode is enabled.

## Offline behavior

Offline mode is a hard no-network contract. Provider getters are never called while it is enabled.

- a warm entry is replayed as-is, including a stale entry;
- a missing entry returns an explicit `miss` result with `offline cache miss` and is not written to disk;
- a corrupt entry remains an explicit corrupt result and is not repaired through the network;
- offline and refresh modes are mutually exclusive.

The public `organizer plan` CLI will expose this cache contract when the end-to-end CLI work in #35 is wired.

## Provenance and snapshot identity

Cache schema version 2 stores the provider name, normalized request key, request URL and parameters, retrieval timestamp, response/error state, provider response, and classified provider failure kind.

Every cache record exposes a deterministic SHA-256 `snapshot_id` derived from the persisted canonical record. Runtime-only values such as whether the record came from disk or the network and whether it is currently fresh or stale are excluded from that identity. Downstream immutable plans can therefore record the provider snapshot they consumed without embedding local cache paths.

## Provider failures

Provider access fails closed. The cache records explicit categories for timeouts, rate limiting (HTTP 429), provider-not-found responses (HTTP 404), transient server failures (HTTP 5xx), network failures, malformed provider responses, and unknown failures. Failed responses are not treated as resolved catalog data.

## Filesystem safety

Cache writes remain atomic through a temporary file followed by replacement inside the configured cache root. Offline misses do not create cache directories. This policy does not add cache-deletion or cleanup commands; any future maintenance command must remain confined to the authorized cache root.
