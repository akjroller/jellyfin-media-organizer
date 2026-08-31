# Metadata provider boundary

Jellyfin Media Organizer currently uses TVMaze, but core planning must not assume every future metadata identity is a bare TVMaze integer.

This foundation introduces a namespaced `ProviderIdentity` (`provider` plus the provider-owned value), normalized provider show/episode records, provider-scoped snapshots, a small `MetadataProvider` protocol, and a TVMaze adapter backed by the existing deterministic cache.

## Safety and determinism

Provider namespaces are normalized with Unicode NFKC plus case folding. Identity values remain provider-owned strings. TVMaze compatibility requires canonical positive integers.

Search and episode snapshots include the provider namespace, cache request key, and cache snapshot hash so identities from different providers cannot collide.

The TVMaze adapter normalizes only cached or explicitly injected provider responses. It preserves episode airdate and episode type because special/date numbering depends on that evidence. Invalid episode entries and duplicate aired coordinates are surfaced as catalog errors rather than guessed through.

Passing a foreign provider identity to the TVMaze adapter fails before any network access.

## Current migration boundary

TVMaze remains the only configured provider. This slice establishes normalized provider-facing domain models without changing existing parser, resolver, planner, destination, duplicate, report, or preflight outcomes.

Later #40 work can migrate those layers incrementally behind the same interface. Adding a second provider should require an adapter plus synthetic offline fixtures, not provider-specific branches in filename parsing or destination policy.
