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
