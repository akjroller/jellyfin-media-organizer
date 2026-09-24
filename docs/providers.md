# Metadata providers

JMO's planner currently uses TVMaze as its default provider. The provider
boundary is also available for an optional TMDb adapter, so installations that
already use TMDb can test the same search/catalog normalization without
changing the default or sharing credentials with JMO.

The adapter is intentionally opt-in at the library boundary while its plan
schema integration is being expanded. It requires a TMDb v4 read access token
and keeps that token in memory only:

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

TMDb is not silently selected by existing plans. TVMaze remains the stable
default until the provider-neutral plan schema and CLI configuration can carry
TMDb identities end-to-end.
