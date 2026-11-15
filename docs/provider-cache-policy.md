# Provider cache policy

The organizer treats provider cache data as reproducibility input, not as an invisible best-effort optimization. Planning code should be able to explain which cached provider snapshot it used and must never refresh data behind the user's back.

## Freshness windows

Title-search entries and episode-catalog entries have separate default freshness windows. TVMaze and the optional TMDb adapter share one cache implementation, so the same windows and rules apply to both:

- title searches: 7 days
- episode catalogs: 30 days

Freshness is an audit signal. In ordinary mode, an existing cache entry is replayed even when it is stale. Ordinary planning therefore remains deterministic against a warmed cache instead of silently changing because wall-clock time passed.

A deliberate refresh mode may replace a stale, corrupt, or previously failed entry. A fresh successful entry is not fetched again merely because refresh mode is enabled. A refresh that fails never replaces a previously successful response: the last good response is kept on disk and replayed, marked stale, so a provider outage during `--refresh` cannot destroy warmed catalog data.

## Offline behavior

Offline mode is a hard no-network contract. Provider getters are never called while it is enabled.

- a warm entry is replayed as-is, including a stale entry;
- a missing entry returns an explicit `miss` result with `offline cache miss` and is not written to disk;
- a corrupt entry remains an explicit corrupt result and is not repaired through the network;
- offline and refresh modes are mutually exclusive.

The public `jmo plan` command exposes this contract through `--offline`, `--refresh`, and ordinary online mode. The compatibility `organizer plan` entry point exposes the same behavior.

## Provenance and snapshot identity

Cache schema version 2 stores the provider name, normalized request key, request URL and parameters, retrieval timestamp, response/error state, provider response, and classified provider failure kind.

Every cache record exposes a deterministic SHA-256 `snapshot_id` derived from the persisted canonical record. Runtime-only values such as whether the record came from disk or the network and whether it is currently fresh or stale are excluded from that identity. Downstream immutable plans can therefore record the provider snapshot they consumed without embedding local cache paths.

## Provider failures

Provider access fails closed. The cache records explicit categories for timeouts, rate limiting (HTTP 429), provider-not-found responses (HTTP 404), transient server failures (HTTP 5xx), network failures, malformed provider responses, and unknown failures. Failed responses are not treated as resolved catalog data.

Only deterministic provider answers are persisted as failures: provider-not-found (HTTP 404) and malformed responses. Timeouts, rate limits, 5xx responses, network failures, and unknown failures describe the moment of the request rather than the provider's data, so they are reported for the current run but not written to the cache. The next online run retries them instead of replaying the failure. Older caches may still contain such transient failure records; online runs retry those too, while offline mode replays them unchanged.

## Polite provider access

The built-in HTTP getters space requests per process (TVMaze: at most one request every 0.5 seconds, matching its published limit of about 20 calls per 10 seconds; TMDb: one every 0.1 seconds). Rate limits (HTTP 429), 5xx responses, timeouts, and network failures are retried up to four attempts with exponential backoff; a `Retry-After` header in seconds is honoured, capped at 30 seconds. Provider-not-found responses and malformed JSON are not retried. Offline mode never constructs a request, so none of this applies there.

## Filesystem safety

Cache writes remain atomic through a temporary file followed by replacement inside the configured cache root. Offline misses do not create cache directories. This policy does not add cache-deletion or cleanup commands; any future maintenance command must remain confined to the authorized cache root.
